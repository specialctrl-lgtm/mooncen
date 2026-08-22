-- Independent, database-attested operator approval receipts for crawler rollout actions.
--
-- The Ops API credential can append an immutable admin proposal, but it cannot
-- approve or lease that proposal.  A separately provisioned approver LOGIN must
-- call the reviewed SECURITY DEFINER function below.  The function resolves the
-- caller from session_user, recomputes the request digest from locked server-side
-- fields, and appends one short-lived receipt.  The release-admin consumer may
-- only claim the five rollout transitions while that exact receipt is live.

DO $$
BEGIN
    IF to_regclass('public.ops_crawler_control_database_marker') IS NULL
       OR NOT EXISTS (
           SELECT 1
           FROM public.ops_crawler_control_database_marker
           WHERE singleton IS TRUE
             AND database_name = current_database()::name
       ) THEN
        RAISE EXCEPTION 'release operator approvals require the marked crawler-control database';
    END IF;
    IF to_regclass('public.ops_crawler_release_action_requests') IS NULL
       OR to_regclass('public.ops_crawler_api_bindings') IS NULL
       OR (
           SELECT count(*)
           FROM pg_roles
           WHERE rolname IN (
               'mooncen_crawler_api', 'mooncen_crawler_release_approver',
               'mooncen_crawler_release_admin'
           )
       ) <> 3 THEN
        RAISE EXCEPTION 'release operator approvals require the release action and role contract';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION crawler_release_action_request_digest(
    request_id UUID,
    request_action TEXT,
    request_environment TEXT,
    request_idempotency_key TEXT,
    request_expected_generation BIGINT,
    request_payload JSONB,
    request_user_id UUID,
    request_database_login NAME,
    request_user_role TEXT,
    request_reason TEXT,
    request_confirmation TEXT
)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
SET search_path = pg_catalog, public
AS $$
    SELECT encode(
        public.digest(
            convert_to(
                jsonb_build_array(
                    request_id::text,
                    request_action,
                    request_environment,
                    request_idempotency_key,
                    request_expected_generation,
                    request_payload,
                    request_user_id::text,
                    request_database_login::text,
                    request_user_role,
                    request_reason,
                    request_confirmation
                )::text,
                'UTF8'
            ),
            'sha256'
        ),
        'hex'
    )
$$;

REVOKE ALL ON FUNCTION crawler_release_action_request_digest(
    UUID, TEXT, TEXT, TEXT, BIGINT, JSONB, UUID, NAME, TEXT, TEXT, TEXT
) FROM PUBLIC;

