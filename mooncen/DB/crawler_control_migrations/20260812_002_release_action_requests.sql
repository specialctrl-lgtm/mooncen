-- Audited Ops API request queue for privileged crawler release administration.
-- Apply only to the dedicated crawler-control staging database, after
-- 20260810_001_crawler_control_plane.sql and its database marker.

DO $$
BEGIN
    IF to_regclass('public.ops_crawler_control_database_marker') IS NULL
       OR NOT EXISTS (
           SELECT 1
           FROM public.ops_crawler_control_database_marker
           WHERE singleton IS TRUE
             AND database_name = current_database()::name
       ) THEN
        RAISE EXCEPTION 'release action queue requires the marked crawler-control database';
    END IF;
    IF to_regclass('public.ops_crawler_release_rollouts') IS NULL
       OR to_regclass('public.ops_audit_logs') IS NULL THEN
        RAISE EXCEPTION 'release action queue requires the crawler and Ops control schemas';
    END IF;
END;
$$;

CREATE TABLE ops_crawler_api_bindings (
    database_login NAME PRIMARY KEY,
    environment TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT ux_ops_crawler_api_binding_login_environment
        UNIQUE (database_login, environment),
    CONSTRAINT chk_ops_crawler_api_binding_environment
        CHECK (environment IN ('production', 'staging', 'development')),
    CONSTRAINT chk_ops_crawler_api_binding_login
        CHECK (
            database_login::text = btrim(database_login::text)
            AND database_login::text ~ '^[a-z_][a-z0-9_]{0,62}$'
        )
);

CREATE OR REPLACE FUNCTION current_crawler_api_environment()
RETURNS TEXT
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT binding.environment
    FROM public.ops_crawler_api_bindings binding
    WHERE binding.database_login = session_user::name
$$;

REVOKE ALL ON FUNCTION current_crawler_api_environment() FROM PUBLIC;
REVOKE ALL ON TABLE ops_crawler_api_bindings FROM PUBLIC;

