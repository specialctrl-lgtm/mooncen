-- Distributed crawler control-plane primitives.
--
-- The generic ops_jobs row remains the authoritative claim/retry state.  A
-- crawler batch maps immutable task identities to those jobs, while attempt
-- observations are append-only evidence.  Nothing in this migration changes
-- the existing course/branch persistence or staging last-writer behaviour.

-- This file intentionally lives outside DB/migrations: generic primary DB
-- setup must never install distributed-worker roles or queue state.  Only the
-- explicit crawler-control installer creates the marker after the operator
-- confirms the shared staging database name.  Direct execution fails closed.
DO $$
DECLARE
    marker_count INTEGER;
BEGIN
    IF to_regclass('public.ops_crawler_control_database_marker') IS NULL THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'crawler control migration requires a preconfirmed database marker';
    END IF;
    EXECUTE
        'SELECT count(*) FROM public.ops_crawler_control_database_marker '
        'WHERE singleton IS TRUE AND database_name = current_database()'
    INTO marker_count;
    IF marker_count <> 1 THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'crawler control migration requires an exact dedicated database marker';
    END IF;
    EXECUTE 'SELECT count(*) FROM public.ops_crawler_control_database_marker'
    INTO marker_count;
    IF marker_count <> 1 THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'crawler control database marker has ambiguous rows';
    END IF;
END;
$$;

ALTER TABLE ops_jobs
    ADD COLUMN IF NOT EXISTS available_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS lease_token UUID,
    ADD COLUMN IF NOT EXISTS lease_epoch BIGINT,
    ADD COLUMN IF NOT EXISTS leased_until TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS required_code_version TEXT,
    ADD COLUMN IF NOT EXISTS artifact_digest TEXT,
    ADD COLUMN IF NOT EXISTS config_revision TEXT,
    ADD COLUMN IF NOT EXISTS attempt_no INTEGER;

UPDATE ops_jobs
SET available_at = COALESCE(available_at, queued_at, created_at, CURRENT_TIMESTAMP),
    lease_epoch = COALESCE(lease_epoch, 0),
    attempt_no = COALESCE(attempt_no, retry_count, 0)
WHERE available_at IS NULL
   OR lease_epoch IS NULL
   OR attempt_no IS NULL;

ALTER TABLE ops_jobs
    ALTER COLUMN available_at SET DEFAULT CURRENT_TIMESTAMP,
    ALTER COLUMN available_at SET NOT NULL,
    ALTER COLUMN lease_epoch SET DEFAULT 0,
    ALTER COLUMN lease_epoch SET NOT NULL,
    ALTER COLUMN attempt_no SET DEFAULT 0,
    ALTER COLUMN attempt_no SET NOT NULL;

-- Rebuild the status constraint so a retry-exhausted row has an explicit,
-- queryable terminal state rather than being mixed with ordinary failures.
ALTER TABLE ops_jobs
    DROP CONSTRAINT IF EXISTS chk_ops_jobs_status;
ALTER TABLE ops_jobs
    ADD CONSTRAINT chk_ops_jobs_status
    CHECK (status IN (
        'queued', 'assigned', 'running', 'success', 'partial_success', 'failed',
        'cancelled', 'timed_out', 'blocked', 'dead_lettered'
    ));

-- Converge named constraints instead of trusting a pre-existing object with the
-- same name.  A partially prepared database must not be able to retain a
-- weaker lease definition when the checksummed control-plane migration runs.
ALTER TABLE ops_jobs
    DROP CONSTRAINT IF EXISTS chk_ops_jobs_fenced_lease,
    DROP CONSTRAINT IF EXISTS chk_ops_jobs_lease_counters,
    DROP CONSTRAINT IF EXISTS chk_ops_jobs_execution_contract,
    DROP CONSTRAINT IF EXISTS chk_ops_jobs_dead_letter_terminal;

ALTER TABLE ops_jobs
    ADD CONSTRAINT chk_ops_jobs_fenced_lease
        CHECK (
            (lease_token IS NULL AND leased_until IS NULL)
            OR (
                lease_token IS NOT NULL
                AND leased_until IS NOT NULL
                AND agent_id IS NOT NULL
            )
        ),
    ADD CONSTRAINT chk_ops_jobs_lease_counters
        CHECK (lease_epoch >= 0 AND attempt_no >= 0),
    ADD CONSTRAINT chk_ops_jobs_execution_contract
        CHECK (
            (required_code_version IS NULL OR (
                required_code_version = btrim(required_code_version)
                AND char_length(required_code_version) BETWEEN 1 AND 200
            ))
            AND (artifact_digest IS NULL OR (
                artifact_digest = btrim(artifact_digest)
                AND char_length(artifact_digest) BETWEEN 1 AND 255
            ))
            AND (config_revision IS NULL OR (
                config_revision = btrim(config_revision)
                AND char_length(config_revision) BETWEEN 1 AND 255
            ))
        ),
    ADD CONSTRAINT chk_ops_jobs_dead_letter_terminal
        CHECK (
            status <> 'dead_lettered'
            OR (
                finished_at IS NOT NULL
                AND lease_token IS NULL
                AND leased_until IS NULL
            )
        );

