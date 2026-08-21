from __future__ import annotations

import argparse
import json
import os
import re
import sys
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterator


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.outbound_http import (  # noqa: E402
    OutboundRequestBlocked,
    OutboundResponseTooLarge,
    SafeSession,
    outbound_request_budget,
)


SCHEMA_VERSION = 1
YAML_PROVIDERS = {
    "AK_PLAZA",
    "ELAND_RETAIL",
    "GALLERIA",
    "HYUNDAI_DEPT",
    "LOTTE_MART",
    "SHINSEGAE_ACADEMY",
}
CULTURE_PROVIDERS = YAML_PROVIDERS | {"HOMEPLUS", "EMART", "LOTTE"}
MAX_PROBE_ROWS = 50
MAX_REQUEST_BUDGET = 50


CAPABILITIES: dict[str, dict[str, Any]] = {
    **{
        provider: {
            "course_collection": "bounded_collect_only_available",
            "registration_schedule": "blocked",
            "precision": "none",
            "live_probe": False,
            "reason_codes": ["MISSING_RECEPTION_SOURCE"],
            "limitations": [
                "현재 YAML collector는 접수 시작/종료 필드를 수집하지 않습니다.",
                "강좌 collect-only 경로는 SafeSession과 aggregate request budget으로 보호되지만 접수일정 근거가 없어 알림 준비 완료로 판정하지 않습니다.",
            ],
        }
        for provider in YAML_PROVIDERS
    },
    "HOMEPLUS": {
        "course_collection": "db_coupled",
        "registration_schedule": "branch_public_guide",
        "precision": "date",
        "live_probe": True,
        "requires": ["branch_code_or_name"],
        "limitations": [
            "지점 운영안내의 기존/신규 회원별 접수창을 원문으로 보존하지만 대표 날짜는 신규회원 행 하나로 평탄화됩니다.",
            "강좌 목록 전체 dry-run은 아직 persistence와 분리되지 않았습니다.",
        ],
    },
    "EMART": {
        "course_collection": "graphql_fragment",
        "registration_schedule": "graphql_register_dates",
        "precision": "date",
        "live_probe": True,
        "requires": ["EMART_GRAPHQL_API_KEY", "branch_code"],
        "limitations": [
            "API key가 없으면 Selenium 강좌 수집은 가능할 수 있어도 접수일 수집은 검증할 수 없습니다.",
            "프로브는 브라우저를 시작하지 않고 GraphQL 한 페이지만 조회합니다.",
        ],
    },
    "LOTTE": {
        "course_collection": "db_coupled_browser",
        "registration_schedule": "detail_text_partial",
        "precision": "date",
        "live_probe": False,
        "reason_codes": ["NO_COLLECT_ONLY_ENTRYPOINT", "BROWSER_REQUIRED"],
        "limitations": [
            "상세 페이지 텍스트에서는 접수일을 파싱하지만 현재 안전한 browser-free collect-only entrypoint가 없습니다.",
            "NetFunnel과 Selenium 탐색을 request budget 안에 넣기 전에는 live probe를 실행하지 않습니다.",
        ],
    },
}


class DatabaseAccessAttempt(RuntimeError):
    pass


def _block_database(*_args: Any, **_kwargs: Any) -> Any:
    raise DatabaseAccessAttempt("provider probe attempted database access")


@contextmanager
def database_access_guard(*modules: Any) -> Iterator[None]:
    """Fail closed if a supposedly read-only provider fragment reaches DB code."""

    from DB import db_utils

    replacements: list[tuple[Any, str, Any]] = []
    candidates = [(db_utils, "get_db_cursor")]
    for module in modules:
        for name in (
            "get_db_cursor",
            "mark_stale_courses",
            "delete_empty_branches_for_provider",
            "coalesce_provider_course_id_by_raw_url",
        ):
            if hasattr(module, name):
                candidates.append((module, name))
    try:
        for owner, name in candidates:
            original = getattr(owner, name)
            replacements.append((owner, name, original))
            setattr(owner, name, _block_database)
        yield
    finally:
        for owner, name, original in reversed(replacements):
            setattr(owner, name, original)


def _json_native(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_native(item) for item in value]
    adapted = getattr(value, "adapted", None)
    if adapted is not None:
        return _json_native(adapted)
    return str(value)


