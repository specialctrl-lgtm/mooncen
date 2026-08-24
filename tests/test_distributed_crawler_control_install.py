from pathlib import Path

import pytest

from tools import provision_crawler_service_login as service_login
from tools.preflight_distributed_crawler_control import (
    PreflightError,
    _assert_quality_environment_catalog,
    _assert_rollout_snapshot_catalog,
    _connection_config,
)
from tools.postgres_scram_verifier import (
    PasswordVerifierError,
    build_scram_sha_256_verifier,
    validate_service_password,
)


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_control_schema_installer_has_fail_closed_application_order() -> None:
    source = _read("tools/ensure_crawler_control_schema.py")
    flow = source.split("def ensure_schema", 1)[1].split("def main", 1)[0]

    base = flow.index("_base_contract(connection, confirmed_database, object_owner)")
    dry_run = flow.index("if dry_run:", base)
    role_bootstrap = flow.index("_execute_roles(connection, roles)", dry_run)
    marker = flow.index("cursor.execute(marker)", role_bootstrap)
    migration = flow.index("cursor.execute(migration)", role_bootstrap)
    staging_guard = flow.index("cursor.execute(staging)", migration)
    final_grants = flow.index("_execute_roles(connection, roles)", staging_guard)
    post_contract = flow.index("_post_contract(", final_grants)

    assert (
        base
        < dry_run
        < role_bootstrap
        < marker
        < migration
        < staging_guard
        < final_grants
        < post_contract
    )
    assert "pg_advisory_lock" in source
    assert "mooncen_schema_migrations" in source
    assert "OPS_CRAWLER_SCHEMA_OBJECT_OWNER" in source
    assert "SET LOCAL ROLE {}" in source
    assert "non-extension staging objects have unexpected owners" in source
    assert "mooncen_crawler_worker" in source
    assert "mooncen_crawler_control" in source
    assert "crawler_control_database_marker.sql" in source
    assert '"crawler_control_migrations"' in source
    assert "crawl_staging.fenced_branch_snapshots" in source
    assert "crawl_staging.fenced_course_snapshots" in source


def test_staging_host_setup_is_unconditionally_blocked_before_any_mutation() -> None:
    source = _read("deploy/ubuntu/setup_distributed_crawler_control.sh")

    argument_parse_end = source.index("done")
    rejection = source.index(
        "NOT READY: distributed crawler control installation is disabled."
    )
    rejection_exit = source.index("exit 70", rejection)
    root_check = source.index('if [ "$(id -u)" -ne 0 ]')
    installer_lock = source.index("installer_lock_dir=")
    schema_apply = source.index(
        '"$PYTHON" -X utf8 -m tools.ensure_crawler_control_schema'
    )

    assert argument_parse_end < rejection < rejection_exit < root_check
    assert rejection_exit < installer_lock < schema_apply
    assert "signed atomic release-tree transport is implemented separately" in source
    assert "release-bound, OpenSSH-signed real-gen1db backup receipt" in source
    assert "/var/lib/mooncen-crawler-control-root-trust/receipts/" in source
    assert "There is no override flag." in source

    assert "systemctl disable --now mooncen-crawler.timer" not in source


def test_gen1db_control_installer_is_host_database_and_release_pinned() -> None:
    source = _read("deploy/ubuntu/setup_distributed_crawler_control.sh")

    assert '"$(hostname -s 2>/dev/null || true)" != gen1db' in source
    assert 'topology.primary_for("crawler_control")' in source
    assert '[ "$topology_control_node" != gen1db ]' in source
    assert '[ "$topology_control_host" != gen1db ]' in source
    assert '[ "$schema_host" != gen1db ]' in source
    assert '[ "$schema_database" != mooncen_staging ]' in source
    assert '[ "$shared_host" != gen1db ]' in source
    assert '[ "$shared_database" != mooncen_staging ]' in source
    assert '"$APP_DIR/.deploy-info"' in source
    assert "deploy_manifest_value DEPLOY_COMMIT" in source
    assert "deploy_manifest_value DEPLOY_ARCHIVE_SHA256" in source
    assert "deploy_manifest_value NODE_ROLE" in source
    assert '[ "$deploy_node_role" != crawler-control ]' in source
    assert source.index("NOT READY: distributed crawler control installation") < source.index(
        '"$PYTHON" -X utf8 -m tools.ensure_crawler_control_schema'
    )