-- Validate the exact semantic proposal before an independent approval receipt
-- can be issued.  This deliberately duplicates the worker's strict decoder so
-- a forged direct SQL proposal cannot consume approval evidence and fail only
-- after it has entered the execution queue.
CREATE OR REPLACE FUNCTION crawler_release_action_proposal_is_valid(
    request_action TEXT,
    request_expected_generation BIGINT,
    request_payload JSONB,
    request_confirmation TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
IMMUTABLE
PARALLEL SAFE
SET search_path = pg_catalog, public
AS $$
DECLARE
    payload_keys TEXT[];
    rollout_text TEXT;
    target_digest TEXT;
    worker_count INTEGER;
    phase TEXT;
BEGIN
    IF jsonb_typeof(request_payload) <> 'object'
       OR request_confirmation IS NULL
       OR request_confirmation <> btrim(request_confirmation)
       OR char_length(request_confirmation) NOT BETWEEN 1 AND 180 THEN
        RETURN FALSE;
    END IF;
    SELECT COALESCE(array_agg(key ORDER BY key), ARRAY[]::TEXT[])
    INTO payload_keys
    FROM jsonb_object_keys(request_payload) AS keys(key);

    IF request_action = 'create_canary' THEN
        IF payload_keys IS DISTINCT FROM ARRAY[
               'artifact_digest', 'baseline_digest', 'rollout_id', 'worker_keys'
           ]::TEXT[]
           OR request_expected_generation < 1
           OR jsonb_typeof(request_payload->'artifact_digest') <> 'string'
           OR jsonb_typeof(request_payload->'baseline_digest') <> 'string'
           OR jsonb_typeof(request_payload->'rollout_id') <> 'string'
           OR jsonb_typeof(request_payload->'worker_keys') <> 'array'
           OR (request_payload->>'artifact_digest') !~ '^[0-9a-f]{64}$'
           OR (request_payload->>'baseline_digest') !~ '^[0-9a-f]{64}$'
           OR request_payload->>'artifact_digest' = request_payload->>'baseline_digest' THEN
            RETURN FALSE;
        END IF;
        rollout_text := (request_payload->>'rollout_id')::UUID::TEXT;
        IF rollout_text IS DISTINCT FROM request_payload->>'rollout_id' THEN
            RETURN FALSE;
        END IF;
        SELECT count(*),
               substring(encode(public.digest(convert_to(
                   COALESCE(string_agg(
                       item.value #>> '{}', E'\n'
                       ORDER BY (item.value #>> '{}') COLLATE "C"
                   ), ''),
                   'UTF8'), 'sha256'), 'hex') FOR 12)
        INTO worker_count, target_digest
        FROM jsonb_array_elements(request_payload->'worker_keys') AS item(value)
        WHERE jsonb_typeof(item.value) = 'string'
          AND (item.value #>> '{}') ~ '^[a-z][a-z0-9_-]{0,63}$';
        IF worker_count <> jsonb_array_length(request_payload->'worker_keys')
           OR worker_count NOT BETWEEN 1 AND 200
           OR worker_count <> (
               SELECT count(DISTINCT item.value #>> '{}')
               FROM jsonb_array_elements(request_payload->'worker_keys') AS item(value)
           ) THEN
            RETURN FALSE;
        END IF;
        RETURN request_confirmation = format(
            'CANARY %s %s %s %s %s', rollout_text, request_expected_generation,
            left(request_payload->>'artifact_digest', 12),
            left(request_payload->>'baseline_digest', 12), target_digest
        );
    ELSIF request_action = 'advance_rollout' THEN
        IF payload_keys IS DISTINCT FROM ARRAY[
               'rollout_id', 'rollout_phase'
           ]::TEXT[]
           AND payload_keys IS DISTINCT FROM ARRAY[
               'rollout_id', 'rollout_phase', 'target_worker_keys'
           ]::TEXT[]
        THEN
            RETURN FALSE;
        END IF;
        IF request_expected_generation < 1
           OR jsonb_typeof(request_payload->'rollout_id') <> 'string'
           OR jsonb_typeof(request_payload->'rollout_phase') <> 'string' THEN
            RETURN FALSE;
        END IF;
        rollout_text := (request_payload->>'rollout_id')::UUID::TEXT;
        IF rollout_text IS DISTINCT FROM request_payload->>'rollout_id' THEN
            RETURN FALSE;
        END IF;
        phase := request_payload->>'rollout_phase';
        IF phase NOT IN ('rolling', 'complete') THEN
            RETURN FALSE;
        END IF;
        IF request_payload ? 'target_worker_keys' THEN
            IF jsonb_typeof(request_payload->'target_worker_keys') <> 'array' THEN
                RETURN FALSE;
            END IF;
            SELECT count(*),
                   substring(encode(public.digest(convert_to(
                       COALESCE(string_agg(
                           item.value #>> '{}', E'\n'
                           ORDER BY (item.value #>> '{}') COLLATE "C"
                       ), ''),
                       'UTF8'), 'sha256'), 'hex') FOR 12)
            INTO worker_count, target_digest
            FROM jsonb_array_elements(request_payload->'target_worker_keys') AS item(value)
            WHERE jsonb_typeof(item.value) = 'string'
              AND (item.value #>> '{}') ~ '^[a-z][a-z0-9_-]{0,63}$';
            IF worker_count <> jsonb_array_length(request_payload->'target_worker_keys')
               OR worker_count > 200
               OR worker_count <> (
                   SELECT count(DISTINCT item.value #>> '{}')
                   FROM jsonb_array_elements(request_payload->'target_worker_keys') AS item(value)
               ) THEN
                RETURN FALSE;
            END IF;
        ELSE
            worker_count := 0;
            target_digest := NULL;
        END IF;
        IF (phase = 'rolling' AND worker_count < 1)
           OR (phase = 'complete' AND worker_count <> 0) THEN
            RETURN FALSE;
        END IF;
        RETURN request_confirmation = format(
            'ADVANCE %s %s %s %s', rollout_text, request_expected_generation,
            phase, CASE WHEN worker_count = 0 THEN 'none' ELSE target_digest END
        );
    ELSIF request_action IN (
        'pause_rollout', 'rollback_rollout', 'complete_rollback'
    ) THEN
        IF payload_keys IS DISTINCT FROM ARRAY['rollout_id']::TEXT[]
           OR request_expected_generation < 1
           OR jsonb_typeof(request_payload->'rollout_id') <> 'string' THEN
            RETURN FALSE;
        END IF;
        rollout_text := (request_payload->>'rollout_id')::UUID::TEXT;
        IF rollout_text IS DISTINCT FROM request_payload->>'rollout_id' THEN
            RETURN FALSE;
        END IF;
        RETURN request_confirmation = format(
            '%s %s %s',
            CASE request_action
                WHEN 'pause_rollout' THEN 'PAUSE'
                WHEN 'rollback_rollout' THEN 'ROLLBACK'
                ELSE 'COMPLETE_ROLLBACK'
            END,
            rollout_text, request_expected_generation
        );
    END IF;
    RETURN FALSE;
EXCEPTION
    WHEN OTHERS THEN
        RETURN FALSE;
END;
$$;

REVOKE ALL ON FUNCTION crawler_release_action_proposal_is_valid(
    TEXT, BIGINT, JSONB, TEXT
) FROM PUBLIC;

ALTER TABLE ops_crawler_release_action_requests
    ADD COLUMN request_digest TEXT;

-- The table already has FORCE RLS.  The migration runs atomically as its safe
-- object owner, so temporarily remove FORCE only for the deterministic backfill.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_trigger trigger
        WHERE trigger.tgrelid = 'public.ops_crawler_release_action_requests'::regclass
          AND trigger.tgname = 'zz_enforce_crawler_release_action_transition'
          AND trigger.tgenabled = 'O'
          AND NOT trigger.tgisinternal
          AND trigger.tgfoid =
              'public.enforce_crawler_release_action_transition()'::regprocedure
          AND trigger.tgtype = 31
    ) THEN
        RAISE EXCEPTION 'release action transition trigger drifted before digest backfill';
    END IF;
END;
$$;

ALTER TABLE ops_crawler_release_action_requests NO FORCE ROW LEVEL SECURITY;
ALTER TABLE ops_crawler_release_action_requests
    DISABLE TRIGGER zz_enforce_crawler_release_action_transition;
UPDATE ops_crawler_release_action_requests request
SET request_digest = crawler_release_action_request_digest(
    request.id, request.action, request.environment, request.idempotency_key,
    request.expected_generation, request.request_payload, request.requested_by,
    request.requester_login, request.requester_role, request.reason,
    request.confirmation
);
ALTER TABLE ops_crawler_release_action_requests
    ENABLE TRIGGER zz_enforce_crawler_release_action_transition;
ALTER TABLE ops_crawler_release_action_requests FORCE ROW LEVEL SECURITY;

ALTER TABLE ops_crawler_release_action_requests
    ALTER COLUMN request_digest SET NOT NULL,
    ADD CONSTRAINT chk_ops_crawler_release_action_request_digest
        CHECK (request_digest ~ '^[0-9a-f]{64}$');

CREATE OR REPLACE FUNCTION stamp_crawler_release_action_request_digest()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    canonical_digest TEXT;
BEGIN
    canonical_digest := public.crawler_release_action_request_digest(
        NEW.id, NEW.action, NEW.environment, NEW.idempotency_key,
        NEW.expected_generation, NEW.request_payload, NEW.requested_by,
        NEW.requester_login, NEW.requester_role, NEW.reason,
        NEW.confirmation
    );
    IF TG_OP = 'UPDATE' AND NEW.request_digest IS DISTINCT FROM canonical_digest THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'crawler release action request digest is immutable';
    END IF;
    NEW.request_digest := canonical_digest;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION stamp_crawler_release_action_request_digest() FROM PUBLIC;

CREATE TRIGGER zzz_stamp_crawler_release_action_request_digest
    BEFORE INSERT OR UPDATE ON ops_crawler_release_action_requests
    FOR EACH ROW
    EXECUTE FUNCTION stamp_crawler_release_action_request_digest();

CREATE OR REPLACE FUNCTION require_crawler_release_action_approval()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF OLD.status = 'queued' AND NEW.status = 'leased' AND (
        OLD.action NOT IN (
            'create_canary', 'advance_rollout', 'pause_rollout',
            'rollback_rollout', 'complete_rollback'
        )
        OR NOT EXISTS (
            SELECT 1
            FROM public.ops_crawler_release_action_approvals approval
            WHERE approval.request_id = OLD.id
              AND approval.environment = OLD.environment
              AND approval.request_digest = OLD.request_digest
              AND (
                  (OLD.started_at IS NULL AND approval.expires_at > clock_timestamp())
                  OR (OLD.started_at IS NOT NULL AND OLD.started_at <= approval.expires_at)
              )
        )
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'crawler release action has no exact live operator approval';
    END IF;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION require_crawler_release_action_approval() FROM PUBLIC;

-- Created after the receipt table below; the function can be compiled before
-- its relation exists because PL/pgSQL resolves the static statement on use.

CREATE TABLE ops_crawler_release_approver_bindings (
    database_login NAME PRIMARY KEY,
    environment TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT ux_ops_crawler_release_approver_binding_login_environment
        UNIQUE (database_login, environment),
    CONSTRAINT ux_ops_crawler_release_approver_binding_environment
        UNIQUE (environment),
    CONSTRAINT chk_ops_crawler_release_approver_binding_environment
        CHECK (environment IN ('production', 'staging', 'development')),
    CONSTRAINT chk_ops_crawler_release_approver_binding_login
        CHECK (
            database_login::text = btrim(database_login::text)
            AND database_login::text ~ '^[a-z_][a-z0-9_]{0,62}$'
        )
);

CREATE TABLE ops_crawler_release_action_approvals (
    receipt_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id UUID NOT NULL UNIQUE
        REFERENCES ops_crawler_release_action_requests(id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    environment TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    approver_login NAME NOT NULL,
    operator_identity TEXT NOT NULL,
    approval_reason TEXT NOT NULL,
    approved_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT fk_ops_crawler_release_approval_binding
        FOREIGN KEY (approver_login, environment)
        REFERENCES ops_crawler_release_approver_bindings(database_login, environment)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT chk_ops_crawler_release_approval_environment
        CHECK (environment IN ('production', 'staging', 'development')),
    CONSTRAINT chk_ops_crawler_release_approval_request_digest
        CHECK (request_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_ops_crawler_release_approval_operator_identity
        CHECK (
            operator_identity = btrim(operator_identity)
            AND char_length(operator_identity) BETWEEN 3 AND 200
            AND operator_identity ~ '^[A-Za-z0-9][A-Za-z0-9._:@/-]{2,199}$'
        ),
    CONSTRAINT chk_ops_crawler_release_approval_reason
        CHECK (
            approval_reason = btrim(approval_reason)
            AND char_length(approval_reason) BETWEEN 3 AND 500
        ),
    CONSTRAINT chk_ops_crawler_release_approval_ttl
        CHECK (
            expires_at > approved_at
            AND expires_at <= approved_at + interval '15 minutes'
        )
);

CREATE INDEX idx_ops_crawler_release_approvals_live
    ON ops_crawler_release_action_approvals
        (environment, expires_at, request_id);

-- A provisioned release-admin login owns no direct DML on this row.  The
-- reviewed heartbeat function below stamps session_user and is the only way a
-- running consumer can make rollout action capabilities live in the API.
CREATE TABLE ops_crawler_release_action_consumers (
    database_login NAME PRIMARY KEY,
    environment TEXT NOT NULL UNIQUE,
    consumer_id TEXT,
    last_seen_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT chk_ops_crawler_release_action_consumer_environment
        CHECK (environment IN ('production', 'staging', 'development')),
    CONSTRAINT chk_ops_crawler_release_action_consumer_login
        CHECK (
            database_login::text = btrim(database_login::text)
            AND database_login::text ~ '^[a-z_][a-z0-9_]{0,62}$'
        ),
    CONSTRAINT chk_ops_crawler_release_action_consumer_id
        CHECK (
            consumer_id IS NULL OR (
                consumer_id = btrim(consumer_id)
                AND char_length(consumer_id) BETWEEN 3 AND 200
                AND consumer_id ~ '^[A-Za-z0-9][A-Za-z0-9._:@/-]{2,199}$'
            )
        ),
    CONSTRAINT chk_ops_crawler_release_action_consumer_heartbeat
        CHECK (
            (consumer_id IS NULL AND last_seen_at IS NULL)
            OR (consumer_id IS NOT NULL AND last_seen_at IS NOT NULL)
        )
);

CREATE TRIGGER zy_require_crawler_release_action_approval
    BEFORE UPDATE ON ops_crawler_release_action_requests
    FOR EACH ROW
    EXECUTE FUNCTION require_crawler_release_action_approval();

CREATE OR REPLACE FUNCTION reject_crawler_release_approval_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = '55000',
        MESSAGE = 'crawler release operator approval receipts are append-only';
END;
$$;

REVOKE ALL ON FUNCTION reject_crawler_release_approval_mutation() FROM PUBLIC;

CREATE TRIGGER zz_reject_crawler_release_approval_mutation
    BEFORE UPDATE OR DELETE ON ops_crawler_release_action_approvals
    FOR EACH ROW
    EXECUTE FUNCTION reject_crawler_release_approval_mutation();

ALTER TABLE ops_crawler_release_approver_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops_crawler_release_approver_bindings FORCE ROW LEVEL SECURITY;
CREATE POLICY crawler_release_approver_binding_owner_access
    ON ops_crawler_release_approver_bindings
    USING (
        current_user::regrole = (
            SELECT relation.relowner
            FROM pg_class relation
            WHERE relation.oid = 'public.ops_crawler_release_approver_bindings'::regclass
        )
    )
    WITH CHECK (
        current_user::regrole = (
            SELECT relation.relowner
            FROM pg_class relation
            WHERE relation.oid = 'public.ops_crawler_release_approver_bindings'::regclass
        )
    );

ALTER TABLE ops_crawler_release_action_approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops_crawler_release_action_approvals FORCE ROW LEVEL SECURITY;
CREATE POLICY crawler_release_approval_owner_access
    ON ops_crawler_release_action_approvals
    USING (
        current_user::regrole = (
            SELECT relation.relowner
            FROM pg_class relation
            WHERE relation.oid = 'public.ops_crawler_release_action_approvals'::regclass
        )
    )
    WITH CHECK (
        current_user::regrole = (
            SELECT relation.relowner
            FROM pg_class relation
            WHERE relation.oid = 'public.ops_crawler_release_action_approvals'::regclass
        )
    );
CREATE POLICY crawler_release_approval_api_select
    ON ops_crawler_release_action_approvals
    FOR SELECT TO mooncen_crawler_api
    USING (environment = current_crawler_api_environment());
CREATE POLICY crawler_release_approval_admin_select
    ON ops_crawler_release_action_approvals
    FOR SELECT TO mooncen_crawler_release_admin
    USING (true);

ALTER TABLE ops_crawler_release_action_consumers ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops_crawler_release_action_consumers FORCE ROW LEVEL SECURITY;
CREATE POLICY crawler_release_action_consumer_owner_access
    ON ops_crawler_release_action_consumers
    USING (
        current_user::regrole = (
            SELECT relation.relowner
            FROM pg_class relation
            WHERE relation.oid = 'public.ops_crawler_release_action_consumers'::regclass
        )
    )
    WITH CHECK (
        current_user::regrole = (
            SELECT relation.relowner
            FROM pg_class relation
            WHERE relation.oid = 'public.ops_crawler_release_action_consumers'::regclass
        )
    );

-- Recreate the four policies introduced by 002 before capturing their
-- canonical PostgreSQL parse trees. This prevents a pre-existing weakened
-- policy from being blessed as the expected approval contract.
DROP POLICY crawler_release_action_api_select
    ON ops_crawler_release_action_requests;
DROP POLICY crawler_release_action_api_insert
    ON ops_crawler_release_action_requests;
DROP POLICY crawler_release_action_admin_select
    ON ops_crawler_release_action_requests;
DROP POLICY crawler_release_action_admin_update
    ON ops_crawler_release_action_requests;

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

-- SECURITY DEFINER needs a narrowly scoped route through the request table's
-- FORCE RLS.  The policy applies only while a separately bound approver LOGIN
-- is the immutable session_user; the function still locks and validates the row.
CREATE POLICY crawler_release_action_approval_owner_select
    ON ops_crawler_release_action_requests
    FOR SELECT
    USING (
        current_user::regrole = (
            SELECT relation.relowner
            FROM pg_class relation
            WHERE relation.oid = 'public.ops_crawler_release_action_requests'::regclass
        )
        AND pg_has_role(session_user, 'mooncen_crawler_release_approver', 'member')
        AND requester_login IS DISTINCT FROM session_user::name
        AND EXISTS (
            SELECT 1
            FROM ops_crawler_release_approver_bindings binding
            WHERE binding.database_login = session_user::name
              AND binding.environment = ops_crawler_release_action_requests.environment
              AND binding.enabled IS TRUE
        )
    );

-- Capture all ten policy parse trees only after every policy has been created
-- from this reviewed migration. pg_node_tree is compared inside the same
-- database, so any later role, command, USING, or WITH CHECK drift changes the
-- digest even when the policy name is retained.
CREATE TABLE ops_crawler_release_policy_contract (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE,
    policy_digest TEXT NOT NULL,
    policy_count INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT chk_ops_crawler_release_policy_contract_singleton
        CHECK (singleton IS TRUE),
    CONSTRAINT chk_ops_crawler_release_policy_contract_digest
        CHECK (policy_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_ops_crawler_release_policy_contract_count
        CHECK (policy_count = 10)
);

CREATE TRIGGER zz_reject_crawler_release_policy_contract_mutation
    BEFORE UPDATE OR DELETE ON ops_crawler_release_policy_contract
    FOR EACH ROW
    EXECUTE FUNCTION reject_crawler_release_approval_mutation();

WITH policy_rows AS (
    SELECT relation.relname::TEXT AS table_name,
           policy.polname::TEXT AS policy_name,
           policy.polpermissive,
           policy.polcmd,
           COALESCE((
               SELECT string_agg(
                   COALESCE(role.rolname, '<public>'), ','
                   ORDER BY COALESCE(role.rolname, '<public>')
               )
               FROM unnest(policy.polroles) policy_role(role_oid)
               LEFT JOIN pg_roles role ON role.oid = policy_role.role_oid
           ), '<public>') AS role_names,
           COALESCE(
               pg_get_expr(policy.polqual, policy.polrelid, FALSE),
               '<null>'
           ) AS using_tree,
           COALESCE(
               pg_get_expr(policy.polwithcheck, policy.polrelid, FALSE),
               '<null>'
           ) AS check_tree
    FROM pg_policy policy
    JOIN pg_class relation ON relation.oid = policy.polrelid
    WHERE policy.polrelid IN (
        'public.ops_crawler_release_action_requests'::regclass,
        'public.ops_crawler_release_approver_bindings'::regclass,
        'public.ops_crawler_release_action_approvals'::regclass,
        'public.ops_crawler_release_action_consumers'::regclass
    )
), policy_contract AS (
    SELECT count(*)::INTEGER AS policy_count,
           encode(public.digest(convert_to(COALESCE(string_agg(
               table_name || ':' || policy_name || ':' ||
               polpermissive::TEXT || ':' || polcmd::TEXT || ':' ||
               role_names || ':' || using_tree || ':' || check_tree,
               E'\n' ORDER BY table_name, policy_name
           ), ''), 'UTF8'), 'sha256'), 'hex') AS policy_digest
    FROM policy_rows
)
INSERT INTO ops_crawler_release_policy_contract (
    singleton, policy_digest, policy_count
)
SELECT TRUE, policy_digest, policy_count
FROM policy_contract
WHERE policy_count = 10;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM ops_crawler_release_policy_contract
        WHERE singleton IS TRUE AND policy_count = 10
    ) THEN
        RAISE EXCEPTION 'crawler release policy contract could not be captured';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION approve_crawler_release_action(
    requested_action_id UUID,
    expected_request_digest TEXT,
    authenticated_operator_identity TEXT,
    reviewed_reason TEXT,
    receipt_ttl_seconds INTEGER DEFAULT 300
)
RETURNS TABLE (
    request_id UUID,
    receipt_id UUID,
    environment TEXT,
    request_digest TEXT,
    approver_login NAME,
    operator_identity TEXT,
    approval_reason TEXT,
    approved_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    bound_environment TEXT;
    locked_request public.ops_crawler_release_action_requests%ROWTYPE;
    existing_receipt public.ops_crawler_release_action_approvals%ROWTYPE;
    canonical_digest TEXT;
    approval_time TIMESTAMPTZ;
BEGIN
    IF NOT pg_has_role(session_user, 'mooncen_crawler_release_approver', 'member')
       OR pg_has_role(session_user, 'mooncen_crawler_approver', 'member')
       OR pg_has_role(session_user, 'mooncen_crawler_api', 'member')
       OR pg_has_role(session_user, 'mooncen_crawler_release_admin', 'member')
       OR pg_has_role(session_user, 'mooncen_crawler_control', 'member')
       OR pg_has_role(session_user, 'mooncen_crawler_worker', 'member') THEN
        RAISE EXCEPTION USING ERRCODE = '42501',
            MESSAGE = 'release approval requires an isolated approver credential';
    END IF;
    IF (
        SELECT COALESCE(array_agg(parent.rolname ORDER BY parent.rolname), ARRAY[]::name[])
        FROM pg_auth_members membership
        JOIN pg_roles member ON member.oid = membership.member
        JOIN pg_roles parent ON parent.oid = membership.roleid
        WHERE member.rolname = session_user
    ) IS DISTINCT FROM ARRAY['mooncen_crawler_release_approver']::name[]
       OR EXISTS (
           SELECT 1
           FROM pg_auth_members membership
           JOIN pg_roles parent ON parent.oid = membership.roleid
           WHERE parent.rolname = session_user
       )
       OR EXISTS (
           SELECT 1
           FROM pg_auth_members membership
           JOIN pg_roles member ON member.oid = membership.member
           WHERE member.rolname = 'mooncen_crawler_release_approver'
       ) THEN
        RAISE EXCEPTION USING ERRCODE = '42501',
            MESSAGE = 'release approver role graph is not isolated';
    END IF;
    IF expected_request_digest !~ '^[0-9a-f]{64}$'
       OR authenticated_operator_identity IS NULL
       OR authenticated_operator_identity <> btrim(authenticated_operator_identity)
       OR authenticated_operator_identity !~ '^[A-Za-z0-9][A-Za-z0-9._:@/-]{2,199}$'
       OR char_length(authenticated_operator_identity) NOT BETWEEN 3 AND 200
       OR reviewed_reason IS NULL
       OR reviewed_reason <> btrim(reviewed_reason)
       OR char_length(reviewed_reason) NOT BETWEEN 3 AND 500
       OR receipt_ttl_seconds NOT BETWEEN 60 AND 900 THEN
        RAISE EXCEPTION USING ERRCODE = '22023',
            MESSAGE = 'release approval evidence is invalid';
    END IF;

    SELECT binding.environment
    INTO STRICT bound_environment
    FROM public.ops_crawler_release_approver_bindings binding
    WHERE binding.database_login = session_user::name
      AND binding.enabled IS TRUE;

    IF NOT public.crawler_release_approval_contract_is_valid(bound_environment) THEN
        RAISE EXCEPTION USING ERRCODE = '42501',
            MESSAGE = 'release approval catalog or credential contract is unavailable';
    END IF;

    SELECT request.*
    INTO STRICT locked_request
    FROM public.ops_crawler_release_action_requests request
    WHERE request.id = requested_action_id
      AND request.environment = bound_environment
    FOR UPDATE;

    canonical_digest := public.crawler_release_action_request_digest(
        locked_request.id, locked_request.action, locked_request.environment,
        locked_request.idempotency_key, locked_request.expected_generation,
        locked_request.request_payload, locked_request.requested_by,
        locked_request.requester_login, locked_request.requester_role,
        locked_request.reason, locked_request.confirmation
    );
    IF locked_request.action NOT IN (
           'create_canary', 'advance_rollout', 'pause_rollout',
           'rollback_rollout', 'complete_rollback'
       )
       OR locked_request.requester_role <> 'admin'
       OR locked_request.requester_login = session_user::name
       OR locked_request.status <> 'queued'
       OR locked_request.attempt_count <> 0
       OR locked_request.reconcile_only IS NOT FALSE
       OR locked_request.started_at IS NOT NULL
       OR NOT public.crawler_release_action_proposal_is_valid(
           locked_request.action, locked_request.expected_generation,
           locked_request.request_payload, locked_request.confirmation
       )
       OR locked_request.request_digest IS DISTINCT FROM canonical_digest
       OR expected_request_digest IS DISTINCT FROM canonical_digest THEN
        RAISE EXCEPTION USING ERRCODE = '42501',
            MESSAGE = 'release request does not match the exact approvable evidence';
    END IF;

    SELECT approval.*
    INTO existing_receipt
    FROM public.ops_crawler_release_action_approvals approval
    WHERE approval.request_id = locked_request.id
    LIMIT 1;
    IF FOUND THEN
        IF existing_receipt.request_digest IS DISTINCT FROM canonical_digest
           OR existing_receipt.approver_login IS DISTINCT FROM session_user::name
           OR existing_receipt.operator_identity IS DISTINCT FROM authenticated_operator_identity
           OR existing_receipt.approval_reason IS DISTINCT FROM reviewed_reason THEN
            RAISE EXCEPTION USING ERRCODE = '23505',
                MESSAGE = 'release request already has different approval evidence';
        END IF;
        IF existing_receipt.expires_at <= clock_timestamp() THEN
            RAISE EXCEPTION USING ERRCODE = '55000',
                MESSAGE = 'release approval expired; create a fresh release request';
        END IF;
        RETURN QUERY SELECT
            existing_receipt.request_id, existing_receipt.receipt_id,
            existing_receipt.environment, existing_receipt.request_digest,
            existing_receipt.approver_login, existing_receipt.operator_identity,
            existing_receipt.approval_reason, existing_receipt.approved_at,
            existing_receipt.expires_at;
        RETURN;
    END IF;

    approval_time := clock_timestamp();
    INSERT INTO public.ops_crawler_release_action_approvals (
        request_id, environment, request_digest, approver_login,
        operator_identity, approval_reason, approved_at, expires_at
    ) VALUES (
        locked_request.id,
        locked_request.environment, canonical_digest,
        session_user::name, authenticated_operator_identity, reviewed_reason,
        approval_time, approval_time + receipt_ttl_seconds * interval '1 second'
    )
    RETURNING * INTO existing_receipt;

    RETURN QUERY SELECT
        existing_receipt.request_id, existing_receipt.receipt_id,
        existing_receipt.environment, existing_receipt.request_digest,
        existing_receipt.approver_login, existing_receipt.operator_identity,
        existing_receipt.approval_reason, existing_receipt.approved_at,
        existing_receipt.expires_at;
EXCEPTION
    WHEN no_data_found OR too_many_rows THEN
        RAISE EXCEPTION USING ERRCODE = '42501',
            MESSAGE = 'release approver binding or request identity is missing or ambiguous';
END;
$$;

CREATE OR REPLACE FUNCTION preview_crawler_release_action_for_approval(
    requested_action_id UUID,
    expected_request_digest TEXT
)
RETURNS TABLE (
    request_id UUID,
    action TEXT,
    environment TEXT,
    expected_generation BIGINT,
    request_payload JSONB,
    requested_by UUID,
    requester_login NAME,
    requester_role TEXT,
    request_reason TEXT,
    confirmation TEXT,
    request_digest TEXT,
    approval_status TEXT,
    proposal_valid BOOLEAN
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    bound_environment TEXT;
BEGIN
    IF NOT pg_has_role(session_user, 'mooncen_crawler_release_approver', 'member')
       OR (
           SELECT COALESCE(array_agg(parent.rolname ORDER BY parent.rolname), ARRAY[]::name[])
           FROM pg_auth_members membership
           JOIN pg_roles parent ON parent.oid = membership.roleid
           WHERE membership.member = session_user::regrole
       ) IS DISTINCT FROM ARRAY['mooncen_crawler_release_approver']::name[] THEN
        RAISE EXCEPTION USING ERRCODE = '42501',
            MESSAGE = 'release approval preview requires an isolated approver credential';
    END IF;
    SELECT binding.environment
    INTO STRICT bound_environment
    FROM public.ops_crawler_release_approver_bindings binding
    WHERE binding.database_login = session_user::name
      AND binding.enabled IS TRUE;
    RETURN QUERY
    SELECT request.id, request.action, request.environment,
           request.expected_generation, request.request_payload,
           request.requested_by,
           request.requester_login, request.requester_role, request.reason,
           request.confirmation, request.request_digest,
           CASE
               WHEN approval.request_id IS NULL THEN 'pending'
               WHEN approval.expires_at <= clock_timestamp() THEN 'expired'
               ELSE 'approved'
           END,
           public.crawler_release_action_proposal_is_valid(
               request.action, request.expected_generation,
               request.request_payload, request.confirmation
           )
    FROM public.ops_crawler_release_action_requests request
    LEFT JOIN public.ops_crawler_release_action_approvals approval
      ON approval.request_id = request.id
    WHERE request.id = requested_action_id
      AND request.environment = bound_environment
      AND request.requester_login IS DISTINCT FROM session_user::name
      AND request.request_digest = expected_request_digest;
EXCEPTION
    WHEN no_data_found OR too_many_rows THEN
        RAISE EXCEPTION USING ERRCODE = '42501',
            MESSAGE = 'release approver binding is missing or ambiguous';
END;
$$;

REVOKE ALL ON FUNCTION approve_crawler_release_action(
    UUID, TEXT, TEXT, TEXT, INTEGER
) FROM PUBLIC;
REVOKE ALL ON FUNCTION preview_crawler_release_action_for_approval(UUID, TEXT)
    FROM PUBLIC;

CREATE OR REPLACE FUNCTION heartbeat_crawler_release_action_consumer(
    requested_environment TEXT,
    requested_consumer_id TEXT
)
RETURNS TIMESTAMPTZ
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    heartbeat_time TIMESTAMPTZ;
BEGIN
    IF requested_environment NOT IN ('production', 'staging', 'development')
       OR requested_consumer_id IS NULL
       OR requested_consumer_id <> btrim(requested_consumer_id)
       OR char_length(requested_consumer_id) NOT BETWEEN 3 AND 200
       OR requested_consumer_id !~ '^[A-Za-z0-9][A-Za-z0-9._:@/-]{2,199}$'
       OR NOT pg_has_role(session_user, 'mooncen_crawler_release_admin', 'member')
       OR pg_has_role(session_user, 'mooncen_crawler_api', 'member')
       OR pg_has_role(session_user, 'mooncen_crawler_release_approver', 'member')
       OR (
           SELECT COALESCE(
               array_agg(parent.rolname ORDER BY parent.rolname),
               ARRAY[]::name[]
           )
           FROM pg_auth_members membership
           JOIN pg_roles parent ON parent.oid = membership.roleid
           WHERE membership.member = session_user::regrole
       ) IS DISTINCT FROM ARRAY['mooncen_crawler_release_admin']::name[]
       OR (
           SELECT shobj_description(login.oid, 'pg_authid')
           FROM pg_roles login
           WHERE login.rolname = session_user
       ) IS DISTINCT FROM 'mooncen-managed-crawler-login:v1:release_admin' THEN
        RAISE EXCEPTION USING ERRCODE = '42501',
            MESSAGE = 'release action consumer credential is not isolated';
    END IF;
    IF NOT public.crawler_release_approval_contract_is_valid(requested_environment) THEN
        RAISE EXCEPTION USING ERRCODE = '42501',
            MESSAGE = 'release approval catalog or credential contract is unavailable';
    END IF;
    heartbeat_time := clock_timestamp();
    UPDATE public.ops_crawler_release_action_consumers consumer
    SET consumer_id = requested_consumer_id,
        last_seen_at = heartbeat_time
    WHERE consumer.database_login = session_user::name
      AND consumer.environment = requested_environment;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '42501',
            MESSAGE = 'release action consumer is not bound to this environment';
    END IF;
    RETURN heartbeat_time;
END;
$$;

REVOKE ALL ON FUNCTION heartbeat_crawler_release_action_consumer(TEXT, TEXT)
    FROM PUBLIC;

CREATE OR REPLACE FUNCTION crawler_release_approval_catalog_is_valid()
RETURNS BOOLEAN
LANGUAGE plpgsql
STABLE
SET search_path = pg_catalog, public
AS $$
DECLARE
    object_owner OID;
    catalog_valid BOOLEAN;
    expected_policy_digest TEXT;
    live_policy_digest TEXT;
    live_policy_count INTEGER;
    key_constraint_signature TEXT[];
BEGIN
    SELECT relation.relowner INTO object_owner
    FROM pg_class relation
    WHERE relation.oid = 'public.ops_crawler_release_action_approvals'::regclass;
    SELECT object_owner IS NOT NULL
       AND NOT EXISTS (
           SELECT 1 FROM pg_roles owner
           WHERE owner.oid = object_owner
             AND (owner.rolcanlogin OR owner.rolsuper OR owner.rolcreaterole
                  OR owner.rolcreatedb OR owner.rolreplication OR owner.rolbypassrls)
       )
       AND (
           SELECT count(*) = 5 AND count(DISTINCT relation.relowner) = 1
           FROM pg_class relation
           WHERE relation.oid IN (
               'public.ops_crawler_release_action_requests'::regclass,
               'public.ops_crawler_release_approver_bindings'::regclass,
               'public.ops_crawler_release_action_approvals'::regclass,
               'public.ops_crawler_release_action_consumers'::regclass,
               'public.ops_crawler_release_policy_contract'::regclass
           ) AND relation.relowner = object_owner
       )
       AND (
           SELECT count(*) = 4
                  AND bool_and(relation.relrowsecurity AND relation.relforcerowsecurity)
           FROM pg_class relation
           WHERE relation.oid IN (
               'public.ops_crawler_release_action_requests'::regclass,
               'public.ops_crawler_release_approver_bindings'::regclass,
               'public.ops_crawler_release_action_approvals'::regclass,
               'public.ops_crawler_release_action_consumers'::regclass
           )
       )
       AND (
           SELECT count(*) = 4
           FROM pg_trigger trigger
           WHERE (trigger.tgrelid, trigger.tgname, trigger.tgtype, trigger.tgfoid) IN (
               ('public.ops_crawler_release_action_requests'::regclass,
                'zzz_stamp_crawler_release_action_request_digest', 23,
                'public.stamp_crawler_release_action_request_digest()'::regprocedure),
               ('public.ops_crawler_release_action_requests'::regclass,
                'zy_require_crawler_release_action_approval', 19,
                'public.require_crawler_release_action_approval()'::regprocedure),
               ('public.ops_crawler_release_action_approvals'::regclass,
                'zz_reject_crawler_release_approval_mutation', 27,
                'public.reject_crawler_release_approval_mutation()'::regprocedure),
               ('public.ops_crawler_release_policy_contract'::regclass,
                'zz_reject_crawler_release_policy_contract_mutation', 27,
                'public.reject_crawler_release_approval_mutation()'::regprocedure)
           ) AND trigger.tgenabled = 'O' AND NOT trigger.tgisinternal
       )
       AND (
           SELECT count(*) = 1 AND bool_and(
               attribute.atttypid = 'text'::regtype AND attribute.attnotnull
               AND attribute.attidentity = '' AND attribute.attgenerated = ''
           )
           FROM pg_attribute attribute
           WHERE attribute.attrelid =
                   'public.ops_crawler_release_action_requests'::regclass
             AND attribute.attname = 'request_digest'
             AND attribute.attnum > 0 AND NOT attribute.attisdropped
       )
       AND NOT EXISTS (
           SELECT 1
           FROM pg_proc procedure
           CROSS JOIN LATERAL aclexplode(
               COALESCE(procedure.proacl, acldefault('f', procedure.proowner))
           ) privilege
           WHERE procedure.oid =
               'public.approve_crawler_release_action(uuid,text,text,text,integer)'::regprocedure
             AND privilege.grantee = 0
             AND privilege.privilege_type = 'EXECUTE'
       )
       AND has_function_privilege(
           'mooncen_crawler_release_approver',
           'public.approve_crawler_release_action(uuid,text,text,text,integer)',
           'EXECUTE'
       )
       AND NOT has_function_privilege(
           'mooncen_crawler_api',
           'public.approve_crawler_release_action(uuid,text,text,text,integer)',
           'EXECUTE'
       )
       AND NOT has_function_privilege(
           'mooncen_crawler_release_admin',
           'public.approve_crawler_release_action(uuid,text,text,text,integer)',
           'EXECUTE'
       )
       AND NOT EXISTS (
           SELECT 1
           FROM pg_attribute attribute
           CROSS JOIN LATERAL aclexplode(attribute.attacl) privilege
           JOIN pg_roles grantee ON grantee.oid = privilege.grantee
           WHERE attribute.attrelid IN (
               'public.ops_crawler_release_approver_bindings'::regclass,
               'public.ops_crawler_release_action_approvals'::regclass,
               'public.ops_crawler_release_action_consumers'::regclass
           )
             AND attribute.attnum > 0 AND NOT attribute.attisdropped
             AND grantee.rolname IN (
                 'mooncen_crawler_api', 'mooncen_crawler_release_approver',
                 'mooncen_crawler_release_admin'
             )
             AND privilege.privilege_type IN ('INSERT', 'UPDATE', 'REFERENCES')
       )
       AND NOT has_table_privilege(
           'mooncen_crawler_api', 'public.ops_crawler_release_action_approvals',
           'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES'
       )
       AND NOT has_table_privilege(
           'mooncen_crawler_release_admin', 'public.ops_crawler_release_action_approvals',
           'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES'
       )
       AND NOT has_table_privilege(
           'mooncen_crawler_release_approver', 'public.ops_crawler_release_action_approvals',
           'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES'
       )
       AND NOT has_table_privilege(
           'mooncen_crawler_api', 'public.ops_crawler_release_policy_contract',
           'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES'
       )
       AND NOT has_table_privilege(
           'mooncen_crawler_release_admin', 'public.ops_crawler_release_policy_contract',
           'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES'
       )
       AND NOT has_table_privilege(
           'mooncen_crawler_release_approver', 'public.ops_crawler_release_policy_contract',
           'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES'
       )
       INTO catalog_valid;

    IF catalog_valid IS NOT TRUE THEN
        RETURN FALSE;
    END IF;

    SELECT array_agg(
        relation.relname::TEXT || ':' || constraint_row.conname::TEXT || ':' ||
        constraint_row.contype::TEXT || ':' ||
        array_to_string(ARRAY(
            SELECT attribute.attname
            FROM unnest(constraint_row.conkey) WITH ORDINALITY key(attnum, position)
            JOIN pg_attribute attribute
              ON attribute.attrelid = constraint_row.conrelid
             AND attribute.attnum = key.attnum
            ORDER BY key.position
        ), ',') || ':' ||
        COALESCE(referenced.relname::TEXT, '<none>') || ':' ||
        CASE WHEN constraint_row.contype = 'f' THEN array_to_string(ARRAY(
                SELECT attribute.attname
                FROM unnest(constraint_row.confkey)
                    WITH ORDINALITY key(attnum, position)
                JOIN pg_attribute attribute
                  ON attribute.attrelid = constraint_row.confrelid
                 AND attribute.attnum = key.attnum
                ORDER BY key.position
            ), ',')
            ELSE '<none>'
        END || ':' ||
        constraint_row.convalidated::TEXT || ':' ||
        constraint_row.condeferrable::TEXT || ':' ||
        constraint_row.condeferred::TEXT || ':' ||
        CASE WHEN constraint_row.contype = 'f' THEN
            constraint_row.confupdtype::TEXT || ':' ||
            constraint_row.confdeltype::TEXT || ':' ||
            constraint_row.confmatchtype::TEXT
        ELSE '<none>:<none>:<none>' END
        ORDER BY relation.relname, constraint_row.conname
    ) INTO key_constraint_signature
    FROM pg_constraint constraint_row
    JOIN pg_class relation ON relation.oid = constraint_row.conrelid
    LEFT JOIN pg_class referenced ON referenced.oid = constraint_row.confrelid
    WHERE constraint_row.conrelid IN (
        'public.ops_crawler_release_approver_bindings'::regclass,
        'public.ops_crawler_release_action_approvals'::regclass,
        'public.ops_crawler_release_action_consumers'::regclass
    ) AND constraint_row.contype IN ('p', 'u', 'f');
    IF key_constraint_signature IS DISTINCT FROM ARRAY[
        'ops_crawler_release_action_approvals:fk_ops_crawler_release_approval_binding:f:approver_login,environment:ops_crawler_release_approver_bindings:database_login,environment:true:false:false:r:r:s',
        'ops_crawler_release_action_approvals:ops_crawler_release_action_approvals_pkey:p:receipt_id:<none>:<none>:true:false:false:<none>:<none>:<none>',
        'ops_crawler_release_action_approvals:ops_crawler_release_action_approvals_request_id_fkey:f:request_id:ops_crawler_release_action_requests:id:true:false:false:r:r:s',
        'ops_crawler_release_action_approvals:ops_crawler_release_action_approvals_request_id_key:u:request_id:<none>:<none>:true:false:false:<none>:<none>:<none>',
        'ops_crawler_release_action_consumers:ops_crawler_release_action_consumers_environment_key:u:environment:<none>:<none>:true:false:false:<none>:<none>:<none>',
        'ops_crawler_release_action_consumers:ops_crawler_release_action_consumers_pkey:p:database_login:<none>:<none>:true:false:false:<none>:<none>:<none>',
        'ops_crawler_release_approver_bindings:ops_crawler_release_approver_bindings_pkey:p:database_login:<none>:<none>:true:false:false:<none>:<none>:<none>',
        'ops_crawler_release_approver_bindings:ux_ops_crawler_release_approver_binding_environment:u:environment:<none>:<none>:true:false:false:<none>:<none>:<none>',
        'ops_crawler_release_approver_bindings:ux_ops_crawler_release_approver_binding_login_environment:u:database_login,environment:<none>:<none>:true:false:false:<none>:<none>:<none>'
    ]::TEXT[] THEN
        RETURN FALSE;
    END IF;

    IF (
        SELECT count(*) <> 12 OR bool_or(
            CASE constraint_name
                WHEN 'chk_ops_crawler_release_action_request_digest' THEN
                    normalized_definition <>
                        'checkrequest_digest~''^[0-9a-f]{64}$'''
                WHEN 'chk_ops_crawler_release_approver_binding_environment' THEN
                    normalized_definition <>
                        'checkenvironment=anyarray[''production'',''staging'',''development'']'
                WHEN 'chk_ops_crawler_release_approver_binding_login' THEN
                    normalized_definition <>
                        'checkdatabase_login=btrimdatabase_loginanddatabase_login~''^[a-z_][a-z0-9_]{0,62}$'''
                WHEN 'chk_ops_crawler_release_approval_environment' THEN
                    normalized_definition <>
                        'checkenvironment=anyarray[''production'',''staging'',''development'']'
                WHEN 'chk_ops_crawler_release_approval_request_digest' THEN
                    normalized_definition <>
                        'checkrequest_digest~''^[0-9a-f]{64}$'''
                WHEN 'chk_ops_crawler_release_approval_operator_identity' THEN
                    normalized_definition NOT IN (
                        'checkoperator_identity=btrimoperator_identityandchar_lengthoperator_identitybetween3and200andoperator_identity~''^[a-za-z0-9][a-za-z0-9._:@/-]{2,199}$''',
                        'checkoperator_identity=btrimoperator_identityandchar_lengthoperator_identity>=3andchar_lengthoperator_identity<=200andoperator_identity~''^[a-za-z0-9][a-za-z0-9._:@/-]{2,199}$'''
                    )
                WHEN 'chk_ops_crawler_release_approval_reason' THEN
                    normalized_definition NOT IN (
                        'checkapproval_reason=btrimapproval_reasonandchar_lengthapproval_reasonbetween3and500',
                        'checkapproval_reason=btrimapproval_reasonandchar_lengthapproval_reason>=3andchar_lengthapproval_reason<=500'
                    )
                WHEN 'chk_ops_crawler_release_approval_ttl' THEN
                    normalized_definition NOT IN (
                        'checkexpires_at>approved_atandexpires_at<=approved_at+''00:15:00''',
                        'checkexpires_at>approved_atandexpires_at<=approved_at+''15minutes'''
                    )
                WHEN 'chk_ops_crawler_release_action_consumer_environment' THEN
                    normalized_definition <>
                        'checkenvironment=anyarray[''production'',''staging'',''development'']'
                WHEN 'chk_ops_crawler_release_action_consumer_login' THEN
                    normalized_definition <>
                        'checkdatabase_login=btrimdatabase_loginanddatabase_login~''^[a-z_][a-z0-9_]{0,62}$'''
                WHEN 'chk_ops_crawler_release_action_consumer_id' THEN
                    normalized_definition NOT IN (
                        'checkconsumer_idisnullorconsumer_id=btrimconsumer_idandchar_lengthconsumer_idbetween3and200andconsumer_id~''^[a-za-z0-9][a-za-z0-9._:@/-]{2,199}$''',
                        'checkconsumer_idisnullorconsumer_id=btrimconsumer_idandchar_lengthconsumer_id>=3andchar_lengthconsumer_id<=200andconsumer_id~''^[a-za-z0-9][a-za-z0-9._:@/-]{2,199}$'''
                    )
                WHEN 'chk_ops_crawler_release_action_consumer_heartbeat' THEN
                    normalized_definition <>
                        'checkconsumer_idisnullandlast_seen_atisnullorconsumer_idisnotnullandlast_seen_atisnotnull'
                ELSE TRUE
            END
        )
        FROM (
            SELECT constraint_row.conname::TEXT AS constraint_name,
                   replace(replace(replace(replace(
                       regexp_replace(
                           lower(pg_get_constraintdef(constraint_row.oid)),
                           '[[:space:]()]', '', 'g'
                       ), '::text', ''), '::name', ''), '::integer', ''),
                       '::interval', '') AS normalized_definition
            FROM pg_constraint constraint_row
            WHERE constraint_row.conrelid IN (
                'public.ops_crawler_release_action_requests'::regclass,
                'public.ops_crawler_release_approver_bindings'::regclass,
                'public.ops_crawler_release_action_approvals'::regclass,
                'public.ops_crawler_release_action_consumers'::regclass
            ) AND constraint_row.contype = 'c'
              AND constraint_row.convalidated
              AND NOT constraint_row.connoinherit
              AND constraint_row.conname IN (
                  'chk_ops_crawler_release_action_request_digest',
                  'chk_ops_crawler_release_approver_binding_environment',
                  'chk_ops_crawler_release_approver_binding_login',
                  'chk_ops_crawler_release_approval_environment',
                  'chk_ops_crawler_release_approval_request_digest',
                  'chk_ops_crawler_release_approval_operator_identity',
                  'chk_ops_crawler_release_approval_reason',
                  'chk_ops_crawler_release_approval_ttl',
                  'chk_ops_crawler_release_action_consumer_environment',
                  'chk_ops_crawler_release_action_consumer_login',
                  'chk_ops_crawler_release_action_consumer_id',
                  'chk_ops_crawler_release_action_consumer_heartbeat'
              )
        ) canonical_checks
    ) THEN
        RETURN FALSE;
    END IF;

    SELECT contract.policy_digest
    INTO expected_policy_digest
    FROM public.ops_crawler_release_policy_contract contract
    WHERE contract.singleton IS TRUE AND contract.policy_count = 10;

    WITH policy_rows AS (
        SELECT relation.relname::TEXT AS table_name,
               policy.polname::TEXT AS policy_name,
               policy.polpermissive,
               policy.polcmd,
               COALESCE((
                   SELECT string_agg(
                       COALESCE(role.rolname, '<public>'), ','
                       ORDER BY COALESCE(role.rolname, '<public>')
                   )
                   FROM unnest(policy.polroles) policy_role(role_oid)
                   LEFT JOIN pg_roles role ON role.oid = policy_role.role_oid
               ), '<public>') AS role_names,
               COALESCE(
                   pg_get_expr(policy.polqual, policy.polrelid, FALSE),
                   '<null>'
               ) AS using_tree,
               COALESCE(
                   pg_get_expr(policy.polwithcheck, policy.polrelid, FALSE),
                   '<null>'
               ) AS check_tree
        FROM pg_policy policy
        JOIN pg_class relation ON relation.oid = policy.polrelid
        WHERE policy.polrelid IN (
            'public.ops_crawler_release_action_requests'::regclass,
            'public.ops_crawler_release_approver_bindings'::regclass,
            'public.ops_crawler_release_action_approvals'::regclass,
            'public.ops_crawler_release_action_consumers'::regclass
        )
    )
    SELECT count(*)::INTEGER,
           encode(public.digest(convert_to(COALESCE(string_agg(
               table_name || ':' || policy_name || ':' ||
               polpermissive::TEXT || ':' || polcmd::TEXT || ':' ||
               role_names || ':' || using_tree || ':' || check_tree,
               E'\n' ORDER BY table_name, policy_name
           ), ''), 'UTF8'), 'sha256'), 'hex')
    INTO live_policy_count, live_policy_digest
    FROM policy_rows;

    IF expected_policy_digest IS NULL
       OR live_policy_count IS DISTINCT FROM 10
       OR live_policy_digest IS DISTINCT FROM expected_policy_digest THEN
        RETURN FALSE;
    END IF;
    RETURN TRUE;
EXCEPTION WHEN OTHERS THEN
    RETURN FALSE;
END;
$$;

REVOKE ALL ON FUNCTION crawler_release_approval_catalog_is_valid() FROM PUBLIC;

-- Runtime readiness is intentionally false until provisioner creates exactly
-- one safe, isolated approver LOGIN for the requested environment.
CREATE OR REPLACE FUNCTION crawler_release_approval_contract_is_valid(
    requested_environment TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    object_owner OID;
BEGIN
    IF requested_environment NOT IN ('production', 'staging', 'development') THEN
        RETURN FALSE;
    END IF;
    IF NOT public.crawler_release_approval_catalog_is_valid() THEN
        RETURN FALSE;
    END IF;
    SELECT relation.relowner
    INTO object_owner
    FROM pg_class relation
    WHERE relation.oid = 'public.ops_crawler_release_action_approvals'::regclass;
    RETURN
        object_owner IS NOT NULL
        AND NOT EXISTS (
            SELECT 1
            FROM pg_roles owner_role
            WHERE owner_role.oid = object_owner
              AND (
                  owner_role.rolcanlogin OR owner_role.rolsuper
                  OR owner_role.rolcreaterole OR owner_role.rolcreatedb
                  OR owner_role.rolreplication OR owner_role.rolbypassrls
              )
        )
        AND (
            SELECT count(*) = 1
            FROM public.ops_crawler_release_approver_bindings binding
            JOIN pg_roles login ON login.rolname = binding.database_login
            WHERE binding.environment = requested_environment
              AND binding.enabled IS TRUE
              AND login.rolcanlogin AND login.rolinherit
              AND NOT login.rolsuper AND NOT login.rolcreaterole
              AND NOT login.rolcreatedb AND NOT login.rolreplication
              AND NOT login.rolbypassrls
              AND shobj_description(login.oid, 'pg_authid') =
                  'mooncen-managed-crawler-login:v1:release_approver'
              AND pg_has_role(
                  login.rolname, 'mooncen_crawler_release_approver', 'member'
              )
              AND NOT pg_has_role(login.rolname, 'mooncen_crawler_approver', 'member')
              AND NOT pg_has_role(login.rolname, 'mooncen_crawler_api', 'member')
              AND NOT pg_has_role(login.rolname, 'mooncen_crawler_release_admin', 'member')
              AND NOT pg_has_role(login.rolname, 'mooncen_crawler_control', 'member')
              AND NOT pg_has_role(login.rolname, 'mooncen_crawler_worker', 'member')
              AND NOT EXISTS (
                  SELECT 1 FROM pg_auth_members child
                  WHERE child.roleid = login.oid
              )
              AND (
                  SELECT COALESCE(
                      array_agg(parent.rolname ORDER BY parent.rolname),
                      ARRAY[]::name[]
                  )
                  FROM pg_auth_members membership
                  JOIN pg_roles parent ON parent.oid = membership.roleid
                  WHERE membership.member = login.oid
              ) = ARRAY['mooncen_crawler_release_approver']::name[]
        )
        AND (
            SELECT count(*) = 1
            FROM public.ops_crawler_release_approver_bindings binding
            WHERE binding.environment = requested_environment
              AND binding.enabled IS TRUE
        )
        AND (
            SELECT count(*) = 1
            FROM public.ops_crawler_release_action_consumers consumer
            JOIN pg_roles login ON login.rolname = consumer.database_login
            WHERE consumer.environment = requested_environment
              AND login.rolcanlogin AND login.rolinherit
              AND NOT login.rolsuper AND NOT login.rolcreaterole
              AND NOT login.rolcreatedb AND NOT login.rolreplication
              AND NOT login.rolbypassrls
              AND shobj_description(login.oid, 'pg_authid') =
                  'mooncen-managed-crawler-login:v1:release_admin'
              AND (
                  SELECT COALESCE(
                      array_agg(parent.rolname ORDER BY parent.rolname),
                      ARRAY[]::name[]
                  )
                  FROM pg_auth_members membership
                  JOIN pg_roles parent ON parent.oid = membership.roleid
                  WHERE membership.member = login.oid
              ) = ARRAY['mooncen_crawler_release_admin']::name[]
              AND NOT EXISTS (
                  SELECT 1 FROM pg_auth_members child
                  WHERE child.roleid = login.oid
              )
        )
        AND NOT EXISTS (
            SELECT 1
            FROM pg_auth_members membership
            JOIN pg_roles role ON role.oid = membership.member
            WHERE role.rolname = 'mooncen_crawler_release_approver'
        )
        AND NOT EXISTS (
            SELECT 1
            FROM pg_auth_members membership
            JOIN pg_roles role ON role.oid = membership.member
            WHERE role.rolname = 'mooncen_crawler_release_admin'
        )
        AND NOT pg_has_role(
            'mooncen_crawler_release_approver',
            (SELECT owner_role.rolname FROM pg_roles owner_role
             WHERE owner_role.oid = object_owner),
            'member'
        )
        AND NOT pg_has_role(
            'mooncen_crawler_api',
            (SELECT owner_role.rolname FROM pg_roles owner_role
             WHERE owner_role.oid = object_owner),
            'member'
        )
        AND NOT pg_has_role(
            'mooncen_crawler_release_admin',
            (SELECT owner_role.rolname FROM pg_roles owner_role
             WHERE owner_role.oid = object_owner),
            'member'
        )
        AND NOT EXISTS (
            SELECT 1
            FROM public.ops_crawler_api_bindings binding
            JOIN pg_roles login ON login.rolname = binding.database_login
            WHERE binding.environment = requested_environment
              AND (
                  pg_has_role(
                      login.rolname,
                      (SELECT owner_role.rolname FROM pg_roles owner_role
                       WHERE owner_role.oid = object_owner),
                      'member'
                  )
                  OR (
                      SELECT COALESCE(
                          array_agg(parent.rolname ORDER BY parent.rolname),
                          ARRAY[]::name[]
                      )
                      FROM pg_auth_members membership
                      JOIN pg_roles parent ON parent.oid = membership.roleid
                      WHERE membership.member = login.oid
                  ) IS DISTINCT FROM ARRAY['mooncen_crawler_api']::name[]
              )
        )
        AND (
            SELECT count(*) = 15
                   AND count(DISTINCT owner_oid) = 1
                   AND min(owner_oid) = object_owner
            FROM (
                SELECT relation.relowner AS owner_oid
                FROM pg_class relation
                WHERE relation.oid IN (
                    'public.ops_crawler_release_action_requests'::regclass,
                    'public.ops_crawler_release_approver_bindings'::regclass,
                    'public.ops_crawler_release_action_approvals'::regclass,
                    'public.ops_crawler_release_action_consumers'::regclass
                )
                UNION ALL
                SELECT procedure.proowner
                FROM pg_proc procedure
                WHERE procedure.oid IN (
                    'public.crawler_release_action_request_digest(uuid,text,text,text,bigint,jsonb,uuid,name,text,text,text)'::regprocedure,
                    'public.crawler_release_action_proposal_is_valid(text,bigint,jsonb,text)'::regprocedure,
                    'public.stamp_crawler_release_action_request_digest()'::regprocedure,
                    'public.require_crawler_release_action_approval()'::regprocedure,
                    'public.reject_crawler_release_approval_mutation()'::regprocedure,
                    'public.approve_crawler_release_action(uuid,text,text,text,integer)'::regprocedure,
                    'public.preview_crawler_release_action_for_approval(uuid,text)'::regprocedure,
                    'public.heartbeat_crawler_release_action_consumer(text,text)'::regprocedure,
                    'public.crawler_release_approval_catalog_is_valid()'::regprocedure,
                    'public.crawler_release_approval_contract_is_valid(text)'::regprocedure,
                    'public.crawler_release_action_runtime_is_ready(text)'::regprocedure
                )
            ) owned_objects
        )
        AND (
            SELECT count(*) = 1
            FROM pg_auth_members membership
            JOIN pg_roles role ON role.oid = membership.roleid
            JOIN pg_roles member ON member.oid = membership.member
            JOIN public.ops_crawler_release_approver_bindings binding
              ON binding.database_login = member.rolname
             AND binding.environment = requested_environment
             AND binding.enabled IS TRUE
            WHERE role.rolname = 'mooncen_crawler_release_approver'
              AND membership.admin_option IS FALSE
              AND COALESCE((to_jsonb(membership)->>'inherit_option')::boolean, true)
              AND COALESCE((to_jsonb(membership)->>'set_option')::boolean, true)
        )
        AND (
            SELECT count(*) = 1 AND bool_and(
                member.rolname = (
                    SELECT binding.database_login::TEXT
                    FROM public.ops_crawler_release_approver_bindings binding
                    WHERE binding.environment = requested_environment
                      AND binding.enabled IS TRUE
                )
            )
            FROM pg_auth_members membership
            JOIN pg_roles role ON role.oid = membership.roleid
            JOIN pg_roles member ON member.oid = membership.member
            WHERE role.rolname = 'mooncen_crawler_release_approver'
        )
        AND (
            SELECT count(*) = 1
            FROM pg_auth_members membership
            JOIN pg_roles role ON role.oid = membership.roleid
            JOIN pg_roles member ON member.oid = membership.member
            JOIN public.ops_crawler_release_action_consumers consumer
              ON consumer.database_login = member.rolname
             AND consumer.environment = requested_environment
            WHERE role.rolname = 'mooncen_crawler_release_admin'
              AND membership.admin_option IS FALSE
              AND COALESCE((to_jsonb(membership)->>'inherit_option')::boolean, true)
              AND COALESCE((to_jsonb(membership)->>'set_option')::boolean, true)
        )
        AND (
            SELECT count(*) = 1 AND bool_and(
                member.rolname = (
                    SELECT consumer.database_login::TEXT
                    FROM public.ops_crawler_release_action_consumers consumer
                    WHERE consumer.environment = requested_environment
                )
            )
            FROM pg_auth_members membership
            JOIN pg_roles role ON role.oid = membership.roleid
            JOIN pg_roles member ON member.oid = membership.member
            WHERE role.rolname = 'mooncen_crawler_release_admin'
        )
        AND (SELECT relrowsecurity AND relforcerowsecurity
             FROM pg_class WHERE oid = 'public.ops_crawler_release_action_requests'::regclass)
        AND (SELECT relrowsecurity AND relforcerowsecurity
             FROM pg_class WHERE oid = 'public.ops_crawler_release_action_approvals'::regclass)
        AND (SELECT relrowsecurity AND relforcerowsecurity
             FROM pg_class WHERE oid = 'public.ops_crawler_release_approver_bindings'::regclass)
        AND (SELECT relrowsecurity AND relforcerowsecurity
             FROM pg_class WHERE oid = 'public.ops_crawler_release_action_consumers'::regclass)
        AND EXISTS (
            SELECT 1 FROM pg_trigger trigger
            WHERE trigger.tgrelid = 'public.ops_crawler_release_action_requests'::regclass
              AND trigger.tgname = 'zzz_stamp_crawler_release_action_request_digest'
              AND trigger.tgenabled = 'O' AND NOT trigger.tgisinternal
              AND trigger.tgtype = 23
              AND trigger.tgfoid =
                  'public.stamp_crawler_release_action_request_digest()'::regprocedure
        )
        AND EXISTS (
            SELECT 1 FROM pg_trigger trigger
            WHERE trigger.tgrelid = 'public.ops_crawler_release_action_requests'::regclass
              AND trigger.tgname = 'zy_require_crawler_release_action_approval'
              AND trigger.tgenabled = 'O' AND NOT trigger.tgisinternal
              AND trigger.tgtype = 19
              AND trigger.tgfoid =
                  'public.require_crawler_release_action_approval()'::regprocedure
        )
        AND EXISTS (
            SELECT 1 FROM pg_trigger trigger
            WHERE trigger.tgrelid = 'public.ops_crawler_release_action_approvals'::regclass
              AND trigger.tgname = 'zz_reject_crawler_release_approval_mutation'
              AND trigger.tgenabled = 'O' AND NOT trigger.tgisinternal
              AND trigger.tgtype = 27
              AND trigger.tgfoid =
                  'public.reject_crawler_release_approval_mutation()'::regprocedure
        )
        AND has_function_privilege(
            'mooncen_crawler_release_approver',
            'public.approve_crawler_release_action(uuid,text,text,text,integer)',
            'EXECUTE'
        )
        AND has_function_privilege(
            'mooncen_crawler_release_approver',
            'public.preview_crawler_release_action_for_approval(uuid,text)',
            'EXECUTE'
        )
        AND NOT has_function_privilege(
            'mooncen_crawler_api',
            'public.approve_crawler_release_action(uuid,text,text,text,integer)',
            'EXECUTE'
        )
        AND NOT has_function_privilege(
            'mooncen_crawler_release_admin',
            'public.approve_crawler_release_action(uuid,text,text,text,integer)',
            'EXECUTE'
        )
        AND has_table_privilege(
            'mooncen_crawler_api', 'public.ops_crawler_release_action_approvals', 'SELECT'
        )
        AND has_table_privilege(
            'mooncen_crawler_release_admin',
            'public.ops_crawler_release_action_approvals', 'SELECT'
        )
        AND NOT has_table_privilege(
            'mooncen_crawler_api', 'public.ops_crawler_release_action_approvals',
            'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES'
        )
        AND NOT has_table_privilege(
            'mooncen_crawler_release_admin',
            'public.ops_crawler_release_action_approvals',
            'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES'
        )
        AND NOT has_table_privilege(
            'mooncen_crawler_release_approver',
            'public.ops_crawler_release_action_approvals',
            'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES'
        )
        AND NOT has_table_privilege(
            'mooncen_crawler_api', 'public.ops_crawler_release_approver_bindings',
            'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES'
        )
        AND NOT has_table_privilege(
            'mooncen_crawler_release_admin', 'public.ops_crawler_release_approver_bindings',
            'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES'
        )
        AND NOT has_table_privilege(
            'mooncen_crawler_release_approver',
            'public.ops_crawler_release_approver_bindings',
            'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES'
        )
        AND NOT has_table_privilege(
            'mooncen_crawler_api', 'public.ops_crawler_release_action_consumers',
            'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES'
        )
        AND NOT has_table_privilege(
            'mooncen_crawler_release_admin',
            'public.ops_crawler_release_action_consumers',
            'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES'
        );
EXCEPTION
    WHEN OTHERS THEN
        RETURN FALSE;
END;
$$;

REVOKE ALL ON FUNCTION crawler_release_approval_contract_is_valid(TEXT) FROM PUBLIC;

CREATE OR REPLACE FUNCTION crawler_release_action_runtime_is_ready(
    requested_environment TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    RETURN public.crawler_release_approval_contract_is_valid(requested_environment)
       AND (
           SELECT count(*) = 1
           FROM public.ops_crawler_release_action_consumers consumer
           JOIN pg_roles login ON login.rolname = consumer.database_login
           WHERE consumer.environment = requested_environment
             AND consumer.consumer_id IS NOT NULL
             AND consumer.last_seen_at > clock_timestamp() - interval '90 seconds'
             AND consumer.last_seen_at <= clock_timestamp() + interval '5 seconds'
             AND login.rolcanlogin AND login.rolinherit
             AND NOT login.rolsuper AND NOT login.rolcreaterole
             AND NOT login.rolcreatedb AND NOT login.rolreplication
             AND NOT login.rolbypassrls
             AND shobj_description(login.oid, 'pg_authid') =
                 'mooncen-managed-crawler-login:v1:release_admin'
             AND (
                 SELECT COALESCE(
                     array_agg(parent.rolname ORDER BY parent.rolname),
                     ARRAY[]::name[]
                 )
                 FROM pg_auth_members membership
                 JOIN pg_roles parent ON parent.oid = membership.roleid
                 WHERE membership.member = login.oid
             ) = ARRAY['mooncen_crawler_release_admin']::name[]
             AND NOT EXISTS (
                 SELECT 1 FROM pg_auth_members child
                 WHERE child.roleid = login.oid
             )
       )
       AND NOT has_table_privilege(
           'mooncen_crawler_release_admin',
           'public.ops_crawler_release_action_consumers',
           'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES'
       );
EXCEPTION
    WHEN OTHERS THEN
        RETURN FALSE;
END;
$$;

REVOKE ALL ON FUNCTION crawler_release_action_runtime_is_ready(TEXT) FROM PUBLIC;

REVOKE ALL ON TABLE ops_crawler_release_approver_bindings,
    ops_crawler_release_action_approvals,
    ops_crawler_release_action_consumers FROM PUBLIC;
REVOKE ALL ON TABLE ops_crawler_release_approver_bindings,
    ops_crawler_release_action_approvals,
    ops_crawler_release_action_consumers
    FROM mooncen_api, mooncen_crawler_api, mooncen_crawler_release_approver,
         mooncen_crawler_release_admin, mooncen_crawler_control,
         mooncen_crawler_worker;

GRANT SELECT ON ops_crawler_release_action_approvals
    TO mooncen_crawler_api, mooncen_crawler_release_admin;
GRANT EXECUTE ON FUNCTION approve_crawler_release_action(
    UUID, TEXT, TEXT, TEXT, INTEGER
) TO mooncen_crawler_release_approver;
GRANT EXECUTE ON FUNCTION preview_crawler_release_action_for_approval(UUID, TEXT)
    TO mooncen_crawler_release_approver;
GRANT EXECUTE ON FUNCTION heartbeat_crawler_release_action_consumer(TEXT, TEXT)
    TO mooncen_crawler_release_admin;
GRANT EXECUTE ON FUNCTION crawler_release_approval_contract_is_valid(TEXT)
    TO mooncen_crawler_api, mooncen_crawler_release_approver,
       mooncen_crawler_release_admin;
GRANT EXECUTE ON FUNCTION crawler_release_action_runtime_is_ready(TEXT)
    TO mooncen_crawler_api, mooncen_crawler_release_admin;

GRANT SELECT (request_digest)
    ON ops_crawler_release_action_requests TO mooncen_crawler_api;

COMMENT ON TABLE ops_crawler_release_action_approvals IS
    'Immutable, short-lived database-attested operator receipts; never writable by API, approver capability, or release-admin roles.';
COMMENT ON TABLE ops_crawler_release_action_consumers IS
    'Provisioned release action consumer binding and recent server-stamped heartbeat; no service role has direct DML.';
