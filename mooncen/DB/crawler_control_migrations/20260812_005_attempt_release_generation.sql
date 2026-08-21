-- Immutable release-generation evidence for crawler task attempts.
--
-- Existing attempts cannot be reconstructed safely, so both columns remain
-- NULL for legacy rows.  Every new production/staging attempt created by the
-- dedicated worker role must carry the exact current desired release identity.
-- This is a forward migration: never fold these columns into 20260810_001,
-- whose checksum may already be recorded by an installed control plane.

DO $$
BEGIN
    IF to_regclass('public.ops_crawler_control_database_marker') IS NULL
       OR NOT EXISTS (
           SELECT 1
           FROM public.ops_crawler_control_database_marker
           WHERE singleton IS TRUE
             AND database_name = current_database()::name
       ) THEN
        RAISE EXCEPTION 'attempt release generation requires the marked crawler-control database';
    END IF;
    IF to_regclass('public.ops_crawler_task_attempts') IS NULL
       OR to_regclass('public.ops_crawler_rollout_worker_snapshots') IS NULL
       OR to_regclass('public.ops_crawler_worker_desired_state') IS NULL
       OR to_regclass('public.ops_crawler_release_rollouts') IS NULL
       OR to_regclass('public.ops_jobs') IS NULL
       OR NOT EXISTS (
           SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_crawler_worker'
       ) THEN
        RAISE EXCEPTION 'attempt release generation requires the crawler release and worker contract';
    END IF;
END;
$$;

ALTER TABLE ops_crawler_task_attempts
    ADD COLUMN rollout_id UUID,
    ADD COLUMN release_generation BIGINT;

ALTER TABLE ops_crawler_task_attempts
    ADD CONSTRAINT chk_ops_crawler_task_attempt_release_pair
        CHECK ((rollout_id IS NULL) = (release_generation IS NULL)),
    ADD CONSTRAINT chk_ops_crawler_task_attempt_release_generation
        CHECK (release_generation IS NULL OR release_generation > 0),
    ADD CONSTRAINT fk_ops_crawler_task_attempt_rollout
        FOREIGN KEY (rollout_id)
        REFERENCES ops_crawler_release_rollouts(id) ON DELETE RESTRICT;

CREATE INDEX idx_ops_crawler_task_attempts_release_generation
    ON ops_crawler_task_attempts
        (rollout_id, release_generation, started_at DESC, id)
    WHERE rollout_id IS NOT NULL;

-- This trigger is SECURITY DEFINER because the worker's ops_jobs SELECT policy
-- is itself fenced by the attempt that is being inserted.  The function uses
-- only server-side enrollment, job, desired-state, and immutable roster data;
-- client input can never select a different release generation.
CREATE OR REPLACE FUNCTION enforce_crawler_attempt_release_generation_insert()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    worker_agent UUID;
    job_environment TEXT;
    job_code_version TEXT;
    job_artifact_digest TEXT;
    job_config_revision TEXT;
    desired_rollout_id UUID;
    desired_generation BIGINT;
    desired_code_version TEXT;
    desired_artifact_digest TEXT;
    desired_config_revision TEXT;
    snapshot_required BOOLEAN;