def test_windows_control_entry_is_blocked_on_backup_attestation_before_release_or_database_mutation() -> None:
    source = _read("deploy_mooncen.ps1")
    topology_contract = source.split("function Get-ProductionCrawlerContract", 1)[
        1
    ].split("function Expand-ConfigPath", 1)[0]
    function = source.split("function Invoke-CrawlerControlInstall", 1)[1].split(
        "function Invoke-HaStatus", 1
    )[0]
    branch = source.split('"crawler-control-install" {', 1)[1].split(
        '"ai-reset-start" {', 1
    )[0]

    assert "Invoke-CrawlerControlInstall" in branch
    assert "Assert-CrawlerControlBackupAttestationReady" in function
    assert "deploy_crawler_control_from_windows.ps1" in function
    assert "ExpectedReleaseTreeSha256" in function
    assert "ReleaseSignaturePath" in function
    assert function.index("Assert-CrawlerControlBackupAttestationReady") < function.index(
        'Join-Path $PSScriptRoot "deploy/ubuntu/deploy_crawler_control_from_windows.ps1"'
    )
    backup_gate = source.split("function Assert-CrawlerControlBackupAttestationReady", 1)[1].split(
        "function Invoke-CrawlerControlInstall", 1
    )[0]
    assert "NOT READY: crawler-control-install requires" in backup_gate
    assert "No SSH connection, release activation, or database mutation was attempted" in backup_gate
    assert "absolute, digest-verified" in backup_gate
    assert "/etc/mooncen/crawler-control-backup-attestation.json" in backup_gate
    assert "Invoke-Remote" not in backup_gate

    assert 'Where-Object { $_.DeployProfile -eq "full-stack" }' in source
    assert "is control-only. Full-stack deploy is forbidden" in source
    assert 'PSObject.Properties["staging_database"]' in topology_contract
    assert "staging_database primary must be co-located with crawler_control" in topology_contract
    assert "production database primary must remain on cloud" in topology_contract


def test_control_login_provisioner_converges_exact_memberships() -> None:
    source = _read("tools/provision_crawler_service_login.py")

    assert '"control": (' in source
    assert '"mooncen_crawler_control"' in source
    assert '"finalizer": (' in source
    assert '"mooncen_crawler_finalizer"' in source
    assert '"publisher": (' in source
    assert '"approver": (' in source
    assert '"reporter": (' in source
    assert '"observer": (' in source
    assert "ALTER ROLE {} RESET ALL" in source
    assert "REVOKE {} FROM {}" in source
    assert "aclexplode(relation.relacl)" in source
    assert "aclexplode(attribute.attacl)" in source
    assert "aclexplode(procedure.proacl)" in source
    assert "verified[1] != [permission_group]" in source
    assert "verified[2] is not False" in source
    assert "build_scram_sha_256_verifier(password)" in source
    assert "IN DATABASE {} RESET ALL" in source


def test_control_preflight_requires_the_dedicated_control_group() -> None:
    source = _read("tools/preflight_distributed_crawler_control.py")
    scheduler_contract = source.split('"scheduler": """', 1)[1].split('""",', 1)[0]
    publisher_contract = source.split('"publisher": """', 1)[1].split('""",', 1)[0]

    assert "pg_has_role(current_user, 'mooncen_crawler_control', 'member')" in scheduler_contract
    assert "pg_has_role(current_user, 'mooncen_crawler_publisher', 'member')" in publisher_contract
    assert "NOT has_table_privilege(current_user, 'ops_jobs', 'INSERT')" in publisher_contract


def test_worker_preflight_rejects_legacy_crawler_inheritance_and_batch_mutation() -> None:
    source = _read("tools/preflight_distributed_crawler_control.py")
    worker_contract = source.split('"worker": """', 1)[1].split('""",', 1)[0]

    assert "NOT pg_has_role(current_user, 'mooncen_crawler', 'member')" in worker_contract
    assert "NOT has_table_privilege(current_user, 'crawl_batches', 'INSERT')" in worker_contract
    assert "NOT has_table_privilege(current_user, 'crawl_batches', 'UPDATE')" in worker_contract
    assert "NOT has_table_privilege(current_user, 'ops_crawler_batches', 'UPDATE')" in worker_contract


