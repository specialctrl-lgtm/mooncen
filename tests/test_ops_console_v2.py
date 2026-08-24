from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute
from pydantic import ValidationError

from backend.main import app
from backend.ops.schemas import CrawlerRunRequest
from backend.ops.service import (
    TERMINAL_JOB_STATUSES,
    deduplication_key,
    local_crawler_runtime_enabled,
    sanitize_for_audit,
    validate_job_parameters,
)
from backend.routers import auth, ops_v2
from backend.routers.ops_v2 import _overall_status
from DB.setup_db import KNOWN_CHECKSUM_TRANSITIONS, migration_checksum_is_accepted
from ops_agent.crawler_outcome import (
    CRAWLER_PARTIAL_SUCCESS_EXIT_CODE,
    ops_status_for_crawler_exit_code,
)
from ops_agent.crawler_worker import build_crawler_command, build_crawler_execution
from ops_agent.quality_worker import _validated_parameters as validated_quality_parameters
from ops_agent import status_agent
from ops_agent.status_agent import _configured_http_url, _database_status, _redis_status


ROOT = Path(__file__).resolve().parents[1]
OPS_IDENTITY_ENV = (
    "MOONCEN_ADMIN_EMAILS",
    "MOONCEN_ADMIN_PROVIDER_IDS",
    "MOONCEN_ADMIN_USER_IDS",
    "MOONCEN_OPS_ADMIN_EMAILS",
    "MOONCEN_OPS_ADMIN_PROVIDER_IDS",
    "MOONCEN_OPS_ADMIN_USER_IDS",
    "MOONCEN_OPS_OPERATOR_EMAILS",
    "MOONCEN_OPS_OPERATOR_PROVIDER_IDS",
    "MOONCEN_OPS_OPERATOR_USER_IDS",
    "MOONCEN_OPS_VIEWER_EMAILS",
    "MOONCEN_OPS_VIEWER_PROVIDER_IDS",
    "MOONCEN_OPS_VIEWER_USER_IDS",
    "MOONCEN_OPS_SINGLE_ACCOUNT_ONLY",
    "MOONCEN_OPS_LOGIN_ID",
    "MOONCEN_OPS_PASSWORD_HASH",
)


def _oauth_user(*, email: str, provider: str = "google", provider_user_id: str = "immutable-id"):
    return SimpleNamespace(
        id=uuid4(),
        email=email,
        name="Ops User",
        oauth_accounts=[
            SimpleNamespace(
                provider=provider,
                provider_user_id=provider_user_id,
                email=email,
                email_verified=True,
            )
        ],
    )


@pytest.fixture(autouse=True)
def clear_ops_identity_environment(monkeypatch):
    for name in OPS_IDENTITY_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MOONCEN_OPS_SINGLE_ACCOUNT_ONLY", "false")


def test_ops_roles_are_hierarchical_and_require_verified_oauth_identity(monkeypatch):
    admin = _oauth_user(email="admin@example.test")
    operator = _oauth_user(email="operator@example.test")
    viewer = _oauth_user(email="viewer@example.test")
    monkeypatch.setenv("MOONCEN_ADMIN_EMAILS", admin.email)
    monkeypatch.setenv("MOONCEN_OPS_OPERATOR_EMAILS", operator.email)
    monkeypatch.setenv("MOONCEN_OPS_VIEWER_EMAILS", viewer.email)

    assert auth.ops_role_for_user(admin) == "admin"
    assert auth.ops_role_for_user(operator) == "operator"
    assert auth.ops_role_for_user(viewer) == "viewer"
    assert auth.require_ops_operator(operator) is operator
    with pytest.raises(Exception, match="operator"):
        auth.require_ops_operator(viewer)

    viewer.oauth_accounts[0].email_verified = False
    assert auth.ops_role_for_user(viewer) is None


def test_quality_counts_reports_unique_incomplete_location_union() -> None:
    payload = {
        "missing_required": 0,
        "invalid_dates": 0,
        "invalid_prices": 0,
        "missing_address": 7,
        "missing_coordinates": 9,
        "incomplete_location": 11,
        "out_of_korea": 0,
        "duplicate_urls": 0,
        "active_courses": 20,
    }

    class FakeMappings:
        def first(self):
            return payload

    class FakeResult:
        def mappings(self):
            return FakeMappings()

    class FakeSession:
        sql = ""

        def execute(self, statement):
            self.sql = str(statement)
            return FakeResult()

    session = FakeSession()
    counts = ops_v2._quality_counts(session)  # type: ignore[arg-type]

    assert counts["missing_address"] == 7
    assert counts["missing_coordinates"] == 9
    assert counts["incomplete_location"] == 11
    normalized_sql = " ".join(session.sql.split())
    assert "COUNT(DISTINCT c.branch_id) FILTER" in normalized_sql
    assert "AS incomplete_location" in normalized_sql
    assert "b.address IS NULL OR btrim(b.address) = '' OR b.lat IS NULL OR b.lon IS NULL" in normalized_sql