CREATE INDEX IF NOT EXISTS idx_ops_jobs_claim_ready
    ON ops_jobs (environment, job_type, available_at, queued_at, id)
    WHERE status = 'queued'
      AND cancel_requested_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_ops_jobs_claim_compatibility
    ON ops_jobs (
        environment,
        required_code_version,
        artifact_digest,
        config_revision,
        available_at,
        queued_at
    )
    WHERE status = 'queued'
      AND cancel_requested_at IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ux_ops_jobs_active_lease_token
    ON ops_jobs (lease_token)
    WHERE lease_token IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ops_jobs_expired_lease
    ON ops_jobs (leased_until, id)
    WHERE status IN ('assigned', 'running')
      AND leased_until IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ops_jobs_dead_lettered
    ON ops_jobs (environment, finished_at DESC, id)
    WHERE status = 'dead_lettered';

-- Legacy runtime progress tables remain useful to crawler implementations, but
-- distributed rows need an immutable worker/attempt provenance so one login
-- cannot edit another worker's counts or progress.
ALTER TABLE crawler_run_log
    ADD COLUMN IF NOT EXISTS ops_job_id UUID REFERENCES ops_jobs(id) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS ops_attempt_no INTEGER,
    ADD COLUMN IF NOT EXISTS ops_lease_epoch BIGINT,
    ADD COLUMN IF NOT EXISTS ops_agent_id UUID REFERENCES ops_agents(id) ON DELETE RESTRICT;

ALTER TABLE crawl_progress
    ADD COLUMN IF NOT EXISTS ops_job_id UUID REFERENCES ops_jobs(id) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS ops_attempt_no INTEGER,
    ADD COLUMN IF NOT EXISTS ops_lease_epoch BIGINT,
    ADD COLUMN IF NOT EXISTS ops_agent_id UUID REFERENCES ops_agents(id) ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_crawler_run_log_ops_attempt
    ON crawler_run_log (ops_job_id, ops_attempt_no, ops_lease_epoch)
    WHERE ops_job_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_crawl_progress_ops_attempt
    ON crawl_progress (ops_job_id, ops_attempt_no, ops_lease_epoch)
    WHERE ops_job_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS ops_crawler_release_artifacts (
    artifact_digest TEXT PRIMARY KEY,
    code_version TEXT NOT NULL,
    config_revision TEXT NOT NULL,
    artifact_path TEXT NOT NULL,
    size_bytes BIGINT NOT NULL,
    signature TEXT,
    key_id TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_ops_crawler_release_artifact_digest
        CHECK (
            artifact_digest ~ '^[0-9a-f]{64}$'
        ),
    CONSTRAINT chk_ops_crawler_release_artifact_code
        CHECK (
            code_version = btrim(code_version)
            AND char_length(code_version) BETWEEN 1 AND 200
        ),
    CONSTRAINT chk_ops_crawler_release_artifact_config
        CHECK (
            config_revision = btrim(config_revision)
            AND char_length(config_revision) BETWEEN 1 AND 255
        ),
    CONSTRAINT chk_ops_crawler_release_artifact_path
        CHECK (
            artifact_path = btrim(artifact_path)
            AND char_length(artifact_path) BETWEEN 1 AND 240
            AND artifact_path ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}(/[A-Za-z0-9][A-Za-z0-9._-]{0,127})+$'
            AND artifact_path !~ '(^|/)\.\.(/|$)'
            AND artifact_path !~ '(^|/)\.(/|$)'
            AND artifact_path !~ '^[\\/]'
            AND artifact_path !~ '[\\]'
            AND artifact_path !~ '://'
            AND artifact_path LIKE '%.tar.gz'
        ),
    CONSTRAINT chk_ops_crawler_release_artifact_size
        CHECK (size_bytes BETWEEN 1 AND 536870912),
    CONSTRAINT chk_ops_crawler_release_artifact_signature
        CHECK (
            (signature IS NULL AND key_id IS NULL)
            OR (
                signature IS NOT NULL
                AND char_length(signature) BETWEEN 1 AND 131072
                AND key_id IS NOT NULL
                AND key_id = btrim(key_id)
                AND char_length(key_id) BETWEEN 1 AND 128
            )
        ),
    CONSTRAINT chk_ops_crawler_release_artifact_metadata
        CHECK (jsonb_typeof(metadata) = 'object')
);

