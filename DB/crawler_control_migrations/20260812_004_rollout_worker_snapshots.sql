-- Append-only worker rosters for every crawler rollout generation.
--
-- This is deliberately a forward migration instead of an edit to the initial
-- control-plane migration. Existing installations must retain their recorded
-- 20260810_001 checksum and receive the history table through this ledgered
-- step.

DO $$
BEGIN
    IF to_regclass('public.ops_crawler_control_database_marker') IS NULL
       OR NOT EXISTS (
           SELECT 1
           FROM public.ops_crawler_control_database_marker
           WHERE singleton IS TRUE
             AND database_name = current_database()::name
       ) THEN
        RAISE EXCEPTION 'rollout worker history requires the marked crawler-control database';
    END IF;
    IF to_regclass('public.ops_crawler_release_rollouts') IS NULL
       OR to_regclass('public.ops_crawler_worker_desired_state') IS NULL
       OR to_regclass('public.ops_crawler_release_artifacts') IS NULL
       OR to_regclass('public.ops_agents') IS NULL
       OR to_regprocedure('public.current_crawler_api_environment()') IS NULL
       OR to_regprocedure('public.mooncen_reject_immutable_crawler_evidence()') IS NULL THEN
        RAISE EXCEPTION 'rollout worker history requires the crawler release contract';
    END IF;
END;
$$;

-- Rows that predate this migration have no reconstructable roster. Mark that
-- boundary explicitly; every future rollout is stamped TRUE by a server-side
-- trigger and must commit a complete snapshot.
ALTER TABLE ops_crawler_release_rollouts
    ADD COLUMN worker_snapshot_required BOOLEAN;
UPDATE ops_crawler_release_rollouts
SET worker_snapshot_required = FALSE
WHERE worker_snapshot_required IS NULL;
ALTER TABLE ops_crawler_release_rollouts
    ALTER COLUMN worker_snapshot_required SET DEFAULT TRUE,
    ALTER COLUMN worker_snapshot_required SET NOT NULL;

CREATE OR REPLACE FUNCTION enforce_crawler_rollout_snapshot_requirement()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.worker_snapshot_required := TRUE;
    ELSIF OLD.worker_snapshot_required IS FALSE
          AND NEW.worker_snapshot_required IS FALSE
          AND NEW.rollout_epoch > OLD.rollout_epoch THEN
        -- The migration-time generation remains explicitly unavailable, but
        -- the first later generation enters the immutable snapshot contract.
        NEW.worker_snapshot_required := TRUE;
    ELSIF NEW.worker_snapshot_required IS DISTINCT FROM OLD.worker_snapshot_required THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'rollout worker snapshot requirement is immutable';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER zz_enforce_crawler_rollout_snapshot_requirement
    BEFORE INSERT OR UPDATE ON ops_crawler_release_rollouts
    FOR EACH ROW EXECUTE FUNCTION enforce_crawler_rollout_snapshot_requirement();

CREATE TABLE ops_crawler_rollout_worker_snapshots (
    environment TEXT NOT NULL,
    rollout_id UUID NOT NULL
        REFERENCES ops_crawler_release_rollouts(id) ON DELETE RESTRICT,
    generation BIGINT NOT NULL,
    worker_key TEXT NOT NULL,
    agent_id UUID NOT NULL REFERENCES ops_agents(id) ON DELETE RESTRICT,
    desired_status TEXT NOT NULL,
    cohort TEXT NOT NULL,
    artifact_digest TEXT NOT NULL
        REFERENCES ops_crawler_release_artifacts(artifact_digest) ON DELETE RESTRICT,
    code_version TEXT NOT NULL,
    config_revision TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_ops_crawler_rollout_worker_snapshots
        PRIMARY KEY (environment, rollout_id, generation, worker_key),
    CONSTRAINT chk_ops_crawler_rollout_worker_snapshot_environment
        CHECK (environment IN ('production', 'staging', 'development')),
    CONSTRAINT chk_ops_crawler_rollout_worker_snapshot_generation
        CHECK (generation > 0),
    CONSTRAINT chk_ops_crawler_rollout_worker_snapshot_key
        CHECK (
            worker_key = btrim(worker_key)
            AND char_length(worker_key) BETWEEN 1 AND 200
        ),
    CONSTRAINT chk_ops_crawler_rollout_worker_snapshot_status
        CHECK (desired_status IN ('active', 'draining', 'disabled')),
    CONSTRAINT chk_ops_crawler_rollout_worker_snapshot_cohort
        CHECK (cohort IN ('canary', 'stable')),
    CONSTRAINT chk_ops_crawler_rollout_worker_snapshot_code
        CHECK (
            code_version = btrim(code_version)
            AND char_length(code_version) BETWEEN 1 AND 200
        ),
    CONSTRAINT chk_ops_crawler_rollout_worker_snapshot_config
        CHECK (
            config_revision = btrim(config_revision)
            AND char_length(config_revision) BETWEEN 1 AND 255
        )
);

CREATE INDEX idx_ops_crawler_rollout_worker_snapshots_latest
    ON ops_crawler_rollout_worker_snapshots
        (environment, rollout_id, worker_key, generation DESC NULLS LAST);