def test_preflight_checks_exact_triggers_direct_acl_and_role_settings() -> None:
    source = _read("tools/preflight_distributed_crawler_control.py")

    assert '"zz_enforce_current_crawler_lease"' in source
    assert '"zz_capture_fenced_crawler_snapshot"' in source
    assert '"trg_fenced_branch_snapshots_immutable"' in source
    assert "trigger.tgtype::integer" in source
    assert "membership.admin_option" in source
    assert "pg_db_role_setting" in source
    assert "pg_attribute object" in source
    assert "retains a direct application ACL" in source
    assert "ux_ops_crawler_release_artifacts_code_version" in source
    assert "crawler artifact code-version unique index definition has drifted" in source
    assert "_assert_rollout_snapshot_catalog(cursor)" in source
    assert "idx_ops_crawler_rollout_worker_snapshots_latest" in source
    assert "[0, 0, 0, 1]" in source
    assert "rollout worker snapshot primary key has drifted" in source
    assert "rollout worker snapshot boundary column has drifted" in source
    assert "_assert_quality_environment_catalog(cursor)" in source
    assert "shared staging quality RLS or owner contract has drifted" in source
    assert 'runtime_environment != "staging"' in source
    assert "non-staging crawler API can read shared staging quality rows" in source


def test_quality_environment_catalog_requires_forced_rls_and_safe_common_owner() -> None:
    class Cursor:
        def execute(self, statement: str) -> None:
            assert "relation.relforcerowsecurity" in statement
            assert "relation.relowner = public_namespace.nspowner" in statement

        @staticmethod
        def fetchall():
            return [
                ("course_quality_score", "r", True, True, True, True),
                ("ops_quality_issues", "r", True, True, True, True),
            ]

    _assert_quality_environment_catalog(Cursor())


def test_quality_environment_catalog_rejects_unforced_owner_bypass() -> None:
    class Cursor:
        @staticmethod
        def execute(_statement: str) -> None:
            return None

        @staticmethod
        def fetchall():
            return [
                ("course_quality_score", "r", True, False, True, True),
                ("ops_quality_issues", "r", True, True, True, True),
            ]

    with pytest.raises(PreflightError, match="quality RLS or owner contract"):
        _assert_quality_environment_catalog(Cursor())


def test_snapshot_catalog_verifier_accepts_list_shaped_foreign_key_arrays() -> None:
    class Cursor:
        def __init__(self) -> None:
            self.step = 0

        def execute(self, _statement: str) -> None:
            self.step += 1

        def fetchall(self):
            if self.step == 1:
                return [
                    ("environment", "text", True, None),
                    ("rollout_id", "uuid", True, None),
                    ("generation", "bigint", True, None),
                    ("worker_key", "text", True, None),
                    ("agent_id", "uuid", True, None),
                    ("desired_status", "text", True, None),
                    ("cohort", "text", True, None),
                    ("artifact_digest", "text", True, None),
                    ("code_version", "text", True, None),
                    ("config_revision", "text", True, None),
                    ("created_at", "timestamp with time zone", True, "CURRENT_TIMESTAMP"),
                ]
            if self.step == 3:
                return [
                    (
                        "fk_agent",
                        "f",
                        True,
                        False,
                        False,
                        ["agent_id"],
                        "public.ops_agents",
                        ["id"],
                        "a",
                        "r",
                        "s",
                    ),
                    (
                        "fk_artifact",
                        "f",
                        True,
                        False,
                        False,
                        ["artifact_digest"],
                        "public.ops_crawler_release_artifacts",
                        ["artifact_digest"],
                        "a",
                        "r",
                        "s",
                    ),
                    (
                        "fk_rollout",
                        "f",
                        True,
                        False,
                        False,
                        ["rollout_id"],
                        "public.ops_crawler_release_rollouts",
                        ["id"],
                        "a",
                        "r",
                        "s",
                    ),
                    (
                        "pk_ops_crawler_rollout_worker_snapshots",
                        "p",
                        True,
                        False,
                        False,
                        ["environment", "rollout_id", "generation", "worker_key"],
                        "",
                        [],
                        " ",
                        " ",
                        " ",
                    ),
                ]
            if self.step == 4:
                return [
                    ("chk_ops_crawler_rollout_worker_snapshot_cohort", True, "CHECK (cohort = ANY (ARRAY['canary'::text, 'stable'::text]))"),
                    ("chk_ops_crawler_rollout_worker_snapshot_code", True, "CHECK (code_version = btrim(code_version) AND char_length(code_version) >= 1 AND char_length(code_version) <= 200)"),
                    ("chk_ops_crawler_rollout_worker_snapshot_config", True, "CHECK (config_revision = btrim(config_revision) AND char_length(config_revision) >= 1 AND char_length(config_revision) <= 255)"),
                    ("chk_ops_crawler_rollout_worker_snapshot_environment", True, "CHECK (environment = ANY (ARRAY['production'::text, 'staging'::text, 'development'::text]))"),
                    ("chk_ops_crawler_rollout_worker_snapshot_generation", True, "CHECK (generation > 0)"),
                    ("chk_ops_crawler_rollout_worker_snapshot_key", True, "CHECK (worker_key = btrim(worker_key) AND char_length(worker_key) >= 1 AND char_length(worker_key) <= 200)"),
                    ("chk_ops_crawler_rollout_worker_snapshot_status", True, "CHECK (desired_status = ANY (ARRAY['active'::text, 'draining'::text, 'disabled'::text]))"),
                ]
            raise AssertionError(self.step)

        def fetchone(self):
            if self.step == 2:
                return True, True
            if self.step == 5:
                return (
                    True,
                    True,
                    True,
                    False,
                    False,
                    False,
                    True,
                    True,
                    4,
                    4,
                    ["environment", "rollout_id", "worker_key", "generation"],
                    [0, 0, 0, 1],
                    "btree",
                    True,
                )
            if self.step == 6:
                return True, "boolean", "true"
            raise AssertionError(self.step)

    _assert_rollout_snapshot_catalog(Cursor())


