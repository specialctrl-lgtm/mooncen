from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from tools import probe_culture_provider as probe


def test_capability_catalog_covers_every_culture_provider_without_live_collection() -> None:
    payload = probe.capability_catalog()
    rows = payload["providers"]

    assert {row["provider"] for row in rows} == probe.CULTURE_PROVIDERS
    assert len(rows) == 9
    assert all(row["mode"] == "capability" for row in rows)
    assert all(row["db_write"] is False for row in rows)
    assert all(row["complete"] is True for row in rows)


@pytest.mark.parametrize("provider", sorted(probe.YAML_PROVIDERS | {"LOTTE"}))
def test_unsafe_or_unseparated_collectors_are_explicitly_blocked(provider: str) -> None:
    payload = probe.probe_provider(provider, live=True)

    assert payload["status"] == "blocked"
    assert payload["reason_code"] in {
        "MISSING_RECEPTION_SOURCE",
        "NO_COLLECT_ONLY_ENTRYPOINT",
    }
    assert payload["rows"] == []
    assert payload["db_write"] is False


def test_homeplus_live_probe_requires_an_explicit_branch_before_factory_use() -> None:
    called = False

    def factory() -> Any:
        nonlocal called
        called = True
        raise AssertionError("factory must not run")

    payload = probe.probe_provider("HOMEPLUS", live=True, homeplus_factory=factory)

    assert payload["status"] == "blocked"
    assert payload["reason_code"] == "BRANCH_REQUIRED"
    assert called is False


def test_homeplus_live_probe_collects_branch_reception_without_db_write() -> None:
    class FakeHomeplus:
        session = None

        def __init__(self) -> None:
            self.closed = False

        @staticmethod
        def scrape_branch_reception_period(branch_code: str) -> dict[str, Any]:
            assert branch_code == "001"
            return {
                "apply_start": date(2026, 8, 1),
                "apply_end": date(2026, 8, 7),
                "apply_period_raw": "기존회원: 2026.07.30-2026.08.07 / 신규회원: 2026.08.01-2026.08.07",
            }

        def close(self) -> None:
            self.closed = True

    crawler = FakeHomeplus()
    payload = probe.probe_provider(
        "HOMEPLUS",
        live=True,
        branch_code="001",
        branch_name="테스트점",
        homeplus_factory=lambda: crawler,
    )

    assert payload["status"] == "ok"
    assert payload["reason_code"] is None
    assert payload["summary"] == {
        "rows": 1,
        "apply_start_ready": 1,
        "apply_end_ready": 1,
        "apply_both_ready": 1,
    }
    assert payload["rows"][0]["apply_start"] == "2026-08-01"
    assert payload["rows"][0]["precision"] == "date"
    assert "MEMBER_SEGMENTS_FLATTENED" in payload["warnings"]
    assert crawler.closed is True


def test_database_guard_turns_any_probe_db_access_into_a_stable_block_reason() -> None:
    class BadHomeplus:
        session = None

        @staticmethod
        def scrape_branch_reception_period(_branch_code: str) -> dict[str, Any]:
            from DB.db_utils import get_db_cursor

            get_db_cursor()
            return {}

        @staticmethod
        def close() -> None:
            return None

    payload = probe.probe_provider(
        "HOMEPLUS",
        live=True,
        branch_code="001",
        homeplus_factory=BadHomeplus,
    )

    assert payload["status"] == "blocked"
    assert payload["reason_code"] == "DB_ACCESS_ATTEMPT"
    assert "database access" in payload["error"]


def test_emart_probe_reports_missing_credential_without_importing_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EMART_GRAPHQL_API_KEY", raising=False)

    payload = probe.probe_provider("EMART", live=True, branch_code="1001")

    assert payload["status"] == "blocked"
    assert payload["reason_code"] == "CREDENTIAL_MISSING"
    assert payload["rows"] == []


def test_emart_graphql_fragment_is_json_native_and_keeps_register_dates() -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class FakeEmart:
        def __init__(self) -> None:
            self.http_session = FakeSession()
            self.driver = None

        @staticmethod
        def _fetch_graphql_courses(branch_code: str, offset: int, size: int) -> dict[str, Any]:
            assert (branch_code, offset, size) == ("1001", 0, 2)
            return {"data": [{"classId": "A"}, {"classId": "B"}], "total": 2}

        @staticmethod
        def _course_data_from_graphql(row: dict[str, Any], branch_id: str, branch_code: str) -> dict[str, Any]:
            assert branch_id == "probe-branch"
            return {
                "branch_id": branch_id,
                "provider": "EMART",
                "provider_course_id": f"{branch_code}:{row['classId']}",
                "title": row["classId"],
                "apply_start": date(2026, 8, 1),
                "apply_end": date(2026, 8, 7),
            }

    module = SimpleNamespace(get_db_cursor=lambda: (_ for _ in ()).throw(AssertionError("must be guarded")))
    crawler = FakeEmart()
    payload = probe.probe_provider(
        "EMART",
        live=True,
        branch_code="1001",
        limit=2,
        emart_factory=lambda: (module, crawler),
    )

    assert payload["status"] == "ok"
    assert payload["summary"]["apply_both_ready"] == 2
    assert payload["rows"][0]["apply_start"] == "2026-08-01"
    assert "branch_id" not in payload["rows"][0]
    assert crawler.http_session.closed is True


def test_probe_errors_redact_emart_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "test-secret-api-key-value"
    monkeypatch.setenv("EMART_GRAPHQL_API_KEY", secret)

    class BrokenEmart:
        http_session = SimpleNamespace(close=lambda: None)
        driver = None

        @staticmethod
        def _fetch_graphql_courses(*_args: Any) -> dict[str, Any]:
            raise RuntimeError(f"api_key={secret}")

    payload = probe.probe_provider(
        "EMART",
        live=True,
        branch_code="1001",
        emart_factory=lambda: (SimpleNamespace(), BrokenEmart()),
    )

    assert payload["status"] == "failed"
    assert secret not in payload["error"]
    assert "[REDACTED]" in payload["error"]


def test_cli_all_prints_structured_catalog(capsys: pytest.CaptureFixture[str]) -> None:
    assert probe.main(["--all"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["schema_version"] == 1
    assert payload["mode"] == "capability_catalog"
    assert len(payload["providers"]) == 9


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": 0},
        {"limit": 51},
        {"timeout": 4},
        {"request_budget": 0},
        {"branch_code": "bad\nvalue"},
    ],
)
def test_probe_bounds_are_fail_closed(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        probe.probe_provider("HOMEPLUS", **kwargs)