def test_crawler_run_sources_push_exact_provider_into_sql(monkeypatch) -> None:
    class EmptyMappings:
        @staticmethod
        def all():
            return []

    class EmptyResult:
        @staticmethod
        def mappings():
            return EmptyMappings()

    class FakeSession:
        def __init__(self):
            self.calls: list[tuple[str, dict[str, object]]] = []

        def execute(self, statement, parameters):
            self.calls.append((" ".join(str(statement).split()), dict(parameters)))
            return EmptyResult()

    monkeypatch.setattr(ops_v2, "table_exists", lambda _db, _table: True)
    session = FakeSession()

    assert ops_v2._ops_crawler_rows(  # type: ignore[arg-type]
        session,
        37,
        provider="MUNI_EXACT",
    ) == []
    assert ops_v2._legacy_crawler_rows(  # type: ignore[arg-type]
        session,
        37,
        provider="MUNI_EXACT",
    ) == []

    ops_sql, ops_parameters = session.calls[0]
    legacy_sql, legacy_parameters = session.calls[1]
    assert "WHERE r.provider = :provider" in ops_sql
    assert "WHERE COALESCE(NULLIF(target_key, ''), NULLIF(crawler_name, '')) = :provider" in legacy_sql
    assert ops_parameters == {"limit": 37, "provider": "MUNI_EXACT"}
    assert legacy_parameters == {"limit": 37, "provider": "MUNI_EXACT"}


def test_crawler_runs_passes_provider_to_each_bounded_source(monkeypatch) -> None:
    observed: list[tuple[str, int, str]] = []

    def fake_ops(_db, fetch_limit, *, provider=""):
        observed.append(("ops", fetch_limit, provider))
        return []

    def fake_legacy(_db, fetch_limit, *, provider=""):
        observed.append(("legacy", fetch_limit, provider))
        return []

    monkeypatch.setattr(ops_v2, "_ops_crawler_rows", fake_ops)
    monkeypatch.setattr(ops_v2, "_legacy_crawler_rows", fake_legacy)

    response = ops_v2.crawler_runs(
        run_status="",
        content_type="",
        provider="MUNI_EXACT",
        limit=50,
        offset=0,
        db=object(),  # type: ignore[arg-type]
    )

    assert observed == [
        ("ops", 550, "MUNI_EXACT"),
        ("legacy", 550, "MUNI_EXACT"),
    ]
    assert response["items"] == []


def test_quality_address_fixes_exposes_available_geocode_state_safely() -> None:
    geocode_columns = list(ops_v2._OPTIONAL_BRANCH_GEOCODE_COLUMNS)
    item = {
        "id": str(uuid4()),
        "provider": "MUNI_TEST",
        "branch_code": "branch-1",
        "name": "Test branch",
        "geocode_status": "retrying",
        "geocode_reason_code": "ambiguous",
        "geocode_attempt_count": 2,
        "geocode_candidates": [{"address": "Seoul", "access_token": "secret-value"}],
        "geocode_next_retry_at": datetime.now(timezone.utc),
        "geocode_last_error": "temporary provider error",
        "geocode_last_attempt_at": datetime.now(timezone.utc),
    }

    class FakeResult:
        def __init__(self, *, rows=None, scalar_value=None):
            self.rows = rows or []
            self.scalar_value = scalar_value

        def mappings(self):
            return self

        def all(self):
            return self.rows

        def scalar(self):
            return self.scalar_value

    class FakeSession:
        def __init__(self):
            self.statements: list[str] = []

        def execute(self, statement, _params=None):
            rendered = str(statement)
            self.statements.append(rendered)
            if "information_schema.columns" in rendered:
                return FakeResult(rows=[{"column_name": name} for name in geocode_columns])
            if "COUNT(*)" in rendered:
                return FakeResult(scalar_value=1)
            return FakeResult(rows=[item])

    db = FakeSession()
    result = ops_v2.quality_address_fixes(
        provider="",
        mode="all",
        limit=100,
        offset=0,
        db=db,  # type: ignore[arg-type]
    )

    assert result["geocode_fields_available"] == sorted(geocode_columns)
    assert result["items"][0]["geocode_status"] == "retrying"
    assert result["items"][0]["geocode_candidates"][0]["access_token"] == "<redacted>"
    branch_select = next(statement for statement in db.statements if "FROM branches b" in statement and "COUNT" not in statement)
    for name in geocode_columns:
        assert f"b.{name}" in branch_select


def test_quality_address_fixes_geocode_projection_tolerates_pending_migration() -> None:
    class EmptyMappings:
        def mappings(self):
            return self

        def all(self):
            return []

    class FakeSession:
        def execute(self, statement, params):
            assert "information_schema.columns" in str(statement)
            assert set(params["column_names"]) == set(ops_v2._OPTIONAL_BRANCH_GEOCODE_COLUMNS)
            return EmptyMappings()

    projection, available = ops_v2._optional_branch_geocode_select(FakeSession())  # type: ignore[arg-type]

    assert available == []
    for name, sql_type in ops_v2._OPTIONAL_BRANCH_GEOCODE_COLUMNS.items():
        assert f"NULL::{sql_type} AS {name}" in projection


def test_provider_identity_preserves_immutable_id_case(monkeypatch):
    user = _oauth_user(
        email="unverified-contact@example.test",
        provider="naver",
        provider_user_id="Provider-ID-With-Case",
    )
    user.oauth_accounts[0].email_verified = False
    monkeypatch.setenv(
        "MOONCEN_OPS_OPERATOR_PROVIDER_IDS",
        "naver:Provider-ID-With-Case",
    )

    assert auth.ops_role_for_user(user) == "operator"