BEGIN
    IF NOT pg_has_role(session_user, 'mooncen_crawler_worker', 'member') THEN
        RETURN NEW;
    END IF;

    SELECT binding.agent_id
    INTO STRICT worker_agent
    FROM public.ops_crawler_agent_bindings binding
    JOIN public.ops_agents agent
      ON agent.id = binding.agent_id
     AND agent.environment = binding.environment
    WHERE binding.binding_type = 'worker'
      AND binding.database_login = session_user
      AND agent.credential_hint = 'crawler-worker:' || session_user
      AND agent.status <> 'disabled'
      AND agent.maintenance_mode IS FALSE;

    SELECT job.environment, job.required_code_version,
           job.artifact_digest, job.config_revision
    INTO STRICT job_environment, job_code_version,
         job_artifact_digest, job_config_revision
    FROM public.ops_jobs job
    WHERE job.id = NEW.job_id;

    IF job_environment = 'development' THEN
        NEW.rollout_id := NULL;
        NEW.release_generation := NULL;
        RETURN NEW;
    END IF;
    IF job_environment NOT IN ('production', 'staging')
       OR NEW.agent_id IS DISTINCT FROM worker_agent THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'crawler worker attempt has no exact protected-environment enrollment';
    END IF;

    BEGIN
        SELECT desired.rollout_id, desired.generation,
               desired.code_version, desired.artifact_digest,
               desired.config_revision
        INTO STRICT desired_rollout_id, desired_generation,
             desired_code_version, desired_artifact_digest,
             desired_config_revision
        FROM public.ops_crawler_worker_desired_state desired
        WHERE desired.environment = job_environment
          AND desired.agent_id = worker_agent
          AND desired.desired_status = 'active'
          AND desired.not_before <= clock_timestamp()
        FOR KEY SHARE;
    EXCEPTION
        WHEN no_data_found OR too_many_rows THEN
            RAISE EXCEPTION USING
                ERRCODE = '42501',
                MESSAGE = 'crawler worker has no single exact current desired release';
    END;

    SELECT rollout.worker_snapshot_required
    INTO STRICT snapshot_required
    FROM public.ops_crawler_release_rollouts rollout
    WHERE rollout.id = desired_rollout_id
      AND rollout.environment = job_environment;

    IF NEW.worker_code_version IS DISTINCT FROM job_code_version
       OR NEW.artifact_digest IS DISTINCT FROM job_artifact_digest
       OR NEW.config_revision IS DISTINCT FROM job_config_revision
       OR NEW.worker_code_version IS DISTINCT FROM desired_code_version
       OR NEW.artifact_digest IS DISTINCT FROM desired_artifact_digest
       OR NEW.config_revision IS DISTINCT FROM desired_config_revision THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'crawler worker attempt differs from its job and current desired release identity';
    END IF;

    -- Rollouts created before the immutable roster migration were explicitly
    -- marked FALSE because their generation roster cannot be reconstructed.
    -- Keep those attempts operational but unattributed.  A later rollout or
    -- generation is marked TRUE and therefore receives exact server-owned
    -- generation evidence.
    IF snapshot_required IS FALSE THEN
        NEW.rollout_id := NULL;
        NEW.release_generation := NULL;
        RETURN NEW;
    END IF;
    IF snapshot_required IS NOT TRUE
       OR NOT EXISTS (
           SELECT 1
           FROM public.ops_crawler_rollout_worker_snapshots snapshot
           WHERE snapshot.environment = job_environment
             AND snapshot.rollout_id = desired_rollout_id
             AND snapshot.generation = desired_generation
             AND snapshot.agent_id = worker_agent
             AND snapshot.desired_status = 'active'
             AND snapshot.code_version = desired_code_version
             AND snapshot.artifact_digest = desired_artifact_digest
             AND snapshot.config_revision = desired_config_revision
       ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'crawler worker attempt differs from its job and current desired release generation';
    END IF;
    NEW.rollout_id := desired_rollout_id;
    NEW.release_generation := desired_generation;
    RETURN NEW;
EXCEPTION
    WHEN no_data_found OR too_many_rows THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'crawler worker login or job identity is ambiguous';
END;
$$;

CREATE TRIGGER zy_enforce_crawler_attempt_release_generation_insert
    BEFORE INSERT ON ops_crawler_task_attempts
    FOR EACH ROW
    EXECUTE FUNCTION enforce_crawler_attempt_release_generation_insert();

CREATE OR REPLACE FUNCTION enforce_crawler_attempt_release_generation_immutable()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NEW.rollout_id IS DISTINCT FROM OLD.rollout_id
       OR NEW.release_generation IS DISTINCT FROM OLD.release_generation THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'crawler task attempt release generation is immutable';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER zy_enforce_crawler_attempt_release_generation_immutable
    BEFORE UPDATE ON ops_crawler_task_attempts
    FOR EACH ROW
    EXECUTE FUNCTION enforce_crawler_attempt_release_generation_immutable();

REVOKE ALL ON FUNCTION enforce_crawler_attempt_release_generation_insert()
    FROM PUBLIC;
REVOKE ALL ON FUNCTION enforce_crawler_attempt_release_generation_immutable()
    FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_crawler_api') THEN
        GRANT SELECT (rollout_id, release_generation)
            ON ops_crawler_task_attempts TO mooncen_crawler_api;
    END IF;
END;
$$;

COMMENT ON COLUMN ops_crawler_task_attempts.rollout_id IS
    'Exact desired rollout captured at claim time; NULL only for legacy or development attempts.';
COMMENT ON COLUMN ops_crawler_task_attempts.release_generation IS
    'Exact positive desired generation captured at claim time; paired with rollout_id.';
