-- Immutable evidence contract for the canonical Docker release.json and
-- validation.json documents.
--
-- The deployment worker must parse and revalidate the bounded canonical
-- documents again after claiming a queue lease.  Browser input never supplies
-- a command, path, Compose file, image tag, or image digest.

-- Container deployment claims reuse the generic lease columns when the
-- crawler-control migration is present, and create the same columns when Ops
-- is installed independently.  A separate global sequence is intentional:
-- the cloud controller retains the greatest epoch it has fenced, so an old
-- job can never reacquire remote mutation authority after a later job ends.
ALTER TABLE ops_jobs
    ADD COLUMN IF NOT EXISTS lease_token UUID,
    ADD COLUMN IF NOT EXISTS lease_epoch BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS leased_until TIMESTAMPTZ;

CREATE SEQUENCE IF NOT EXISTS ops_container_deployment_lease_epoch_seq
    AS BIGINT
    MINVALUE 1
    NO MAXVALUE
    START WITH 1
    INCREMENT BY 1
    NO CYCLE;

ALTER TABLE ops_jobs
    DROP CONSTRAINT IF EXISTS chk_ops_jobs_container_deployment_lease;
ALTER TABLE ops_jobs
    ADD CONSTRAINT chk_ops_jobs_container_deployment_lease
    CHECK (
        job_type <> 'deployment'
        OR COALESCE(parameters->>'deployment_mode', 'native') <> 'container'
        OR (
            status = 'queued'
            AND lease_token IS NULL
            AND lease_epoch = 0
            AND leased_until IS NULL
        )
        OR (
            status IN ('assigned', 'running')
            AND agent_id IS NOT NULL
            AND lease_token IS NOT NULL
            AND lease_epoch > 0
            AND leased_until IS NOT NULL
        )
        OR (
            status IN ('success', 'failed', 'cancelled', 'timed_out', 'blocked')
            AND lease_token IS NULL
            AND leased_until IS NULL
        )
    );

CREATE INDEX IF NOT EXISTS idx_ops_jobs_container_expired_lease
    ON ops_jobs (leased_until, id)
    WHERE job_type = 'deployment'
      AND status IN ('assigned', 'running')
      AND leased_until IS NOT NULL;

