from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from pydantic import ValidationError
from starlette.requests import Request

from backend.ops.schemas import (
    CrawlerReleaseActionRequest,
    crawler_release_worker_set_digest,
)
from backend import crawler_control_database
from backend.routers import crawler_releases
from ops_agent import crawler_release_action_worker


ROOT = Path(__file__).resolve().parents[1]
COMMIT = "a" * 40
TREE = "b" * 40
DIGEST = "c" * 64
BASELINE = "d" * 64


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/ops/crawler-control/actions",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("test", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )


def test_release_routes_are_isolated_from_legacy_deployment_api() -> None:
    paths = {
        route.path
        for route in crawler_releases.router.routes
        if isinstance(route, APIRoute)
    }
    assert {
        "/api/ops/crawler-control/summary",
        "/api/ops/crawler-control/artifacts",
        "/api/ops/crawler-control/rollouts",
        "/api/ops/crawler-control/rollouts/{rollout_id}",
        "/api/ops/crawler-control/workers",
        "/api/ops/crawler-control/actions",
        "/api/ops/crawler-control/actions/{action_id}",
    }.issubset(paths)


def test_read_models_explicitly_report_unconfigured_control_database() -> None:
    summary = crawler_releases.release_summary(None)
    assert summary["available"] is False
    assert summary["action_capabilities"]["build"] == {
        "available": False,
        "reason": "immutable_builder_evidence_handoff_not_implemented",
    }
    assert summary["action_capabilities"]["create_canary"] == {
        "available": False,
        "reason": "independent_operator_approval_unavailable",
    }
    assert crawler_releases.release_workers(None) == {"available": False, "items": []}
    assert crawler_releases.release_artifacts(10, 0, None) == {
        "available": False,
        "items": [],
        "total": 0,
        "limit": 10,
        "offset": 0,
    }


def test_release_health_treats_a_verified_rollback_report_as_healthy() -> None:
    source = (ROOT / "backend/routers/crawler_releases.py").read_text(encoding="utf-8")
    assert "latest.status NOT IN ('ready', 'rolled_back')" in source
    assert "latest.health->'healthy' IS DISTINCT FROM 'true'::jsonb" in source
    assert "latest.rollout_id IS DISTINCT FROM desired.rollout_id" in source
    assert "latest.code_version IS DISTINCT FROM desired.code_version" in source
    assert "latest.config_revision IS DISTINCT FROM desired.config_revision" in source
    assert "latest.reported_at < clock_timestamp()" in source
    assert "agent.last_seen_at < clock_timestamp()" in source
    assert ") AS release_converged" in source


def test_pre_snapshot_rollout_history_is_unavailable_not_zero_workers(monkeypatch) -> None:
    class ReadDb:
        def execute(self, *_args, **_kwargs):
            return object()

        def rollback(self) -> None:
            return None

    monkeypatch.setattr(crawler_releases, "_control_schema_available", lambda _db: True)
    monkeypatch.setattr(
        crawler_releases,
        "mapped_one",
        lambda _result: {
            "id": str(uuid4()),
            "environment": "staging",
            "rollout_epoch": 4,
            "requested_worker_count": 2,
        },
    )
    monkeypatch.setattr(crawler_releases, "_rollout_worker_rows", lambda *_args, **_kwargs: [])

    result = crawler_releases.release_rollout_detail(uuid4(), ReadDb())  # type: ignore[arg-type]

    assert result["available"] is True
    assert result["workers"] == []
    assert result["worker_history_available"] is False
    assert result["worker_history_reason"] == (
        "rollout_worker_history_predates_snapshot_contract"
    )


def test_build_request_has_exact_confirmation_and_no_artifact_bytes() -> None:
    payload = CrawlerReleaseActionRequest(
        action="build",
        idempotency_key="build:review:0001",
        environment="development",
        expected_generation=0,
        confirmation=f"BUILD {TREE[:12]}",
        reason="reviewed crawler source",
        source_commit=COMMIT,
        source_tree=TREE,
        test_profile="crawler_full",
    )
    assert payload.request_payload() == {
        "source_commit": COMMIT,
        "source_tree": TREE,
        "test_profile": "crawler_full",
    }
    with pytest.raises(ValidationError, match="confirmation"):
        CrawlerReleaseActionRequest(
            **{
                **payload.model_dump(),
                "confirmation": "BUILD unreviewed",
            }
        )