def test_dedicated_ops_user_is_the_only_identity_in_single_account_mode(monkeypatch):
    password_user = SimpleNamespace(
        id=uuid4(),
        email="local-operator@example.test",
        provider="email",
        password_hash="argon2-hash",
        oauth_accounts=[],
    )

    assert auth.ops_role_for_user(password_user) is None

    monkeypatch.setenv("MOONCEN_OPS_OPERATOR_USER_IDS", str(password_user.id))
    assert auth.ops_role_for_user(password_user) == "operator"

    monkeypatch.setenv("MOONCEN_OPS_SINGLE_ACCOUNT_ONLY", "true")
    assert auth.ops_role_for_user(password_user) is None

    dedicated_user = SimpleNamespace(
        id=uuid4(),
        email=auth.OPS_ACCOUNT_EMAIL,
        provider="ops",
        password_hash=None,
        oauth_accounts=[],
    )
    assert auth.ops_role_for_user(dedicated_user) == "admin"


def test_dedicated_ops_password_uses_iterated_sha256(monkeypatch):
    password = "a-strong-standalone-password"
    rounds = 600_000
    salt = "random-salt-value"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), rounds).hex()
    encoded = f"pbkdf2_sha256${rounds}${salt}${digest}"

    assert auth._verify_password(password, encoded) is True
    assert auth._verify_password("wrong-password-value", encoded) is False

    monkeypatch.setenv("MOONCEN_OPS_LOGIN_ID", "opsadmin")
    monkeypatch.setenv("MOONCEN_OPS_PASSWORD_HASH", encoded)
    assert auth._ops_login_configuration() == ("opsadmin", encoded)


def test_dedicated_ops_login_accepts_eight_character_password() -> None:
    payload = auth.OpsLoginRequest(login_id="opsadmin", password="12345678")

    assert payload.password == "12345678"


def test_crawler_request_requires_scope_target_and_safe_url():
    with pytest.raises(ValidationError, match="provider is required"):
        CrawlerRunRequest(scope="provider")
    with pytest.raises(ValidationError, match="safe HTTP"):
        CrawlerRunRequest(scope="url", url="file:///etc/passwd")
    with pytest.raises(ValidationError, match="specific content_type"):
        CrawlerRunRequest(scope="data_type", content_type="all")

    request = CrawlerRunRequest(
        scope="branch",
        provider="SUWON_LIBRARY",
        branch="중앙도서관",
        content_type="education",
        max_retries=2,
    )
    assert request.branch == "중앙도서관"
    assert request.max_retries == 2


def test_audit_sanitizer_masks_nested_secrets_and_bounds_collections():
    sanitized = sanitize_for_audit(
        {
            "provider": "SUWON_LIBRARY",
            "authorization": "Bearer secret-token",
            "nested": {"db_password": "database-secret", "safe": "value"},
        }
    )

    assert sanitized["provider"] == "SUWON_LIBRARY"
    assert sanitized["authorization"] == "<redacted>"
    assert sanitized["nested"]["db_password"] == "<redacted>"
    assert sanitized["nested"]["safe"] == "value"


def test_job_parameters_reject_secrets_without_truncating_valid_operational_values():
    long_url = "https://example.test/course?" + "a" * 2_500
    validated = validate_job_parameters({"url": long_url, "max_retries": 1})
    assert validated["url"] == long_url

    with pytest.raises(Exception, match="Secret-bearing"):
        validate_job_parameters({"authorization_token": "must-not-enter-a-job"})


def test_job_deduplication_key_is_stable_and_parameter_sensitive():
    first = deduplication_key("crawler_run", "production", {"provider": "LOTTE", "limit": 10})
    reordered = deduplication_key("crawler_run", "production", {"limit": 10, "provider": "LOTTE"})
    changed = deduplication_key("crawler_run", "production", {"provider": "LOTTE", "limit": 20})

    assert first == reordered
    assert first != changed
    assert first.startswith("crawler_run:production:")


def test_crawler_run_merge_keeps_the_scheduled_ops_record_and_unmatched_direct_runs():
    started = datetime(2026, 7, 27, 4, 27, 28, tzinfo=timezone.utc)
    ops_row = {
        "id": "ops-run",
        "provider": "HOMEPLUS",
        "started_at": started,
        "trigger": "local_schedule",
        "source": "ops_crawler_runs",
    }
    matching_legacy = {
        "id": "legacy-20",
        "provider": "HOMEPLUS",
        "started_at": started + timedelta(seconds=8),
        "trigger": "standalone",
        "source": "crawler_run_log",
    }
    direct_legacy = {
        "id": "legacy-21",
        "provider": "EMART",
        "started_at": started + timedelta(minutes=5),
        "trigger": "standalone",
        "source": "crawler_run_log",
    }

    merged = ops_v2._merge_crawler_rows(
        [ops_row],
        [matching_legacy, direct_legacy],
    )

    assert [row["id"] for row in merged] == ["ops-run", "legacy-21"]
    assert merged[0]["trigger"] == "local_schedule"
    assert merged[0]["legacy_run_id"] == "legacy-20"
    assert merged[0]["source"] == "ops_crawler_runs+crawler_run_log"


def test_crawler_worker_uses_only_registry_backed_argv_templates():
    command = build_crawler_command(
        {
            "scope": "provider",
            "provider": "HOMEPLUS",
            "run_mode": "apply",
            "concurrency": 1,
        }
    )

    assert command[1].endswith("run_crawlers.py")
    assert command[command.index("--providers") + 1] == "HOMEPLUS"
    assert "--once" in command
    assert "--ignore-active-window" in command

    with pytest.raises(ValueError, match="reviewed crawler registry"):
        build_crawler_command(
            {
                "scope": "provider",
                "provider": "HOMEPLUS; rm -rf /",
                "run_mode": "apply",
                "concurrency": 1,
            }
        )