-- A version label is an immutable release identity on every worker.  Reusing
-- it for different bytes would be rejected locally, so reject the ambiguity at
-- the central authority as well. Existing duplicates make migration fail
-- closed for explicit operator cleanup.
DROP INDEX IF EXISTS ux_ops_crawler_release_artifacts_code_version;
CREATE UNIQUE INDEX ux_ops_crawler_release_artifacts_code_version
    ON ops_crawler_release_artifacts (code_version);

CREATE TABLE IF NOT EXISTS ops_crawler_batches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    environment TEXT NOT NULL,
    scheduled_slot TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'planning',
    expected_task_count INTEGER NOT NULL,
    code_version TEXT NOT NULL,
    artifact_digest TEXT NOT NULL
        REFERENCES ops_crawler_release_artifacts(artifact_digest) ON DELETE RESTRICT,
    config_revision TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    CONSTRAINT ux_ops_crawler_batches_environment_slot
        UNIQUE (environment, scheduled_slot),
    CONSTRAINT chk_ops_crawler_batches_environment
        CHECK (environment IN ('production', 'staging', 'development')),
    CONSTRAINT chk_ops_crawler_batches_status
        CHECK (status IN (
            'planning', 'queued', 'running', 'finalizing', 'success',
            'partial_success', 'failed', 'cancelled', 'dead_lettered'
        )),
    CONSTRAINT chk_ops_crawler_batches_expected_count
        CHECK (expected_task_count > 0),
    CONSTRAINT chk_ops_crawler_batches_code
        CHECK (
            code_version = btrim(code_version)
            AND char_length(code_version) BETWEEN 1 AND 200
        ),
    CONSTRAINT chk_ops_crawler_batches_config
        CHECK (
            config_revision = btrim(config_revision)
            AND char_length(config_revision) BETWEEN 1 AND 255
        ),
    CONSTRAINT chk_ops_crawler_batches_finished
        CHECK (
            (status IN ('success', 'partial_success', 'failed', 'cancelled', 'dead_lettered'))
            = (finished_at IS NOT NULL)
        )
);

CREATE INDEX IF NOT EXISTS idx_ops_crawler_batches_status_slot
    ON ops_crawler_batches (environment, status, scheduled_slot);

CREATE TABLE IF NOT EXISTS ops_crawler_batch_tasks (
    batch_id UUID NOT NULL
        REFERENCES ops_crawler_batches(id) ON DELETE RESTRICT,
    job_id UUID NOT NULL
        REFERENCES ops_jobs(id) ON DELETE RESTRICT,
    task_key TEXT NOT NULL,
    provider TEXT NOT NULL,
    allowed_output_providers TEXT[] NOT NULL,
    required BOOLEAN NOT NULL DEFAULT true,
    close_missing_eligible BOOLEAN NOT NULL DEFAULT false,
    shard_index INTEGER NOT NULL DEFAULT 0,
    shard_count INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_ops_crawler_batch_tasks PRIMARY KEY (batch_id, task_key),
    CONSTRAINT ux_ops_crawler_batch_tasks_job UNIQUE (job_id),
    CONSTRAINT chk_ops_crawler_batch_tasks_key
        CHECK (
            task_key = btrim(task_key)
            AND char_length(task_key) BETWEEN 1 AND 255
        ),
    CONSTRAINT chk_ops_crawler_batch_tasks_provider
        CHECK (provider ~ '^[A-Z][A-Z0-9_]{0,99}$'),
    CONSTRAINT chk_ops_crawler_batch_tasks_output_providers
        CHECK (
            cardinality(allowed_output_providers) BETWEEN 1 AND 4096
            AND array_position(allowed_output_providers, NULL) IS NULL
            AND array_to_string(allowed_output_providers, ',')
                ~ '^[A-Z][A-Z0-9_]{0,99}(,[A-Z][A-Z0-9_]{0,99})*$'
        ),
    CONSTRAINT chk_ops_crawler_batch_tasks_shard
        CHECK (shard_count > 0 AND shard_index >= 0 AND shard_index < shard_count)
);

-- A partially applied pre-release copy of this migration may already have the
-- task table without the immutable provider scope.  Direct providers can be
-- repaired deterministically, but an aggregate task must be re-enqueued from
-- the reviewed manifest rather than receiving an unsafe guessed scope.
ALTER TABLE ops_crawler_batch_tasks
    ADD COLUMN IF NOT EXISTS allowed_output_providers TEXT[];

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM ops_crawler_batch_tasks task
        JOIN ops_jobs job ON job.id = task.job_id
        WHERE task.allowed_output_providers IS NULL
          AND lower(coalesce(job.parameters ->> 'allow_provider_fanout', 'false'))
              IN ('true', '1', 'yes', 'on')
    ) THEN
        RAISE EXCEPTION
            'aggregate crawler tasks require an exact allowed_output_providers backfill';
    END IF;

    UPDATE ops_crawler_batch_tasks
    SET allowed_output_providers = ARRAY[provider]
    WHERE allowed_output_providers IS NULL;