def test_rollout_advance_carries_phase_and_exact_worker_set() -> None:
    rollout_id = uuid4()
    rolling = CrawlerReleaseActionRequest(
        action="advance_rollout",
        idempotency_key="rollout:advance:0001",
        environment="production",
        expected_generation=7,
        confirmation=(
            f"ADVANCE {rollout_id} 7 rolling "
            f"{crawler_release_worker_set_digest(['crawler-a', 'crawler-b'])}"
        ),
        reason="canary health and quality gates passed",
        rollout_id=rollout_id,
        rollout_phase="rolling",
        target_worker_keys=["crawler-a", "crawler-b"],
    )
    assert rolling.request_payload()["rollout_phase"] == "rolling"

    with pytest.raises(ValidationError, match="requires at least one"):
        CrawlerReleaseActionRequest(
            **{
                **rolling.model_dump(),
                "confirmation": f"ADVANCE {rollout_id} 7 rolling none",
                "target_worker_keys": [],
            }
        )
    with pytest.raises(ValidationError, match="does not accept"):
        CrawlerReleaseActionRequest(
            **{
                **rolling.model_dump(),
                "rollout_phase": "complete",
                "confirmation": f"ADVANCE {rollout_id} 7 complete",
            }
        )


def test_canary_confirmation_binds_artifacts_and_order_independent_worker_set() -> None:
    rollout_id = uuid4()
    digest = crawler_release_worker_set_digest(["worker-b", "worker-a"])
    assert digest == crawler_release_worker_set_digest(["worker-a", "worker-b"])
    payload = CrawlerReleaseActionRequest(
        action="create_canary",
        idempotency_key="rollout:canary:0001",
        environment="staging",
        expected_generation=3,
        confirmation=(
            f"CANARY {rollout_id} 3 {DIGEST[:12]} {BASELINE[:12]} {digest}"
        ),
        reason="reviewed canary identities approved",
        rollout_id=rollout_id,
        artifact_digest=DIGEST,
        baseline_digest=BASELINE,
        worker_keys=["worker-b", "worker-a"],
    )
    assert payload.request_payload()["worker_keys"] == ["worker-b", "worker-a"]
    with pytest.raises(ValidationError, match="confirmation"):
        CrawlerReleaseActionRequest(
            **{
                **payload.model_dump(),
                "worker_keys": ["worker-a", "worker-c"],
            }
        )


def test_action_specific_fields_cannot_cross_action_boundaries() -> None:
    with pytest.raises(ValidationError, match="not valid"):
        CrawlerReleaseActionRequest(
            action="pause_rollout",
            idempotency_key="rollout:pause:0001",
            environment="staging",
            expected_generation=2,
            confirmation=f"PAUSE {uuid4()} 2",
            reason="pause for investigation",
            rollout_id=uuid4(),
            artifact_digest=DIGEST,
        )

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CrawlerReleaseActionRequest(
            action="pause_rollout",
            idempotency_key="rollout:pause:0002",
            environment="staging",
            expected_generation=2,
            confirmation=f"PAUSE {uuid4()} 2",
            reason="pause for investigation",
            rollout_id=uuid4(),
            privileged_command="ignored-by-old-contract",  # type: ignore[call-arg]
        )


def test_complete_rollback_has_distinct_terminal_confirmation() -> None:
    rollout_id = uuid4()
    payload = CrawlerReleaseActionRequest(
        action="complete_rollback",
        idempotency_key="rollout:complete-rollback:0001",
        environment="staging",
        expected_generation=9,
        confirmation=f"COMPLETE_ROLLBACK {rollout_id} 9",
        reason="rollback reports match the baseline release",
        rollout_id=rollout_id,
    )

    assert payload.request_payload() == {"rollout_id": str(rollout_id)}
    with pytest.raises(ValidationError, match="confirmation"):
        CrawlerReleaseActionRequest(
            **{
                **payload.model_dump(),
                "confirmation": f"ROLLBACK {rollout_id} 9",
            }
        )