CREATE OR REPLACE FUNCTION enforce_crawler_rollout_snapshot_insert()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM public.ops_crawler_release_rollouts rollout
        JOIN public.ops_crawler_worker_desired_state desired
          ON desired.environment = rollout.environment
         AND desired.rollout_id = rollout.id
         AND desired.generation = rollout.rollout_epoch
        WHERE rollout.id = NEW.rollout_id
          AND rollout.environment = NEW.environment
          AND rollout.rollout_epoch = NEW.generation
          AND desired.worker_key = NEW.worker_key
          AND desired.agent_id = NEW.agent_id
          AND desired.desired_status = NEW.desired_status
          AND desired.cohort = NEW.cohort
          AND desired.artifact_digest = NEW.artifact_digest
          AND desired.code_version = NEW.code_version
          AND desired.config_revision = NEW.config_revision
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'rollout worker snapshot does not match current desired evidence';
    END IF;
    NEW.created_at := clock_timestamp();
    RETURN NEW;
END
$$;

CREATE TRIGGER zz_enforce_crawler_rollout_snapshot_insert
    BEFORE INSERT ON ops_crawler_rollout_worker_snapshots
    FOR EACH ROW EXECUTE FUNCTION enforce_crawler_rollout_snapshot_insert();

CREATE OR REPLACE FUNCTION enforce_crawler_rollout_snapshot_commit()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    exact_count INTEGER;
BEGIN
    IF NEW.worker_snapshot_required IS NOT TRUE THEN
        RETURN NULL;
    END IF;
    SELECT count(*)
    INTO exact_count
    FROM public.ops_crawler_rollout_worker_snapshots snapshot
    JOIN public.ops_crawler_worker_desired_state desired
      ON desired.environment = snapshot.environment
     AND desired.worker_key = snapshot.worker_key
     AND desired.agent_id = snapshot.agent_id
     AND desired.rollout_id = snapshot.rollout_id
     AND desired.generation = snapshot.generation
     AND desired.desired_status = snapshot.desired_status
     AND desired.cohort = snapshot.cohort
     AND desired.artifact_digest = snapshot.artifact_digest
     AND desired.code_version = snapshot.code_version
     AND desired.config_revision = snapshot.config_revision
    WHERE snapshot.environment = NEW.environment
      AND snapshot.rollout_id = NEW.id
      AND snapshot.generation = NEW.rollout_epoch;

    IF exact_count <> NEW.requested_worker_count THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'rollout worker snapshot is incomplete or differs from desired state';
    END IF;
    RETURN NULL;
END
$$;

CREATE CONSTRAINT TRIGGER zz_enforce_crawler_rollout_snapshot_commit
    AFTER INSERT OR UPDATE ON ops_crawler_release_rollouts
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION enforce_crawler_rollout_snapshot_commit();

CREATE TRIGGER trg_ops_crawler_rollout_worker_snapshots_immutable
    BEFORE UPDATE OR DELETE ON ops_crawler_rollout_worker_snapshots
    FOR EACH ROW EXECUTE FUNCTION mooncen_reject_immutable_crawler_evidence();

REVOKE ALL ON FUNCTION enforce_crawler_rollout_snapshot_insert() FROM PUBLIC;
REVOKE ALL ON FUNCTION enforce_crawler_rollout_snapshot_commit() FROM PUBLIC;
REVOKE ALL ON FUNCTION enforce_crawler_rollout_snapshot_requirement() FROM PUBLIC;
REVOKE ALL ON TABLE ops_crawler_rollout_worker_snapshots FROM PUBLIC;
REVOKE ALL ON TABLE ops_crawler_rollout_worker_snapshots FROM mooncen_api;

ALTER TABLE ops_crawler_rollout_worker_snapshots ENABLE ROW LEVEL SECURITY;
CREATE POLICY crawler_rollout_worker_snapshot_acl_access
    ON ops_crawler_rollout_worker_snapshots
    USING (true)
    WITH CHECK (true);
CREATE POLICY crawler_api_rollout_worker_snapshot_environment_scope
    ON ops_crawler_rollout_worker_snapshots AS RESTRICTIVE FOR SELECT
    USING (
        NOT pg_has_role(session_user, 'mooncen_crawler_api', 'member')
        OR environment = current_crawler_api_environment()
    );

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_crawler_control') THEN
        GRANT SELECT ON ops_crawler_rollout_worker_snapshots
            TO mooncen_crawler_control;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_crawler_release_admin') THEN
        GRANT SELECT, INSERT ON ops_crawler_rollout_worker_snapshots
            TO mooncen_crawler_release_admin;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_crawler_api') THEN
        GRANT SELECT (worker_snapshot_required)
            ON ops_crawler_release_rollouts TO mooncen_crawler_api;
        GRANT SELECT (environment, rollout_id, generation, worker_key, agent_id,
            desired_status, cohort, artifact_digest, code_version,
            config_revision, created_at)
            ON ops_crawler_rollout_worker_snapshots TO mooncen_crawler_api;
    END IF;
END;
$$;

COMMENT ON TABLE ops_crawler_rollout_worker_snapshots IS
    'Append-only exact worker target roster for every rollout generation; retained after desired state advances to later rollouts.';