END;
$$;

ALTER TABLE ops_crawler_batch_tasks
    ALTER COLUMN allowed_output_providers SET NOT NULL;

ALTER TABLE ops_crawler_batch_tasks
    DROP CONSTRAINT IF EXISTS chk_ops_crawler_batch_tasks_output_providers;
ALTER TABLE ops_crawler_batch_tasks
    ADD CONSTRAINT chk_ops_crawler_batch_tasks_output_providers
    CHECK (
        cardinality(allowed_output_providers) BETWEEN 1 AND 4096
        AND array_position(allowed_output_providers, NULL) IS NULL
        AND array_to_string(allowed_output_providers, ',')
            ~ '^[A-Z][A-Z0-9_]{0,99}(,[A-Z][A-Z0-9_]{0,99})*$'
    );

CREATE INDEX IF NOT EXISTS idx_ops_crawler_batch_tasks_batch_provider
    ON ops_crawler_batch_tasks (batch_id, provider, shard_index);

CREATE TABLE IF NOT EXISTS ops_crawler_task_attempts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL REFERENCES ops_jobs(id) ON DELETE RESTRICT,
    attempt_no INTEGER NOT NULL,
    lease_epoch BIGINT NOT NULL,
    lease_token UUID NOT NULL,
    agent_id UUID NOT NULL REFERENCES ops_agents(id) ON DELETE RESTRICT,
    status TEXT NOT NULL DEFAULT 'running',
    worker_code_version TEXT NOT NULL,
    artifact_digest TEXT NOT NULL,
    config_revision TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMPTZ,
    exit_code INTEGER,
    error_code TEXT,
    error_message TEXT,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ux_ops_crawler_task_attempt_identity
        UNIQUE (job_id, attempt_no),
    CONSTRAINT ux_ops_crawler_task_attempt_epoch
        UNIQUE (job_id, lease_epoch),
    CONSTRAINT ux_ops_crawler_task_attempt_token
        UNIQUE (lease_token),
    CONSTRAINT ux_ops_crawler_task_attempt_observation_fk
        UNIQUE (id, job_id, attempt_no, lease_epoch),
    CONSTRAINT chk_ops_crawler_task_attempt_number
        CHECK (attempt_no > 0 AND lease_epoch > 0),
    CONSTRAINT chk_ops_crawler_task_attempt_status
        CHECK (status IN (
            'running', 'success', 'partial_success', 'failed', 'timed_out',
            'cancelled', 'lease_lost', 'dead_lettered'
        )),
    CONSTRAINT chk_ops_crawler_task_attempt_finished
        CHECK ((status = 'running') = (finished_at IS NULL)),
    CONSTRAINT chk_ops_crawler_task_attempt_code
        CHECK (
            worker_code_version = btrim(worker_code_version)
            AND char_length(worker_code_version) BETWEEN 1 AND 200
        ),
    CONSTRAINT chk_ops_crawler_task_attempt_artifact
        CHECK (
            artifact_digest = btrim(artifact_digest)
            AND char_length(artifact_digest) BETWEEN 1 AND 255
        ),
    CONSTRAINT chk_ops_crawler_task_attempt_config
        CHECK (
            config_revision = btrim(config_revision)
            AND char_length(config_revision) BETWEEN 1 AND 255
        ),
    CONSTRAINT chk_ops_crawler_task_attempt_metrics
        CHECK (jsonb_typeof(metrics) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_ops_crawler_task_attempts_job_started
    ON ops_crawler_task_attempts (job_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_ops_crawler_task_attempts_agent_running
    ON ops_crawler_task_attempts (agent_id, started_at)
    WHERE status = 'running';

CREATE TABLE IF NOT EXISTS ops_crawler_task_observations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_id UUID NOT NULL,
    job_id UUID NOT NULL,
    attempt_no INTEGER NOT NULL,
    lease_epoch BIGINT NOT NULL,
    observation_kind TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_ops_crawler_task_observation_attempt
        FOREIGN KEY (attempt_id, job_id, attempt_no, lease_epoch)
        REFERENCES ops_crawler_task_attempts(id, job_id, attempt_no, lease_epoch)
        ON DELETE RESTRICT,
    CONSTRAINT chk_ops_crawler_task_observation_number
        CHECK (attempt_no > 0 AND lease_epoch > 0),
    CONSTRAINT chk_ops_crawler_task_observation_kind
        CHECK (observation_kind IN (
            'claimed', 'started', 'heartbeat', 'progress', 'result',
            'error', 'lease_lost', 'finished'
        )),
    CONSTRAINT chk_ops_crawler_task_observation_payload
        CHECK (jsonb_typeof(payload) = 'object')
);

-- CREATE TABLE IF NOT EXISTS does not repair a partially-created predecessor.
-- Rebuild the evidence identity constraints explicitly so a drifted database
-- fails closed on duplicate rows instead of silently accepting ambiguous
-- terminal evidence.  The composite foreign key must be removed before its
-- referenced unique constraint and restored only after every identity fence.
ALTER TABLE ops_crawler_task_observations
    DROP CONSTRAINT IF EXISTS fk_ops_crawler_task_observation_attempt;
ALTER TABLE ops_crawler_task_attempts
    DROP CONSTRAINT IF EXISTS ux_ops_crawler_task_attempt_identity,
    DROP CONSTRAINT IF EXISTS ux_ops_crawler_task_attempt_epoch,
    DROP CONSTRAINT IF EXISTS ux_ops_crawler_task_attempt_token,
    DROP CONSTRAINT IF EXISTS ux_ops_crawler_task_attempt_observation_fk;
ALTER TABLE ops_crawler_task_attempts
    ADD CONSTRAINT ux_ops_crawler_task_attempt_identity
        UNIQUE (job_id, attempt_no),
    ADD CONSTRAINT ux_ops_crawler_task_attempt_epoch
        UNIQUE (job_id, lease_epoch),
    ADD CONSTRAINT ux_ops_crawler_task_attempt_token
        UNIQUE (lease_token),
    ADD CONSTRAINT ux_ops_crawler_task_attempt_observation_fk
        UNIQUE (id, job_id, attempt_no, lease_epoch);
ALTER TABLE ops_crawler_task_observations
    ADD CONSTRAINT fk_ops_crawler_task_observation_attempt
        FOREIGN KEY (attempt_id, job_id, attempt_no, lease_epoch)
        REFERENCES ops_crawler_task_attempts(id, job_id, attempt_no, lease_epoch)
        ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_ops_crawler_task_observations_attempt_time
    ON ops_crawler_task_observations (attempt_id, observed_at, created_at);
CREATE INDEX IF NOT EXISTS idx_ops_crawler_task_observations_job_attempt
    ON ops_crawler_task_observations (job_id, attempt_no, lease_epoch, observed_at);
-- A terminal result is the immutable evidence consumed by the finalizer.  It
-- must never be possible to append a second, later "finished" result for the
-- same physical attempt.
DROP INDEX IF EXISTS ux_ops_crawler_task_observations_finished_once;
CREATE UNIQUE INDEX ux_ops_crawler_task_observations_finished_once
    ON ops_crawler_task_observations (attempt_id)
    WHERE observation_kind = 'finished';

CREATE TABLE IF NOT EXISTS ops_crawler_release_rollouts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    environment TEXT NOT NULL,
    rollout_epoch BIGINT NOT NULL,
    artifact_digest TEXT NOT NULL
        REFERENCES ops_crawler_release_artifacts(artifact_digest) ON DELETE RESTRICT,
    previous_artifact_digest TEXT
        REFERENCES ops_crawler_release_artifacts(artifact_digest) ON DELETE RESTRICT,
    status TEXT NOT NULL DEFAULT 'planned',
    requested_worker_count INTEGER NOT NULL,
    strategy JSONB NOT NULL DEFAULT '{}'::jsonb,
    requested_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    CONSTRAINT ux_ops_crawler_release_rollouts_epoch
        UNIQUE (environment, rollout_epoch),
    CONSTRAINT chk_ops_crawler_release_rollouts_environment
        CHECK (environment IN ('production', 'staging', 'development')),
    CONSTRAINT chk_ops_crawler_release_rollouts_epoch
        CHECK (rollout_epoch > 0),
    CONSTRAINT chk_ops_crawler_release_rollouts_worker_count
        CHECK (requested_worker_count > 0),
    CONSTRAINT chk_ops_crawler_release_rollouts_status
        CHECK (status IN (
            'planned', 'running', 'paused', 'success', 'failed',
            'cancelled', 'rolling_back', 'rolled_back'
        )),
    CONSTRAINT chk_ops_crawler_release_rollouts_strategy
        CHECK (jsonb_typeof(strategy) = 'object')
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_ops_crawler_release_rollouts_active
    ON ops_crawler_release_rollouts (environment)
    WHERE status IN ('planned', 'running', 'paused', 'rolling_back');

CREATE INDEX IF NOT EXISTS idx_ops_crawler_release_rollouts_created
    ON ops_crawler_release_rollouts (environment, created_at DESC);

CREATE TABLE IF NOT EXISTS ops_crawler_worker_desired_state (
    environment TEXT NOT NULL,
    worker_key TEXT NOT NULL,
    agent_id UUID REFERENCES ops_agents(id) ON DELETE SET NULL,
    rollout_id UUID NOT NULL
        REFERENCES ops_crawler_release_rollouts(id) ON DELETE RESTRICT,
    generation BIGINT NOT NULL,
    desired_status TEXT NOT NULL DEFAULT 'active',
    cohort TEXT NOT NULL DEFAULT 'stable',
    artifact_digest TEXT NOT NULL
        REFERENCES ops_crawler_release_artifacts(artifact_digest) ON DELETE RESTRICT,
    code_version TEXT NOT NULL,
    config_revision TEXT NOT NULL,
    not_before TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_ops_crawler_worker_desired_state
        PRIMARY KEY (environment, worker_key),
    CONSTRAINT chk_ops_crawler_worker_desired_environment
        CHECK (environment IN ('production', 'staging', 'development')),
    CONSTRAINT chk_ops_crawler_worker_desired_key
        CHECK (
            worker_key = btrim(worker_key)
            AND char_length(worker_key) BETWEEN 1 AND 200
        ),
    CONSTRAINT chk_ops_crawler_worker_desired_generation
        CHECK (generation > 0),
    CONSTRAINT chk_ops_crawler_worker_desired_status
        CHECK (desired_status IN ('active', 'draining', 'disabled')),
    CONSTRAINT chk_ops_crawler_worker_desired_cohort
        CHECK (cohort IN ('canary', 'stable')),
    CONSTRAINT chk_ops_crawler_worker_desired_code
        CHECK (
            code_version = btrim(code_version)
            AND char_length(code_version) BETWEEN 1 AND 200
        ),
    CONSTRAINT chk_ops_crawler_worker_desired_config
        CHECK (
            config_revision = btrim(config_revision)
            AND char_length(config_revision) BETWEEN 1 AND 255
        )
);

CREATE INDEX IF NOT EXISTS idx_ops_crawler_worker_desired_agent
    ON ops_crawler_worker_desired_state (agent_id)
    WHERE agent_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ops_crawler_worker_desired_rollout
    ON ops_crawler_worker_desired_state (rollout_id, generation);

-- A worker process and its release reporter use distinct PostgreSQL logins but
-- represent one enrolled ops_agent.  This server-owned binding prevents either
-- credential from claiming/reporting as another agent without overloading the
-- legacy single credential_hint field.
CREATE TABLE IF NOT EXISTS ops_crawler_agent_bindings (
    agent_id UUID NOT NULL REFERENCES ops_agents(id) ON DELETE RESTRICT,
    environment TEXT NOT NULL,
    binding_type TEXT NOT NULL,
    database_login TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_ops_crawler_agent_bindings
        PRIMARY KEY (binding_type, database_login),
    CONSTRAINT ux_ops_crawler_agent_binding_agent_type
        UNIQUE (agent_id, binding_type),
    CONSTRAINT chk_ops_crawler_agent_binding_environment
        CHECK (environment IN ('production', 'staging', 'development')),
    CONSTRAINT chk_ops_crawler_agent_binding_type
        CHECK (binding_type IN ('worker', 'reporter')),
    CONSTRAINT chk_ops_crawler_agent_binding_login
        CHECK (
            database_login = btrim(database_login)
            AND database_login ~ '^[a-z_][a-z0-9_]{0,62}$'
        )
);

CREATE INDEX IF NOT EXISTS idx_ops_crawler_agent_bindings_agent
    ON ops_crawler_agent_bindings (agent_id, binding_type);

CREATE TABLE IF NOT EXISTS ops_crawler_release_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rollout_id UUID NOT NULL
        REFERENCES ops_crawler_release_rollouts(id) ON DELETE RESTRICT,
    environment TEXT NOT NULL,
    worker_key TEXT NOT NULL,
    agent_id UUID NOT NULL REFERENCES ops_agents(id) ON DELETE RESTRICT,
    desired_generation BIGINT NOT NULL,
    status TEXT NOT NULL,
    artifact_digest TEXT,
    code_version TEXT,
    config_revision TEXT,
    health JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_code TEXT,
    error_message TEXT,
    reported_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_ops_crawler_release_reports_environment
        CHECK (environment IN ('production', 'staging', 'development')),
    CONSTRAINT chk_ops_crawler_release_reports_worker_key
        CHECK (
            worker_key = btrim(worker_key)
            AND char_length(worker_key) BETWEEN 1 AND 200
        ),
    CONSTRAINT chk_ops_crawler_release_reports_generation
        CHECK (desired_generation > 0),
    CONSTRAINT chk_ops_crawler_release_reports_status
        CHECK (status IN (
            'pending', 'downloading', 'installing', 'verifying', 'ready',
            'failed', 'rolled_back', 'drifted'
        )),
    CONSTRAINT chk_ops_crawler_release_reports_health
        CHECK (jsonb_typeof(health) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_ops_crawler_release_reports_worker_latest
    ON ops_crawler_release_reports (environment, worker_key, reported_at DESC, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ops_crawler_release_reports_rollout
    ON ops_crawler_release_reports (rollout_id, reported_at DESC);

-- Observations, release artifacts, and deployment reports are evidence.  The
-- application roles receive no UPDATE/DELETE grants, and this trigger also
-- protects them from accidental mutation through a broader owner session.
CREATE OR REPLACE FUNCTION mooncen_reject_immutable_crawler_evidence()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION '% is immutable; append a new evidence row instead', TG_TABLE_NAME
        USING ERRCODE = '55000';
END
$$;

DROP TRIGGER IF EXISTS trg_ops_crawler_task_observations_immutable
    ON ops_crawler_task_observations;
CREATE TRIGGER trg_ops_crawler_task_observations_immutable
    BEFORE UPDATE OR DELETE ON ops_crawler_task_observations
    FOR EACH ROW EXECUTE FUNCTION mooncen_reject_immutable_crawler_evidence();

DROP TRIGGER IF EXISTS trg_ops_crawler_release_artifacts_immutable
    ON ops_crawler_release_artifacts;
CREATE TRIGGER trg_ops_crawler_release_artifacts_immutable
    BEFORE UPDATE OR DELETE ON ops_crawler_release_artifacts
    FOR EACH ROW EXECUTE FUNCTION mooncen_reject_immutable_crawler_evidence();

DROP TRIGGER IF EXISTS trg_ops_crawler_release_reports_immutable
    ON ops_crawler_release_reports;
CREATE TRIGGER trg_ops_crawler_release_reports_immutable
    BEFORE UPDATE OR DELETE ON ops_crawler_release_reports
    FOR EACH ROW EXECUTE FUNCTION mooncen_reject_immutable_crawler_evidence();

REVOKE ALL ON FUNCTION mooncen_reject_immutable_crawler_evidence() FROM PUBLIC;
REVOKE ALL ON TABLE
    ops_crawler_batches,
    ops_crawler_batch_tasks,
    ops_crawler_task_attempts,
    ops_crawler_task_observations,
    ops_crawler_release_artifacts,
    ops_crawler_release_rollouts,
    ops_crawler_worker_desired_state,
    ops_crawler_agent_bindings,
    ops_crawler_release_reports
FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_api') THEN
        GRANT SELECT, INSERT, UPDATE ON TABLE ops_crawler_batches TO mooncen_api;
        GRANT SELECT, INSERT ON TABLE ops_crawler_batch_tasks TO mooncen_api;
        GRANT SELECT ON TABLE
            ops_crawler_task_attempts,
            ops_crawler_task_observations
        TO mooncen_api;
        -- Release access is intentionally absent. A dedicated crawler API
        -- role receives bounded reads and request-queue INSERT in the later
        -- 20260812_002 control-only migration.
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_crawler_control') THEN
        GRANT SELECT, INSERT, UPDATE ON TABLE ops_jobs, ops_crawler_runs
            TO mooncen_crawler_control;
        GRANT SELECT, INSERT ON TABLE ops_job_logs TO mooncen_crawler_control;
        GRANT SELECT, INSERT ON TABLE ops_crawler_batches,
            ops_crawler_batch_tasks TO mooncen_crawler_control;
        GRANT SELECT ON TABLE ops_crawler_task_attempts,
            ops_crawler_task_observations, ops_crawler_release_artifacts,
            ops_crawler_release_rollouts, ops_crawler_worker_desired_state,
            ops_crawler_release_reports TO mooncen_crawler_control;
        GRANT UPDATE (status, finished_at, error_code, error_message)
            ON TABLE ops_crawler_task_attempts TO mooncen_crawler_control;
        GRANT INSERT ON TABLE ops_crawler_task_observations
            TO mooncen_crawler_control;
        GRANT SELECT (id, environment, status, maintenance_mode, last_seen_at)
            ON TABLE ops_agents TO mooncen_crawler_control;
        GRANT SELECT (agent_id, environment, binding_type)
            ON TABLE ops_crawler_agent_bindings TO mooncen_crawler_control;
        IF to_regclass('public.ops_job_logs_id_seq') IS NOT NULL THEN
            GRANT USAGE, SELECT ON SEQUENCE ops_job_logs_id_seq
                TO mooncen_crawler_control;
        END IF;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_crawler_publisher') THEN
        GRANT SELECT ON TABLE ops_crawler_release_artifacts,
            ops_crawler_release_rollouts, ops_crawler_worker_desired_state
            TO mooncen_crawler_publisher;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_crawler_release_admin') THEN
        GRANT SELECT, INSERT ON TABLE ops_crawler_release_artifacts
            TO mooncen_crawler_release_admin;
        GRANT SELECT, INSERT, UPDATE ON TABLE ops_crawler_release_rollouts,
            ops_crawler_worker_desired_state
            TO mooncen_crawler_release_admin;
        GRANT SELECT ON TABLE ops_crawler_release_reports
            TO mooncen_crawler_release_admin;
        GRANT SELECT (id, name, hostname, environment, status,
            maintenance_mode, capabilities, last_seen_at)
            ON TABLE ops_agents TO mooncen_crawler_release_admin;
        GRANT SELECT (agent_id, environment, binding_type)
            ON TABLE ops_crawler_agent_bindings TO mooncen_crawler_release_admin;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_crawler_finalizer') THEN
        GRANT SELECT ON TABLE ops_jobs, ops_crawler_batches,
            ops_crawler_batch_tasks, ops_crawler_task_attempts,
            ops_crawler_task_observations TO mooncen_crawler_finalizer;
        GRANT UPDATE (status, started_at, finished_at)
            ON TABLE ops_crawler_batches TO mooncen_crawler_finalizer;
        IF to_regclass('public.crawl_batches') IS NOT NULL THEN
            GRANT SELECT, INSERT ON TABLE crawl_batches TO mooncen_crawler_finalizer;
            GRANT UPDATE (
                status, finished_at, total_branches, total_courses,
                valid_courses, invalid_courses, result, updated_at
            ) ON TABLE crawl_batches TO mooncen_crawler_finalizer;
        END IF;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_crawler_approver') THEN
        GRANT SELECT ON TABLE ops_crawler_batches, ops_crawler_batch_tasks,
            ops_crawler_task_attempts, ops_crawler_task_observations
            TO mooncen_crawler_approver;
        IF to_regclass('public.crawl_batches') IS NOT NULL THEN
            GRANT SELECT ON TABLE crawl_batches TO mooncen_crawler_approver;
            GRANT UPDATE (result, updated_at)
                ON TABLE crawl_batches TO mooncen_crawler_approver;
        END IF;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_crawler_reporter') THEN
        GRANT SELECT ON TABLE ops_agents, ops_crawler_worker_desired_state,
            ops_crawler_release_reports TO mooncen_crawler_reporter;
        GRANT INSERT ON TABLE ops_crawler_release_reports
            TO mooncen_crawler_reporter;
    END IF;

    -- The dedicated distributed worker is intentionally not a member of the
    -- legacy mooncen_crawler group.  In particular, it must not inherit the
    -- legacy crawl_batches UPDATE grant that controls primary promotion.
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_crawler_worker') THEN
        GRANT SELECT ON TABLE ops_crawler_worker_desired_state
            TO mooncen_crawler_worker;
        GRANT SELECT, INSERT ON TABLE ops_crawler_task_attempts
            TO mooncen_crawler_worker;
        GRANT UPDATE (
            status, finished_at, exit_code, error_code, error_message, metrics
        ) ON TABLE ops_crawler_task_attempts TO mooncen_crawler_worker;
        GRANT SELECT, INSERT ON TABLE ops_crawler_task_observations
            TO mooncen_crawler_worker;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_applier') THEN
        GRANT SELECT ON TABLE ops_jobs TO mooncen_applier;
        GRANT SELECT ON TABLE ops_crawler_batches TO mooncen_applier;
        GRANT SELECT ON TABLE
            ops_crawler_batch_tasks,
            ops_crawler_task_attempts,
            ops_crawler_task_observations
        TO mooncen_applier;
        IF to_regclass('public.crawl_batches') IS NOT NULL THEN
            GRANT SELECT ON TABLE crawl_batches TO mooncen_applier;
        END IF;
    END IF;
END
$$;

COMMENT ON COLUMN ops_jobs.agent_id IS
    'Assigned worker and fenced lease owner; lease_token and lease_epoch guard every worker mutation.';
COMMENT ON COLUMN ops_jobs.available_at IS
    'Earliest instant at which a queued job may be claimed or retried.';
COMMENT ON TABLE ops_crawler_batches IS
    'Central scheduled crawler cycle; one row per environment and scheduled slot.';
COMMENT ON TABLE ops_crawler_batch_tasks IS
    'Immutable mapping from a crawler batch task key to its generic ops_jobs row.';
COMMENT ON TABLE ops_crawler_task_attempts IS
    'Fenced execution attempt snapshot keyed to the job lease epoch and token.';
COMMENT ON TABLE ops_crawler_task_observations IS
    'Append-only observations emitted by one fenced crawler task attempt.';
COMMENT ON TABLE ops_crawler_agent_bindings IS
    'Server-side binding between one enrolled agent and its separate worker/reporter database logins.';
COMMENT ON TABLE ops_crawler_release_artifacts IS
    'Immutable reviewed crawler release artifacts addressed by digest.';
COMMENT ON TABLE ops_crawler_worker_desired_state IS
    'Central per-worker desired release generation consumed by deployment agents.';
COMMENT ON TABLE ops_crawler_release_reports IS
    'Append-only worker deployment observations for a release rollout.';