def test_crawler_worker_routes_operational_municipal_provider_through_owner():
    provider = "MUNI_YEYAK_HSCITY_GO_KR_2DFD650A"

    command, environment = build_crawler_execution(
        {
            "scope": "provider",
            "provider": provider,
            "run_mode": "apply",
            "concurrency": 1,
        }
    )

    assert command[command.index("--providers") + 1] == "MUNICIPAL_RESERVATION_TARGETS"
    excluded = set(environment["CRAWLER_PROVIDERS"].split())
    assert provider not in excluded
    assert excluded
    with pytest.raises(ValueError, match="staging review worker"):
        build_crawler_command(
            {
                "scope": "provider",
                "provider": "HOMEPLUS",
                "run_mode": "dry_run",
                "concurrency": 1,
            }
        )


def test_quality_worker_accepts_only_bounded_production_scopes():
    assert validated_quality_parameters(
        {
            "content_type": "education",
            "provider": "SUWON_LIBRARY",
            "branch": "중앙도서관",
        }
    ) == {
        "content_type": "education",
        "provider": "SUWON_LIBRARY",
        "branch": "중앙도서관",
    }
    with pytest.raises(ValueError, match="unsupported content_type"):
        validated_quality_parameters({"content_type": "temporary_fake_type"})
    with pytest.raises(ValueError, match="provider is invalid"):
        validated_quality_parameters({"provider": "X" * 101})


def test_quality_categories_use_normalized_category_and_bound_filters(monkeypatch):
    class FakeSession:
        sql = ""
        params: dict[str, object] = {}

        def execute(self, statement, params):
            self.sql = str(statement)
            self.params = params
            return object()

    db = FakeSession()
    rows = [
        {
            "content_type": "education",
            "category": "문화예술",
            "active_count": 12,
            "average_score": 91.2,
        }
    ]
    monkeypatch.setattr(ops_v2, "table_exists", lambda _db, table: table == "course_quality_score")
    monkeypatch.setattr(ops_v2, "mapped_rows", lambda _result: rows)

    result = ops_v2.quality_categories(
        content_type="education",
        category="문화예술",
        limit=25,
        db=db,
    )

    assert result == {"available": True, "items": rows, "total": 1}
    assert "standard_category_label" in db.sql
    assert "COUNT(DISTINCT c.provider)" in db.sql
    assert "field_completeness" in db.sql
    assert "encoding_issue_count" in db.sql
    assert "target_count" in db.sql
    assert "fee_count" in db.sql
    assert "date_count" in db.sql
    assert "place_count" in db.sql
    assert "category_count" in db.sql
    assert "time_count" in db.sql
    assert db.params == {
        "limit": 25,
        "content_type": "education",
        "category": "문화예술",
    }


def test_quality_categories_can_group_by_major_service_category(monkeypatch):
    class FakeSession:
        sql = ""

        def execute(self, statement, _params):
            self.sql = str(statement)
            return object()

    db = FakeSession()
    rows = [{"content_type": "education", "category": "교육", "active_count": 12}]
    monkeypatch.setattr(ops_v2, "table_exists", lambda _db, _table: False)
    monkeypatch.setattr(ops_v2, "mapped_rows", lambda _result: rows)

    result = ops_v2.quality_categories(
        content_type="",
        category="",
        level="major",
        limit=10,
        db=db,
    )

    assert result == {"available": True, "items": rows, "total": 1}
    assert "THEN '문화센터'" in db.sql
    assert "THEN '체험'" in db.sql
    assert "THEN '교육'" in db.sql
    assert "c.provider IN" in db.sql
    assert "'HOMEPLUS'" in db.sql
    assert "'LOTTE_MART'" in db.sql
    assert "c.service_group = '문화센터'" not in db.sql
    assert "%시청%" in db.sql
    assert "%주민센터%" in db.sql
    assert "%도서관%" in db.sql
    assert "education_institution" in db.sql
    assert "(시|군|구|읍|면|동)$" in db.sql
    assert "sports_facility" in db.sql


def test_quality_providers_include_required_field_completeness(monkeypatch):
    class FakeSession:
        sql = ""
        params: dict[str, object] = {}

        def execute(self, statement, params):
            self.sql = str(statement)
            self.params = params
            return object()

    db = FakeSession()
    rows = [{"provider": "MUNI_TEST", "content_type": "education", "active_count": 12}]
    monkeypatch.setattr(ops_v2, "table_exists", lambda _db, _table: False)
    monkeypatch.setattr(ops_v2, "mapped_rows", lambda _result: rows)

    result = ops_v2.quality_providers(
        content_type="education",
        provider="",
        category="교육",
        level="major",
        limit=500,
        db=db,
    )

    assert result == {"available": True, "items": rows, "total": 1}
    assert "field_completeness" in db.sql
    assert "encoding_issue_count" in db.sql
    assert "target_count" in db.sql
    assert "fee_count" in db.sql
    assert "date_count" in db.sql
    assert "place_count" in db.sql
    assert "category_count" in db.sql
    assert "time_count" in db.sql
    assert "THEN '교육'" in db.sql
    assert "c.raw_fields->>'fee'" in db.sql
    assert "c.raw_fields->>'period'" in db.sql
    assert db.params == {
        "limit": 500,
        "content_type": "education",
        "category": "교육",
    }