def test_mutation_is_fail_closed_when_central_schema_is_unavailable(monkeypatch) -> None:
    payload = CrawlerReleaseActionRequest(
        action="register_artifact",
        idempotency_key="artifact:register:0001",
        environment="development",
        expected_generation=0,
        confirmation=f"REGISTER {DIGEST[:12]}",
        reason="reviewed builder evidence passed",
        build_request_id=uuid4(),
        artifact_digest=DIGEST,
        code_version="git-abc123",
        config_revision="config-v1",
    )
    monkeypatch.setattr(crawler_releases, "_control_schema_available", lambda _db: False)
    with pytest.raises(HTTPException) as rejected:
        crawler_releases.request_release_action(
            payload,
            _request(),
            SimpleNamespace(id=uuid4()),
            None,
        )
    assert rejected.value.status_code == 409
    assert rejected.value.detail["code"] == "immutable_builder_evidence_handoff_not_implemented"


def test_release_request_schema_has_leased_state_machine_and_least_privilege() -> None:
    migration = (
        ROOT
        / "DB/crawler_control_migrations/20260812_002_release_action_requests.sql"
    ).read_text(encoding="utf-8")
    original = (
        ROOT
        / "DB/crawler_control_migrations/20260810_001_crawler_control_plane.sql"
    ).read_text(encoding="utf-8")
    roles = (ROOT / "DB/roles.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE ops_crawler_release_action_requests" in migration
    assert "UNIQUE (environment, requested_by, idempotency_key)" in migration
    assert "'queued', 'leased', 'succeeded', 'failed', 'cancelled'," in migration
    assert "'reconciliation_required'" in migration
    assert "'complete_rollback'" in migration
    assert "enforce_crawler_release_action_transition" in migration
    assert "crawler_release_action_api_insert" in migration
    assert "crawler_release_action_admin_update" in migration
    assert "SELECT (database_login, environment) ON ops_crawler_api_bindings" in migration
    assert "CREATE TABLE ops_crawler_api_bindings" in migration
    assert "FOREIGN KEY (requester_login, environment)" in migration
    assert "NEW.requester_login := session_user::name" in migration
    assert "environment = current_crawler_api_environment()" in migration
    select_policy = migration.split(
        "CREATE POLICY crawler_release_action_api_select", 1
    )[1].split("CREATE POLICY crawler_release_action_api_insert", 1)[0]
    assert "requester_login = session_user" not in select_policy
    assert "ALTER TABLE ops_crawler_release_action_requests FORCE ROW LEVEL SECURITY" in migration
    assert "crawler_api_job_environment_scope" in migration
    assert "crawler_api_rollout_environment_scope" in migration
    assert "AS RESTRICTIVE FOR SELECT" in migration
    assert "REVOKE ALL ON TABLE ops_crawler_release_artifacts" in migration
    assert "INSERT, UPDATE ON TABLE\n            ops_crawler_release_rollouts" not in original
    assert "TO mooncen_api;\n        GRANT SELECT, INSERT, UPDATE ON ops_crawler_release_rollouts" not in roles
    assert "INSERT (\n                action, environment, idempotency_key" in roles
    assert "TO mooncen_crawler_api" in roles
    assert "lease_owner, leased_until" not in roles
    assert "lease_owner, leased_until" not in migration
    assert "'lease_token', 'SELECT'" in (
        ROOT / "tools/preflight_distributed_crawler_control.py"
    ).read_text(encoding="utf-8")
    assert "TO mooncen_api" not in migration.split(
        "CREATE TABLE ops_crawler_release_action_requests", 1
    )[1]


def test_release_approval_catalog_binds_every_action_policy_parse_tree() -> None:
    migration = (
        ROOT
        / "DB/crawler_control_migrations/20260812_006_release_operator_approvals.sql"
    ).read_text(encoding="utf-8")
    catalog = migration.split(
        "CREATE OR REPLACE FUNCTION crawler_release_approval_catalog_is_valid()", 1
    )[1].split(
        "CREATE OR REPLACE FUNCTION crawler_release_approval_contract_is_valid(", 1
    )[0]

    for policy_name in (
        "crawler_release_action_api_select",
        "crawler_release_action_api_insert",
        "crawler_release_action_admin_select",
        "crawler_release_action_admin_update",
        "crawler_release_action_approval_owner_select",
        "crawler_release_approver_binding_owner_access",
        "crawler_release_approval_owner_access",
        "crawler_release_approval_api_select",
        "crawler_release_approval_admin_select",
        "crawler_release_action_consumer_owner_access",
    ):
        assert f"DROP POLICY {policy_name}" in migration or f"CREATE POLICY {policy_name}" in migration
    assert "CREATE TABLE ops_crawler_release_policy_contract" in migration
    assert migration.count(
        "pg_get_expr(policy.polqual, policy.polrelid, FALSE)"
    ) == 2
    assert migration.count(
        "pg_get_expr(policy.polwithcheck, policy.polrelid, FALSE)"
    ) == 2
    assert "policy.polqual::TEXT" not in migration
    assert "policy.polwithcheck::TEXT" not in migration
    assert "live_policy_digest IS DISTINCT FROM expected_policy_digest" in catalog
    assert "live_policy_count IS DISTINCT FROM 10" in catalog
    assert "zz_reject_crawler_release_policy_contract_mutation" in catalog


def test_release_approval_function_hashes_match_migration_and_consumer() -> None:
    migration = (
        ROOT
        / "DB/crawler_control_migrations/20260812_006_release_operator_approvals.sql"
    ).read_text(encoding="utf-8")

    actual: dict[str, str] = {}
    for name in crawler_releases._RELEASE_APPROVAL_FUNCTION_SHA256:
        function = migration.split(f"CREATE OR REPLACE FUNCTION {name}(", 1)[1]
        body = function.split("AS $$", 1)[1].split("\n$$;", 1)[0]
        canonical = body.replace("\r\n", "\n").replace("\r", "\n").strip()
        actual[name] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    assert actual == crawler_releases._RELEASE_APPROVAL_FUNCTION_SHA256
    assert actual == crawler_release_action_worker.RELEASE_APPROVAL_FUNCTION_SHA256


def test_control_database_module_has_no_primary_session_fallback() -> None:
    source = (ROOT / "backend/crawler_control_database.py").read_text(encoding="utf-8")
    router = (ROOT / "backend/routers/crawler_releases.py").read_text(encoding="utf-8")
    assert "OPS_CRAWLER_API_DB_USER" in source
    assert "OPS_CRAWLER_API_DB_PASSWORD" in source
    assert "from backend.database import" not in source
    assert "subprocess" not in router
    assert "os.system" not in router
    assert "manage_crawler_release" not in router


def test_control_database_rejects_unknown_environment_and_primary_reuse(monkeypatch) -> None:
    configured = {
        "OPS_CRAWLER_SHARED_DB_HOST": "central-db",
        "OPS_CRAWLER_SHARED_DB_NAME": "crawler_control",
        "OPS_CRAWLER_API_DB_USER": "crawler_api_login",
        "OPS_CRAWLER_API_DB_PASSWORD": "not-a-real-test-secret",
    }
    for key, value in configured.items():
        monkeypatch.setenv(key, value)

    monkeypatch.setenv("ENVIRONMENT", "stagign")
    with pytest.raises(RuntimeError, match="environment is invalid"):
        crawler_control_database._configured_endpoint()

    monkeypatch.setenv("ENVIRONMENT", "stage")
    monkeypatch.setenv("DB_HOST", "central-db")
    monkeypatch.setenv("DB_NAME", "crawler_control")
    monkeypatch.setenv("DB_PORT", "5432")
    with pytest.raises(RuntimeError, match="separate from the primary"):
        crawler_control_database._configured_endpoint()

    monkeypatch.setenv("DB_HOST", "primary-db")
    monkeypatch.setenv("DB_API_USER", "CRAWLER_API_LOGIN")
    with pytest.raises(RuntimeError, match="distinct login"):
        crawler_control_database._configured_endpoint()


def test_api_read_sql_does_not_reference_forbidden_action_lease_identity() -> None:
    source = (ROOT / "backend/routers/crawler_releases.py").read_text(encoding="utf-8")
    action_select = source.split("def _action_select()", 1)[1].split(
        '@router.get("/actions")', 1
    )[0]
    insert_returning = source.split(
        "RETURNING id::text, action, environment, status", 1
    )[1].split('"""', 1)[0]

    assert "lease_owner" not in action_select
    assert "lease_token" not in action_select
    assert "FOR UPDATE" not in source
    assert "lease_owner" not in insert_returning
    assert "lease_token" not in insert_returning