CREATE TABLE ops_container_releases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    release_digest TEXT NOT NULL UNIQUE,
    base_commit TEXT NOT NULL,
    source_tree TEXT NOT NULL,
    snapshot_commit TEXT NOT NULL,
    platform TEXT NOT NULL,
    api_image_digest TEXT NOT NULL,
    frontend_image_digest TEXT NOT NULL,
    bundle_sha256 TEXT NOT NULL,
    compose_sha256 TEXT NOT NULL,
    build_policy_sha256 TEXT NOT NULL,
    migration_ledger_sha256 TEXT NOT NULL,
    manifest_json JSONB NOT NULL,
    builder_target_identity TEXT NOT NULL,
    builder_hostname TEXT NOT NULL,
    built_by UUID REFERENCES users(id) ON DELETE SET NULL,
    built_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_ops_container_releases_release_digest
        CHECK (release_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_ops_container_releases_base_commit
        CHECK (base_commit ~ '^[0-9a-f]{40}$'),
    CONSTRAINT chk_ops_container_releases_source_tree
        CHECK (source_tree ~ '^[0-9a-f]{40}$'),
    CONSTRAINT chk_ops_container_releases_snapshot_commit
        CHECK (snapshot_commit ~ '^[0-9a-f]{40}$'),
    CONSTRAINT chk_ops_container_releases_platform
        CHECK (platform IN ('linux/amd64', 'linux/arm64')),
    CONSTRAINT chk_ops_container_releases_api_image
        CHECK (api_image_digest ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT chk_ops_container_releases_frontend_image
        CHECK (frontend_image_digest ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT chk_ops_container_releases_bundle
        CHECK (bundle_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_ops_container_releases_compose
        CHECK (compose_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_ops_container_releases_build_policy
        CHECK (build_policy_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_ops_container_releases_migration_ledger
        CHECK (migration_ledger_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_ops_container_releases_manifest_json
        CHECK (jsonb_typeof(manifest_json) = 'object'),
    CONSTRAINT chk_ops_container_releases_builder_identity
        CHECK (builder_target_identity ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_ops_container_releases_builder_hostname
        CHECK (
            char_length(builder_hostname) BETWEEN 1 AND 253
            AND builder_hostname ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,252}$'
        ),
    CONSTRAINT chk_ops_container_releases_timestamps
        CHECK (built_at <= created_at + INTERVAL '5 minutes'),
    CONSTRAINT ux_ops_container_releases_artifact UNIQUE (
        source_tree,
        platform,
        bundle_sha256
    )
);

CREATE INDEX idx_ops_container_releases_created
    ON ops_container_releases (created_at DESC);

CREATE TABLE ops_container_validation_receipts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    receipt_digest TEXT NOT NULL UNIQUE,
    release_id UUID NOT NULL REFERENCES ops_container_releases(id) ON DELETE RESTRICT,
    release_digest TEXT NOT NULL,
    source_tree TEXT NOT NULL,
    target TEXT NOT NULL,
    target_identity TEXT NOT NULL,
    platform TEXT NOT NULL,
    bundle_sha256 TEXT NOT NULL,
    compose_sha256 TEXT NOT NULL,
    api_image_digest TEXT NOT NULL,
    frontend_image_digest TEXT NOT NULL,
    checks JSONB NOT NULL,
    status TEXT NOT NULL,
    receipt_json JSONB NOT NULL,
    validated_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_ops_container_validation_receipt_digest
        CHECK (receipt_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_ops_container_validation_release_digest
        CHECK (release_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_ops_container_validation_source_tree
        CHECK (source_tree ~ '^[0-9a-f]{40}$'),
    CONSTRAINT chk_ops_container_validation_target
        CHECK (target ~ '^[a-z][a-z0-9_-]{0,31}$'),
    CONSTRAINT chk_ops_container_validation_target_identity
        CHECK (target_identity ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_ops_container_validation_platform
        CHECK (platform IN ('linux/amd64', 'linux/arm64')),
    CONSTRAINT chk_ops_container_validation_bundle
        CHECK (bundle_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_ops_container_validation_compose
        CHECK (compose_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_ops_container_validation_api_image
        CHECK (api_image_digest ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT chk_ops_container_validation_frontend_image
        CHECK (frontend_image_digest ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT chk_ops_container_validation_checks
        CHECK (jsonb_typeof(checks) = 'object'),
    CONSTRAINT chk_ops_container_validation_status
        CHECK (status IN ('passed', 'failed')),
    CONSTRAINT chk_ops_container_validation_receipt_json
        CHECK (jsonb_typeof(receipt_json) = 'object'),
    CONSTRAINT chk_ops_container_validation_timestamps
        CHECK (
            validated_at < expires_at
            AND validated_at <= created_at + INTERVAL '5 minutes'
        ),
    CONSTRAINT ux_ops_container_validation_receipt UNIQUE (
        release_id,
        target_identity,
        receipt_digest
    )
);

CREATE INDEX idx_ops_container_validation_release_created
    ON ops_container_validation_receipts (release_id, created_at DESC);
CREATE INDEX idx_ops_container_validation_pass_expiry
    ON ops_container_validation_receipts (release_id, expires_at DESC)
    WHERE status = 'passed';

CREATE TABLE ops_container_approval_evidence (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action TEXT NOT NULL,
    target_environment TEXT NOT NULL,
    target_identity TEXT NOT NULL,
    target_name TEXT NOT NULL,
    target_runtime_kind TEXT NOT NULL,
    native_baseline_identity TEXT,
    release_id UUID REFERENCES ops_container_releases(id) ON DELETE RESTRICT,
    release_digest TEXT,
    current_release_id UUID REFERENCES ops_container_releases(id) ON DELETE RESTRICT,
    current_release_digest TEXT,
    expected_runtime_generation BIGINT NOT NULL,
    expected_controller_state_sha256 TEXT NOT NULL,
    expected_previous_release_digest TEXT,
    validation_receipt_id UUID REFERENCES ops_container_validation_receipts(id) ON DELETE RESTRICT,
    validation_receipt_digest TEXT,
    typed_confirmation TEXT NOT NULL,
    reason TEXT NOT NULL,
    approved_by UUID REFERENCES users(id) ON DELETE SET NULL,
    approved_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_ops_container_approval_action
        CHECK (action IN ('promote', 'rollback', 'rollback_native')),
    CONSTRAINT chk_ops_container_approval_runtime_target
        CHECK (
            (target_runtime_kind = 'container' AND native_baseline_identity IS NULL)
            OR (
                target_runtime_kind = 'native'
                AND native_baseline_identity ~ '^[0-9a-f]{64}$'
            )
        ),
    CONSTRAINT chk_ops_container_approval_environment
        CHECK (target_environment IN ('staging', 'production')),
    CONSTRAINT chk_ops_container_approval_target_identity
        CHECK (target_identity ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_ops_container_approval_target_name
        CHECK (target_name ~ '^[a-z][a-z0-9_-]{0,31}$'),
    CONSTRAINT chk_ops_container_approval_release_digest
        CHECK (release_digest IS NULL OR release_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_ops_container_approval_current_digest
        CHECK (
            current_release_digest IS NULL
            OR current_release_digest ~ '^[0-9a-f]{64}$'
        ),
    CONSTRAINT chk_ops_container_approval_runtime_cas
        CHECK (
            expected_runtime_generation BETWEEN 0 AND 1000000000
            AND expected_controller_state_sha256 ~ '^[0-9a-f]{64}$'
            AND (
                expected_previous_release_digest IS NULL
                OR expected_previous_release_digest ~ '^[0-9a-f]{64}$'
            )
            AND (
                (
                    expected_runtime_generation = 0
                    AND current_release_id IS NULL
                    AND current_release_digest IS NULL
                    AND expected_previous_release_digest IS NULL
                )
                OR (
                    expected_runtime_generation >= 1
                    AND current_release_id IS NOT NULL
                    AND current_release_digest IS NOT NULL
                )
            )
        ),
    CONSTRAINT chk_ops_container_approval_receipt_digest
        CHECK (
            validation_receipt_digest IS NULL
            OR validation_receipt_digest ~ '^[0-9a-f]{64}$'
        ),
    CONSTRAINT chk_ops_container_approval_confirmation
        CHECK (char_length(typed_confirmation) BETWEEN 1 AND 320),
    CONSTRAINT chk_ops_container_approval_reason
        CHECK (char_length(btrim(reason)) BETWEEN 3 AND 500),
    CONSTRAINT chk_ops_container_approval_action_evidence
        CHECK (
            (
                action = 'promote'
                AND target_runtime_kind = 'container'
                AND release_id IS NOT NULL
                AND release_digest IS NOT NULL
                AND validation_receipt_id IS NOT NULL
                AND validation_receipt_digest IS NOT NULL
                AND current_release_id IS DISTINCT FROM release_id
                AND current_release_digest IS DISTINCT FROM release_digest
            )
            OR (
                action = 'rollback'
                AND target_runtime_kind = 'container'
                AND release_id IS NOT NULL
                AND release_digest IS NOT NULL
                AND validation_receipt_id IS NULL
                AND validation_receipt_digest IS NULL
                AND current_release_id IS NOT NULL
                AND current_release_digest IS NOT NULL
                AND current_release_id <> release_id
                AND current_release_digest <> release_digest
                AND expected_previous_release_digest = release_digest
            )
            OR (
                action = 'rollback_native'
                AND target_runtime_kind = 'native'
                AND release_id IS NULL
                AND release_digest IS NULL
                AND validation_receipt_id IS NULL
                AND validation_receipt_digest IS NULL
                AND current_release_id IS NOT NULL
                AND current_release_digest IS NOT NULL
                AND expected_runtime_generation >= 1
            )
        ),
    CONSTRAINT chk_ops_container_approval_timestamps
        CHECK (
            approved_at <= created_at + INTERVAL '5 minutes'
            AND expires_at > approved_at
            AND expires_at <= approved_at + INTERVAL '15 minutes'
        )
);

CREATE INDEX idx_ops_container_approval_target_created
    ON ops_container_approval_evidence (target_identity, created_at DESC);
CREATE INDEX idx_ops_container_approval_release
    ON ops_container_approval_evidence (release_id, created_at DESC);

CREATE FUNCTION validate_ops_container_release_manifest()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    manifest_keys TEXT[];
    image_keys TEXT[];
    api_keys TEXT[];
    frontend_keys TEXT[];
BEGIN
    SELECT array_agg(key ORDER BY key) INTO manifest_keys
      FROM jsonb_object_keys(NEW.manifest_json) AS item(key);
    SELECT array_agg(key ORDER BY key) INTO image_keys
      FROM jsonb_object_keys(NEW.manifest_json->'images') AS item(key);
    SELECT array_agg(key ORDER BY key) INTO api_keys
      FROM jsonb_object_keys(NEW.manifest_json->'images'->'api') AS item(key);
    SELECT array_agg(key ORDER BY key) INTO frontend_keys
      FROM jsonb_object_keys(NEW.manifest_json->'images'->'frontend') AS item(key);

    IF manifest_keys IS DISTINCT FROM ARRAY[
            'base_commit', 'build_policy_sha256', 'bundle_sha256',
            'compose_sha256', 'created_at', 'images',
            'migration_ledger_sha256', 'platform', 'release_digest',
            'schema_version', 'snapshot_commit', 'source_tree'
       ]::TEXT[]
       OR image_keys IS DISTINCT FROM ARRAY['api', 'frontend']::TEXT[]
       OR api_keys IS DISTINCT FROM ARRAY['image_id', 'tag']::TEXT[]
       OR frontend_keys IS DISTINCT FROM ARRAY['image_id', 'tag']::TEXT[]
       OR NEW.manifest_json->'schema_version' IS DISTINCT FROM '1'::jsonb
       OR NEW.manifest_json->>'release_digest' IS DISTINCT FROM NEW.release_digest
       OR NEW.manifest_json->>'base_commit' IS DISTINCT FROM NEW.base_commit
       OR NEW.manifest_json->>'source_tree' IS DISTINCT FROM NEW.source_tree
       OR NEW.manifest_json->>'snapshot_commit' IS DISTINCT FROM NEW.snapshot_commit
       OR NEW.manifest_json->>'platform' IS DISTINCT FROM NEW.platform
       OR NEW.manifest_json->>'bundle_sha256' IS DISTINCT FROM NEW.bundle_sha256
       OR NEW.manifest_json->>'compose_sha256' IS DISTINCT FROM NEW.compose_sha256
       OR NEW.manifest_json->>'build_policy_sha256' IS DISTINCT FROM NEW.build_policy_sha256
       OR NEW.manifest_json->>'migration_ledger_sha256' IS DISTINCT FROM NEW.migration_ledger_sha256
       OR NEW.manifest_json->'images'->'api'->>'image_id' IS DISTINCT FROM NEW.api_image_digest
       OR NEW.manifest_json->'images'->'frontend'->>'image_id' IS DISTINCT FROM NEW.frontend_image_digest
       OR NEW.manifest_json->'images'->'api'->>'tag'
            IS DISTINCT FROM 'mooncen/api:release-' || NEW.source_tree
       OR NEW.manifest_json->'images'->'frontend'->>'tag'
            IS DISTINCT FROM 'mooncen/frontend:release-' || NEW.source_tree
       OR (NEW.manifest_json->>'created_at')::timestamptz IS DISTINCT FROM NEW.built_at THEN
        RAISE EXCEPTION 'release scalar columns do not exactly match canonical manifest_json'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER trg_ops_container_release_manifest
BEFORE INSERT ON ops_container_releases
FOR EACH ROW EXECUTE FUNCTION validate_ops_container_release_manifest();

CREATE FUNCTION reject_ops_container_evidence_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION '% evidence is immutable after insert', TG_TABLE_NAME
        USING ERRCODE = '55000';
END
$$;

CREATE TRIGGER trg_ops_container_releases_immutable
BEFORE UPDATE OR DELETE ON ops_container_releases
FOR EACH ROW EXECUTE FUNCTION reject_ops_container_evidence_mutation();
CREATE TRIGGER trg_ops_container_validation_receipts_immutable
BEFORE UPDATE OR DELETE ON ops_container_validation_receipts
FOR EACH ROW EXECUTE FUNCTION reject_ops_container_evidence_mutation();
CREATE TRIGGER trg_ops_container_approval_evidence_immutable
BEFORE UPDATE OR DELETE ON ops_container_approval_evidence
FOR EACH ROW EXECUTE FUNCTION reject_ops_container_evidence_mutation();

CREATE FUNCTION validate_ops_container_receipt_binding()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    expected ops_container_releases%ROWTYPE;
    receipt_keys TEXT[];
    image_keys TEXT[];
    check_keys TEXT[];
    all_checks_passed BOOLEAN;
BEGIN
    SELECT array_agg(key ORDER BY key) INTO receipt_keys
      FROM jsonb_object_keys(NEW.receipt_json) AS item(key);
    SELECT array_agg(key ORDER BY key) INTO image_keys
      FROM jsonb_object_keys(NEW.receipt_json->'image_ids') AS item(key);
    SELECT array_agg(key ORDER BY key) INTO check_keys
      FROM jsonb_object_keys(NEW.receipt_json->'checks') AS item(key);
    all_checks_passed :=
        jsonb_typeof(NEW.receipt_json->'checks'->'migration_ledger') = 'boolean'
        AND jsonb_typeof(NEW.receipt_json->'checks'->'api_health') = 'boolean'
        AND jsonb_typeof(NEW.receipt_json->'checks'->'frontend_health') = 'boolean'
        AND jsonb_typeof(NEW.receipt_json->'checks'->'protected_routes') = 'boolean'
        AND jsonb_typeof(NEW.receipt_json->'checks'->'database_least_privilege') = 'boolean'
        AND jsonb_typeof(NEW.receipt_json->'checks'->'runtime_hardening') = 'boolean'
        AND (NEW.receipt_json->'checks'->>'migration_ledger')::boolean
        AND (NEW.receipt_json->'checks'->>'api_health')::boolean
        AND (NEW.receipt_json->'checks'->>'frontend_health')::boolean
        AND (NEW.receipt_json->'checks'->>'protected_routes')::boolean
        AND (NEW.receipt_json->'checks'->>'database_least_privilege')::boolean
        AND (NEW.receipt_json->'checks'->>'runtime_hardening')::boolean;

    IF receipt_keys IS DISTINCT FROM ARRAY[
            'bundle_sha256', 'checks', 'compose_sha256', 'expires_at',
            'image_ids', 'platform', 'receipt_digest', 'release_digest',
            'schema_version', 'source_tree', 'status', 'target',
            'target_identity', 'validated_at'
       ]::TEXT[]
       OR image_keys IS DISTINCT FROM ARRAY['api', 'frontend']::TEXT[]
       OR check_keys IS DISTINCT FROM ARRAY[
            'api_health', 'database_least_privilege', 'frontend_health',
            'migration_ledger', 'protected_routes', 'runtime_hardening'
       ]::TEXT[]
       OR NEW.receipt_json->'schema_version' IS DISTINCT FROM '1'::jsonb
       OR NEW.receipt_json->>'receipt_digest' IS DISTINCT FROM NEW.receipt_digest
       OR NEW.receipt_json->>'release_digest' IS DISTINCT FROM NEW.release_digest
       OR NEW.receipt_json->>'source_tree' IS DISTINCT FROM NEW.source_tree
       OR NEW.receipt_json->>'target' IS DISTINCT FROM NEW.target
       OR NEW.receipt_json->>'target_identity' IS DISTINCT FROM NEW.target_identity
       OR NEW.receipt_json->>'platform' IS DISTINCT FROM NEW.platform
       OR NEW.receipt_json->>'bundle_sha256' IS DISTINCT FROM NEW.bundle_sha256
       OR NEW.receipt_json->>'compose_sha256' IS DISTINCT FROM NEW.compose_sha256
       OR NEW.receipt_json->'image_ids'->>'api' IS DISTINCT FROM NEW.api_image_digest
       OR NEW.receipt_json->'image_ids'->>'frontend' IS DISTINCT FROM NEW.frontend_image_digest
       OR NEW.receipt_json->'checks' IS DISTINCT FROM NEW.checks
       OR NEW.receipt_json->>'status' IS DISTINCT FROM NEW.status
       OR (NEW.receipt_json->>'validated_at')::timestamptz IS DISTINCT FROM NEW.validated_at
       OR (NEW.receipt_json->>'expires_at')::timestamptz IS DISTINCT FROM NEW.expires_at
       OR (NEW.status = 'passed') IS DISTINCT FROM all_checks_passed THEN
        RAISE EXCEPTION 'receipt scalar columns and checks do not match canonical receipt_json'
            USING ERRCODE = '23514';
    END IF;

    SELECT * INTO expected FROM ops_container_releases WHERE id = NEW.release_id;
    IF expected.id IS NULL
       OR NEW.release_digest <> expected.release_digest
       OR NEW.source_tree <> expected.source_tree
       OR NEW.platform <> expected.platform
       OR NEW.bundle_sha256 <> expected.bundle_sha256
       OR NEW.compose_sha256 <> expected.compose_sha256
       OR NEW.api_image_digest <> expected.api_image_digest
       OR NEW.frontend_image_digest <> expected.frontend_image_digest THEN
        RAISE EXCEPTION 'validation receipt does not match the immutable release'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER trg_ops_container_validation_receipt_binding
BEFORE INSERT ON ops_container_validation_receipts
FOR EACH ROW EXECUTE FUNCTION validate_ops_container_receipt_binding();

CREATE FUNCTION validate_ops_container_approval_binding()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    release_value TEXT;
    current_release_value TEXT;
    receipt_release_id UUID;
    receipt_release_digest TEXT;
    receipt_digest_value TEXT;
    receipt_target TEXT;
    receipt_status TEXT;
    receipt_expires_at TIMESTAMPTZ;
    expected_confirmation TEXT;
BEGIN
    SELECT release_digest INTO release_value
      FROM ops_container_releases WHERE id = NEW.release_id;
    IF release_value IS DISTINCT FROM NEW.release_digest THEN
        RAISE EXCEPTION 'approval release digest does not match release identity'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.current_release_id IS NOT NULL THEN
        SELECT release_digest INTO current_release_value
          FROM ops_container_releases WHERE id = NEW.current_release_id;
        IF current_release_value IS DISTINCT FROM NEW.current_release_digest THEN
            RAISE EXCEPTION 'approval current release digest is invalid'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    IF NEW.action = 'promote' THEN
        SELECT release_id, release_digest, receipt_digest, target, status, expires_at
          INTO receipt_release_id, receipt_release_digest, receipt_digest_value,
               receipt_target, receipt_status, receipt_expires_at
          FROM ops_container_validation_receipts
         WHERE id = NEW.validation_receipt_id;
        IF receipt_release_id IS DISTINCT FROM NEW.release_id
           OR receipt_release_digest IS DISTINCT FROM NEW.release_digest
           OR receipt_digest_value IS DISTINCT FROM NEW.validation_receipt_digest
           OR receipt_target IS DISTINCT FROM 'an2p-dev'
           OR receipt_status IS DISTINCT FROM 'passed'
           OR receipt_expires_at <= NEW.approved_at THEN
            RAISE EXCEPTION 'promotion requires an exact, unexpired an2p-dev passed receipt'
                USING ERRCODE = '23514';
        END IF;
        expected_confirmation :=
            'PROMOTE ' || NEW.target_identity || ' ' || NEW.release_digest ||
            ' ' || NEW.validation_receipt_digest || ' ' ||
            NEW.expected_runtime_generation::TEXT || ' ' ||
            NEW.expected_controller_state_sha256;
    ELSIF NEW.action = 'rollback' THEN
        expected_confirmation :=
            'ROLLBACK ' || NEW.target_identity || ' ' ||
            NEW.current_release_digest || ' ' || NEW.release_digest || ' ' ||
            NEW.expected_runtime_generation::TEXT || ' ' ||
            NEW.expected_controller_state_sha256;
    ELSE
        expected_confirmation :=
            'ROLLBACK_NATIVE ' || NEW.target_identity || ' ' ||
            NEW.current_release_digest || ' ' || NEW.native_baseline_identity || ' ' ||
            NEW.expected_runtime_generation::TEXT || ' ' ||
            NEW.expected_controller_state_sha256;
    END IF;

    IF NEW.typed_confirmation <> expected_confirmation THEN
        RAISE EXCEPTION 'typed confirmation does not match target and evidence identity'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER trg_ops_container_approval_binding
BEFORE INSERT ON ops_container_approval_evidence
FOR EACH ROW EXECUTE FUNCTION validate_ops_container_approval_binding();

ALTER TABLE ops_deployments
    ADD COLUMN deployment_mode TEXT NOT NULL DEFAULT 'native',
    ADD COLUMN deployment_action TEXT NOT NULL DEFAULT 'deploy',
    ADD COLUMN target_environment TEXT,
    ADD COLUMN target_name TEXT,
    ADD COLUMN target_identity TEXT,
    ADD COLUMN target_runtime_kind TEXT,
    ADD COLUMN native_baseline_identity TEXT,
    ADD COLUMN expected_runtime_generation BIGINT,
    ADD COLUMN expected_controller_state_sha256 TEXT,
    ADD COLUMN expected_previous_release_digest TEXT,
    ADD COLUMN container_release_id UUID REFERENCES ops_container_releases(id) ON DELETE RESTRICT,
    ADD COLUMN container_release_digest TEXT,
    ADD COLUMN previous_container_release_id UUID REFERENCES ops_container_releases(id) ON DELETE RESTRICT,
    ADD COLUMN previous_container_release_digest TEXT,
    ADD COLUMN validation_receipt_id UUID REFERENCES ops_container_validation_receipts(id) ON DELETE RESTRICT,
    ADD COLUMN validation_receipt_digest TEXT,
    ADD COLUMN approval_evidence_id UUID REFERENCES ops_container_approval_evidence(id) ON DELETE RESTRICT,
    ADD COLUMN api_image_digest TEXT,
    ADD COLUMN frontend_image_digest TEXT,
    ADD COLUMN bundle_sha256 TEXT,
    ADD COLUMN runtime_generation BIGINT,
    ADD COLUMN activated_release_digest TEXT,
    ADD COLUMN runtime_previous_release_digest TEXT,
    ADD COLUMN controller_state_sha256 TEXT,
    ADD COLUMN runtime_target_kind TEXT,
    ADD COLUMN runtime_native_baseline_identity TEXT,
    ADD CONSTRAINT chk_ops_deployments_mode
        CHECK (deployment_mode IN ('native', 'container')),
    ADD CONSTRAINT chk_ops_deployments_action
        CHECK (deployment_action IN ('deploy', 'promote', 'rollback', 'rollback_native')),
    ADD CONSTRAINT chk_ops_deployments_container_shape
        CHECK (
            (
                deployment_mode = 'native'
                AND deployment_action = 'deploy'
                AND target_environment IS NULL
                AND target_name IS NULL
                AND target_identity IS NULL
                AND target_runtime_kind IS NULL
                AND native_baseline_identity IS NULL
                AND expected_runtime_generation IS NULL
                AND expected_controller_state_sha256 IS NULL
                AND expected_previous_release_digest IS NULL
                AND container_release_id IS NULL
                AND container_release_digest IS NULL
                AND previous_container_release_id IS NULL
                AND previous_container_release_digest IS NULL
                AND validation_receipt_id IS NULL
                AND validation_receipt_digest IS NULL
                AND approval_evidence_id IS NULL
                AND api_image_digest IS NULL
                AND frontend_image_digest IS NULL
                AND bundle_sha256 IS NULL
                AND runtime_generation IS NULL
                AND activated_release_digest IS NULL
                AND runtime_previous_release_digest IS NULL
                AND controller_state_sha256 IS NULL
                AND runtime_target_kind IS NULL
                AND runtime_native_baseline_identity IS NULL
            )
            OR (
                deployment_mode = 'container'
                AND deployment_action IN ('promote', 'rollback', 'rollback_native')
                AND target_environment IN ('staging', 'production')
                AND target_name ~ '^[a-z][a-z0-9_-]{0,31}$'
                AND target_identity ~ '^[0-9a-f]{64}$'
                AND (
                    (
                        target_runtime_kind = 'container'
                        AND native_baseline_identity IS NULL
                    )
                    OR (
                        target_runtime_kind = 'native'
                        AND native_baseline_identity ~ '^[0-9a-f]{64}$'
                    )
                )
                AND expected_runtime_generation BETWEEN 0 AND 1000000000
                AND expected_controller_state_sha256 ~ '^[0-9a-f]{64}$'
                AND (
                    expected_previous_release_digest IS NULL
                    OR expected_previous_release_digest ~ '^[0-9a-f]{64}$'
                )
                AND (
                    (
                        expected_runtime_generation = 0
                        AND previous_container_release_id IS NULL
                        AND previous_container_release_digest IS NULL
                        AND expected_previous_release_digest IS NULL
                    )
                    OR (
                        expected_runtime_generation >= 1
                        AND previous_container_release_id IS NOT NULL
                        AND previous_container_release_digest IS NOT NULL
                    )
                )
                AND approval_evidence_id IS NOT NULL
                AND (
                    (
                        target_runtime_kind = 'container'
                        AND container_release_id IS NOT NULL
                        AND container_release_digest ~ '^[0-9a-f]{64}$'
                        AND container_release_id IS DISTINCT FROM previous_container_release_id
                        AND container_release_digest IS DISTINCT FROM previous_container_release_digest
                        AND api_image_digest ~ '^sha256:[0-9a-f]{64}$'
                        AND frontend_image_digest ~ '^sha256:[0-9a-f]{64}$'
                        AND bundle_sha256 ~ '^[0-9a-f]{64}$'
                    )
                    OR (
                        target_runtime_kind = 'native'
                        AND container_release_id IS NULL
                        AND container_release_digest IS NULL
                        AND previous_container_release_id IS NOT NULL
                        AND previous_container_release_digest ~ '^[0-9a-f]{64}$'
                        AND api_image_digest IS NULL
                        AND frontend_image_digest IS NULL
                        AND bundle_sha256 IS NULL
                    )
                )
                AND (
                    (
                        deployment_status = 'success'
                        AND (
                            (
                                target_runtime_kind = 'container'
                                AND runtime_target_kind = 'container'
                                AND runtime_native_baseline_identity ~ '^[0-9a-f]{64}$'
                                AND runtime_generation BETWEEN 1 AND 1000000000
                                AND runtime_generation = expected_runtime_generation + 1
                                AND activated_release_digest = container_release_digest
                                AND runtime_previous_release_digest
                                    IS NOT DISTINCT FROM previous_container_release_digest
                                AND controller_state_sha256 ~ '^[0-9a-f]{64}$'
                            )
                            OR (
                                target_runtime_kind = 'native'
                                AND runtime_target_kind = 'native'
                                AND runtime_native_baseline_identity = native_baseline_identity
                                AND runtime_generation = 0
                                AND activated_release_digest IS NULL
                                AND runtime_previous_release_digest IS NULL
                                AND controller_state_sha256 =
                                    '74234e98afe7498fb5daf1f36ac2d78acc339464f950703b8c019892f982b90b'
                            )
                        )
                    )
                    OR (
                        deployment_status <> 'success'
                        AND runtime_generation IS NULL
                        AND activated_release_digest IS NULL
                        AND runtime_previous_release_digest IS NULL
                        AND controller_state_sha256 IS NULL
                        AND runtime_target_kind IS NULL
                        AND runtime_native_baseline_identity IS NULL
                    )
                )
                AND (
                    (
                        deployment_action = 'promote'
                        AND validation_receipt_id IS NOT NULL
                        AND validation_receipt_digest ~ '^[0-9a-f]{64}$'
                    )
                    OR (
                        deployment_action = 'rollback'
                        AND validation_receipt_id IS NULL
                        AND validation_receipt_digest IS NULL
                        AND previous_container_release_id IS NOT NULL
                        AND previous_container_release_digest ~ '^[0-9a-f]{64}$'
                        AND expected_previous_release_digest = container_release_digest
                    )
                    OR (
                        deployment_action = 'rollback_native'
                        AND target_runtime_kind = 'native'
                        AND validation_receipt_id IS NULL
                        AND validation_receipt_digest IS NULL
                        AND previous_container_release_id IS NOT NULL
                        AND previous_container_release_digest ~ '^[0-9a-f]{64}$'
                    )
                )
            )
        );

CREATE INDEX idx_ops_deployments_container_target_created
    ON ops_deployments (target_identity, created_at DESC)
    WHERE deployment_mode = 'container';
CREATE INDEX idx_ops_deployments_container_release
    ON ops_deployments (container_release_id, created_at DESC)
    WHERE deployment_mode = 'container';
ALTER TABLE ops_deployments
    ADD CONSTRAINT ux_ops_deployments_container_approval_consumption
    UNIQUE (approval_evidence_id);

CREATE FUNCTION validate_ops_container_deployment_binding()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    release_value ops_container_releases%ROWTYPE;
    previous_release_value TEXT;
    receipt_value ops_container_validation_receipts%ROWTYPE;
    approval_value ops_container_approval_evidence%ROWTYPE;
    job_parameter_keys TEXT[];
    job_action TEXT;
    job_approval_id TEXT;
    job_current_release_digest TEXT;
    job_deployment_mode TEXT;
    job_native_baseline_identity TEXT;
    job_expected_controller_state_sha256 TEXT;
    job_expected_previous_release_digest TEXT;
    job_expected_runtime_generation BIGINT;
    job_release_digest TEXT;
    job_service_type TEXT;
    job_source_tree TEXT;
    job_target_name TEXT;
    job_target_environment TEXT;
    job_target_identity TEXT;
    job_target_runtime_kind TEXT;
    job_validation_receipt_digest TEXT;
BEGIN
    IF TG_OP = 'UPDATE' AND (
        NEW.deployment_mode IS DISTINCT FROM OLD.deployment_mode
        OR NEW.deployment_action IS DISTINCT FROM OLD.deployment_action
        OR NEW.target_environment IS DISTINCT FROM OLD.target_environment
        OR NEW.target_name IS DISTINCT FROM OLD.target_name
        OR NEW.target_identity IS DISTINCT FROM OLD.target_identity
        OR NEW.target_runtime_kind IS DISTINCT FROM OLD.target_runtime_kind
        OR NEW.native_baseline_identity IS DISTINCT FROM OLD.native_baseline_identity
        OR NEW.expected_runtime_generation IS DISTINCT FROM OLD.expected_runtime_generation
        OR NEW.expected_controller_state_sha256
            IS DISTINCT FROM OLD.expected_controller_state_sha256
        OR NEW.expected_previous_release_digest
            IS DISTINCT FROM OLD.expected_previous_release_digest
        OR NEW.container_release_id IS DISTINCT FROM OLD.container_release_id
        OR NEW.container_release_digest IS DISTINCT FROM OLD.container_release_digest
        OR NEW.previous_container_release_id IS DISTINCT FROM OLD.previous_container_release_id
        OR NEW.previous_container_release_digest IS DISTINCT FROM OLD.previous_container_release_digest
        OR NEW.validation_receipt_id IS DISTINCT FROM OLD.validation_receipt_id
        OR NEW.validation_receipt_digest IS DISTINCT FROM OLD.validation_receipt_digest
        OR NEW.approval_evidence_id IS DISTINCT FROM OLD.approval_evidence_id
        OR NEW.api_image_digest IS DISTINCT FROM OLD.api_image_digest
        OR NEW.frontend_image_digest IS DISTINCT FROM OLD.frontend_image_digest
        OR NEW.bundle_sha256 IS DISTINCT FROM OLD.bundle_sha256
        OR (
            OLD.runtime_generation IS NOT NULL
            AND NEW.runtime_generation IS DISTINCT FROM OLD.runtime_generation
        )
        OR (
            OLD.activated_release_digest IS NOT NULL
            AND NEW.activated_release_digest
                IS DISTINCT FROM OLD.activated_release_digest
        )
        OR (
            OLD.runtime_previous_release_digest IS NOT NULL
            AND NEW.runtime_previous_release_digest
                IS DISTINCT FROM OLD.runtime_previous_release_digest
        )
        OR (
            OLD.controller_state_sha256 IS NOT NULL
            AND NEW.controller_state_sha256
                IS DISTINCT FROM OLD.controller_state_sha256
        )
        OR (
            OLD.runtime_target_kind IS NOT NULL
            AND NEW.runtime_target_kind IS DISTINCT FROM OLD.runtime_target_kind
        )
        OR (
            OLD.runtime_native_baseline_identity IS NOT NULL
            AND NEW.runtime_native_baseline_identity
                IS DISTINCT FROM OLD.runtime_native_baseline_identity
        )
    ) THEN
        RAISE EXCEPTION 'deployment artifact and approval identity is immutable'
            USING ERRCODE = '55000';
    END IF;

    IF NEW.deployment_mode = 'native' THEN
        RETURN NEW;
    END IF;

    SELECT ARRAY(
               SELECT parameter.key
               FROM jsonb_object_keys(job.parameters) AS parameter(key)
               ORDER BY parameter.key
           ),
           parameters->>'action',
           parameters->>'approval_evidence_id',
           parameters->>'current_release_digest',
           parameters->>'deployment_mode',
           parameters->>'native_baseline_identity',
           parameters->>'expected_controller_state_sha256',
           parameters->>'expected_previous_release_digest',
           (parameters->>'expected_runtime_generation')::BIGINT,
           parameters->>'release_digest',
           parameters->>'service_type',
           parameters->>'source_tree',
           parameters->>'target',
           parameters->>'target_environment',
           parameters->>'target_identity',
           parameters->>'target_runtime_kind',
           parameters->>'validation_receipt_digest'
      INTO job_parameter_keys, job_action, job_approval_id,
           job_current_release_digest, job_deployment_mode,
           job_native_baseline_identity,
           job_expected_controller_state_sha256,
           job_expected_previous_release_digest,
           job_expected_runtime_generation,
           job_release_digest, job_service_type, job_source_tree,
           job_target_name, job_target_environment, job_target_identity,
           job_target_runtime_kind,
           job_validation_receipt_digest
      FROM ops_jobs AS job
     WHERE job.id = NEW.job_id;
    IF job_parameter_keys IS DISTINCT FROM ARRAY[
            'action', 'approval_evidence_id', 'current_release_digest',
            'deployment_mode', 'expected_controller_state_sha256',
            'expected_previous_release_digest', 'expected_runtime_generation',
            'native_baseline_identity', 'release_digest',
            'required_agent_hostname', 'service_type', 'source_tree',
            'target', 'target_environment', 'target_identity', 'target_runtime_kind',
            'validation_receipt_digest'
       ]::TEXT[]
       OR job_action IS DISTINCT FROM NEW.deployment_action
       OR job_approval_id IS DISTINCT FROM NEW.approval_evidence_id::TEXT
       OR job_current_release_digest
            IS DISTINCT FROM NEW.previous_container_release_digest
       OR job_deployment_mode IS DISTINCT FROM 'container'
       OR job_native_baseline_identity IS DISTINCT FROM NEW.native_baseline_identity
       OR job_expected_controller_state_sha256
            IS DISTINCT FROM NEW.expected_controller_state_sha256
       OR job_expected_previous_release_digest
            IS DISTINCT FROM NEW.expected_previous_release_digest
       OR job_expected_runtime_generation
            IS DISTINCT FROM NEW.expected_runtime_generation
       OR job_release_digest IS DISTINCT FROM NEW.container_release_digest
       OR job_service_type IS DISTINCT FROM 'full'
       OR job_target_name IS DISTINCT FROM NEW.target_name
       OR job_target_environment IS DISTINCT FROM NEW.target_environment
       OR job_target_identity IS DISTINCT FROM NEW.target_identity
       OR job_target_runtime_kind IS DISTINCT FROM NEW.target_runtime_kind
       OR job_validation_receipt_digest
            IS DISTINCT FROM NEW.validation_receipt_digest THEN
        RAISE EXCEPTION 'deployment target identity differs from its queue job'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.target_runtime_kind = 'container' THEN
        SELECT * INTO release_value
          FROM ops_container_releases WHERE id = NEW.container_release_id;
        IF release_value.id IS NULL
           OR release_value.release_digest <> NEW.container_release_digest
           OR release_value.source_tree <> job_source_tree
           OR release_value.api_image_digest <> NEW.api_image_digest
           OR release_value.frontend_image_digest <> NEW.frontend_image_digest
           OR release_value.bundle_sha256 <> NEW.bundle_sha256 THEN
            RAISE EXCEPTION 'deployment artifacts do not match immutable release evidence'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    IF NEW.previous_container_release_id IS NOT NULL THEN
        SELECT release_digest INTO previous_release_value
          FROM ops_container_releases WHERE id = NEW.previous_container_release_id;
        IF previous_release_value IS DISTINCT FROM NEW.previous_container_release_digest THEN
            RAISE EXCEPTION 'previous release pointer is invalid'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    IF NEW.validation_receipt_id IS NOT NULL THEN
        SELECT * INTO receipt_value
          FROM ops_container_validation_receipts WHERE id = NEW.validation_receipt_id;
        IF receipt_value.id IS NULL
           OR receipt_value.release_id <> NEW.container_release_id
           OR receipt_value.release_digest <> NEW.container_release_digest
           OR receipt_value.receipt_digest <> NEW.validation_receipt_digest
           OR receipt_value.target <> 'an2p-dev'
           OR receipt_value.status <> 'passed'
           OR (TG_OP = 'INSERT' AND receipt_value.expires_at <= CURRENT_TIMESTAMP) THEN
            RAISE EXCEPTION 'deployment receipt is not an exact current an2p-dev pass'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    SELECT * INTO approval_value
      FROM ops_container_approval_evidence WHERE id = NEW.approval_evidence_id;
    IF approval_value.id IS NULL
       OR approval_value.action <> NEW.deployment_action
       OR approval_value.target_environment <> NEW.target_environment
       OR approval_value.target_name <> NEW.target_name
       OR approval_value.target_identity <> NEW.target_identity
       OR approval_value.target_runtime_kind <> NEW.target_runtime_kind
       OR approval_value.native_baseline_identity
            IS DISTINCT FROM NEW.native_baseline_identity
       OR approval_value.expected_runtime_generation
            <> NEW.expected_runtime_generation
       OR approval_value.expected_controller_state_sha256
            <> NEW.expected_controller_state_sha256
       OR approval_value.expected_previous_release_digest
            IS DISTINCT FROM NEW.expected_previous_release_digest
       OR approval_value.release_id IS DISTINCT FROM NEW.container_release_id
       OR approval_value.release_digest IS DISTINCT FROM NEW.container_release_digest
       OR approval_value.current_release_id IS DISTINCT FROM NEW.previous_container_release_id
       OR approval_value.current_release_digest IS DISTINCT FROM NEW.previous_container_release_digest
       OR approval_value.validation_receipt_id IS DISTINCT FROM NEW.validation_receipt_id
       OR approval_value.validation_receipt_digest IS DISTINCT FROM NEW.validation_receipt_digest
       OR (TG_OP = 'INSERT' AND approval_value.expires_at <= CURRENT_TIMESTAMP) THEN
        RAISE EXCEPTION 'deployment approval does not match target and release evidence'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.runtime_generation IS NOT NULL AND (
        (
            NEW.target_runtime_kind = 'container'
            AND (
                NEW.runtime_target_kind IS DISTINCT FROM 'container'
                OR NEW.runtime_native_baseline_identity IS NULL
                OR NEW.activated_release_digest IS DISTINCT FROM NEW.container_release_digest
                OR NEW.runtime_previous_release_digest
                    IS DISTINCT FROM NEW.previous_container_release_digest
            )
        )
        OR (
            NEW.target_runtime_kind = 'native'
            AND (
                NEW.runtime_target_kind IS DISTINCT FROM 'native'
                OR NEW.runtime_native_baseline_identity
                    IS DISTINCT FROM NEW.native_baseline_identity
                OR NEW.activated_release_digest IS NOT NULL
                OR NEW.runtime_previous_release_digest IS NOT NULL
            )
        )
    ) THEN
        RAISE EXCEPTION 'controller result does not match the approved release transition'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER trg_ops_container_deployment_binding
BEFORE INSERT OR UPDATE ON ops_deployments
FOR EACH ROW EXECUTE FUNCTION validate_ops_container_deployment_binding();

REVOKE ALL PRIVILEGES ON TABLE
    ops_container_releases,
    ops_container_validation_receipts,
    ops_container_approval_evidence
FROM PUBLIC;
REVOKE ALL ON FUNCTION reject_ops_container_evidence_mutation() FROM PUBLIC;
REVOKE ALL ON FUNCTION validate_ops_container_release_manifest() FROM PUBLIC;
REVOKE ALL ON FUNCTION validate_ops_container_receipt_binding() FROM PUBLIC;
REVOKE ALL ON FUNCTION validate_ops_container_approval_binding() FROM PUBLIC;
REVOKE ALL ON FUNCTION validate_ops_container_deployment_binding() FROM PUBLIC;
REVOKE ALL PRIVILEGES ON SEQUENCE
    ops_container_deployment_lease_epoch_seq
FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_api') THEN
        GRANT SELECT ON TABLE
            ops_container_releases,
            ops_container_validation_receipts,
            ops_container_approval_evidence
        TO mooncen_api;
        GRANT INSERT ON TABLE ops_container_approval_evidence TO mooncen_api;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_deployment_worker') THEN
        REVOKE ALL PRIVILEGES ON TABLE
            ops_container_releases,
            ops_container_validation_receipts,
            ops_container_approval_evidence
        FROM mooncen_deployment_worker;
        GRANT SELECT, INSERT ON TABLE
            ops_container_releases,
            ops_container_validation_receipts
        TO mooncen_deployment_worker;
        GRANT SELECT ON TABLE ops_container_approval_evidence
            TO mooncen_deployment_worker;
        GRANT SELECT, INSERT, UPDATE ON TABLE ops_agents
            TO mooncen_deployment_worker;
        GRANT SELECT ON TABLE ops_jobs, ops_deployments, ops_job_logs
            TO mooncen_deployment_worker;
        GRANT UPDATE (
            status, agent_id, assigned_at, started_at, heartbeat_at,
            progress, result, error_code, error_message, cancel_requested_at,
            finished_at, updated_at, lease_token, lease_epoch, leased_until
        ) ON TABLE ops_jobs TO mooncen_deployment_worker;
        GRANT UPDATE (
            target_version, target_commit, deployment_status, started_at,
            finished_at, runtime_generation, activated_release_digest,
            runtime_previous_release_digest, controller_state_sha256,
            runtime_target_kind, runtime_native_baseline_identity
        ) ON TABLE ops_deployments TO mooncen_deployment_worker;
        GRANT INSERT ON TABLE ops_job_logs TO mooncen_deployment_worker;
        GRANT USAGE, SELECT ON SEQUENCE ops_job_logs_id_seq
            TO mooncen_deployment_worker;
        GRANT USAGE, SELECT ON SEQUENCE
            ops_container_deployment_lease_epoch_seq
        TO mooncen_deployment_worker;
    END IF;
END
$$;