def test_quality_gap_samples_return_missing_fields_and_parser_hint(monkeypatch):
    class FakeSession:
        sql = ""
        params: dict[str, object] = {}

        def execute(self, statement, params):
            self.sql = str(statement)
            self.params = params
            return object()

    db = FakeSession()
    rows = [
        {
            "id": "course-id",
            "provider": "MUNI_TEST",
            "title": "테스트 강좌",
            "missing_fields": ["fee", "time"],
            "current_parser": "generic_table",
            "source_url": "https://example.go.kr/lecture/list.do",
            "total": 7,
            "missing_target_count": 1,
            "missing_fee_count": 7,
            "missing_date_count": 2,
            "missing_place_count": 0,
            "missing_category_count": 0,
            "missing_time_count": 5,
        }
    ]
    monkeypatch.setattr(ops_v2, "mapped_rows", lambda _result: rows)

    result = ops_v2.quality_gap_samples(
        provider="MUNI_TEST",
        content_type="education",
        category="교육",
        level="major",
        limit=10,
        db=db,
    )

    assert result["total"] == 7
    assert result["items"][0]["missing_fields"] == ["fee", "time"]
    assert result["items"][0]["source_url"] == "https://example.go.kr/lecture/list.do"
    assert result["missing_counts"] == {
        "target": 1,
        "fee": 7,
        "date": 2,
        "place": 0,
        "category": 0,
        "time": 5,
    }
    assert result["suggested_parser_family"] == "municipal board/list + detail"
    assert "missing_fields" in db.sql
    assert "COUNT(*) OVER ()" in db.sql
    assert db.params == {
        "provider": "MUNI_TEST",
        "limit": 10,
        "content_type": "education",
        "category": "교육",
    }


def test_ops_category_metadata_hides_corrupted_values_and_records_fields():
    row = {
        "standard_category_label": "미술·공예",
        "domain_category": "??깃문??덈뮸",
        "collection_category": "????",
        "category_raw": "미술",
    }

    result = ops_v2._sanitize_category_metadata(row)

    assert result["standard_category_label"] == "미술·공예"
    assert result["domain_category"] is None
    assert result["collection_category"] is None
    assert result["category_raw"] == "미술"
    assert result["category_encoding_issue"] is True
    assert result["damaged_category_fields"] == [
        "domain_category",
        "collection_category",
    ]


def test_dashboard_overall_status_does_not_claim_healthy_for_unknown_components():
    partial = [
        {"type": "backend", "status": "healthy"},
        {"type": "database", "status": "healthy"},
    ]
    complete = [
        {"type": "frontend", "status": "healthy"},
        {"type": "backend", "status": "healthy"},
        {"type": "database", "status": "healthy"},
        {"type": "crawler", "status": "healthy"},
        {"type": "ai_worker", "status": "healthy"},
        {"type": "agent", "status": "healthy"},
    ]

    assert _overall_status(partial) == "unknown"
    assert _overall_status(complete) == "healthy"
    complete[-1]["status"] = "critical"
    assert _overall_status(complete) == "critical"


def test_priority_ops_routes_are_registered_with_role_dependencies():
    routes: dict[tuple[str, str], set[str]] = {}
    for included in app.routes:
        if isinstance(included, APIRoute):
            candidates = [("", included)]
        elif hasattr(included, "original_router") and hasattr(included, "include_context"):
            candidates = [
                (included.include_context.prefix, route)
                for route in included.original_router.routes
                if isinstance(route, APIRoute)
            ]
        else:
            candidates = []
        for prefix, route in candidates:
            routes[(prefix + route.path, next(iter(route.methods or {"GET"})))] = {
                getattr(dependency.call, "__name__", "") for dependency in route.dependant.dependencies
            }

    required_reads = {
        "/api/ops/dashboard/summary",
        "/api/ops/runtime-metrics",
        "/api/ops/services",
        "/api/ops/crawlers",
        "/api/ops/crawlers/runs",
        "/api/ops/quality/providers",
        "/api/ops/quality/gap-samples",
        "/api/ops/quality/categories",
        "/api/ops/quality/issues",
        "/api/ops/content",
        "/api/ops/content/{course_id}",
        "/api/ops/jobs",
        "/api/ops/audit-logs",
        "/api/ops/agents",
        "/api/ops/deployments",
        "/api/ops/settings",
    }
    for path in required_reads:
        matching = [dependencies for (route_path, _method), dependencies in routes.items() if route_path == path]
        assert matching
        assert any("require_ops_viewer" in dependencies for dependencies in matching)

    mutation_routes = {
        "/api/ops/crawlers/run",
        "/api/ops/crawlers/parser-probe",
        "/api/ops/quality/scan",
        "/api/ops/jobs/{job_id}/cancel",
    }
    for path in mutation_routes:
        matching = [dependencies for (route_path, _method), dependencies in routes.items() if route_path == path]
        assert matching
        assert any("require_ops_operator" in dependencies for dependencies in matching)


def test_ops_migration_is_additive_and_has_queue_and_audit_guards():
    migration = (ROOT / "DB/migrations/20260725_001_ops_console_core.sql").read_text(encoding="utf-8")
    roles = (ROOT / "DB/roles.sql").read_text(encoding="utf-8")
    quality_worker = (ROOT / "ops_agent/quality_worker.py").read_text(encoding="utf-8")

    for table in (
        "ops_agents",
        "ops_services",
        "ops_jobs",
        "ops_job_logs",
        "ops_audit_logs",
        "ops_alerts",
        "ops_crawler_runs",
        "ops_crawler_errors",
        "ops_quality_issues",
        "ops_content_overrides",
    ):
        assert f"CREATE TABLE {table}" in migration
    assert "FOR UPDATE SKIP LOCKED" in migration
    assert "ux_ops_jobs_active_deduplication" in migration
    assert "DROP TABLE" not in migration.upper()
    assert "GRANT SELECT, INSERT ON ops_audit_logs TO mooncen_api" in roles
    assert "FOR UPDATE SKIP LOCKED" in quality_worker
    assert "ON CONFLICT (issue_key)" in quality_worker
    assert "ops_quality_v1" in quality_worker