def test_release_admin_uses_isolated_publication_paths_and_runbook() -> None:
    setup = _read("deploy/ubuntu/setup_distributed_crawler_control.sh")
    preflight = _read("tools/preflight_distributed_crawler_control.py")
    documentation = _read("docs/distributed-crawler-control-plane.md")

    assert "public/state/desired-state.json" in setup
    assert "public/artifacts" in setup
    assert "root:root:755" in setup
    assert "OPS_CRAWLER_RELEASE_PUBLIC_ROOT" in preflight
    assert "release-admin public root must use the fixed served path" in preflight
    assert "tools.manage_crawler_release" in documentation
    assert "register-artifact" in documentation
    assert "create-rollout" in documentation
    assert "advance-rollout" in documentation
    assert "/state/desired-state.json" in documentation


def test_finalizer_preflight_covers_pinned_apply_columns_and_both_evidence_tables() -> None:
    source = _read("tools/preflight_distributed_crawler_control.py")
    contract = source.split('"finalizer": """', 1)[1].split('""",', 1)[0]

    for column in (
        "status",
        "finished_at",
        "total_branches",
        "total_courses",
        "valid_courses",
        "invalid_courses",
        "result",
        "updated_at",
    ):
        assert f"'crawl_batches', '{column}', 'UPDATE'" in contract
    assert "crawl_staging.fenced_branch_snapshots" in contract
    assert "crawl_staging.fenced_course_snapshots" in contract


def test_legacy_worker_login_sql_fails_before_any_secret_or_database_mutation() -> None:
    source = _read("DB/provision_crawler_worker_login.sql")

    assert "DEPRECATED — DO NOT USE" in source
    assert "tools/provision_crawler_service_login.py" in source
    assert "\\quit 3" in source
    assert "ALTER ROLE" not in source
    assert "decode(" not in source


def test_control_units_use_separate_protected_credentials_and_preflight() -> None:
    scheduler = _read("deploy/ubuntu/systemd/mooncen-crawler-control-scheduler.service")
    finalizer = _read("deploy/ubuntu/systemd/mooncen-crawler-control-finalizer.service")
    publisher = _read("deploy/ubuntu/systemd/mooncen-crawler-release-publisher.service")

    assert "User=mooncen-crawler-control" in scheduler
    assert "EnvironmentFile=/etc/mooncen/crawler-control-scheduler.env" in scheduler
    assert "--component scheduler" in scheduler
    assert "crawler-control-finalizer.env" not in scheduler

    assert "User=mooncen-crawler-finalizer" in finalizer
    assert "EnvironmentFile=/etc/mooncen/crawler-control-finalizer.env" in finalizer
    assert "--component finalizer" in finalizer
    assert "crawler-control-scheduler.env" not in finalizer

    assert "User=mooncen-crawler-publisher" in publisher
    assert "EnvironmentFile=/etc/mooncen/crawler-release-publisher.env" in publisher
    assert "--component publisher" in publisher
    assert "ExecStartPre=/usr/bin/mkdir -p /var/lib/mooncen-crawler-control/public" in publisher
    assert "ReadWritePaths=/var/lib/mooncen-crawler-control" in publisher
    assert "StateDirectory=mooncen-crawler-control" not in publisher


