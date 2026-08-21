from __future__ import annotations

from pathlib import Path

from tools import approve_crawler_release_action as approval_cli
from tools import build_crawler_control_release


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "DB/crawler_control_migrations/20260812_006_release_operator_approvals.sql"
WORKER = ROOT / "ops_agent/crawler_release_action_worker.py"
SETUP = ROOT / "deploy/ubuntu/setup_distributed_crawler_control.sh"


def _migration() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_release_approval_uses_a_dedicated_execute_only_role() -> None:
    sql = _migration()
    roles = (ROOT / "DB/roles_body.sql").read_text(encoding="utf-8")

    assert "mooncen_crawler_release_approver" in roles
    assert "mooncen_crawler_approver" in sql
    assert "OR pg_has_role(session_user, 'mooncen_crawler_approver', 'member')" in sql
    assert "REVOKE ALL ON TABLE ops_crawler_release_approver_bindings" in sql
    assert "GRANT EXECUTE ON FUNCTION approve_crawler_release_action" in sql
    assert "TO mooncen_crawler_release_approver" in sql
    assert "GRANT INSERT ON ops_crawler_release_action_approvals" not in sql
    assert "GRANT UPDATE ON ops_crawler_release_action_approvals" not in sql
    assert "GRANT DELETE ON ops_crawler_release_action_approvals" not in sql
    assert "REVOKE ALL ON FUNCTION approve_crawler_release_action(" in sql
    assert ") FROM PUBLIC;" in sql


def test_approval_recomputes_digest_and_rejects_mismatch_or_expired_replay() -> None:
    sql = _migration()
    approve = sql.split("CREATE OR REPLACE FUNCTION approve_crawler_release_action(", 1)[1]
    approve = approve.split("CREATE OR REPLACE FUNCTION preview_crawler_release_action", 1)[0]

    assert "FOR UPDATE;" in approve
    assert "canonical_digest := public.crawler_release_action_request_digest(" in approve
    assert "expected_request_digest IS DISTINCT FROM canonical_digest" in approve
    assert "locked_request.request_digest IS DISTINCT FROM canonical_digest" in approve
    assert "locked_request.requester_login = session_user::name" in approve
    assert "session_user::name, authenticated_operator_identity, reviewed_reason" in approve
    assert "existing_receipt.expires_at <= clock_timestamp()" in approve
    assert "release approval expired; create a fresh release request" in approve
    assert "release request already has different approval evidence" in approve


def test_receipt_ttl_only_gates_first_claim_and_started_retry_is_stable() -> None:
    sql = _migration()
    worker = WORKER.read_text(encoding="utf-8")

    assert "expires_at <= approved_at + interval '15 minutes'" in sql
    assert "receipt_ttl_seconds NOT BETWEEN 60 AND 900" in sql
    assert worker.count("request.started_at IS NULL") == 2
    assert worker.count("approval.expires_at > clock_timestamp()") == 2
    assert worker.count("request.started_at <= approval.expires_at") == 2
    assert "OLD.started_at IS NOT NULL AND OLD.started_at <= approval.expires_at" in sql


def test_claim_locks_only_request_and_requires_exact_immutable_receipt_join() -> None:
    source = WORKER.read_text(encoding="utf-8")
    claim = source.split("def claim_next(", 1)[1].split("def renew_lease(", 1)[0]

    assert "JOIN ops_crawler_release_action_approvals approval" in claim
    assert "approval.request_id = request.id" in claim
    assert "approval.environment = request.environment" in claim
    assert "approval.request_digest = request.request_digest" in claim
    assert "FOR UPDATE OF request SKIP LOCKED" in claim
    assert "FOR UPDATE SKIP LOCKED" not in claim
    assert "'build'" not in claim
    assert "'register_artifact'" not in claim


