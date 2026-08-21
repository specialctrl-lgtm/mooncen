-- Distributed crawler control-plane guards for the dedicated staging DB.
--
-- Apply only after schema.sql, staging_schema.sql, and the Ops control-plane
-- migrations.  This file must never be applied to the primary database.

DO $$
DECLARE
    marker_count INTEGER;
BEGIN
    IF to_regclass('public.ops_crawler_control_database_marker') IS NULL THEN
        RAISE EXCEPTION 'staging_control_plane.sql requires the dedicated database marker';
    END IF;
    EXECUTE
        'SELECT count(*) FROM public.ops_crawler_control_database_marker '
        'WHERE singleton IS TRUE AND database_name = current_database()'
    INTO marker_count;
    IF marker_count <> 1 THEN
        RAISE EXCEPTION 'staging_control_plane.sql database marker mismatch';
    END IF;
    IF to_regprocedure('public.current_crawl_batch_id()') IS NULL THEN
        RAISE EXCEPTION 'staging_control_plane.sql requires the crawler staging schema';
    END IF;
    IF to_regclass('public.ops_jobs') IS NULL THEN
        RAISE EXCEPTION 'staging_control_plane.sql requires the Ops job queue';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_crawler_worker') THEN
        RAISE EXCEPTION 'staging_control_plane.sql requires the dedicated mooncen_crawler_worker role';
    END IF;
END;
$$;

-- A worker heartbeat is the central liveness proof used by scheduling and
-- rollout policy.  Dedicated worker logins may refresh only the ops_agents row
-- that enrollment bound to their exact session_user; they cannot register a
-- new identity, rename a host, change capabilities, or revive a disabled row.
CREATE OR REPLACE FUNCTION public.enforce_crawler_worker_agent_heartbeat()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    expected_credential TEXT := 'crawler-worker:' || session_user;
BEGIN
    IF NOT pg_has_role(session_user, 'mooncen_crawler_worker', 'member') THEN
        RETURN NEW;
    END IF;
    IF TG_OP <> 'UPDATE'
       OR OLD.credential_hint IS DISTINCT FROM expected_credential
       OR NEW.credential_hint IS DISTINCT FROM expected_credential
       OR OLD.status NOT IN ('unknown', 'healthy')
       OR OLD.maintenance_mode IS TRUE
       OR NEW.status <> 'healthy'
       OR NEW.last_seen_at IS NULL
       OR ROW(
            NEW.id, NEW.name, NEW.hostname, NEW.environment, NEW.os_type,
            NEW.ip_address, NEW.capabilities, NEW.credential_hint,
            NEW.maintenance_mode, NEW.created_at
          ) IS DISTINCT FROM ROW(
            OLD.id, OLD.name, OLD.hostname, OLD.environment, OLD.os_type,
            OLD.ip_address, OLD.capabilities, OLD.credential_hint,
            OLD.maintenance_mode, OLD.created_at
          ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'crawler worker heartbeat escaped its enrolled agent identity';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM public.ops_crawler_agent_bindings binding
        WHERE binding.agent_id = OLD.id
          AND binding.environment = OLD.environment
          AND binding.binding_type = 'worker'
          AND binding.database_login = session_user
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'crawler worker heartbeat has no server-side login binding';
    END IF;
    NEW.last_seen_at := clock_timestamp();
    NEW.updated_at := NEW.last_seen_at;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.enforce_crawler_worker_agent_heartbeat() FROM PUBLIC;

DROP TRIGGER IF EXISTS zz_enforce_crawler_worker_agent_heartbeat ON public.ops_agents;
CREATE TRIGGER zz_enforce_crawler_worker_agent_heartbeat
    BEFORE UPDATE ON public.ops_agents
    FOR EACH ROW
    EXECUTE FUNCTION public.enforce_crawler_worker_agent_heartbeat();

-- Resolve the immutable server-side identity for the current worker login.
-- session_user is deliberately used instead of current_user so SET ROLE cannot
-- detach a connection from the login enrolled by the central control plane.
CREATE OR REPLACE FUNCTION public.current_crawler_worker_agent_id()
RETURNS UUID
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT CASE
        WHEN pg_has_role(session_user, 'mooncen_crawler_worker', 'member') THEN (
            SELECT agent.id
            FROM public.ops_agents agent
            JOIN public.ops_crawler_agent_bindings binding
              ON binding.agent_id = agent.id
             AND binding.environment = agent.environment
             AND binding.binding_type = 'worker'
             AND binding.database_login = session_user
            WHERE agent.credential_hint = 'crawler-worker:' || session_user
              AND agent.status <> 'disabled'
              AND agent.maintenance_mode IS FALSE
        )
        ELSE NULL::uuid
    END
$$;

CREATE OR REPLACE FUNCTION public.current_crawler_worker_environment()
RETURNS TEXT
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT CASE
        WHEN pg_has_role(session_user, 'mooncen_crawler_worker', 'member') THEN (
            SELECT agent.environment
            FROM public.ops_agents agent
            JOIN public.ops_crawler_agent_bindings binding
              ON binding.agent_id = agent.id
             AND binding.environment = agent.environment
             AND binding.binding_type = 'worker'
             AND binding.database_login = session_user
            WHERE agent.credential_hint = 'crawler-worker:' || session_user
              AND agent.status <> 'disabled'
              AND agent.maintenance_mode IS FALSE
        )
        ELSE NULL::text
    END
$$;

REVOKE ALL ON FUNCTION public.current_crawler_worker_agent_id() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.current_crawler_worker_environment() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.current_crawler_worker_agent_id()
    TO mooncen_crawler_worker;
GRANT EXECUTE ON FUNCTION public.current_crawler_worker_environment()
    TO mooncen_crawler_worker;

CREATE OR REPLACE FUNCTION public.current_crawler_reporter_agent_id()
RETURNS UUID
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT CASE
        WHEN pg_has_role(session_user, 'mooncen_crawler_reporter', 'member') THEN (
            SELECT agent.id
            FROM public.ops_agents agent
            JOIN public.ops_crawler_agent_bindings binding
              ON binding.agent_id = agent.id
             AND binding.environment = agent.environment
             AND binding.binding_type = 'reporter'
             AND binding.database_login = session_user
            WHERE agent.status <> 'disabled'
              AND agent.maintenance_mode IS FALSE
        )
        ELSE NULL::uuid
    END
$$;

CREATE OR REPLACE FUNCTION public.current_crawler_reporter_environment()
RETURNS TEXT
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT CASE
        WHEN pg_has_role(session_user, 'mooncen_crawler_reporter', 'member') THEN (
            SELECT agent.environment
            FROM public.ops_agents agent
            JOIN public.ops_crawler_agent_bindings binding
              ON binding.agent_id = agent.id
             AND binding.environment = agent.environment
             AND binding.binding_type = 'reporter'
             AND binding.database_login = session_user
            WHERE agent.status <> 'disabled'
              AND agent.maintenance_mode IS FALSE
        )
        ELSE NULL::text
    END
$$;

REVOKE ALL ON FUNCTION public.current_crawler_reporter_agent_id() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.current_crawler_reporter_environment() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.current_crawler_reporter_agent_id()
    TO mooncen_crawler_reporter;
GRANT EXECUTE ON FUNCTION public.current_crawler_reporter_environment()
    TO mooncen_crawler_reporter;

CREATE OR REPLACE FUNCTION public.is_crawler_managed_agent(candidate UUID)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.ops_crawler_agent_bindings binding
        WHERE binding.agent_id = candidate
    )
$$;

CREATE OR REPLACE FUNCTION public.is_crawler_control_job(candidate UUID)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.ops_crawler_batch_tasks task
        WHERE task.job_id = candidate
    )
$$;

CREATE OR REPLACE FUNCTION public.is_current_crawler_worker_job(candidate UUID)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM public.ops_jobs job
        JOIN public.ops_crawler_task_attempts attempt
          ON attempt.job_id = job.id
         AND attempt.attempt_no = job.attempt_no
         AND attempt.lease_epoch = job.lease_epoch
        WHERE job.id = candidate
          AND job.environment = public.current_crawler_worker_environment()
          AND attempt.agent_id = public.current_crawler_worker_agent_id()
    )
$$;