def test_worker_and_reporter_units_are_wired_to_exact_preflights() -> None:
    worker = _read("deploy/ubuntu/systemd/mooncen-crawler-pull-worker.service")
    reporter = _read("deploy/ubuntu/systemd/mooncen-crawler-release-reporter.service")

    assert "EnvironmentFile=/etc/mooncen/crawler-worker.env" in worker
    assert "EnvironmentFile=/opt/mooncen-crawler/current/release.env" in worker
    assert "--component worker --env-file /etc/mooncen/crawler-worker.env" in worker
    assert "User=mooncen-crawler-worker" in worker

    assert "User=mooncen-crawler-reporter" in reporter
    assert "EnvironmentFile=/etc/mooncen/crawler-release-reporter.env" in reporter
    assert "run_crawler_release_reporter.py --preflight" in reporter
    assert "python -I" in reporter
    assert "EnvironmentFile=/etc/mooncen/crawler-release-agent.env" not in reporter
    assert "WorkingDirectory=/opt/mooncen-crawler/current" not in reporter


def test_worker_template_co_locates_queue_staging_and_control() -> None:
    worker = _read("deploy/ubuntu/templates/crawler-worker.env.example")

    host = "gen1db"
    assert f"OPS_CRAWLER_SHARED_DB_HOST={host}" in worker
    assert f"OPS_QUEUE_DB_HOST={host}" in worker
    assert f"CRAWL_STAGING_DB_HOST={host}" in worker
    assert "CRAWL_WRITE_MODE=staging" in worker
    assert "OPS_QUEUE_DB_USER=mooncen_crawler_worker_01_login" in worker
    assert "CRAWL_STAGING_DB_USER=mooncen_crawler_worker_01_login" in worker
    assert "OPS_CRAWLER_CODE_VERSION=" not in worker
    assert "OPS_CRAWLER_WORKER_HOSTNAME=worker-01" in worker
    assert "OPS_AGENT_ID=00000000-0000-0000-0000-000000000000" not in worker


def test_all_control_database_templates_pin_the_reviewed_gen1db_endpoint() -> None:
    template_names = (
        "crawler-control-schema.env.example",
        "crawler-control-scheduler.env.example",
        "crawler-control-finalizer.env.example",
        "crawler-control-approver.env.example",
        "crawler-control-metrics.env.example",
        "crawler-release-admin.env.example",
        "crawler-release-publisher.env.example",
        "crawler-release-reporter.env.example",
        "crawler-worker.env.example",
    )

    for name in template_names:
        source = _read(f"deploy/ubuntu/templates/{name}")
        host_lines = [
            line
            for line in source.splitlines()
            if line.startswith(
                (
                    "OPS_CRAWLER_SCHEMA_DB_HOST=",
                    "OPS_CRAWLER_SHARED_DB_HOST=",
                    "OPS_QUEUE_DB_HOST=",
                    "CRAWL_STAGING_DB_HOST=",
                )
            )
        ]
        assert host_lines, name
        assert all(line.endswith("=gen1db") for line in host_lines), name
        assert "DB_HOST=cloud" not in source


def test_worker_enrollment_is_blocked_until_atomic_pair_provisioning_exists() -> None:
    source = _read("deploy/ubuntu/enroll_distributed_crawler_worker.sh")

    gate = source.index("NOT READY: crawler worker enrollment")
    first_lock_mutation = source.index("installer_lock_dir=")
    assert gate < first_lock_mutation
    assert "-m tools.provision_crawler_service_login" not in source
    assert "atomic worker/reporter pair provisioner is unavailable" in source
    assert "active-rotation fencing" in source
    assert "provision_crawler_worker_login.sql" not in source
    assert "PGPASSWORD" not in source
    assert "base64" not in source
    assert 'queue_user="$(read_env_value OPS_QUEUE_DB_USER' in source
    assert 'staging_user="$(read_env_value CRAWL_STAGING_DB_USER' in source
    assert '[ "$queue_user" != "$staging_user" ]' in source
    assert '[ "$queue_password" != "$staging_password" ]' in source
    assert "Worker queue, staging, and shared control endpoints must match exactly." in source
    assert "--component worker --env-file" in source
    assert "--reporter-env PATH" in source
    assert "--component reporter" in source
    assert "--installation-validation" in source
    assert "--require-applied" in source
    assert "systemctl enable" not in source


