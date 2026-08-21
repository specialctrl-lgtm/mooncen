-- Active deployments must use one canonical queue identity across every writer.
-- Terminal legacy rows are intentionally outside this invariant so historical
-- data can remain immutable.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM ops_jobs
        WHERE job_type = 'deployment'
          AND status IN ('queued', 'assigned', 'running')
          AND NOT COALESCE(
              target_key IS NOT NULL
              AND jsonb_typeof(parameters -> 'target') = 'string'
              AND char_length(parameters ->> 'target') BETWEEN 1 AND 32
              AND (parameters ->> 'target') ~ '^[a-z][a-z0-9_-]{0,31}$'
              AND target_key = 'deployment:' || (parameters ->> 'target'),
              false
          )
    ) THEN
        RAISE EXCEPTION
            'Cannot enforce deployment target identity: invalid active deployment jobs exist';
    END IF;
END
$$;

ALTER TABLE ops_jobs
    ADD CONSTRAINT chk_ops_jobs_active_deployment_target_key
    CHECK (
        job_type <> 'deployment'
        OR status NOT IN ('queued', 'assigned', 'running')
        OR COALESCE(
            target_key IS NOT NULL
            AND jsonb_typeof(parameters -> 'target') = 'string'
            AND char_length(parameters ->> 'target') BETWEEN 1 AND 32
            AND (parameters ->> 'target') ~ '^[a-z][a-z0-9_-]{0,31}$'
            AND target_key = 'deployment:' || (parameters ->> 'target'),
            false
        )
    ) NOT VALID;

ALTER TABLE ops_jobs
    VALIDATE CONSTRAINT chk_ops_jobs_active_deployment_target_key;