def test_partial_crawler_outcome_is_supported_across_job_contracts() -> None:
    migration = (
        ROOT / "DB/migrations/20260804_001_ops_job_partial_success.sql"
    ).read_text(encoding="utf-8")
    schema_helper = (ROOT / "tools/ensure_ops_console_schema.py").read_text(
        encoding="utf-8"
    )
    worker = (ROOT / "ops_agent/crawler_worker.py").read_text(encoding="utf-8")
    frontend_types = (ROOT / "ops-console/src/types.ts").read_text(encoding="utf-8")

    assert "DROP CONSTRAINT chk_ops_jobs_status" in migration
    assert "'partial_success'" in migration
    assert "20260804_001_ops_job_partial_success" in schema_helper
    assert "'partial_success'" in frontend_types
    assert "IN ('success', 'partial_success')" in worker
    assert "partial_success" in TERMINAL_JOB_STATUSES
    assert ops_status_for_crawler_exit_code(0) == "success"
    assert (
        ops_status_for_crawler_exit_code(CRAWLER_PARTIAL_SUCCESS_EXIT_CODE)
        == "partial_success"
    )
    assert ops_status_for_crawler_exit_code(1) == "failed"


def test_local_crawler_runtime_requires_explicit_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("OPS_LOCAL_CRAWLER_RUNTIME_ENABLED", raising=False)
    assert local_crawler_runtime_enabled() is False

    monkeypatch.setenv("OPS_LOCAL_CRAWLER_RUNTIME_ENABLED", "true")
    assert local_crawler_runtime_enabled() is True

    monkeypatch.setenv("OPS_LOCAL_CRAWLER_RUNTIME_ENABLED", "false")
    assert local_crawler_runtime_enabled() is False

    router = (ROOT / "backend/routers/ops_v2.py").read_text(encoding="utf-8")
    launcher = (ROOT / "start_ops_console.ps1").read_text(encoding="utf-8")
    assert router.count("local_crawler_runtime_enabled()") >= 4
    assert 'OPS_LOCAL_CRAWLER_RUNTIME_ENABLED = "true"' in launcher
    assert 'OPS_LOCAL_CRAWLER_RUNTIME_ENABLED = "false"' in launcher


def test_status_agent_reports_unconfigured_redis_as_disabled(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)

    status = _redis_status()

    assert status.name == "Redis"
    assert status.status == "disabled"
    assert "not configured" in str(status.error)


def test_status_agent_rejects_credential_bearing_health_urls(monkeypatch):
    monkeypatch.setenv("OPS_STATUS_BACKEND_URL", "http://admin:secret@127.0.0.1:8001/health")

    with pytest.raises(RuntimeError, match="plain HTTP"):
        _configured_http_url("OPS_STATUS_BACKEND_URL", "http://127.0.0.1:8001/health")


def test_status_agent_keeps_database_host_separate_from_reporter() -> None:
    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, _query: str) -> None:
            return None

        def fetchone(self):
            return (1,)

    class FakeConnection:
        def cursor(self):
            return FakeCursor()

    status = _database_status(FakeConnection(), "cloud")

    assert status.status == "healthy"
    assert status.service_host == "cloud"


def test_status_agent_does_not_claim_crawler_runtime_from_database_history(monkeypatch) -> None:
    monkeypatch.setattr(
        status_agent,
        "_http_status",
        lambda name, service_type, _url: status_agent.ServiceStatus(name, service_type, "healthy"),
    )
    monkeypatch.setattr(
        status_agent,
        "_database_status",
        lambda _connection, host: status_agent.ServiceStatus("PostgreSQL", "database", "healthy", service_host=host),
    )
    monkeypatch.setattr(
        status_agent,
        "_redis_status",
        lambda: status_agent.ServiceStatus("Redis", "redis", "disabled"),
    )
    monkeypatch.setattr(status_agent, "database_config", lambda: {"host": "cloud"})

    statuses = status_agent.collect_statuses(object())
    source = (ROOT / "ops_agent/status_agent.py").read_text(encoding="utf-8")

    assert all(item.service_type != "crawler" for item in statuses)
    assert "crawler_run_log" not in source
    assert '"crawler_queue"' not in source
    assert '"quality_queue"' not in source
    assert '"parser_probe"' not in source


def test_ops_service_location_migration_and_api_keep_reporter_distinct() -> None:
    migration = (ROOT / "DB/migrations/20260803_001_ops_service_host.sql").read_text(encoding="utf-8")
    router = (ROOT / "backend/routers/ops_v2.py").read_text(encoding="utf-8")
    schema_helper = (ROOT / "tools/ensure_ops_console_schema.py").read_text(encoding="utf-8")

    assert "ADD COLUMN service_host TEXT" in migration
    assert "s.service_host" in router
    assert "a.hostname AS reporter_hostname" in router
    assert "20260803_001_ops_service_host" in schema_helper