CREATE TABLE ops_crawler_release_action_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action TEXT NOT NULL,
    environment TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    idempotency_key TEXT NOT NULL,
    expected_generation BIGINT NOT NULL,
    request_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    requested_by UUID NOT NULL,
    requester_login NAME NOT NULL DEFAULT session_user,
    requester_role TEXT NOT NULL,
    reason TEXT NOT NULL,
    confirmation TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    reconcile_only BOOLEAN NOT NULL DEFAULT FALSE,
    lease_owner TEXT,
    lease_token UUID,
    leased_until TIMESTAMPTZ,
    result JSONB,
    error_code TEXT,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT ux_ops_crawler_release_action_idempotency
        UNIQUE (environment, requested_by, idempotency_key),
    CONSTRAINT fk_ops_crawler_release_action_api_binding
        FOREIGN KEY (requester_login, environment)
        REFERENCES ops_crawler_api_bindings (database_login, environment)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT chk_ops_crawler_release_action
        CHECK (action IN (
            'build', 'register_artifact', 'create_canary',
            'advance_rollout', 'pause_rollout', 'rollback_rollout',
            'complete_rollback'
        )),
    CONSTRAINT chk_ops_crawler_release_action_environment
        CHECK (environment IN ('production', 'staging', 'development')),
    CONSTRAINT chk_ops_crawler_release_action_status
        CHECK (status IN (
            'queued', 'leased', 'succeeded', 'failed', 'cancelled',
            'reconciliation_required'
        )),
    CONSTRAINT chk_ops_crawler_release_action_idempotency
        CHECK (
            idempotency_key = btrim(idempotency_key)
            AND char_length(idempotency_key) BETWEEN 16 AND 128
            AND idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$'
        ),
    CONSTRAINT chk_ops_crawler_release_action_generation
        CHECK (expected_generation >= 0),
    CONSTRAINT chk_ops_crawler_release_action_payload
        CHECK (
            jsonb_typeof(request_payload) = 'object'
            AND pg_column_size(request_payload) <= 16384
        ),
    CONSTRAINT chk_ops_crawler_release_action_requester_role
        CHECK (requester_role = 'admin'),
    CONSTRAINT chk_ops_crawler_release_action_reason
        CHECK (
            reason = btrim(reason)
            AND char_length(reason) BETWEEN 3 AND 500
        ),
    CONSTRAINT chk_ops_crawler_release_action_confirmation
        CHECK (
            confirmation = btrim(confirmation)
            AND char_length(confirmation) BETWEEN 1 AND 180
        ),
    CONSTRAINT chk_ops_crawler_release_action_attempts
        CHECK (attempt_count BETWEEN 0 AND 20),
    CONSTRAINT chk_ops_crawler_release_action_lease_owner
        CHECK (
            lease_owner IS NULL
            OR (
                lease_owner = btrim(lease_owner)
                AND char_length(lease_owner) BETWEEN 1 AND 200
                AND lease_owner ~ '^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,199}$'
            )
        ),
    CONSTRAINT chk_ops_crawler_release_action_result
        CHECK (
            result IS NULL
            OR (jsonb_typeof(result) = 'object' AND pg_column_size(result) <= 65536)
        ),
    CONSTRAINT chk_ops_crawler_release_action_error
        CHECK (
            (error_code IS NULL OR char_length(error_code) BETWEEN 1 AND 120)
            AND (error_message IS NULL OR char_length(error_message) BETWEEN 1 AND 4000)
        ),
    CONSTRAINT chk_ops_crawler_release_action_state_fields
        CHECK (
            (status = 'queued'
                AND lease_owner IS NULL AND lease_token IS NULL AND leased_until IS NULL
                AND finished_at IS NULL AND result IS NULL
                AND error_code IS NULL AND error_message IS NULL)
            OR
            (status = 'leased'
                AND lease_owner IS NOT NULL AND lease_token IS NOT NULL
                AND leased_until IS NOT NULL AND started_at IS NOT NULL
                AND finished_at IS NULL AND result IS NULL
                AND error_code IS NULL AND error_message IS NULL)
            OR
            (status = 'succeeded'
                AND lease_owner IS NULL AND lease_token IS NULL AND leased_until IS NULL
                AND started_at IS NOT NULL AND finished_at IS NOT NULL
                AND result IS NOT NULL AND error_code IS NULL AND error_message IS NULL
                AND reconcile_only IS FALSE)
            OR
            (status = 'failed'
                AND lease_owner IS NULL AND lease_token IS NULL AND leased_until IS NULL
                AND started_at IS NOT NULL AND finished_at IS NOT NULL
                AND result IS NULL
                AND error_code IS NOT NULL AND error_message IS NOT NULL
                AND reconcile_only IS FALSE)
            OR
            (status = 'reconciliation_required'
                AND lease_owner IS NULL AND lease_token IS NULL AND leased_until IS NULL
                AND started_at IS NOT NULL AND finished_at IS NOT NULL
                AND result IS NULL
                AND error_code = 'reconciliation_required'
                AND error_message IS NOT NULL
                AND reconcile_only IS FALSE)
            OR
            (status = 'cancelled'
                AND lease_owner IS NULL AND lease_token IS NULL AND leased_until IS NULL
                AND result IS NULL AND error_code IS NULL AND error_message IS NULL
                AND finished_at IS NOT NULL AND reconcile_only IS FALSE)
        )
);

CREATE INDEX idx_ops_crawler_release_actions_ready
    ON ops_crawler_release_action_requests (environment, created_at, id)
    WHERE status = 'queued';
CREATE INDEX idx_ops_crawler_release_actions_lease
    ON ops_crawler_release_action_requests (leased_until, id)
    WHERE status = 'leased';
CREATE INDEX idx_ops_crawler_release_actions_requested
    ON ops_crawler_release_action_requests (requested_by, created_at DESC, id DESC);

