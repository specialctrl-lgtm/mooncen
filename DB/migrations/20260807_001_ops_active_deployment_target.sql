-- Only one active deployment may own an environment/target queue slot.
--
-- API-side advisory locking makes creation race-safe during rolling upgrades;
-- this partial unique index is the final invariant for every database writer.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM ops_jobs
        WHERE job_type = 'deployment'
          AND status IN ('queued', 'assigned', 'running')
          AND target_key IS NOT NULL
          AND btrim(target_key) <> ''
        GROUP BY environment, target_key
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION
            'Cannot enforce active deployment target uniqueness: duplicate active deployment jobs exist';
    END IF;
END
$$;

CREATE UNIQUE INDEX ux_ops_jobs_active_deployment_target
    ON ops_jobs (environment, target_key)
    WHERE job_type = 'deployment'
      AND target_key IS NOT NULL
      AND btrim(target_key) <> ''
      AND status IN ('queued', 'assigned', 'running');