@pytest.mark.parametrize("environment", ["development", "production"])
@pytest.mark.parametrize("service_type", ["frontend", "backend", "database"])
def test_ops_api_maps_active_production_web_and_database_to_cloud(
    service_type: str,
    environment: str,
) -> None:
    item = {
        "environment": environment,
        "service_type": service_type,
        "service_host": "localhost",
    }

    mapped = ops_v2._with_production_placement(item)

    assert mapped is not None
    assert mapped["topology_node"] == "cloud"
    assert mapped["topology_host"] == "cloud"
    assert mapped["topology_role"] == "primary"
    assert mapped["service_host"] == "localhost"


def test_ops_api_maps_crawler_to_reviewed_gen1crawler_owner() -> None:
    mapped = ops_v2._with_production_placement(
        {
            "environment": "development",
            "service_type": "crawler",
            "service_host": "gen1win",
            "reporter_hostname": "observed-crawler-host",
        }
    )

    assert mapped is not None
    assert mapped["topology_node"] == "gen1crawler"
    assert mapped["topology_host"] == "gen1crawler"
    assert mapped["topology_role"] == "primary"
    assert mapped["service_host"] == "gen1win"
    assert mapped["reporter_hostname"] == "observed-crawler-host"
    assert mapped["configured_owner_node"] == "gen1crawler"
    assert mapped["configured_owner_host"] == "gen1crawler"
    assert mapped["configured_owner_role"] == "primary"
    assert mapped["observed_runtime_host"] is None
    assert mapped["runtime_host_verified"] is False
    assert mapped["runtime_host_evidence_source"] is None
    assert mapped["reporter_is_runtime_evidence"] is False
    assert mapped["service_host_is_runtime_evidence"] is False


def test_registered_component_keeps_agent_reporter_out_of_runtime_host(monkeypatch) -> None:
    class FakeResult:
        def mappings(self):
            return self

        def first(self):
            return {
                "type": "crawler",
                "name": "Crawler",
                "service_host": "crawler-endpoint",
                "reporter_hostname": "actual-worker",
                "status": "healthy",
            }

    class FakeSession:
        sql = ""

        def execute(self, statement, _params):
            self.sql = str(statement)
            return FakeResult()

    monkeypatch.setattr(ops_v2, "table_exists", lambda _db, _table: True)
    db = FakeSession()

    mapped = ops_v2._registered_component(db, "crawler")  # type: ignore[arg-type]

    assert mapped is not None
    assert "LEFT JOIN ops_agents a ON a.id = s.agent_id" in db.sql
    assert mapped["service_host"] == "crawler-endpoint"
    assert mapped["reporter_hostname"] == "actual-worker"
    assert mapped["observed_runtime_host"] is None
    assert mapped["configured_owner_host"] == "gen1crawler"
    assert mapped["runtime_host_verified"] is False


def test_ops_api_accepts_only_explicit_runtime_host_evidence() -> None:
    mapped = ops_v2._with_production_placement(
        {
            "service_type": "crawler",
            "service_host": "status-endpoint",
            "reporter_hostname": "status-reporter",
            "observed_runtime_host": "proven-executor",
        }
    )

    assert mapped is not None
    assert mapped["configured_owner_host"] == "gen1crawler"
    assert mapped["service_host"] == "status-endpoint"
    assert mapped["reporter_hostname"] == "status-reporter"
    assert mapped["observed_runtime_host"] == "proven-executor"
    assert mapped["runtime_host_verified"] is True
    assert mapped["runtime_host_evidence_source"] == "explicit_observed_runtime_host"


def test_ops_crawler_fallback_keeps_reviewed_gen1crawler_owner(monkeypatch) -> None:
    monkeypatch.setattr(
        ops_v2,
        "_registered_component",
        lambda _db, _service: pytest.fail("crawler summary must not consume reporter-owned service rows"),
    )
    monkeypatch.setattr(ops_v2, "table_exists", lambda _db, _table: False)

    mapped = ops_v2._crawler_component(object())

    assert mapped["status"] == "unknown"
    assert mapped["topology_node"] == "gen1crawler"
    assert mapped["topology_host"] == "gen1crawler"
    assert mapped["topology_role"] == "primary"
    assert mapped["configured_owner_node"] == "gen1crawler"
    assert mapped["configured_owner_host"] == "gen1crawler"
    assert mapped["configured_owner_role"] == "primary"
    assert mapped["observed_runtime_host"] is None
    assert mapped["runtime_host_verified"] is False
    assert mapped["status_observation_source"] == "crawler_run_log"


def test_ops_crawler_run_history_reports_health_without_inventing_executor(monkeypatch) -> None:
    class FakeResult:
        def mappings(self):
            return self

        def first(self):
            return {
                "status": "success",
                "started_at": datetime(2026, 8, 11, tzinfo=timezone.utc),
                "ended_at": datetime(2026, 8, 11, 1, tzinfo=timezone.utc),
                "error_message": None,
            }

    class FakeSession:
        def execute(self, _statement):
            return FakeResult()

    monkeypatch.setattr(ops_v2, "table_exists", lambda _db, table: table == "crawler_run_log")
    monkeypatch.setattr(
        ops_v2,
        "_registered_component",
        lambda _db, _service: pytest.fail("stale reporter row must not determine crawler placement"),
    )

    mapped = ops_v2._crawler_component(FakeSession())  # type: ignore[arg-type]

    assert mapped["status"] == "healthy"
    assert mapped["configured_owner_host"] == "gen1crawler"
    assert mapped["observed_runtime_host"] is None
    assert mapped["runtime_host_verified"] is False
    assert mapped["status_observation_source"] == "crawler_run_log"
    assert "reporter_hostname" not in mapped
    assert "service_host" not in mapped