def test_approval_catalog_fails_closed_on_acl_rls_trigger_and_key_drift() -> None:
    sql = _migration()
    catalog = sql.split(
        "CREATE OR REPLACE FUNCTION crawler_release_approval_catalog_is_valid()", 1
    )[1].split("-- Runtime readiness", 1)[0]

    assert "key_constraint_signature IS DISTINCT FROM ARRAY[" in catalog
    assert "ux_ops_crawler_release_approver_binding_environment" in catalog
    assert "ops_crawler_release_action_consumers_environment_key" in catalog
    assert "fk_ops_crawler_release_approval_binding" in catalog
    assert "live_policy_digest IS DISTINCT FROM expected_policy_digest" in catalog
    assert "live_policy_count IS DISTINCT FROM 10" in catalog
    assert "pg_get_expr(policy.polqual, policy.polrelid, FALSE)" in catalog
    assert "pg_get_expr(policy.polwithcheck, policy.polrelid, FALSE)" in catalog
    assert "relrowsecurity AND relation.relforcerowsecurity" in catalog
    assert "'zzz_stamp_crawler_release_action_request_digest', 23" in catalog
    assert "'zy_require_crawler_release_action_approval', 19" in catalog
    assert "'zz_reject_crawler_release_approval_mutation', 27" in catalog
    assert "INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES" in catalog


def test_migration_backfill_preserves_preexisting_terminal_rows() -> None:
    sql = _migration()
    disable = sql.index(
        "DISABLE TRIGGER zz_enforce_crawler_release_action_transition"
    )
    update = sql.index("UPDATE ops_crawler_release_action_requests request")
    enable = sql.index("ENABLE TRIGGER zz_enforce_crawler_release_action_transition")

    assert disable < update < enable
    assert "transition trigger drifted before digest backfill" in sql
    assert "trigger.tgtype = 31" in sql
    assert "ALTER TABLE ops_crawler_release_action_requests FORCE ROW LEVEL SECURITY" in sql


def test_proposal_validation_precedes_receipt_insert() -> None:
    sql = _migration()
    approve = sql.split("CREATE OR REPLACE FUNCTION approve_crawler_release_action(", 1)[1]
    approve = approve.split("CREATE OR REPLACE FUNCTION preview_crawler_release_action", 1)[0]

    assert "crawler_release_action_proposal_is_valid(" in approve
    assert approve.index("crawler_release_action_proposal_is_valid(") < approve.index(
        "INSERT INTO public.ops_crawler_release_action_approvals"
    )
    for action in (
        "create_canary",
        "advance_rollout",
        "pause_rollout",
        "rollback_rollout",
        "complete_rollback",
    ):
        assert action in sql
    assert "CANARY %s %s %s %s %s" in sql
    assert "ADVANCE %s %s %s %s" in sql
    assert "COMPLETE_ROLLBACK" in sql


def test_cli_is_preview_only_unless_a_second_reviewed_mode_is_explicit() -> None:
    preview = approval_cli.parse_args(
        ["--request-id", "11111111-1111-4111-8111-111111111111", "--request-digest", "a" * 64]
    )
    approve = approval_cli.parse_args(
        [
            "--request-id",
            "11111111-1111-4111-8111-111111111111",
            "--request-digest",
            "a" * 64,
            "--approve-reviewed",
            "--operator-label",
            "operator.one",
            "--reason",
            "reviewed exact proposal",
            "--confirm",
            "PAUSE 22222222-2222-4222-8222-222222222222 7",
        ]
    )

    assert preview.approve_reviewed is False
    assert approve.approve_reviewed is True


def test_runtime_capability_requires_long_running_consumer_heartbeat() -> None:
    sql = _migration()
    worker = WORKER.read_text(encoding="utf-8")

    assert "last_seen_at > clock_timestamp() - interval '90 seconds'" in sql
    check = worker.split("def check_runtime(", 1)[1].split("def run_once(", 1)[0]
    loop = worker.split("def run_worker(", 1)[1].split("def _stop(", 1)[0]
    assert "heartbeat_runtime(" not in check
    assert "heartbeat_runtime(heartbeat_connection, config)" in loop
    assert "crawler_release_action_runtime_is_ready" in (
        ROOT / "backend/routers/crawler_releases.py"
    ).read_text(encoding="utf-8")


def test_release_bundle_and_installer_wire_006_then_007() -> None:
    paths = build_crawler_control_release.CONTROL_RELEASE_PATHS
    approval = "DB/crawler_control_migrations/20260812_006_release_operator_approvals.sql"
    quality = "DB/crawler_control_migrations/20260812_007_quality_environment_isolation.sql"
    assert paths.index(approval) < paths.index(quality)
    assert "tools/approve_crawler_release_action.py" in paths

    setup = SETUP.read_text(encoding="utf-8")
    assert setup.index("20260812_006_release_operator_approvals.sql") < setup.index(
        "20260812_007_quality_environment_isolation.sql"
    )
    assert "--release-approver-env" in setup
    assert "--enable-release-actions" in setup
    assert "--component release_approver" in setup