def _safe_error(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    for secret_name in ("EMART_GRAPHQL_API_KEY",):
        secret = os.getenv(secret_name, "").strip()
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(
        r"(?i)(api[_-]?key|token|secret|password)(\s*[=:]\s*)[^\s,;&]+",
        r"\1\2[REDACTED]",
        text,
    )
    return text[:500]


def _reason_for_exception(exc: BaseException) -> str:
    if isinstance(exc, DatabaseAccessAttempt):
        return "DB_ACCESS_ATTEMPT"
    if isinstance(exc, OutboundResponseTooLarge):
        return "RESPONSE_TOO_LARGE"
    if isinstance(exc, OutboundRequestBlocked):
        if "budget" in str(exc).lower():
            return "REQUEST_BUDGET_EXHAUSTED"
        return "UNTRUSTED_URL"
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    if "timeout" in name or "timeout" in message:
        return "UPSTREAM_TIMEOUT"
    if "maintenance" in message:
        return "UPSTREAM_MAINTENANCE"
    if "schema" in message or isinstance(exc, (KeyError, TypeError, ValueError)):
        return "SCHEMA_DRIFT"
    return "PROBE_FAILED"


def _base_result(provider: str, *, mode: str) -> dict[str, Any]:
    capability = _json_native(CAPABILITIES[provider])
    return {
        "schema_version": SCHEMA_VERSION,
        "provider": provider,
        "mode": mode,
        "status": "ready" if capability.get("live_probe") else "blocked",
        "reason_code": None
        if capability.get("live_probe")
        else (capability.get("reason_codes") or ["NO_COLLECT_ONLY_ENTRYPOINT"])[0],
        "complete": False,
        "db_write": False,
        "request_budget": 0,
        "rows": [],
        "summary": {
            "rows": 0,
            "apply_start_ready": 0,
            "apply_end_ready": 0,
            "apply_both_ready": 0,
        },
        "capability": capability,
        "warnings": [],
    }


def _finalize_rows(result: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    native_rows = [_json_native(row) for row in rows]
    result["rows"] = native_rows
    start_ready = sum(1 for row in native_rows if row.get("apply_start"))
    end_ready = sum(1 for row in native_rows if row.get("apply_end"))
    both_ready = sum(1 for row in native_rows if row.get("apply_start") and row.get("apply_end"))
    result["summary"] = {
        "rows": len(native_rows),
        "apply_start_ready": start_ready,
        "apply_end_ready": end_ready,
        "apply_both_ready": both_ready,
    }
    return result


def _probe_homeplus(
    *,
    branch_code: str,
    branch_name: str,
    timeout: int,
    request_budget: int,
    crawler_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    result = _base_result("HOMEPLUS", mode="live")
    result["request_budget"] = request_budget
    if not branch_code and not branch_name:
        result.update(status="blocked", reason_code="BRANCH_REQUIRED", complete=True)
        return result

    module = None
    crawler = None
    try:
        if crawler_factory is None:
            from Crawler import Crawler_Homeplus as module

            def crawler_factory() -> Any:
                return module.HomeplusCrawler(use_selenium=False)

        crawler = crawler_factory()
        if getattr(crawler, "session", None) is not None and isinstance(crawler.session, SafeSession):
            crawler.session.close()
            crawler.session = SafeSession(max_response_bytes=2 * 1024 * 1024, total_timeout_seconds=timeout)

        with (
            database_access_guard(*(item for item in (module,) if item is not None)),
            outbound_request_budget(request_budget),
        ):
            selected_code = branch_code
            selected_name = branch_name
            if not selected_code:
                stores = crawler.fetch_store_list()
                normalized_name = crawler._normalize_store_name(selected_name)
                store = next(
                    (row for row in stores if crawler._normalize_store_name(row.get("StoreName")) == normalized_name),
                    None,
                )
                if not store:
                    result.update(status="empty", reason_code="EMPTY_BRANCH_LIST", complete=True)
                    return result
                selected_code = str(store.get("StoreCode") or "").strip()
                selected_name = str(store.get("StoreName") or selected_name).strip()
            period = crawler.scrape_branch_reception_period(selected_code)

        if not period:
            result.update(status="empty", reason_code="MISSING_RECEPTION_SOURCE", complete=True)
            return result
        row = {
            "scope": "branch_reception",
            "branch_code": selected_code,
            "branch": selected_name or selected_code,
            "apply_start": period.get("apply_start"),
            "apply_end": period.get("apply_end"),
            "apply_period_raw": period.get("apply_period_raw"),
            "precision": "date",
        }
        _finalize_rows(result, [row])
        result.update(status="ok", reason_code=None, complete=True)
        if "/" in str(period.get("apply_period_raw") or ""):
            result["warnings"].append("MEMBER_SEGMENTS_FLATTENED")
        return result
    except Exception as exc:
        result.update(
            status="blocked" if isinstance(exc, (DatabaseAccessAttempt, OutboundRequestBlocked)) else "failed",
            reason_code=_reason_for_exception(exc),
            complete=False,
            error=_safe_error(exc),
        )
        return result
    finally:
        if crawler is not None and hasattr(crawler, "close"):
            crawler.close()


def _probe_emart(
    *,
    branch_code: str,
    limit: int,
    timeout: int,
    request_budget: int,
    crawler_factory: Callable[[], tuple[Any, Any]] | None = None,
) -> dict[str, Any]:
    result = _base_result("EMART", mode="live")
    result["request_budget"] = request_budget
    if not os.getenv("EMART_GRAPHQL_API_KEY", "").strip() and crawler_factory is None:
        result.update(status="blocked", reason_code="CREDENTIAL_MISSING", complete=True)
        return result
    if not branch_code:
        result.update(status="blocked", reason_code="BRANCH_REQUIRED", complete=True)
        return result

    module = None
    crawler = None
    try:
        if crawler_factory is None:
            from Crawler import Crawler_Emart as module

            crawler = module.EmartCrawler.__new__(module.EmartCrawler)
            crawler.config = module.PROVIDERS["EMART"]
            crawler.base_url = crawler.config["base_url"]
            crawler.http_session = SafeSession(
                max_response_bytes=4 * 1024 * 1024,
                total_timeout_seconds=timeout,
            )
            crawler.driver = None
            crawler.had_errors = False
            crawler.crawl_complete = True
        else:
            module, crawler = crawler_factory()

        with (
            database_access_guard(*(item for item in (module,) if item is not None)),
            outbound_request_budget(request_budget),
        ):
            payload = crawler._fetch_graphql_courses(branch_code, 0, limit) or {}
            raw_rows = payload.get("data") or []
            if not isinstance(raw_rows, list):
                raise ValueError("EMART GraphQL data must be an array")
            rows = []
            for raw_row in raw_rows[:limit]:
                if not isinstance(raw_row, dict):
                    continue
                normalized = crawler._course_data_from_graphql(raw_row, "probe-branch", branch_code)
                if normalized:
                    normalized.pop("branch_id", None)
                    rows.append(normalized)

        _finalize_rows(result, rows)
        if not rows:
            result.update(status="empty", reason_code="SCHEMA_DRIFT", complete=True)
        elif result["summary"]["apply_both_ready"] == 0:
            result.update(status="partial", reason_code="MISSING_RECEPTION_SOURCE", complete=True)
        else:
            result.update(status="ok", reason_code=None, complete=True)
        return result
    except Exception as exc:
        result.update(
            status="blocked" if isinstance(exc, (DatabaseAccessAttempt, OutboundRequestBlocked)) else "failed",
            reason_code=_reason_for_exception(exc),
            complete=False,
            error=_safe_error(exc),
        )
        return result
    finally:
        if crawler is not None:
            session = getattr(crawler, "http_session", None)
            if session is not None:
                session.close()
            driver = getattr(crawler, "driver", None)
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass


def probe_provider(
    provider: str,
    *,
    live: bool = False,
    limit: int = 5,
    branch_code: str = "",
    branch_name: str = "",
    timeout: int = 30,
    request_budget: int = 10,
    homeplus_factory: Callable[[], Any] | None = None,
    emart_factory: Callable[[], tuple[Any, Any]] | None = None,
) -> dict[str, Any]:
    provider = str(provider or "").strip().upper()
    if provider not in CULTURE_PROVIDERS:
        raise ValueError("unsupported culture provider")
    if not 1 <= limit <= MAX_PROBE_ROWS:
        raise ValueError(f"limit must be between 1 and {MAX_PROBE_ROWS}")
    if not 5 <= timeout <= 60:
        raise ValueError("timeout must be between 5 and 60")
    if not 1 <= request_budget <= MAX_REQUEST_BUDGET:
        raise ValueError(f"request_budget must be between 1 and {MAX_REQUEST_BUDGET}")
    for field_name, value in (("branch_code", branch_code), ("branch_name", branch_name)):
        if len(value) > 100 or any(ord(character) < 32 for character in value):
            raise ValueError(f"{field_name} must be at most 100 printable characters")

    if not live:
        result = _base_result(provider, mode="capability")
        result["complete"] = True
        return result
    if not CAPABILITIES[provider].get("live_probe"):
        result = _base_result(provider, mode="live")
        result["complete"] = True
        return result
    if provider == "HOMEPLUS":
        return _probe_homeplus(
            branch_code=branch_code.strip(),
            branch_name=branch_name.strip(),
            timeout=timeout,
            request_budget=request_budget,
            crawler_factory=homeplus_factory,
        )
    if provider == "EMART":
        return _probe_emart(
            branch_code=branch_code.strip(),
            limit=limit,
            timeout=timeout,
            request_budget=request_budget,
            crawler_factory=emart_factory,
        )
    raise AssertionError("unreachable provider probe adapter")


def capability_catalog() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "capability_catalog",
        "providers": [probe_provider(provider) for provider in sorted(CULTURE_PROVIDERS)],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only culture-provider registration schedule probe. Never writes crawler data to DB."
    )
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--provider", choices=sorted(CULTURE_PROVIDERS))
    selector.add_argument("--all", action="store_true", help="Print the capability catalog without network access")
    parser.add_argument("--live", action="store_true", help="Run the bounded provider fragment when supported")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--branch-code", default="")
    parser.add_argument("--branch-name", default="")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--request-budget", type=int, default=10)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.all:
        payload = capability_catalog()
    else:
        payload = probe_provider(
            args.provider,
            live=args.live,
            limit=args.limit,
            branch_code=args.branch_code,
            branch_name=args.branch_name,
            timeout=args.timeout,
            request_budget=args.request_budget,
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if isinstance(payload, dict) and payload.get("status") == "failed":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
