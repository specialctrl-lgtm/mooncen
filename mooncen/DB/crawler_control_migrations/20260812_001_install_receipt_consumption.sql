-- FUTURE INTENDED PATH / DIRECT EXECUTION FORBIDDEN.
--
-- One-time crawler-control install receipt ledger. The current --apply path
-- has an unconditional NOT READY gate and does not execute this migration.
--
-- It may become executable only after ensure_crawler_control_schema owns one
-- transaction containing the canonical advisory lock, this DDL, both role
-- convergence passes, every marker/control/staging write, final validation,
-- and the receipt INSERT. The ledger row and all other writes must then commit
-- or roll back together. Do not execute this file directly.

CREATE TABLE public.ops_crawler_control_install_receipt_consumptions (
    receipt_sha256 TEXT PRIMARY KEY,
    nonce TEXT NOT NULL,
    deploy_commit TEXT NOT NULL,
    archive_sha256 TEXT NOT NULL,
    tree_sha256 TEXT NOT NULL,
    release_id TEXT NOT NULL,
    receipt_format TEXT NOT NULL,
    node_role TEXT NOT NULL,
    target_host TEXT NOT NULL,
    database_host TEXT NOT NULL,
    database_port INTEGER NOT NULL,
    database_name TEXT NOT NULL,
    database_sslmode TEXT NOT NULL,
    release_signer_principal TEXT NOT NULL,
    receipt_signer_principal TEXT NOT NULL,
    receipt_signature_sha256 TEXT NOT NULL,
    backup_attestation_sha256 TEXT NOT NULL,
    backup_attestation_key_id TEXT NOT NULL,
    canonical_receipt BYTEA NOT NULL,
    receipt_issued_at TIMESTAMPTZ NOT NULL,
    receipt_valid_until TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ NOT NULL,
    consumed_by NAME NOT NULL,
    CONSTRAINT ux_crawler_install_receipt_nonce UNIQUE (nonce),
    CONSTRAINT ux_crawler_install_receipt_release_id UNIQUE (release_id),
    CONSTRAINT chk_crawler_install_receipt_sha256
        CHECK (receipt_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_crawler_install_receipt_nonce
        CHECK (nonce ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_crawler_install_receipt_commit
        CHECK (deploy_commit ~ '^([0-9a-f]{40}|[0-9a-f]{64})$'),
    CONSTRAINT chk_crawler_install_receipt_archive_sha256
        CHECK (archive_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_crawler_install_receipt_tree_sha256
        CHECK (tree_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_crawler_install_receipt_release_id
        CHECK (release_id ~ '^[0-9a-f]{32}$'),
    CONSTRAINT chk_crawler_install_receipt_backup_sha256
        CHECK (backup_attestation_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_crawler_install_receipt_signature_sha256
        CHECK (receipt_signature_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_crawler_install_receipt_backup_key_id
        CHECK (backup_attestation_key_id ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT chk_crawler_install_receipt_format
        CHECK (receipt_format = 'mooncen-crawler-control-backup-receipt-v1'),
    CONSTRAINT chk_crawler_install_receipt_target
        CHECK (
            node_role = 'crawler-control'
            AND target_host = 'gen1db'
            AND database_host = 'gen1db'
            AND database_port = 5432
            AND database_name = 'mooncen_staging'
            AND database_sslmode = 'verify-full'
        ),
    CONSTRAINT chk_crawler_install_receipt_canonical_size
        CHECK (octet_length(canonical_receipt) BETWEEN 1 AND 262144),
    CONSTRAINT chk_crawler_install_receipt_release_principal
        CHECK (release_signer_principal = 'mooncen-crawler-control-release'),
    CONSTRAINT chk_crawler_install_receipt_backup_principal
        CHECK (receipt_signer_principal = 'mooncen-gen1db-backup-receipt'),
    CONSTRAINT chk_crawler_install_receipt_lifetime
        CHECK (
            receipt_issued_at < receipt_valid_until
            AND receipt_valid_until <= receipt_issued_at + INTERVAL '24 hours'
            AND receipt_issued_at <= consumed_at
            AND consumed_at < receipt_valid_until
        )
);

REVOKE ALL ON public.ops_crawler_control_install_receipt_consumptions
FROM PUBLIC,
     mooncen_api,
     mooncen_crawler,
     mooncen_crawler_worker,
     mooncen_crawler_control,
     mooncen_crawler_publisher,
     mooncen_crawler_finalizer,
     mooncen_crawler_approver,
     mooncen_crawler_reporter,
     mooncen_crawler_observer,
     mooncen_crawler_release_admin,
     mooncen_applier,
     mooncen_ai,
     mooncen_check,
     mooncen_readonly;

COMMENT ON TABLE public.ops_crawler_control_install_receipt_consumptions IS
    'Append-only, one-time release-bound recovery receipts; no runtime role receives privileges.';