CREATE OR REPLACE FUNCTION enforce_crawler_release_action_transition()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'crawler release action evidence is immutable';
    END IF;
    IF TG_OP = 'INSERT' THEN
        NEW.status := 'queued';
        NEW.attempt_count := 0;
        NEW.reconcile_only := FALSE;
        NEW.lease_owner := NULL;
        NEW.lease_token := NULL;
        NEW.leased_until := NULL;
        NEW.result := NULL;
        NEW.error_code := NULL;
        NEW.error_message := NULL;
        NEW.requester_login := session_user::name;
        NEW.created_at := clock_timestamp();
        NEW.started_at := NULL;
        NEW.finished_at := NULL;
        NEW.updated_at := NEW.created_at;
        RETURN NEW;
    END IF;

    IF ROW(
        NEW.id, NEW.action, NEW.environment, NEW.idempotency_key,
        NEW.expected_generation, NEW.request_payload, NEW.requested_by,
        NEW.requester_login, NEW.requester_role, NEW.reason,
        NEW.confirmation, NEW.created_at
    ) IS DISTINCT FROM ROW(
        OLD.id, OLD.action, OLD.environment, OLD.idempotency_key,
        OLD.expected_generation, OLD.request_payload, OLD.requested_by,
        OLD.requester_login, OLD.requester_role, OLD.reason,
        OLD.confirmation, OLD.created_at
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'crawler release action request identity is immutable';
    END IF;
    IF NOT (
        (OLD.status = 'queued' AND NEW.status IN ('queued', 'leased', 'cancelled'))
        OR (OLD.status = 'leased' AND NEW.status IN (
            'queued', 'leased', 'succeeded', 'failed', 'reconciliation_required'
        ))
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'crawler release action status transition is invalid';
    END IF;
    IF (
        OLD.status = 'queued'
        AND NEW.status = 'leased'
        AND NEW.attempt_count <> OLD.attempt_count
            + CASE WHEN OLD.reconcile_only THEN 0 ELSE 1 END
    ) OR (
        NOT (OLD.status = 'queued' AND NEW.status = 'leased')
        AND NEW.attempt_count <> OLD.attempt_count
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
        MESSAGE = 'crawler release action attempt fence is invalid';
    END IF;
    IF OLD.status = 'queued' AND NEW.status = 'leased'
       AND NEW.reconcile_only IS DISTINCT FROM OLD.reconcile_only THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'crawler release action reconciliation mode changed while claiming';
    END IF;
    IF (
        OLD.status = 'queued'
        AND NEW.status = 'queued'
        AND NEW.reconcile_only IS DISTINCT FROM OLD.reconcile_only
    ) OR (
        OLD.status = 'leased'
        AND NEW.status = 'leased'
        AND NEW.reconcile_only IS DISTINCT FROM OLD.reconcile_only
    ) OR (
        OLD.status = 'leased'
        AND NEW.status = 'queued'
        AND NOT (
            OLD.reconcile_only IS FALSE
            AND (
                NEW.reconcile_only IS FALSE
                OR (NEW.reconcile_only IS TRUE AND OLD.attempt_count >= 5)
            )
        )
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'crawler release action reconciliation mode transition is invalid';
    END IF;
    IF NEW.status IN ('succeeded', 'failed', 'cancelled', 'reconciliation_required')
       AND NEW.reconcile_only IS NOT FALSE THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'terminal crawler release actions cannot retain reconciliation mode';
    END IF;
    IF OLD.status = 'leased'
       AND NEW.started_at IS DISTINCT FROM OLD.started_at THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'crawler release action start evidence is immutable';
    END IF;
    IF OLD.status = 'leased' AND NEW.status = 'leased' AND (
        NEW.lease_owner IS DISTINCT FROM OLD.lease_owner
        OR NEW.lease_token IS DISTINCT FROM OLD.lease_token
        OR NEW.leased_until <= OLD.leased_until
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'crawler release action lease fence is invalid';
    END IF;
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION enforce_crawler_release_action_transition() FROM PUBLIC;

CREATE TRIGGER zz_enforce_crawler_release_action_transition
    BEFORE INSERT OR UPDATE OR DELETE ON ops_crawler_release_action_requests
    FOR EACH ROW
    EXECUTE FUNCTION enforce_crawler_release_action_transition();

ALTER TABLE ops_crawler_release_action_requests ENABLE ROW LEVEL SECURITY;

CREATE POLICY crawler_release_action_api_select
    ON ops_crawler_release_action_requests
    FOR SELECT TO mooncen_crawler_api
    USING (environment = current_crawler_api_environment());
CREATE POLICY crawler_release_action_api_insert
    ON ops_crawler_release_action_requests
    FOR INSERT TO mooncen_crawler_api
    WITH CHECK (
        requester_login = session_user::name
        AND environment = current_crawler_api_environment()
        AND status = 'queued'
        AND attempt_count = 0
        AND reconcile_only IS FALSE
        AND lease_owner IS NULL AND lease_token IS NULL AND leased_until IS NULL
        AND result IS NULL AND error_code IS NULL AND error_message IS NULL
        AND started_at IS NULL AND finished_at IS NULL
    );
CREATE POLICY crawler_release_action_admin_select
    ON ops_crawler_release_action_requests
    FOR SELECT TO mooncen_crawler_release_admin
    USING (true);
CREATE POLICY crawler_release_action_admin_update
    ON ops_crawler_release_action_requests
    FOR UPDATE TO mooncen_crawler_release_admin
    USING (true)
    WITH CHECK (true);

ALTER TABLE ops_crawler_release_action_requests FORCE ROW LEVEL SECURITY;

-- A crawler API login is permanently bound to one environment. ACL-preserving
-- permissive policies retain the existing non-API writers, while restrictive
-- SELECT policies make direct SQL observe only the login's bound environment.
ALTER TABLE ops_crawler_release_rollouts ENABLE ROW LEVEL SECURITY;
CREATE POLICY crawler_release_rollout_acl_access
    ON ops_crawler_release_rollouts
    USING (true)
    WITH CHECK (true);
CREATE POLICY crawler_api_rollout_environment_scope
    ON ops_crawler_release_rollouts AS RESTRICTIVE FOR SELECT
    USING (
        NOT pg_has_role(session_user, 'mooncen_crawler_api', 'member')
        OR environment = current_crawler_api_environment()
    );

ALTER TABLE ops_crawler_batches ENABLE ROW LEVEL SECURITY;
CREATE POLICY crawler_batch_acl_access
    ON ops_crawler_batches
    USING (true)
    WITH CHECK (true);
CREATE POLICY crawler_api_batch_environment_scope
    ON ops_crawler_batches AS RESTRICTIVE FOR SELECT
    USING (
        NOT pg_has_role(session_user, 'mooncen_crawler_api', 'member')
        OR environment = current_crawler_api_environment()
    );

ALTER TABLE ops_crawler_batch_tasks ENABLE ROW LEVEL SECURITY;
CREATE POLICY crawler_batch_task_acl_access
    ON ops_crawler_batch_tasks
    USING (true)
    WITH CHECK (true);
CREATE POLICY crawler_api_batch_task_environment_scope
    ON ops_crawler_batch_tasks AS RESTRICTIVE FOR SELECT
    USING (
        NOT pg_has_role(session_user, 'mooncen_crawler_api', 'member')
        OR EXISTS (
            SELECT 1
            FROM ops_crawler_batches scoped_batch
            WHERE scoped_batch.id = ops_crawler_batch_tasks.batch_id
              AND scoped_batch.environment = current_crawler_api_environment()
        )
    );

CREATE POLICY crawler_api_task_attempt_environment_scope
    ON ops_crawler_task_attempts AS RESTRICTIVE FOR SELECT
    USING (
        NOT pg_has_role(session_user, 'mooncen_crawler_api', 'member')
        OR EXISTS (
            SELECT 1
            FROM ops_crawler_batch_tasks scoped_task
            JOIN ops_crawler_batches scoped_batch ON scoped_batch.id = scoped_task.batch_id
            WHERE scoped_task.job_id = ops_crawler_task_attempts.job_id
              AND scoped_batch.environment = current_crawler_api_environment()
        )
    );

CREATE POLICY crawler_api_task_observation_environment_scope
    ON ops_crawler_task_observations AS RESTRICTIVE FOR SELECT
    USING (
        NOT pg_has_role(session_user, 'mooncen_crawler_api', 'member')
        OR EXISTS (
            SELECT 1
            FROM ops_crawler_task_attempts scoped_attempt
            JOIN ops_crawler_batch_tasks scoped_task ON scoped_task.job_id = scoped_attempt.job_id
            JOIN ops_crawler_batches scoped_batch ON scoped_batch.id = scoped_task.batch_id
            WHERE scoped_attempt.id = ops_crawler_task_observations.attempt_id
              AND scoped_batch.environment = current_crawler_api_environment()
        )
    );

ALTER TABLE ops_crawler_agent_bindings ENABLE ROW LEVEL SECURITY;
CREATE POLICY crawler_agent_binding_acl_access
    ON ops_crawler_agent_bindings
    USING (true)
    WITH CHECK (true);
CREATE POLICY crawler_api_agent_binding_environment_scope
    ON ops_crawler_agent_bindings AS RESTRICTIVE FOR SELECT
    USING (
        NOT pg_has_role(session_user, 'mooncen_crawler_api', 'member')
        OR environment = current_crawler_api_environment()
    );

ALTER TABLE crawl_batches ENABLE ROW LEVEL SECURITY;
CREATE POLICY crawler_staging_batch_acl_access
    ON crawl_batches
    USING (true)
    WITH CHECK (true);
CREATE POLICY crawler_api_staging_batch_environment_scope
    ON crawl_batches AS RESTRICTIVE FOR SELECT
    USING (
        NOT pg_has_role(session_user, 'mooncen_crawler_api', 'member')
        OR EXISTS (
            SELECT 1
            FROM ops_crawler_batches control_batch
            WHERE control_batch.id::text = crawl_batches.crawl_batch_id
              AND control_batch.environment = current_crawler_api_environment()
        )
    );

CREATE POLICY crawler_api_agent_environment_scope
    ON ops_agents AS RESTRICTIVE FOR SELECT
    USING (
        NOT pg_has_role(session_user, 'mooncen_crawler_api', 'member')
        OR environment = current_crawler_api_environment()
    );

CREATE POLICY crawler_api_job_environment_scope
    ON ops_jobs AS RESTRICTIVE FOR SELECT
    USING (
        NOT pg_has_role(session_user, 'mooncen_crawler_api', 'member')
        OR environment = current_crawler_api_environment()
    );

CREATE POLICY crawler_api_run_environment_scope
    ON ops_crawler_runs AS RESTRICTIVE FOR SELECT
    USING (
        NOT pg_has_role(session_user, 'mooncen_crawler_api', 'member')
        OR EXISTS (
            SELECT 1
            FROM ops_jobs bound_job
            WHERE bound_job.id = ops_crawler_runs.job_id
              AND bound_job.environment = current_crawler_api_environment()
        )
    );

CREATE POLICY crawler_api_report_environment_scope
    ON ops_crawler_release_reports AS RESTRICTIVE FOR SELECT
    USING (
        NOT pg_has_role(session_user, 'mooncen_crawler_api', 'member')
        OR environment = current_crawler_api_environment()
    );

CREATE POLICY crawler_api_desired_environment_scope
    ON ops_crawler_worker_desired_state AS RESTRICTIVE FOR SELECT
    USING (
        NOT pg_has_role(session_user, 'mooncen_crawler_api', 'member')
        OR environment = current_crawler_api_environment()
    );

REVOKE ALL ON TABLE ops_crawler_release_action_requests FROM PUBLIC;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_crawler_api') THEN
        RAISE EXCEPTION 'release action queue requires mooncen_crawler_api role';
    END IF;
END;
$$;

REVOKE ALL ON TABLE ops_crawler_release_artifacts,
    ops_crawler_release_rollouts, ops_crawler_worker_desired_state
    FROM mooncen_api;
REVOKE ALL ON TABLE ops_crawler_release_action_requests FROM mooncen_api;
REVOKE ALL ON TABLE ops_crawler_release_artifacts,
    ops_crawler_release_rollouts, ops_crawler_worker_desired_state,
    ops_crawler_release_reports,
    ops_crawler_release_action_requests
    FROM mooncen_crawler_api;
REVOKE ALL ON TABLE ops_crawler_api_bindings FROM mooncen_crawler_api;

GRANT SELECT (artifact_digest, code_version, config_revision, size_bytes,
    key_id, metadata, created_at)
    ON ops_crawler_release_artifacts TO mooncen_crawler_api;
GRANT SELECT (id, environment, rollout_epoch, artifact_digest,
    previous_artifact_digest, status, requested_worker_count, strategy,
    requested_by, created_at, started_at, finished_at)
    ON ops_crawler_release_rollouts TO mooncen_crawler_api;
GRANT SELECT (environment, worker_key, agent_id, rollout_id, generation,
    desired_status, cohort, artifact_digest, code_version, config_revision,
    not_before, updated_at)
    ON ops_crawler_worker_desired_state TO mooncen_crawler_api;
GRANT SELECT (id, rollout_id, environment, worker_key, agent_id,
    desired_generation, status, artifact_digest, code_version, config_revision,
    health, error_code, error_message, reported_at, created_at)
    ON ops_crawler_release_reports TO mooncen_crawler_api;
GRANT SELECT (id, environment, status, scheduled_slot, expected_task_count,
    code_version, artifact_digest, config_revision, started_at, finished_at, created_at)
    ON ops_crawler_batches TO mooncen_crawler_api;
GRANT SELECT (batch_id, job_id, task_key, provider, allowed_output_providers,
    required, shard_index, shard_count, created_at)
    ON ops_crawler_batch_tasks TO mooncen_crawler_api;
GRANT SELECT (id, job_id, attempt_no, lease_epoch, agent_id, status,
    worker_code_version, artifact_digest, config_revision, started_at,
    finished_at, exit_code, error_code, created_at)
    ON ops_crawler_task_attempts TO mooncen_crawler_api;
GRANT SELECT (id, attempt_id, job_id, attempt_no, lease_epoch,
    observation_kind, observed_at, created_at)
    ON ops_crawler_task_observations TO mooncen_crawler_api;
GRANT SELECT (id, job_id, provider, status, total_count, processed_count,
    success_count, failed_count, new_count, updated_count,
    deleted_candidate_count, started_at, finished_at, created_at)
    ON ops_crawler_runs TO mooncen_crawler_api;
GRANT SELECT (id, job_type, status, environment, available_at,
    cancel_requested_at, leased_until, retry_count, max_retries,
    queued_at, started_at, finished_at)
    ON ops_jobs TO mooncen_crawler_api;
GRANT SELECT (crawl_batch_id, total_courses, valid_courses,
    invalid_courses, result) ON crawl_batches TO mooncen_crawler_api;
GRANT SELECT (provider, status, severity, blocked_sync, detected_at)
    ON ops_quality_issues TO mooncen_crawler_api;
GRANT SELECT (id, name, hostname, environment, status,
    maintenance_mode, last_seen_at)
    ON ops_agents TO mooncen_crawler_api;
GRANT SELECT (agent_id, environment, binding_type)
    ON ops_crawler_agent_bindings TO mooncen_crawler_api;
GRANT SELECT (singleton, database_name)
    ON ops_crawler_control_database_marker TO mooncen_crawler_api;
GRANT SELECT (version, checksum)
    ON mooncen_schema_migrations TO mooncen_crawler_api;
GRANT INSERT (user_id, action, resource_type, resource_id, ip_address,
    user_agent, before_data, after_data, result, job_id)
    ON ops_audit_logs TO mooncen_crawler_api;
GRANT USAGE ON SEQUENCE ops_audit_logs_id_seq TO mooncen_crawler_api;
GRANT SELECT (id, action, environment, status, idempotency_key,
    expected_generation, request_payload, requested_by, requester_role,
    reason, confirmation, attempt_count, reconcile_only, leased_until,
    result, error_code, error_message, created_at, started_at,
    finished_at, updated_at), INSERT (
    action, environment, idempotency_key, expected_generation,
    request_payload, requested_by, requester_role, reason, confirmation
) ON ops_crawler_release_action_requests TO mooncen_crawler_api;
GRANT EXECUTE ON FUNCTION current_crawler_api_environment()
    TO mooncen_crawler_api;

GRANT SELECT ON ops_crawler_release_action_requests
    TO mooncen_crawler_release_admin;
GRANT SELECT (database_login, environment) ON ops_crawler_api_bindings
    TO mooncen_crawler_release_admin;
GRANT UPDATE (
    status, attempt_count, reconcile_only, lease_owner, lease_token, leased_until,
    result, error_code, error_message, started_at, finished_at, updated_at
) ON ops_crawler_release_action_requests TO mooncen_crawler_release_admin;

COMMENT ON TABLE ops_crawler_release_action_requests IS
    'Immutable Ops-admin requests leased by a separate crawler release-admin agent; the API never executes them inline.';