def test_ops_crawler_runtime_disabled_message_uses_configured_owner(monkeypatch) -> None:
    placement = SimpleNamespace(node="crawler-node", service_host="crawler.example", role="primary")
    topology = SimpleNamespace(primary_for=lambda service: placement if service == "crawler" else None)
    monkeypatch.setattr(ops_v2, "load_production_topology", lambda: topology)

    detail = ops_v2._crawler_runtime_disabled_detail()

    assert "crawler.example" in detail
    assert "cloud" not in detail
    router = (ROOT / "backend/routers/ops_v2.py").read_text(encoding="utf-8")
    assert "one-shot on cloud" not in router
    assert router.count("detail=_crawler_runtime_disabled_detail()") == 3


def test_local_launcher_contains_control_plane_and_opt_in_data_components():
    launcher = (ROOT / "start_ops_console.ps1").read_text(encoding="utf-8")
    launcher_wrapper = (ROOT / "start_ops_console.cmd").read_text(encoding="utf-8")
    schema_helper = (ROOT / "tools/ensure_ops_console_schema.py").read_text(encoding="utf-8")

    for component in (
        "backend.main:app",
        "ops_agent.status_agent",
        "ops_agent.crawler_worker",
        "ops_agent.quality_worker",
    ):
        assert component in launcher
    assert "[switch]$EnableLocalCrawlerRuntime" in launcher
    assert "if ($EnableLocalCrawlerRuntime)" in launcher
    assert "tools\\ensure_ops_console_schema.py" in launcher
    assert "20260725_001_ops_console_core" in schema_helper
    assert "Required Ops migration checksum mismatch" in schema_helper
    assert "function Invoke-CheckedNative" in launcher
    assert "function Resolve-NodeExecutable" in launcher
    assert "function Resolve-GitExecutable" in launcher
    assert "Add-ExecutableDirectoryToPath (Resolve-GitExecutable)" in launcher
    assert '$ErrorActionPreference = "Continue"' in launcher
    assert "2>&1" in launcher
    assert "-ExecutionPolicy Bypass" in launcher_wrapper


def test_migration_checksum_compatibility_is_exact_and_fail_closed():
    version, (recorded, current) = next(iter(KNOWN_CHECKSUM_TRANSITIONS.items()))

    assert migration_checksum_is_accepted(version, current, current) is True
    assert migration_checksum_is_accepted(version, recorded, current) is True
    assert migration_checksum_is_accepted(version, "0" * 64, current) is False
    assert migration_checksum_is_accepted("unknown_version", recorded, current) is False
    assert migration_checksum_is_accepted(version, current, recorded) is False


def test_ops_database_status_checks_schema_and_privileges_then_recovers_transaction():
    class PermissionBrokenDB:
        def __init__(self):
            self.statements: list[str] = []
            self.rolled_back = False

        def execute(self, statement, *_args, **_kwargs):
            rendered = str(statement)
            self.statements.append(rendered)
            if "FROM ops_jobs" in rendered:
                raise RuntimeError("permission denied for ops_jobs")
            return SimpleNamespace()

        def rollback(self):
            self.rolled_back = True

    db = PermissionBrokenDB()
    status, latency = ops_v2._database_status(db)

    assert status == "critical"
    assert latency is None
    assert db.rolled_back is True
    assert any("FROM branches" in statement for statement in db.statements)
    assert any("FROM courses" in statement for statement in db.statements)
    assert any("FROM users" in statement for statement in db.statements)
    assert any("FROM ops_jobs" in statement for statement in db.statements)


def test_job_stream_releases_request_session_before_streaming(monkeypatch):
    class ExistingJobDB:
        def __init__(self):
            self.closed = False

        def execute(self, *_args, **_kwargs):
            return SimpleNamespace(scalar=lambda: 1)

        def close(self):
            self.closed = True

    db = ExistingJobDB()
    monkeypatch.setattr(ops_v2, "require_ops_schema", lambda *_args: None)

    response = ops_v2._job_stream_response(uuid4(), SimpleNamespace(), db)

    assert response.media_type == "text/event-stream"
    assert db.closed is True


def test_job_stream_poll_uses_a_short_lived_session(monkeypatch):
    class FakeResult:
        def __init__(self, *, first=None, rows=None):
            self._first = first
            self._rows = rows or []

        def mappings(self):
            return self

        def first(self):
            return self._first

        def all(self):
            return self._rows

    class PollSession:
        def __init__(self):
            self.exited = False
            self.calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.exited = True

        def execute(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return FakeResult(
                    first={
                        "id": str(uuid4()),
                        "status": "running",
                        "progress": 25,
                        "error_code": None,
                        "error_message": None,
                        "updated_at": datetime.now(timezone.utc),
                        "finished_at": None,
                    }
                )
            return FakeResult(
                rows=[
                    {
                        "id": 3,
                        "log_level": "info",
                        "message": "still running",
                        "metadata": {},
                        "created_at": datetime.now(timezone.utc),
                    }
                ]
            )

    session = PollSession()
    monkeypatch.setattr(ops_v2, "SessionLocal", lambda: session)

    job, logs = ops_v2._read_job_stream_batch(str(uuid4()), 0)

    assert job is not None and job["status"] == "running"
    assert logs[0]["id"] == 3
    assert session.exited is True