-- Historical ownership is sufficient for reading an already-completed job,
-- but every mutable runtime side effect must still be protected by the live
-- lease.  Keep the two predicates separate so RLS cannot accidentally turn a
-- past attempt into permanent write authority.
CREATE OR REPLACE FUNCTION public.is_live_crawler_worker_job(candidate UUID)
RETURNS BOOLEAN
LANGUAGE sql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM public.ops_jobs job
        JOIN public.ops_crawler_task_attempts attempt
          ON attempt.job_id = job.id
         AND attempt.attempt_no = job.attempt_no
         AND attempt.lease_epoch = job.lease_epoch
         AND attempt.lease_token = job.lease_token
        WHERE job.id = candidate
          AND job.environment = public.current_crawler_worker_environment()
          AND job.agent_id = public.current_crawler_worker_agent_id()
          AND job.status IN ('assigned', 'running')
          AND job.leased_until > clock_timestamp()
          AND attempt.agent_id = public.current_crawler_worker_agent_id()
          AND attempt.status = 'running'
    )
$$;

REVOKE ALL ON FUNCTION public.is_crawler_managed_agent(UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.is_crawler_control_job(UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.is_current_crawler_worker_job(UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.is_live_crawler_worker_job(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.is_current_crawler_worker_job(UUID)
    TO mooncen_crawler_worker;
GRANT EXECUTE ON FUNCTION public.is_live_crawler_worker_job(UUID)
    TO mooncen_crawler_worker;

-- A dedicated worker may claim only an eligible job for the release currently
-- assigned to its enrolled agent.  Every later mutation is fenced to that same
-- agent.  The worker can still report its own result (it necessarily controls
-- its crawler process), but cannot copy another worker's token or reassign that
-- worker's job to itself.
CREATE OR REPLACE FUNCTION public.enforce_crawler_worker_job_transition()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    worker_agent UUID;
    worker_environment TEXT;
    claim_is_compatible BOOLEAN;
BEGIN
    IF NOT pg_has_role(session_user, 'mooncen_crawler_worker', 'member') THEN
        RETURN NEW;
    END IF;

    worker_agent := public.current_crawler_worker_agent_id();
    worker_environment := public.current_crawler_worker_environment();
    IF worker_agent IS NULL OR worker_environment IS NULL THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'crawler worker login is not bound to an enabled agent';
    END IF;
    IF OLD.environment IS DISTINCT FROM worker_environment
       OR OLD.job_type NOT IN ('crawler_run', 'crawler_retry', 'agent_command') THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'crawler worker attempted to mutate an out-of-scope job';
    END IF;
    IF ROW(
          NEW.id, NEW.job_type, NEW.environment, NEW.service_id,
          NEW.parent_job_id, NEW.requested_by, NEW.target_key,
          NEW.deduplication_key, NEW.parameters, NEW.max_retries,
          NEW.created_at, NEW.required_code_version, NEW.artifact_digest,
          NEW.config_revision, NEW.cancel_requested_at
       ) IS DISTINCT FROM ROW(
          OLD.id, OLD.job_type, OLD.environment, OLD.service_id,
          OLD.parent_job_id, OLD.requested_by, OLD.target_key,
          OLD.deduplication_key, OLD.parameters, OLD.max_retries,
          OLD.created_at, OLD.required_code_version, OLD.artifact_digest,
          OLD.config_revision, OLD.cancel_requested_at
       ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'crawler worker attempted to rewrite immutable job input';
    END IF;

    IF OLD.status = 'queued' THEN
        SELECT EXISTS (
            SELECT 1
            FROM public.ops_crawler_worker_desired_state desired
            WHERE desired.agent_id = worker_agent
              AND desired.environment = worker_environment
              AND desired.desired_status = 'active'
              AND desired.not_before <= clock_timestamp()
              AND desired.code_version = OLD.required_code_version
              AND desired.artifact_digest = OLD.artifact_digest
              AND desired.config_revision = OLD.config_revision
        )
        INTO claim_is_compatible;
        IF OLD.available_at > clock_timestamp()
           OR OLD.cancel_requested_at IS NOT NULL
           OR (
               OLD.job_type IN ('crawler_run', 'crawler_retry')
               AND NOT public.is_crawler_control_job(OLD.id)
           )
           OR (
               OLD.job_type = 'agent_command'
               AND OLD.agent_id IS DISTINCT FROM worker_agent
           )
           OR OLD.agent_id IS NOT NULL AND OLD.agent_id IS DISTINCT FROM worker_agent
           OR NEW.status <> 'assigned'
           OR NEW.agent_id IS DISTINCT FROM worker_agent
           OR NEW.lease_token IS NULL
           OR NEW.leased_until IS NULL
           OR NEW.leased_until <= clock_timestamp()
           OR NEW.leased_until > clock_timestamp() + interval '2 hours'
           OR NEW.lease_epoch <> OLD.lease_epoch + 1
           OR NEW.attempt_no <> OLD.attempt_no + 1
           OR NEW.retry_count <> OLD.retry_count
           OR NEW.progress <> 1
           OR NEW.assigned_at IS NULL
           OR NEW.heartbeat_at IS NULL
           OR NEW.finished_at IS NOT NULL
           OR NOT claim_is_compatible THEN
            RAISE EXCEPTION USING
                ERRCODE = '42501',
                MESSAGE = 'crawler worker claim escaped its enrolled release or lease contract';
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.status NOT IN ('assigned', 'running')
       OR (
           OLD.cancel_requested_at IS NOT NULL
           AND NEW.status <> 'cancelled'
       )
       OR OLD.agent_id IS DISTINCT FROM worker_agent
       OR OLD.lease_token IS NULL
       OR OLD.leased_until IS NULL
       OR OLD.leased_until <= clock_timestamp()
       OR OLD.lease_epoch <= 0
       OR OLD.attempt_no <= 0
       OR NEW.lease_epoch <> OLD.lease_epoch
       OR NEW.attempt_no <> OLD.attempt_no THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'crawler worker attempted to use another agent lease';
    END IF;

    IF NEW.status IN ('assigned', 'running') THEN
        IF NEW.agent_id IS DISTINCT FROM worker_agent
           OR NEW.lease_token IS DISTINCT FROM OLD.lease_token
           OR NEW.leased_until IS NULL
           OR NEW.leased_until <= clock_timestamp()
           OR NEW.leased_until > clock_timestamp() + interval '2 hours'
           OR NEW.retry_count <> OLD.retry_count
           OR NEW.finished_at IS NOT NULL
           OR NEW.progress < OLD.progress THEN
            RAISE EXCEPTION USING
                ERRCODE = '42501',
                MESSAGE = 'crawler worker active lease transition is invalid';
        END IF;
    ELSIF NEW.status = 'queued' THEN
        IF NEW.agent_id IS NOT NULL
           OR NEW.lease_token IS NOT NULL
           OR NEW.leased_until IS NOT NULL
           OR NEW.retry_count <> OLD.retry_count + 1
           OR NEW.progress <> 0
           OR NEW.finished_at IS NOT NULL
           OR NEW.available_at <= clock_timestamp() THEN
            RAISE EXCEPTION USING
                ERRCODE = '42501',
                MESSAGE = 'crawler worker retry transition is invalid';
        END IF;
    ELSIF NEW.status IN (
        'success', 'partial_success', 'failed', 'cancelled',
        'timed_out', 'blocked', 'dead_lettered'
    ) THEN
        IF NEW.agent_id IS DISTINCT FROM worker_agent
           OR NEW.lease_token IS NOT NULL
           OR NEW.leased_until IS NOT NULL
           OR NEW.retry_count <> OLD.retry_count
           OR NEW.finished_at IS NULL THEN
            RAISE EXCEPTION USING
                ERRCODE = '42501',
                MESSAGE = 'crawler worker terminal transition is invalid';
        END IF;
    ELSE
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'crawler worker job transition is not allowed';
    END IF;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.enforce_crawler_worker_job_transition() FROM PUBLIC;

DROP TRIGGER IF EXISTS zz_enforce_crawler_worker_job_transition ON public.ops_jobs;
CREATE TRIGGER zz_enforce_crawler_worker_job_transition
    BEFORE UPDATE ON public.ops_jobs
    FOR EACH ROW
    EXECUTE FUNCTION public.enforce_crawler_worker_job_transition();

-- The job row and its append-only attempt are one claim transaction.  A buggy
-- or hostile worker cannot commit an assigned lease without the matching
-- attempt evidence, even though the job UPDATE necessarily precedes the
-- attempt INSERT in the client transaction.
CREATE OR REPLACE FUNCTION public.enforce_crawler_worker_active_attempt()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NOT pg_has_role(session_user, 'mooncen_crawler_worker', 'member')
       OR NEW.status NOT IN ('assigned', 'running') THEN
        RETURN NEW;
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM public.ops_crawler_task_attempts attempt
        WHERE attempt.job_id = NEW.id
          AND attempt.agent_id = NEW.agent_id
          AND attempt.lease_token = NEW.lease_token
          AND attempt.lease_epoch = NEW.lease_epoch
          AND attempt.attempt_no = NEW.attempt_no
          AND attempt.status = 'running'
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'crawler worker active lease has no matching attempt evidence';
    END IF;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.enforce_crawler_worker_active_attempt() FROM PUBLIC;

DROP TRIGGER IF EXISTS zz_enforce_crawler_worker_active_attempt ON public.ops_jobs;
CREATE CONSTRAINT TRIGGER zz_enforce_crawler_worker_active_attempt
    AFTER INSERT OR UPDATE ON public.ops_jobs
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION public.enforce_crawler_worker_active_attempt();

-- Attempts can only be created for the active lease that the same login just
-- claimed.  Column grants make attempt identity immutable after insertion.
CREATE OR REPLACE FUNCTION public.enforce_crawler_worker_attempt_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NOT pg_has_role(session_user, 'mooncen_crawler_worker', 'member') THEN
        RETURN NEW;
    END IF;
    IF NEW.agent_id IS DISTINCT FROM public.current_crawler_worker_agent_id()
       OR NEW.status <> 'running'
       OR NOT EXISTS (
           SELECT 1
           FROM public.ops_jobs job
           WHERE job.id = NEW.job_id
             AND job.environment = public.current_crawler_worker_environment()
             AND job.agent_id = NEW.agent_id
             AND job.lease_token = NEW.lease_token
             AND job.lease_epoch = NEW.lease_epoch
             AND job.attempt_no = NEW.attempt_no
             AND job.status IN ('assigned', 'running')
             AND job.leased_until > clock_timestamp()
             AND (
                 job.environment = 'development'
                 OR (
                     job.environment IN ('production', 'staging')
                     AND NEW.worker_code_version = job.required_code_version
                     AND NEW.artifact_digest = job.artifact_digest
                     AND NEW.config_revision = job.config_revision
                     AND EXISTS (
                         SELECT 1
                         FROM public.ops_crawler_worker_desired_state desired
                         JOIN public.ops_crawler_release_rollouts rollout
                           ON rollout.id = desired.rollout_id
                          AND rollout.environment = desired.environment
                         WHERE desired.environment = job.environment
                           AND desired.agent_id = NEW.agent_id
                           AND desired.desired_status = 'active'
                           AND desired.not_before <= clock_timestamp()
                           AND desired.code_version = NEW.worker_code_version
                           AND desired.artifact_digest = NEW.artifact_digest
                           AND desired.config_revision = NEW.config_revision
                           AND (
                               (
                                   rollout.worker_snapshot_required IS FALSE
                                   AND NEW.rollout_id IS NULL
                                   AND NEW.release_generation IS NULL
                               )
                               OR (
                                   rollout.worker_snapshot_required IS TRUE
                                   AND desired.rollout_id = NEW.rollout_id
                                   AND desired.generation = NEW.release_generation
                               )
                           )
                     )
                 )
             )
       ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'crawler worker attempt does not match its active lease';
    END IF;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.enforce_crawler_worker_attempt_insert() FROM PUBLIC;

DROP TRIGGER IF EXISTS zz_enforce_crawler_worker_attempt_insert
    ON public.ops_crawler_task_attempts;
CREATE TRIGGER zz_enforce_crawler_worker_attempt_insert
    BEFORE INSERT ON public.ops_crawler_task_attempts
    FOR EACH ROW
    EXECUTE FUNCTION public.enforce_crawler_worker_attempt_insert();

-- A worker may seal its currently leased attempt exactly once.  Reapers use
-- the separate control role and are therefore not constrained by this worker
-- transition, but a worker cannot rewrite old terminal metrics or manufacture
-- evidence after its lease expires.
CREATE OR REPLACE FUNCTION public.enforce_crawler_worker_attempt_transition()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    matching_job UUID;
BEGIN
    IF NOT pg_has_role(session_user, 'mooncen_crawler_worker', 'member') THEN
        RETURN NEW;
    END IF;
    IF OLD.status <> 'running'
       OR ROW(
           NEW.id, NEW.job_id, NEW.attempt_no, NEW.lease_epoch,
           NEW.lease_token, NEW.agent_id, NEW.worker_code_version,
           NEW.artifact_digest, NEW.config_revision, NEW.rollout_id,
           NEW.release_generation, NEW.started_at, NEW.created_at
       ) IS DISTINCT FROM ROW(
           OLD.id, OLD.job_id, OLD.attempt_no, OLD.lease_epoch,
           OLD.lease_token, OLD.agent_id, OLD.worker_code_version,
           OLD.artifact_digest, OLD.config_revision, OLD.rollout_id,
           OLD.release_generation, OLD.started_at, OLD.created_at
       )
       OR OLD.agent_id IS DISTINCT FROM public.current_crawler_worker_agent_id()
       OR NEW.status NOT IN (
           'success', 'partial_success', 'failed', 'timed_out', 'cancelled'
       ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'crawler worker attempt transition is not a one-time terminal seal';
    END IF;

    SELECT job.id
    INTO matching_job
    FROM public.ops_jobs job
    WHERE job.id = OLD.job_id
      AND job.environment = public.current_crawler_worker_environment()
      AND job.agent_id = OLD.agent_id
      AND job.lease_token = OLD.lease_token
      AND job.lease_epoch = OLD.lease_epoch
      AND job.attempt_no = OLD.attempt_no
      AND job.status IN ('assigned', 'running')
      AND job.leased_until > clock_timestamp()
    FOR KEY SHARE;
    IF matching_job IS NULL THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'crawler worker cannot seal an attempt without its live lease';
    END IF;
    NEW.finished_at := clock_timestamp();
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.enforce_crawler_worker_attempt_transition()
    FROM PUBLIC;

DROP TRIGGER IF EXISTS zz_enforce_crawler_worker_attempt_transition
    ON public.ops_crawler_task_attempts;
CREATE TRIGGER zz_enforce_crawler_worker_attempt_transition
    BEFORE UPDATE ON public.ops_crawler_task_attempts
    FOR EACH ROW
    EXECUTE FUNCTION public.enforce_crawler_worker_attempt_transition();

-- Observation timestamps are server-owned.  Active observations require the
-- current live lease; the single finished observation must describe the
-- terminal attempt exactly and carry the same immutable metrics document.
CREATE OR REPLACE FUNCTION public.enforce_crawler_worker_observation_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    attempt_status TEXT;
    attempt_metrics JSONB;
    attempt_finished_at TIMESTAMPTZ;
    worker_agent UUID;
BEGIN
    IF NOT pg_has_role(session_user, 'mooncen_crawler_worker', 'member') THEN
        RETURN NEW;
    END IF;
    worker_agent := public.current_crawler_worker_agent_id();
    SELECT attempt.status, attempt.metrics, attempt.finished_at
    INTO attempt_status, attempt_metrics, attempt_finished_at
    FROM public.ops_crawler_task_attempts attempt
    JOIN public.ops_jobs job
      ON job.id = attempt.job_id
     AND job.attempt_no = attempt.attempt_no
     AND job.lease_epoch = attempt.lease_epoch
    WHERE attempt.id = NEW.attempt_id
      AND attempt.job_id = NEW.job_id
      AND attempt.attempt_no = NEW.attempt_no
      AND attempt.lease_epoch = NEW.lease_epoch
      AND attempt.agent_id = worker_agent
      AND job.environment = public.current_crawler_worker_environment()
      AND job.agent_id = worker_agent
      AND job.lease_token = attempt.lease_token
      AND job.status IN ('assigned', 'running')
      AND job.leased_until > clock_timestamp()
    FOR KEY SHARE OF job, attempt;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'crawler worker observation does not match its live lease';
    END IF;

    IF NEW.observation_kind IN (
        'claimed', 'started', 'heartbeat', 'progress', 'result', 'error'
    ) THEN
        IF attempt_status <> 'running' OR attempt_finished_at IS NOT NULL THEN
            RAISE EXCEPTION USING
                ERRCODE = '42501',
                MESSAGE = 'crawler worker active observation targets a sealed attempt';
        END IF;
    ELSIF NEW.observation_kind = 'finished' THEN
        IF attempt_status NOT IN (
               'success', 'partial_success', 'failed', 'timed_out', 'cancelled'
           )
           OR attempt_finished_at IS NULL
           OR NEW.payload->>'attempt_status' IS DISTINCT FROM attempt_status
           OR NEW.payload->'result' IS DISTINCT FROM attempt_metrics
           OR NEW.payload->>'job_status' IS NULL
           OR NOT (NEW.payload->>'job_status' = ANY (ARRAY[
               'queued', 'success', 'partial_success', 'failed', 'cancelled',
               'timed_out', 'blocked', 'dead_lettered'
           ]))
           OR (
               NEW.payload->>'job_status' = 'queued'
               AND attempt_status NOT IN ('failed', 'timed_out')
           )
           OR (
               NEW.payload->>'job_status' = 'blocked'
               AND attempt_status <> 'failed'
           )
           OR (
               NEW.payload->>'job_status' = 'dead_lettered'
               AND attempt_status NOT IN ('failed', 'timed_out')
           )
           OR (
               NEW.payload->>'job_status' NOT IN ('queued', 'blocked', 'dead_lettered')
               AND NEW.payload->>'job_status' IS DISTINCT FROM attempt_status
           ) THEN
            RAISE EXCEPTION USING
                ERRCODE = '42501',
                MESSAGE = 'crawler worker finished observation does not match the sealed attempt';
        END IF;
    ELSE
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'crawler worker cannot publish this observation kind';
    END IF;
    NEW.observed_at := clock_timestamp();
    NEW.created_at := NEW.observed_at;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.enforce_crawler_worker_observation_insert()
    FROM PUBLIC;

DROP TRIGGER IF EXISTS zz_enforce_crawler_worker_observation_insert
    ON public.ops_crawler_task_observations;
CREATE TRIGGER zz_enforce_crawler_worker_observation_insert
    BEFORE INSERT ON public.ops_crawler_task_observations
    FOR EACH ROW
    EXECUTE FUNCTION public.enforce_crawler_worker_observation_insert();

-- Both sides of terminalization are deferred so the client can seal the
-- attempt, append evidence, and then clear the job lease in one transaction.
CREATE OR REPLACE FUNCTION public.enforce_crawler_worker_terminal_job_commit()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NOT pg_has_role(session_user, 'mooncen_crawler_worker', 'member')
       OR OLD.status NOT IN ('assigned', 'running')
       OR NEW.status IN ('assigned', 'running') THEN
        RETURN NEW;
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM public.ops_crawler_task_attempts attempt
        JOIN public.ops_crawler_task_observations observation
          ON observation.attempt_id = attempt.id
         AND observation.observation_kind = 'finished'
        WHERE attempt.job_id = NEW.id
          AND attempt.attempt_no = OLD.attempt_no
          AND attempt.lease_epoch = OLD.lease_epoch
          AND attempt.lease_token = OLD.lease_token
          AND attempt.agent_id = OLD.agent_id
          AND attempt.status <> 'running'
          AND attempt.finished_at IS NOT NULL
          AND attempt.metrics IS NOT DISTINCT FROM NEW.result
          AND observation.payload->>'attempt_status' IS NOT DISTINCT FROM attempt.status
          AND observation.payload->>'job_status' IS NOT DISTINCT FROM NEW.status
          AND observation.payload->'result' IS NOT DISTINCT FROM attempt.metrics
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'crawler worker terminal job has no exact immutable attempt evidence';
    END IF;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.enforce_crawler_worker_terminal_job_commit()
    FROM PUBLIC;

DROP TRIGGER IF EXISTS zz_enforce_crawler_worker_terminal_job_commit
    ON public.ops_jobs;
CREATE CONSTRAINT TRIGGER zz_enforce_crawler_worker_terminal_job_commit
    AFTER UPDATE ON public.ops_jobs
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION public.enforce_crawler_worker_terminal_job_commit();

CREATE OR REPLACE FUNCTION public.enforce_crawler_worker_terminal_attempt_commit()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NOT pg_has_role(session_user, 'mooncen_crawler_worker', 'member')
       OR OLD.status <> 'running' OR NEW.status = 'running' THEN
        RETURN NEW;
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM public.ops_jobs job
        JOIN public.ops_crawler_task_observations observation
          ON observation.attempt_id = NEW.id
         AND observation.observation_kind = 'finished'
        WHERE job.id = NEW.job_id
          AND job.attempt_no = NEW.attempt_no
          AND job.lease_epoch = NEW.lease_epoch
          AND job.status IN (
              'queued', 'success', 'partial_success', 'failed', 'cancelled',
              'timed_out', 'blocked', 'dead_lettered'
          )
          AND NEW.metrics IS NOT DISTINCT FROM job.result
          AND observation.payload->>'attempt_status' IS NOT DISTINCT FROM NEW.status
          AND observation.payload->>'job_status' IS NOT DISTINCT FROM job.status
          AND observation.payload->'result' IS NOT DISTINCT FROM NEW.metrics
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'crawler worker sealed attempt was not atomically terminalized';
    END IF;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.enforce_crawler_worker_terminal_attempt_commit()
    FROM PUBLIC;

DROP TRIGGER IF EXISTS zz_enforce_crawler_worker_terminal_attempt_commit
    ON public.ops_crawler_task_attempts;
CREATE CONSTRAINT TRIGGER zz_enforce_crawler_worker_terminal_attempt_commit
    AFTER UPDATE ON public.ops_crawler_task_attempts
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION public.enforce_crawler_worker_terminal_attempt_commit();

-- RLS keeps lease tokens and mutable evidence from crossing worker identities.
-- Non-worker control/API/applier sessions retain their explicit table grants;
-- only members of the dedicated worker group are narrowed by these policies.
ALTER TABLE public.ops_agents ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS crawler_worker_agent_scope ON public.ops_agents;
CREATE POLICY crawler_worker_agent_scope ON public.ops_agents
    USING (
        (
            NOT pg_has_role(session_user, 'mooncen_crawler_worker', 'member')
            AND NOT pg_has_role(session_user, 'mooncen_crawler_reporter', 'member')
        )
        OR id = public.current_crawler_worker_agent_id()
        OR id = public.current_crawler_reporter_agent_id()
    )
    WITH CHECK (
        NOT pg_has_role(session_user, 'mooncen_crawler_worker', 'member')
        OR id = public.current_crawler_worker_agent_id()
    );

DROP POLICY IF EXISTS crawler_managed_agent_isolation ON public.ops_agents;
CREATE POLICY crawler_managed_agent_isolation ON public.ops_agents
    AS RESTRICTIVE
    USING (
        NOT public.is_crawler_managed_agent(ops_agents.id)
        OR pg_has_role(session_user, 'mooncen_api', 'member')
        OR pg_has_role(session_user, 'mooncen_crawler_control', 'member')
        OR pg_has_role(session_user, 'mooncen_crawler_observer', 'member')
        OR pg_has_role(session_user, 'mooncen_crawler_release_admin', 'member')
        OR pg_has_role(session_user, 'mooncen_crawler_api', 'member')
        OR id = public.current_crawler_worker_agent_id()
        OR id = public.current_crawler_reporter_agent_id()
    )
    WITH CHECK (
        NOT public.is_crawler_managed_agent(ops_agents.id)
        OR pg_has_role(session_user, 'mooncen_api', 'member')
        OR id = public.current_crawler_worker_agent_id()
    );

ALTER TABLE public.ops_jobs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS crawler_worker_job_scope ON public.ops_jobs;
CREATE POLICY crawler_worker_job_scope ON public.ops_jobs
    USING (
        NOT pg_has_role(session_user, 'mooncen_crawler_worker', 'member')
        OR (
            environment = public.current_crawler_worker_environment()
            AND (
                (
                    job_type IN ('crawler_run', 'crawler_retry')
                    AND public.is_crawler_control_job(id)
                    AND (
                        agent_id = public.current_crawler_worker_agent_id()
                        OR (agent_id IS NULL AND status = 'queued')
                    )
                )
                OR (
                    job_type = 'agent_command'
                    AND agent_id = public.current_crawler_worker_agent_id()
                )
            )
        )
    )
    WITH CHECK (
        NOT pg_has_role(session_user, 'mooncen_crawler_worker', 'member')
        OR (
            environment = public.current_crawler_worker_environment()
            AND (
                (
                    job_type IN ('crawler_run', 'crawler_retry')
                    AND public.is_crawler_control_job(id)
                    AND (
                        agent_id = public.current_crawler_worker_agent_id()
                        OR (agent_id IS NULL AND status = 'queued')
                    )
                )
                OR (
                    job_type = 'agent_command'
                    AND agent_id = public.current_crawler_worker_agent_id()
                )
            )
        )
    );

DROP POLICY IF EXISTS crawler_control_job_isolation ON public.ops_jobs;
CREATE POLICY crawler_control_job_isolation ON public.ops_jobs
    AS RESTRICTIVE
    USING (
        NOT public.is_crawler_control_job(ops_jobs.id)
        OR pg_has_role(session_user, 'mooncen_api', 'member')
        OR pg_has_role(session_user, 'mooncen_crawler_control', 'member')
        OR pg_has_role(session_user, 'mooncen_crawler_finalizer', 'member')
        OR pg_has_role(session_user, 'mooncen_applier', 'member')
        OR pg_has_role(session_user, 'mooncen_crawler_observer', 'member')
        OR pg_has_role(session_user, 'mooncen_crawler_api', 'member')
        OR (
            pg_has_role(session_user, 'mooncen_crawler_worker', 'member')
            AND environment = public.current_crawler_worker_environment()
            AND (
                agent_id = public.current_crawler_worker_agent_id()
                OR (agent_id IS NULL AND status = 'queued')
            )
        )
    )
    WITH CHECK (
        NOT public.is_crawler_control_job(ops_jobs.id)
        OR pg_has_role(session_user, 'mooncen_api', 'member')
        OR pg_has_role(session_user, 'mooncen_crawler_control', 'member')
        OR (
            pg_has_role(session_user, 'mooncen_crawler_worker', 'member')
            AND environment = public.current_crawler_worker_environment()
            AND (
                agent_id = public.current_crawler_worker_agent_id()
                OR (agent_id IS NULL AND status = 'queued')
            )
        )
    );

ALTER TABLE public.ops_job_logs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS crawler_worker_job_log_scope ON public.ops_job_logs;
DROP POLICY IF EXISTS crawler_worker_job_log_insert_scope ON public.ops_job_logs;
CREATE POLICY crawler_worker_job_log_scope ON public.ops_job_logs
    FOR SELECT
    USING (
        NOT pg_has_role(session_user, 'mooncen_crawler_worker', 'member')
        OR public.is_current_crawler_worker_job(ops_job_logs.job_id)
    );
CREATE POLICY crawler_worker_job_log_insert_scope ON public.ops_job_logs
    FOR INSERT
    WITH CHECK (
        NOT pg_has_role(session_user, 'mooncen_crawler_worker', 'member')
        OR public.is_live_crawler_worker_job(ops_job_logs.job_id)
    );
DROP POLICY IF EXISTS crawler_control_job_log_isolation ON public.ops_job_logs;
CREATE POLICY crawler_control_job_log_isolation ON public.ops_job_logs
    AS RESTRICTIVE
    USING (
        NOT public.is_crawler_control_job(ops_job_logs.job_id)
        OR pg_has_role(session_user, 'mooncen_api', 'member')
        OR pg_has_role(session_user, 'mooncen_crawler_control', 'member')
        OR pg_has_role(session_user, 'mooncen_crawler_finalizer', 'member')
        OR pg_has_role(session_user, 'mooncen_crawler_observer', 'member')
        OR pg_has_role(session_user, 'mooncen_crawler_api', 'member')
        OR public.is_current_crawler_worker_job(ops_job_logs.job_id)
    )
    WITH CHECK (
        NOT public.is_crawler_control_job(ops_job_logs.job_id)
        OR pg_has_role(session_user, 'mooncen_api', 'member')
        OR pg_has_role(session_user, 'mooncen_crawler_control', 'member')
        OR public.is_live_crawler_worker_job(ops_job_logs.job_id)
    );

ALTER TABLE public.ops_crawler_runs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS crawler_worker_run_scope ON public.ops_crawler_runs;
DROP POLICY IF EXISTS crawler_worker_run_update_scope ON public.ops_crawler_runs;
DROP POLICY IF EXISTS crawler_nonworker_run_insert_scope ON public.ops_crawler_runs;
CREATE POLICY crawler_worker_run_scope ON public.ops_crawler_runs
    FOR SELECT
    USING (
        NOT pg_has_role(session_user, 'mooncen_crawler_worker', 'member')
        OR public.is_current_crawler_worker_job(ops_crawler_runs.job_id)
    );
CREATE POLICY crawler_worker_run_update_scope ON public.ops_crawler_runs
    FOR UPDATE
    USING (
        NOT pg_has_role(session_user, 'mooncen_crawler_worker', 'member')
        OR public.is_live_crawler_worker_job(ops_crawler_runs.job_id)
    )
    WITH CHECK (
        NOT pg_has_role(session_user, 'mooncen_crawler_worker', 'member')
        OR public.is_live_crawler_worker_job(ops_crawler_runs.job_id)
    );
CREATE POLICY crawler_nonworker_run_insert_scope ON public.ops_crawler_runs
    FOR INSERT
    WITH CHECK (
        NOT pg_has_role(session_user, 'mooncen_crawler_worker', 'member')
    );
DROP POLICY IF EXISTS crawler_control_run_isolation ON public.ops_crawler_runs;
CREATE POLICY crawler_control_run_isolation ON public.ops_crawler_runs
    AS RESTRICTIVE
    USING (
        job_id IS NULL
        OR NOT public.is_crawler_control_job(job_id)
        OR pg_has_role(session_user, 'mooncen_api', 'member')
        OR pg_has_role(session_user, 'mooncen_crawler_control', 'member')
        OR pg_has_role(session_user, 'mooncen_crawler_finalizer', 'member')
        OR pg_has_role(session_user, 'mooncen_crawler_observer', 'member')
        OR pg_has_role(session_user, 'mooncen_crawler_api', 'member')
        OR public.is_current_crawler_worker_job(ops_crawler_runs.job_id)
    )
    WITH CHECK (
        job_id IS NULL
        OR NOT public.is_crawler_control_job(job_id)
        OR pg_has_role(session_user, 'mooncen_api', 'member')
        OR pg_has_role(session_user, 'mooncen_crawler_control', 'member')
        OR public.is_live_crawler_worker_job(ops_crawler_runs.job_id)
    );

ALTER TABLE public.ops_crawler_task_attempts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS crawler_worker_attempt_scope ON public.ops_crawler_task_attempts;
CREATE POLICY crawler_worker_attempt_scope ON public.ops_crawler_task_attempts
    USING (
        NOT pg_has_role(session_user, 'mooncen_crawler_worker', 'member')
        OR agent_id = public.current_crawler_worker_agent_id()
    )
    WITH CHECK (
        NOT pg_has_role(session_user, 'mooncen_crawler_worker', 'member')
        OR agent_id = public.current_crawler_worker_agent_id()
    );

ALTER TABLE public.ops_crawler_task_observations ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS crawler_worker_observation_scope ON public.ops_crawler_task_observations;
CREATE POLICY crawler_worker_observation_scope ON public.ops_crawler_task_observations
    USING (
        NOT pg_has_role(session_user, 'mooncen_crawler_worker', 'member')
        OR EXISTS (
            SELECT 1 FROM public.ops_crawler_task_attempts attempt
            WHERE attempt.id = ops_crawler_task_observations.attempt_id
              AND attempt.agent_id = public.current_crawler_worker_agent_id()
        )
    )
    WITH CHECK (
        NOT pg_has_role(session_user, 'mooncen_crawler_worker', 'member')
        OR EXISTS (
            SELECT 1 FROM public.ops_crawler_task_attempts attempt
            WHERE attempt.id = ops_crawler_task_observations.attempt_id
              AND attempt.agent_id = public.current_crawler_worker_agent_id()
        )
    );

ALTER TABLE public.ops_crawler_release_reports ENABLE ROW LEVEL SECURITY;

-- A reporter supplies release facts, not chronology.  Server-owned timestamps
-- prevent a compromised reporter from pinning a far-future row at the top of
-- observer queries and shadowing every later healthy report.
CREATE OR REPLACE FUNCTION public.enforce_crawler_release_report_timestamp()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF jsonb_typeof(NEW.health->'healthy') IS DISTINCT FROM 'boolean'
       OR ((NEW.status IN ('ready', 'rolled_back')) IS DISTINCT FROM
           (NEW.health->'healthy' = 'true'::jsonb)) THEN
        RAISE EXCEPTION USING
            ERRCODE = '22023',
            MESSAGE = 'crawler release report status and health contract differs';
    END IF;
    NEW.reported_at := clock_timestamp();
    NEW.created_at := NEW.reported_at;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.enforce_crawler_release_report_timestamp()
    FROM PUBLIC;

DROP TRIGGER IF EXISTS zz_enforce_crawler_release_report_timestamp
    ON public.ops_crawler_release_reports;
CREATE TRIGGER zz_enforce_crawler_release_report_timestamp
    BEFORE INSERT ON public.ops_crawler_release_reports
    FOR EACH ROW
    EXECUTE FUNCTION public.enforce_crawler_release_report_timestamp();

DROP POLICY IF EXISTS crawler_worker_release_report_scope ON public.ops_crawler_release_reports;
CREATE POLICY crawler_worker_release_report_scope ON public.ops_crawler_release_reports
    USING (
        NOT pg_has_role(session_user, 'mooncen_crawler_reporter', 'member')
        OR agent_id = public.current_crawler_reporter_agent_id()
    )
    WITH CHECK (
        NOT pg_has_role(session_user, 'mooncen_crawler_reporter', 'member')
        OR (
            agent_id = public.current_crawler_reporter_agent_id()
            AND environment = public.current_crawler_reporter_environment()
            AND EXISTS (
                SELECT 1
                FROM public.ops_crawler_worker_desired_state desired
                WHERE desired.environment = ops_crawler_release_reports.environment
                  AND desired.worker_key = ops_crawler_release_reports.worker_key
                  AND desired.agent_id = ops_crawler_release_reports.agent_id
                  AND desired.rollout_id = ops_crawler_release_reports.rollout_id
                  AND desired.generation = ops_crawler_release_reports.desired_generation
            )
        )
    );

ALTER TABLE public.ops_crawler_worker_desired_state ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS crawler_runtime_desired_state_scope
    ON public.ops_crawler_worker_desired_state;
CREATE POLICY crawler_runtime_desired_state_scope
    ON public.ops_crawler_worker_desired_state
    USING (
        (
            NOT pg_has_role(session_user, 'mooncen_crawler_worker', 'member')
            AND NOT pg_has_role(session_user, 'mooncen_crawler_reporter', 'member')
        )
        OR agent_id = public.current_crawler_worker_agent_id()
        OR agent_id = public.current_crawler_reporter_agent_id()
    )
    WITH CHECK (
        NOT pg_has_role(session_user, 'mooncen_crawler_worker', 'member')
        AND NOT pg_has_role(session_user, 'mooncen_crawler_reporter', 'member')
    );

-- Sealing evidence and authorizing primary promotion are separate database
-- capabilities.  The finalizer can only create/update held control batches;
-- the approver can only add the four reviewed approval fields to an otherwise
-- byte-identical result document.
CREATE OR REPLACE FUNCTION public.enforce_crawler_promotion_role_separation()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    is_finalizer BOOLEAN := pg_has_role(
        session_user, 'mooncen_crawler_finalizer', 'member'
    );
    is_approver BOOLEAN := pg_has_role(
        session_user, 'mooncen_crawler_approver', 'member'
    );
BEGIN
    IF is_finalizer THEN
        IF jsonb_typeof(NEW.result) IS DISTINCT FROM 'object'
           OR NEW.result->>'control_plane' IS DISTINCT FROM 'true'
           OR NEW.result->>'promotion_eligible' IS DISTINCT FROM 'false'
           OR NEW.result->>'promotion_policy' IS DISTINCT FROM 'held'
           OR (
               TG_OP = 'UPDATE'
               AND OLD.result->>'promotion_eligible' = 'true'
           ) THEN
            RAISE EXCEPTION USING
                ERRCODE = '42501',
                MESSAGE = 'crawler finalizer may only seal a held control batch';
        END IF;
        RETURN NEW;
    END IF;

    IF is_approver THEN
        IF TG_OP <> 'UPDATE'
           OR OLD.result->>'control_plane' IS DISTINCT FROM 'true'
           OR NEW.result->>'control_plane' IS DISTINCT FROM 'true'
           OR OLD.result->>'promotion_eligible' IS DISTINCT FROM 'false'
           OR OLD.result->>'promotion_policy' IS DISTINCT FROM 'held'
           OR NEW.result->>'promotion_eligible' IS DISTINCT FROM 'true'
           OR NEW.result->>'promotion_policy' IS DISTINCT FROM 'approved'
           OR COALESCE(
               NEW.result->>'promotion_approval_fingerprint'
                   ~ '^[0-9a-f]{64}$',
               FALSE
           ) IS NOT TRUE
           OR NOT (NEW.result ? 'promotion_approved_at')
           OR (
               NEW.result - ARRAY[
                   'promotion_eligible', 'promotion_policy',
                   'promotion_approval_fingerprint', 'promotion_approved_at'
               ]
           ) IS DISTINCT FROM (
               OLD.result - ARRAY[
                   'promotion_eligible', 'promotion_policy',
                   'promotion_approval_fingerprint', 'promotion_approved_at'
               ]
           )
           OR ROW(
               NEW.crawl_batch_id, NEW.source_host, NEW.mode, NEW.providers,
               NEW.status, NEW.started_at, NEW.finished_at,
               NEW.total_branches, NEW.total_courses, NEW.valid_courses,
               NEW.invalid_courses, NEW.created_at
           ) IS DISTINCT FROM ROW(
               OLD.crawl_batch_id, OLD.source_host, OLD.mode, OLD.providers,
               OLD.status, OLD.started_at, OLD.finished_at,
               OLD.total_branches, OLD.total_courses, OLD.valid_courses,
               OLD.invalid_courses, OLD.created_at
           ) THEN
            RAISE EXCEPTION USING
                ERRCODE = '42501',
                MESSAGE = 'crawler approver may only authorize unchanged reviewed evidence';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.result->>'control_plane' = 'true'
       OR (
           TG_OP = 'UPDATE'
           AND OLD.result->>'control_plane' = 'true'
       ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'control-plane batch mutation requires the finalizer or approver role';
    END IF;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.enforce_crawler_promotion_role_separation()
    FROM PUBLIC;

DROP TRIGGER IF EXISTS zz_enforce_crawler_promotion_role_separation
    ON public.crawl_batches;
CREATE TRIGGER zz_enforce_crawler_promotion_role_separation
    BEFORE INSERT OR UPDATE ON public.crawl_batches
    FOR EACH ROW
    EXECUTE FUNCTION public.enforce_crawler_promotion_role_separation();

CREATE OR REPLACE FUNCTION public.enforce_crawler_worker_runtime_evidence()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    session_job_id UUID;
    session_token UUID;
    session_epoch BIGINT;
    session_attempt INTEGER;
    worker_agent UUID;
BEGIN
    IF NOT pg_has_role(session_user, 'mooncen_crawler_worker', 'member') THEN
        RETURN NEW;
    END IF;
    BEGIN
        session_job_id := current_setting('mooncen.crawl_job_id', true)::uuid;
        session_token := current_setting('mooncen.crawl_lease_token', true)::uuid;
        session_epoch := current_setting('mooncen.crawl_lease_epoch', true)::bigint;
        session_attempt := current_setting('mooncen.crawl_attempt_no', true)::integer;
    EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'crawler runtime evidence lacks a valid fenced lease';
    END;
    worker_agent := public.current_crawler_worker_agent_id();
    IF worker_agent IS NULL OR NOT EXISTS (
        SELECT 1
        FROM public.ops_jobs job
        JOIN public.ops_crawler_task_attempts attempt
          ON attempt.job_id = job.id
         AND attempt.attempt_no = job.attempt_no
         AND attempt.lease_epoch = job.lease_epoch
         AND attempt.lease_token = job.lease_token
         AND attempt.agent_id = job.agent_id
         AND attempt.status = 'running'
        WHERE job.id = session_job_id
          AND job.agent_id = worker_agent
          AND job.lease_token = session_token
          AND job.lease_epoch = session_epoch
          AND job.attempt_no = session_attempt
          AND job.status IN ('assigned', 'running')
          AND job.leased_until > clock_timestamp()
        FOR UPDATE OF job, attempt
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'crawler runtime evidence does not match the current worker lease';
    END IF;
    IF TG_OP = 'UPDATE' AND ROW(
        OLD.ops_job_id, OLD.ops_attempt_no, OLD.ops_lease_epoch, OLD.ops_agent_id
    ) IS DISTINCT FROM ROW(
        session_job_id, session_attempt, session_epoch, worker_agent
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'crawler worker attempted to rewrite another attempt runtime row';
    END IF;
    NEW.ops_job_id := session_job_id;
    NEW.ops_attempt_no := session_attempt;
    NEW.ops_lease_epoch := session_epoch;
    NEW.ops_agent_id := worker_agent;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.enforce_crawler_worker_runtime_evidence()
    FROM PUBLIC;

DROP TRIGGER IF EXISTS zz_enforce_crawler_worker_runtime_evidence
    ON public.crawler_run_log;
CREATE TRIGGER zz_enforce_crawler_worker_runtime_evidence
    BEFORE INSERT OR UPDATE ON public.crawler_run_log
    FOR EACH ROW
    EXECUTE FUNCTION public.enforce_crawler_worker_runtime_evidence();

DROP TRIGGER IF EXISTS zz_enforce_crawler_worker_runtime_evidence
    ON public.crawl_progress;
CREATE TRIGGER zz_enforce_crawler_worker_runtime_evidence
    BEFORE INSERT OR UPDATE ON public.crawl_progress
    FOR EACH ROW
    EXECUTE FUNCTION public.enforce_crawler_worker_runtime_evidence();

ALTER TABLE public.crawler_run_log ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS crawler_worker_run_log_scope ON public.crawler_run_log;
CREATE POLICY crawler_worker_run_log_scope ON public.crawler_run_log
    USING (
        NOT pg_has_role(session_user, 'mooncen_crawler_worker', 'member')
        OR ops_agent_id = public.current_crawler_worker_agent_id()
    )
    WITH CHECK (
        NOT pg_has_role(session_user, 'mooncen_crawler_worker', 'member')
        OR ops_agent_id = public.current_crawler_worker_agent_id()
    );
DROP POLICY IF EXISTS crawler_managed_run_log_isolation ON public.crawler_run_log;
CREATE POLICY crawler_managed_run_log_isolation ON public.crawler_run_log
    AS RESTRICTIVE
    USING (
        ops_agent_id IS NULL
        OR pg_has_role(session_user, 'mooncen_api', 'member')
        OR pg_has_role(session_user, 'mooncen_crawler_control', 'member')
        OR pg_has_role(session_user, 'mooncen_crawler_finalizer', 'member')
        OR pg_has_role(session_user, 'mooncen_crawler_observer', 'member')
        OR pg_has_role(session_user, 'mooncen_crawler_api', 'member')
        OR ops_agent_id = public.current_crawler_worker_agent_id()
    )
    WITH CHECK (
        ops_agent_id IS NULL
        OR pg_has_role(session_user, 'mooncen_api', 'member')
        OR pg_has_role(session_user, 'mooncen_crawler_control', 'member')
        OR ops_agent_id = public.current_crawler_worker_agent_id()
    );

ALTER TABLE public.crawl_progress ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS crawler_worker_progress_scope ON public.crawl_progress;
CREATE POLICY crawler_worker_progress_scope ON public.crawl_progress
    USING (
        NOT pg_has_role(session_user, 'mooncen_crawler_worker', 'member')
        OR ops_agent_id = public.current_crawler_worker_agent_id()
    )
    WITH CHECK (
        NOT pg_has_role(session_user, 'mooncen_crawler_worker', 'member')
        OR ops_agent_id = public.current_crawler_worker_agent_id()
    );
DROP POLICY IF EXISTS crawler_managed_progress_isolation ON public.crawl_progress;
CREATE POLICY crawler_managed_progress_isolation ON public.crawl_progress
    AS RESTRICTIVE
    USING (
        ops_agent_id IS NULL
        OR pg_has_role(session_user, 'mooncen_api', 'member')
        OR pg_has_role(session_user, 'mooncen_crawler_control', 'member')
        OR pg_has_role(session_user, 'mooncen_crawler_finalizer', 'member')
        OR pg_has_role(session_user, 'mooncen_crawler_observer', 'member')
        OR pg_has_role(session_user, 'mooncen_crawler_api', 'member')
        OR ops_agent_id = public.current_crawler_worker_agent_id()
    )
    WITH CHECK (
        ops_agent_id IS NULL
        OR pg_has_role(session_user, 'mooncen_api', 'member')
        OR pg_has_role(session_user, 'mooncen_crawler_control', 'member')
        OR ops_agent_id = public.current_crawler_worker_agent_id()
    );

-- Every mutation is retained with the exact attempt identity.  A retry writes
-- a new immutable evidence stream instead of overwriting the evidence from an
-- expired attempt.  Consumers select only the terminal attempt recorded in the
-- sealed crawl_batches result.
CREATE TABLE IF NOT EXISTS crawl_staging.fenced_branch_snapshots (
    snapshot_id BIGSERIAL PRIMARY KEY,
    crawl_batch_id UUID NOT NULL REFERENCES public.ops_crawler_batches(id) ON DELETE RESTRICT,
    attempt_id UUID NOT NULL,
    job_id UUID NOT NULL,
    attempt_no INTEGER NOT NULL,
    lease_epoch BIGINT NOT NULL,
    provider TEXT NOT NULL,
    branch_code TEXT NOT NULL,
    row_data JSONB NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT fk_fenced_branch_snapshot_attempt
        FOREIGN KEY (attempt_id, job_id, attempt_no, lease_epoch)
        REFERENCES public.ops_crawler_task_attempts(id, job_id, attempt_no, lease_epoch)
        ON DELETE RESTRICT,
    CONSTRAINT chk_fenced_branch_snapshot_provider
        CHECK (provider ~ '^[A-Z][A-Z0-9_]{0,99}$'),
    CONSTRAINT chk_fenced_branch_snapshot_row
        CHECK (jsonb_typeof(row_data) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_fenced_branch_snapshot_attempt_row
    ON crawl_staging.fenced_branch_snapshots (
        crawl_batch_id, attempt_id, provider, branch_code, snapshot_id DESC
    );

CREATE TABLE IF NOT EXISTS crawl_staging.fenced_course_snapshots (
    snapshot_id BIGSERIAL PRIMARY KEY,
    crawl_batch_id UUID NOT NULL REFERENCES public.ops_crawler_batches(id) ON DELETE RESTRICT,
    attempt_id UUID NOT NULL,
    job_id UUID NOT NULL,
    attempt_no INTEGER NOT NULL,
    lease_epoch BIGINT NOT NULL,
    provider TEXT NOT NULL,
    provider_course_id TEXT NOT NULL,
    row_data JSONB NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT fk_fenced_course_snapshot_attempt
        FOREIGN KEY (attempt_id, job_id, attempt_no, lease_epoch)
        REFERENCES public.ops_crawler_task_attempts(id, job_id, attempt_no, lease_epoch)
        ON DELETE RESTRICT,
    CONSTRAINT chk_fenced_course_snapshot_provider
        CHECK (provider ~ '^[A-Z][A-Z0-9_]{0,99}$'),
    CONSTRAINT chk_fenced_course_snapshot_row
        CHECK (jsonb_typeof(row_data) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_fenced_course_snapshot_attempt_row
    ON crawl_staging.fenced_course_snapshots (
        crawl_batch_id, attempt_id, provider, provider_course_id, snapshot_id DESC
    );

DROP TRIGGER IF EXISTS trg_fenced_branch_snapshots_immutable
    ON crawl_staging.fenced_branch_snapshots;
CREATE TRIGGER trg_fenced_branch_snapshots_immutable
    BEFORE UPDATE OR DELETE ON crawl_staging.fenced_branch_snapshots
    FOR EACH ROW EXECUTE FUNCTION public.mooncen_reject_immutable_crawler_evidence();

DROP TRIGGER IF EXISTS trg_fenced_course_snapshots_immutable
    ON crawl_staging.fenced_course_snapshots;
CREATE TRIGGER trg_fenced_course_snapshots_immutable
    BEFORE UPDATE OR DELETE ON crawl_staging.fenced_course_snapshots
    FOR EACH ROW EXECUTE FUNCTION public.mooncen_reject_immutable_crawler_evidence();

CREATE OR REPLACE FUNCTION public.enforce_current_crawler_lease()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    require_lease TEXT := lower(coalesce(current_setting('mooncen.require_crawler_lease', true), 'off'));
    dedicated_worker BOOLEAN := pg_has_role(session_user, 'mooncen_crawler_worker', 'member');
    session_job_id UUID;
    session_token UUID;
    session_epoch BIGINT;
    session_attempt INTEGER;
    session_batch TEXT;
    job_batch TEXT;
    row_provider TEXT;
    allowed_output_providers TEXT[];
BEGIN
    IF NOT dedicated_worker THEN
        IF require_lease IN ('on', 'true', '1', 'yes') THEN
            RAISE EXCEPTION USING
                ERRCODE = '42501',
                MESSAGE = 'fenced crawler writes require an enrolled dedicated worker login';
        END IF;
        RETURN NEW;
    END IF;

    BEGIN
        session_job_id := current_setting('mooncen.crawl_job_id', true)::uuid;
        session_token := current_setting('mooncen.crawl_lease_token', true)::uuid;
        session_epoch := current_setting('mooncen.crawl_lease_epoch', true)::bigint;
        session_attempt := current_setting('mooncen.crawl_attempt_no', true)::integer;
    EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'crawler staging write lacks a valid fenced lease';
    END;
    session_batch := nullif(btrim(current_setting('mooncen.crawl_batch_id', true)), '');

    SELECT task.batch_id::text, task.allowed_output_providers
      INTO job_batch, allowed_output_providers
      FROM public.ops_jobs job
      JOIN public.ops_crawler_batch_tasks task ON task.job_id = job.id
      JOIN public.ops_crawler_task_attempts attempt
        ON attempt.job_id = job.id
       AND attempt.attempt_no = job.attempt_no
       AND attempt.lease_epoch = job.lease_epoch
       AND attempt.lease_token = job.lease_token
       AND attempt.agent_id = job.agent_id
       AND attempt.status = 'running'
     WHERE job.id = session_job_id
       AND job.lease_token = session_token
       AND job.lease_epoch = session_epoch
       AND job.attempt_no = session_attempt
       AND job.status = 'running'
       AND job.leased_until > clock_timestamp()
       AND (
           NOT dedicated_worker
           OR EXISTS (
               SELECT 1
               FROM public.ops_agents agent
               JOIN public.ops_crawler_agent_bindings binding
                 ON binding.agent_id = agent.id
                AND binding.environment = agent.environment
                AND binding.binding_type = 'worker'
                AND binding.database_login = session_user
               WHERE agent.id = job.agent_id
                 AND agent.environment = job.environment
                 AND agent.credential_hint = 'crawler-worker:' || session_user
                 AND agent.status <> 'disabled'
                 AND agent.maintenance_mode IS FALSE
           )
       )
     FOR UPDATE OF job, attempt;

    IF NOT FOUND THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'crawler staging write rejected because its lease is no longer current';
    END IF;
    IF session_batch IS NULL OR job_batch IS NULL OR session_batch <> job_batch THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'crawler staging write rejected because its batch does not match the lease';
    END IF;
    row_provider := nullif(btrim(NEW.provider), '');
    IF row_provider IS NULL OR row_provider IS DISTINCT FROM upper(row_provider) THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'crawler staging writes require a canonical uppercase provider';
    END IF;
    IF NOT row_provider = ANY(allowed_output_providers) THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'crawler staging write escaped its leased provider scope';
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.provider IS DISTINCT FROM NEW.provider THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'crawler staging update cannot change provider ownership';
    END IF;
    IF TG_OP = 'UPDATE'
       AND TG_TABLE_NAME = 'branches'
       AND OLD.branch_code IS DISTINCT FROM NEW.branch_code THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'crawler staging update cannot change branch identity';
    END IF;
    IF TG_OP = 'UPDATE'
       AND TG_TABLE_NAME = 'courses'
       AND OLD.provider_course_id IS DISTINCT FROM NEW.provider_course_id THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'crawler staging update cannot change course identity';
    END IF;
    IF TG_TABLE_NAME = 'courses'
       AND NEW.branch_id IS NOT NULL
       AND NOT EXISTS (
           SELECT 1
           FROM public.branches branch
           WHERE branch.id = NEW.branch_id
             AND branch.provider = row_provider
       ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'crawler staging course escaped its provider branch ownership';
    END IF;

    NEW.crawl_batch_id := session_batch;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.enforce_current_crawler_lease() FROM PUBLIC;

DROP TRIGGER IF EXISTS zz_enforce_current_crawler_lease ON public.branches;
CREATE TRIGGER zz_enforce_current_crawler_lease
    BEFORE INSERT OR UPDATE ON public.branches
    FOR EACH ROW
    EXECUTE FUNCTION public.enforce_current_crawler_lease();

DROP TRIGGER IF EXISTS zz_enforce_current_crawler_lease ON public.courses;
CREATE TRIGGER zz_enforce_current_crawler_lease
    BEFORE INSERT OR UPDATE ON public.courses
    FOR EACH ROW
    EXECUTE FUNCTION public.enforce_current_crawler_lease();

CREATE OR REPLACE FUNCTION public.capture_fenced_crawler_snapshot()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    require_lease TEXT := lower(coalesce(current_setting('mooncen.require_crawler_lease', true), 'off'));
    dedicated_worker BOOLEAN := pg_has_role(session_user, 'mooncen_crawler_worker', 'member');
    session_job_id UUID;
    session_token UUID;
    session_epoch BIGINT;
    session_attempt INTEGER;
    session_batch UUID;
    current_attempt_id UUID;
BEGIN
    IF NOT dedicated_worker THEN
        IF require_lease IN ('on', 'true', '1', 'yes') THEN
            RAISE EXCEPTION USING
                ERRCODE = '42501',
                MESSAGE = 'fenced crawler snapshots require an enrolled dedicated worker login';
        END IF;
        RETURN NEW;
    END IF;

    BEGIN
        session_job_id := current_setting('mooncen.crawl_job_id', true)::uuid;
        session_token := current_setting('mooncen.crawl_lease_token', true)::uuid;
        session_epoch := current_setting('mooncen.crawl_lease_epoch', true)::bigint;
        session_attempt := current_setting('mooncen.crawl_attempt_no', true)::integer;
        session_batch := current_setting('mooncen.crawl_batch_id', true)::uuid;
    EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'crawler snapshot capture lacks a valid fenced lease';
    END;

    SELECT attempt.id
      INTO current_attempt_id
      FROM public.ops_jobs job
      JOIN public.ops_crawler_batch_tasks task
        ON task.job_id = job.id
       AND task.batch_id = session_batch
       AND upper(btrim(NEW.provider)) = ANY(task.allowed_output_providers)
      JOIN public.ops_crawler_task_attempts attempt
        ON attempt.job_id = job.id
       AND attempt.attempt_no = job.attempt_no
       AND attempt.lease_epoch = job.lease_epoch
       AND attempt.lease_token = job.lease_token
       AND attempt.agent_id = job.agent_id
       AND attempt.status = 'running'
     WHERE job.id = session_job_id
       AND job.lease_token = session_token
       AND job.lease_epoch = session_epoch
       AND job.attempt_no = session_attempt
       AND job.status = 'running'
       AND job.leased_until > clock_timestamp()
       AND (
           NOT dedicated_worker
           OR EXISTS (
               SELECT 1
               FROM public.ops_agents agent
               JOIN public.ops_crawler_agent_bindings binding
                 ON binding.agent_id = agent.id
                AND binding.environment = agent.environment
                AND binding.binding_type = 'worker'
                AND binding.database_login = session_user
               WHERE agent.id = job.agent_id
                 AND agent.environment = job.environment
                 AND agent.credential_hint = 'crawler-worker:' || session_user
                 AND agent.status <> 'disabled'
                 AND agent.maintenance_mode IS FALSE
           )
       )
     FOR UPDATE OF job, attempt;

    IF NOT FOUND THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'crawler snapshot capture rejected a stale or out-of-scope attempt';
    END IF;

    IF TG_TABLE_NAME = 'branches' THEN
        INSERT INTO crawl_staging.fenced_branch_snapshots (
            crawl_batch_id, attempt_id, job_id, attempt_no, lease_epoch,
            provider, branch_code, row_data, captured_at
        )
        VALUES (
            session_batch, current_attempt_id, session_job_id, session_attempt,
            session_epoch, upper(btrim(NEW.provider)), NEW.branch_code,
            to_jsonb(NEW), clock_timestamp()
        );
    ELSIF TG_TABLE_NAME = 'courses' THEN
        INSERT INTO crawl_staging.fenced_course_snapshots (
            crawl_batch_id, attempt_id, job_id, attempt_no, lease_epoch,
            provider, provider_course_id, row_data, captured_at
        )
        VALUES (
            session_batch,
            current_attempt_id,
            session_job_id,
            session_attempt,
            session_epoch,
            upper(btrim(NEW.provider)),
            NEW.provider_course_id,
            to_jsonb(NEW),
            clock_timestamp()
        );
    ELSE
        RAISE EXCEPTION 'unsupported crawler snapshot relation: %', TG_TABLE_NAME;
    END IF;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.capture_fenced_crawler_snapshot() FROM PUBLIC;

DROP TRIGGER IF EXISTS zz_capture_fenced_crawler_snapshot ON public.branches;
CREATE TRIGGER zz_capture_fenced_crawler_snapshot
    AFTER INSERT OR UPDATE ON public.branches
    FOR EACH ROW
    EXECUTE FUNCTION public.capture_fenced_crawler_snapshot();

DROP TRIGGER IF EXISTS zz_capture_fenced_crawler_snapshot ON public.courses;
CREATE TRIGGER zz_capture_fenced_crawler_snapshot
    AFTER INSERT OR UPDATE ON public.courses
    FOR EACH ROW
    EXECUTE FUNCTION public.capture_fenced_crawler_snapshot();

COMMENT ON FUNCTION public.enforce_current_crawler_lease() IS
    'Rejects staging mutations from expired or superseded distributed crawler attempts.';
COMMENT ON FUNCTION public.capture_fenced_crawler_snapshot() IS
    'Appends attempt-bound staging evidence for every mutation made by a current crawler lease.';