@pytest.mark.parametrize("component", ["worker", "reporter"])
def test_single_login_api_blocks_pair_components_before_lock_or_database(
    component: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service_login,
        "_installer_lock",
        lambda: pytest.fail("pair gate must run before installer lock mutation"),
    )

    with pytest.raises(service_login.ServiceLoginError, match="atomic pair"):
        service_login.provision_service_login(
            Path("missing-schema.env"),
            Path("missing-service.env"),
            component=component,
            confirmed_database="mooncen_staging",
        )
    with pytest.raises(service_login.ServiceLoginError, match="atomic pair"):
        service_login._provision_service_login_locked(
            Path("missing-schema.env"),
            Path("missing-service.env"),
            component=component,
            confirmed_database="mooncen_staging",
        )


def test_scram_verifier_is_client_generated_and_rejects_shipped_sentinels() -> None:
    password = "A9_worker-password-with-32-characters_"
    verifier = build_scram_sha_256_verifier(password, salt=bytes(range(16)))

    assert verifier.startswith("SCRAM-SHA-256$4096:AAECAwQFBgcICQoLDA0ODw==$")
    assert password not in verifier
    with pytest.raises(PasswordVerifierError, match="template placeholder"):
        validate_service_password("replace_with_worker_login_password")
    with pytest.raises(PasswordVerifierError, match="unquoted URL-safe ASCII"):
        validate_service_password("가" * 32)


def test_worker_preflight_rejects_split_database_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    environment = {
        "ENVIRONMENT": "production",
        "OPS_CRAWLER_SHARED_DB_HOST": "staging-db",
        "OPS_CRAWLER_SHARED_DB_PORT": "5432",
        "OPS_CRAWLER_SHARED_DB_NAME": "mooncen_staging",
        "OPS_QUEUE_DB_HOST": "queue-db",
        "OPS_QUEUE_DB_PORT": "5432",
        "OPS_QUEUE_DB_NAME": "mooncen_staging",
        "CRAWL_STAGING_DB_HOST": "staging-db",
        "CRAWL_STAGING_DB_PORT": "5432",
        "CRAWL_STAGING_DB_NAME": "mooncen_staging",
        "CRAWL_WRITE_MODE": "staging",
        "OPS_QUEUE_DB_USER": "mooncen_crawler_worker_01_login",
        "OPS_QUEUE_DB_PASSWORD": "secret",
    }

    with pytest.raises(PreflightError, match="must be identical"):
        _connection_config("worker", environment)


def test_worker_preflight_rejects_distinct_queue_and_staging_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    environment = {
        "ENVIRONMENT": "production",
        "OPS_CRAWLER_SHARED_DB_HOST": "staging-db",
        "OPS_CRAWLER_SHARED_DB_PORT": "5432",
        "OPS_CRAWLER_SHARED_DB_NAME": "mooncen_staging",
        "OPS_QUEUE_DB_HOST": "staging-db",
        "OPS_QUEUE_DB_PORT": "5432",
        "OPS_QUEUE_DB_NAME": "mooncen_staging",
        "OPS_QUEUE_DB_USER": "worker_queue_login",
        "OPS_QUEUE_DB_PASSWORD": "queue-password",
        "CRAWL_STAGING_DB_HOST": "staging-db",
        "CRAWL_STAGING_DB_PORT": "5432",
        "CRAWL_STAGING_DB_NAME": "mooncen_staging",
        "CRAWL_STAGING_DB_USER": "worker_staging_login",
        "CRAWL_STAGING_DB_PASSWORD": "staging-password",
        "CRAWL_WRITE_MODE": "staging",
    }

    with pytest.raises(PreflightError, match="identical credential"):
        _connection_config("worker", environment)


def test_cutover_is_explicitly_non_executable_until_enable_is_available() -> None:
    documentation = _read("docs/distributed-crawler-control-plane.md")

    assert "The legacy crawler remains" in documentation
    assert "Cutover and rollback design — NOT EXECUTABLE" in documentation
    assert "do not stop,\ndisable, or alter the legacy scheduler" in documentation
    assert 'remains `"crawlerMode": "legacy"`' in documentation
    assert "atomic root-owned control-only" in documentation
    assert "canonical release-tree digest" in documentation
    assert "fresh, machine-verified `gen1db` backup/restore attestation" in documentation
    assert "sudo systemctl disable --now mooncen-crawler.timer" not in documentation
    assert "sudo systemctl enable --now mooncen-crawler.timer" not in documentation
    assert "--enable-control-plane" not in documentation
    assert "--allow-held-control-batch" in documentation
    assert "tools.approve_crawler_control_batch" in documentation
    assert "crawler-control-approver.env" in documentation
    assert "OPS_CRAWLER_AUTO_PROMOTION_ENABLED=false" in documentation
