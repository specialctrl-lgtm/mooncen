from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass


LOTTE_MART_AGE_REGRESSION_URL = (
    "https://culture.lottemart.com/cu/gus/course/courseinfo/courseview.do"
    "?cls_cd=20260246832013&search_str_cd=468"
)
COURSE_SCOPE_LATENCY_BUDGET_MS = 8_000

SENSITIVE_QUERY_RE = re.compile(
    r"([?&](?:access_token|api_key|auth|code|credential|key|secret|state|token)=)[^&#\s]+",
    re.IGNORECASE,
)
TELEGRAM_TOKEN_PATH_RE = re.compile(r"/bot[^/\s]+/", re.IGNORECASE)


def redact_sensitive_text(value: Any) -> str:
    text = str(value or "")
    text = TELEGRAM_TOKEN_PATH_RE.sub("/bot<redacted>/", text)
    text = SENSITIVE_QUERY_RE.sub(r"\1<redacted>", text)
    for env_name in (
        "MOONCEN_BOT_TOKEN",
        "AUTH_SECRET",
        "DB_PASSWORD",
        "DB_API_PASSWORD",
        "DB_CRAWLER_PASSWORD",
    ):
        secret = os.getenv(env_name, "")
        if secret:
            text = text.replace(secret, "<redacted>")
    return text


class SkipCheck(Exception):
    pass


@dataclass
class CheckResult:
    name: str
    status: str
    required: bool
    duration_ms: int
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    trace: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in {"pass", "skip"}


class FunctionalTestContext:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.session = requests.Session()
        self.state: dict[str, Any] = {}

    def make_url(self, base_url: str, path: str) -> str:
        return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))

    def get_json(self, base_url: str, path: str) -> tuple[dict[str, Any] | list[Any], int, str]:
        url = self.make_url(base_url, path)
        response = self.session.get(url, timeout=self.args.timeout)
        content_type = response.headers.get("content-type", "")
        try:
            payload = response.json()
        except Exception as exc:
            raise AssertionError(f"{url} did not return JSON: {exc}") from exc
        if response.status_code != 200:
            raise AssertionError(f"{url} returned HTTP {response.status_code}: {payload}")
        return payload, response.status_code, content_type

    def get_text(self, base_url: str, path: str) -> tuple[str, int, str]:
        url = self.make_url(base_url, path)
        response = self.session.get(url, timeout=self.args.timeout)
        if response.status_code >= 400:
            raise AssertionError(f"{url} returned HTTP {response.status_code}")
        return response.text, response.status_code, response.headers.get("content-type", "")


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def public_base_url() -> str:
    return (
        os.getenv("MOONCEN_FUNCTIONAL_TEST_BASE_URL")
        or os.getenv("VITE_SITE_URL")
        or os.getenv("SITE_URL")
        or "https://mooncen.kr"
    ).rstrip("/")


def internal_api_url() -> str:
    return (
        os.getenv("MOONCEN_FUNCTIONAL_TEST_INTERNAL_API_URL")
        or "http://127.0.0.1:8001"
    ).rstrip("/")


def check_internal_api_health(ctx: FunctionalTestContext) -> dict[str, Any]:
    payload, status_code, content_type = ctx.get_json(ctx.args.internal_api_url, "/health")
    if not isinstance(payload, dict) or payload.get("status") != "ready":
        raise AssertionError(f"unexpected health payload: {payload}")
    return {
        "url": ctx.make_url(ctx.args.internal_api_url, "/health"),
        "http_status": status_code,
        "content_type": content_type,
        "environment": payload.get("environment"),
    }


def check_public_api_health(ctx: FunctionalTestContext) -> dict[str, Any]:
    payload, status_code, content_type = ctx.get_json(ctx.args.base_url, "/health")
    if not isinstance(payload, dict) or payload.get("status") != "ready":
        raise AssertionError(f"unexpected public health payload: {payload}")
    return {
        "url": ctx.make_url(ctx.args.base_url, "/health"),
        "http_status": status_code,
        "content_type": content_type,
        "environment": payload.get("environment"),
    }


def check_frontend_root(ctx: FunctionalTestContext) -> dict[str, Any]:
    text, status_code, content_type = ctx.get_text(ctx.args.base_url, "/")
    if '<div id="root"' not in text:
        raise AssertionError("frontend root marker was not found")
    return {
        "url": ctx.make_url(ctx.args.base_url, "/"),
        "http_status": status_code,
        "content_type": content_type,
        "bytes": len(text.encode("utf-8")),
    }


def check_provider_api(ctx: FunctionalTestContext) -> dict[str, Any]:
    payload, status_code, _content_type = ctx.get_json(ctx.args.internal_api_url, "/api/branches/providers")
    if not isinstance(payload, list) or not payload:
        raise AssertionError("provider API returned an empty payload")
    active_course_count = sum(int(row.get("active_course_count") or 0) for row in payload if isinstance(row, dict))
    if active_course_count < ctx.args.min_active_courses:
        raise AssertionError(
            f"active_course_count too low: {active_course_count} < {ctx.args.min_active_courses}"
        )
    return {
        "http_status": status_code,
        "provider_count": len(payload),
        "active_course_count": active_course_count,
        "sample_providers": [row.get("provider") for row in payload[:5] if isinstance(row, dict)],
    }


def check_course_list_api(ctx: FunctionalTestContext) -> dict[str, Any]:
    payload, status_code, _content_type = ctx.get_json(ctx.args.internal_api_url, "/api/courses/?size=5")
    if not isinstance(payload, dict):
        raise AssertionError("course list API did not return an object")
    total = int(payload.get("total") or 0)
    items = payload.get("items") or []
    if total < ctx.args.min_active_courses:
        raise AssertionError(f"course total too low: {total} < {ctx.args.min_active_courses}")
    if not isinstance(items, list) or not items:
        raise AssertionError("course list API returned no items")
    first = items[0]
    for key in ("id", "provider", "title", "raw_url"):
        if not first.get(key):
            raise AssertionError(f"first course is missing {key}: {first}")
    ctx.state["sample_course_id"] = first["id"]
    ctx.state["sample_course_title"] = first.get("title")
    return {
        "http_status": status_code,
        "total": total,
        "sample_course_id": first["id"],
        "sample_provider": first.get("provider"),
        "sample_title": first.get("title"),
    }


def check_course_scope_api(ctx: FunctionalTestContext, scope: str) -> dict[str, Any]:
    path = (
        f"/api/courses/?page=1&size=30&scope={scope}"
        "&statuses=OPEN%2CDEADLINE&exclude_unavailable=true&sort=latest"
    )
    started = time.perf_counter()
    payload, status_code, _content_type = ctx.get_json(ctx.args.internal_api_url, path)
    duration_ms = int((time.perf_counter() - started) * 1000)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise AssertionError(f"{scope} course scope API returned an invalid payload")
    if duration_ms > COURSE_SCOPE_LATENCY_BUDGET_MS:
        raise AssertionError(
            f"{scope} course scope API exceeded latency budget: "
            f"{duration_ms}ms > {COURSE_SCOPE_LATENCY_BUDGET_MS}ms"
        )
    return {
        "http_status": status_code,
        "scope": scope,
        "total": int(payload.get("total") or 0),
        "returned_items": len(payload["items"]),
        "duration_ms": duration_ms,
        "latency_budget_ms": COURSE_SCOPE_LATENCY_BUDGET_MS,
    }


def check_course_scope_provider(ctx: FunctionalTestContext) -> dict[str, Any]:
    return check_course_scope_api(ctx, "provider")


def check_course_scope_experience(ctx: FunctionalTestContext) -> dict[str, Any]:
    return check_course_scope_api(ctx, "experience")


def check_course_scope_education(ctx: FunctionalTestContext) -> dict[str, Any]:
    return check_course_scope_api(ctx, "education")


def check_course_detail_api(ctx: FunctionalTestContext) -> dict[str, Any]:
    course_id = ctx.state.get("sample_course_id")
    if not course_id:
        raise SkipCheck("course list did not provide a sample course id")
    payload, status_code, _content_type = ctx.get_json(ctx.args.internal_api_url, f"/api/courses/{course_id}")
    if not isinstance(payload, dict):
        raise AssertionError("course detail API did not return an object")
    if str(payload.get("id")) != str(course_id):
        raise AssertionError(f"course detail id mismatch: {payload.get('id')} != {course_id}")
    return {
        "http_status": status_code,
        "course_id": course_id,
        "provider": payload.get("provider"),
        "title": payload.get("title"),
        "has_branch": bool(payload.get("branch")),
    }


def check_age_filter_api(ctx: FunctionalTestContext) -> dict[str, Any]:
    payload, status_code, _content_type = ctx.get_json(
        ctx.args.internal_api_url,
        "/api/courses/?child_age_months=24&size=10",
    )
    if not isinstance(payload, dict):
        raise AssertionError("age filter API did not return an object")
    items = payload.get("items") or []
    if not isinstance(items, list):
        raise AssertionError("age filter API items is not a list")
    return {
        "http_status": status_code,
        "total": int(payload.get("total") or 0),
        "returned_items": len(items),
        "sample_ids": [item.get("id") for item in items[:3] if isinstance(item, dict)],
    }


def _get_db_cursor():
    from DB.db_utils import get_db_cursor

    return get_db_cursor()


def check_database_counts(ctx: FunctionalTestContext) -> dict[str, Any]:
    if ctx.args.no_db:
        raise SkipCheck("database checks disabled by --no-db")
    with _get_db_cursor() as cur:
        cur.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM courses WHERE COALESCE(is_active, true) IS TRUE) AS active_courses,
                (SELECT COUNT(*) FROM branches) AS branches,
                (SELECT COUNT(*) FROM branches WHERE lat IS NOT NULL AND lon IS NOT NULL) AS branches_with_coordinates,
                (SELECT COALESCE(MAX(updated_at)::text, '') FROM courses) AS latest_course_update
            """
        )
        row = dict(cur.fetchone())
    active_courses = int(row["active_courses"] or 0)
    branches = int(row["branches"] or 0)
    if active_courses < ctx.args.min_active_courses:
        raise AssertionError(f"active course count too low: {active_courses} < {ctx.args.min_active_courses}")
    if branches < ctx.args.min_branches:
        raise AssertionError(f"branch count too low: {branches} < {ctx.args.min_branches}")
    return row


def check_lotte_mart_age_regression_db(ctx: FunctionalTestContext) -> dict[str, Any]:
    if ctx.args.no_db:
        raise SkipCheck("database checks disabled by --no-db")
    with _get_db_cursor() as cur:
        cur.execute(
            """
            SELECT id, provider_course_id, title, target, target_age_group,
                   target_min_age, target_max_age, target_age_is_explicit, is_active
            FROM courses
            WHERE raw_url = %s
            LIMIT 1
            """,
            (LOTTE_MART_AGE_REGRESSION_URL,),
        )
        row = cur.fetchone()
        if not row:
            raise SkipCheck("known Lotte Mart age regression row is not present")
        row = dict(row)
        cur.execute(
            """
            SELECT
                EXISTS (
                    SELECT 1 FROM courses
                    WHERE raw_url = %s
                      AND (target_min_age IS NOT NULL OR target_max_age IS NOT NULL)
                      AND (target_min_age IS NULL OR target_min_age <= 24)
                      AND (target_max_age IS NULL OR target_max_age >= 24)
                ) AS match_24_months,
                EXISTS (
                    SELECT 1 FROM courses
                    WHERE raw_url = %s
                      AND (target_min_age IS NOT NULL OR target_max_age IS NOT NULL)
                      AND (target_min_age IS NULL OR target_min_age <= 48)
                      AND (target_max_age IS NULL OR target_max_age >= 48)
                ) AS match_48_months,
                EXISTS (
                    SELECT 1 FROM courses
                    WHERE raw_url = %s
                      AND (target_min_age IS NOT NULL OR target_max_age IS NOT NULL)
                      AND (target_min_age IS NULL OR target_min_age <= 84)
                      AND (target_max_age IS NULL OR target_max_age >= 84)
                ) AS match_84_months
            """,
            (LOTTE_MART_AGE_REGRESSION_URL, LOTTE_MART_AGE_REGRESSION_URL, LOTTE_MART_AGE_REGRESSION_URL),
        )
        matches = dict(cur.fetchone())
    expected = {
        "target": "4~6세",
        "target_min_age": 48,
        "target_max_age": 83,
        "target_age_is_explicit": True,
    }
    mismatches = {
        key: {"expected": value, "actual": row.get(key)}
        for key, value in expected.items()
        if row.get(key) != value
    }
    if mismatches:
        raise AssertionError(f"Lotte Mart age regression values changed: {mismatches}")
    if matches["match_24_months"] or not matches["match_48_months"] or matches["match_84_months"]:
        raise AssertionError(f"Lotte Mart age regression filter result is wrong: {matches}")
    ctx.state["lotte_mart_regression_course_id"] = str(row["id"])
    return {**row, **matches}


def check_lotte_mart_age_regression_api(ctx: FunctionalTestContext) -> dict[str, Any]:
    course_id = ctx.state.get("lotte_mart_regression_course_id")
    if not course_id:
        raise SkipCheck("known Lotte Mart age regression row was not available from DB")
    payload, status_code, _content_type = ctx.get_json(ctx.args.internal_api_url, f"/api/courses/{course_id}")
    expected = {
        "target": "4~6세",
        "target_min_age": 48,
        "target_max_age": 83,
        "target_age_is_explicit": True,
    }
    mismatches = {
        key: {"expected": value, "actual": payload.get(key)}
        for key, value in expected.items()
        if payload.get(key) != value
    }
    if mismatches:
        raise AssertionError(f"Lotte Mart age regression API values changed: {mismatches}")
    return {
        "http_status": status_code,
        "course_id": course_id,
        "provider_course_id": payload.get("provider_course_id"),
        "title": payload.get("title"),
        **{key: payload.get(key) for key in expected},
    }


CHECKS: list[tuple[str, Callable[[FunctionalTestContext], dict[str, Any]], bool]] = [
    ("internal_api_health", check_internal_api_health, True),
    ("public_api_health", check_public_api_health, True),
    ("frontend_root", check_frontend_root, True),
    ("database_counts", check_database_counts, True),
    ("provider_api", check_provider_api, True),
    ("course_list_api", check_course_list_api, True),
    ("course_scope_provider", check_course_scope_provider, True),
    ("course_scope_experience", check_course_scope_experience, True),
    ("course_scope_education", check_course_scope_education, True),
    ("course_detail_api", check_course_detail_api, True),
    ("age_filter_api", check_age_filter_api, True),
    ("lotte_mart_age_regression_db", check_lotte_mart_age_regression_db, True),
    ("lotte_mart_age_regression_api", check_lotte_mart_age_regression_api, True),
]


def run_check(
    name: str,
    fn: Callable[[FunctionalTestContext], dict[str, Any]],
    required: bool,
    ctx: FunctionalTestContext,
) -> CheckResult:
    started = time.perf_counter()
    try:
        details = fn(ctx)
        status = "pass"
        error = None
        trace = None
    except SkipCheck as exc:
        details = {}
        status = "skip"
        error = redact_sensitive_text(exc)
        trace = None
    except Exception as exc:
        details = {}
        status = "fail"
        error = redact_sensitive_text(exc)
        trace = redact_sensitive_text(traceback.format_exc())
    duration_ms = int((time.perf_counter() - started) * 1000)
    return CheckResult(
        name=name,
        status=status,
        required=required,
        duration_ms=duration_ms,
        details=details,
        error=error,
        trace=trace,
    )


def write_report(report: dict[str, Any], report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = report_dir / f"functional_test_{stamp}.json"
    payload = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    report_path.write_text(payload + "\n", encoding="utf-8")
    (report_dir / "latest.json").write_text(payload + "\n", encoding="utf-8")
    return report_path


def notify_telegram(report: dict[str, Any], report_path: Path | None, timeout: int) -> None:
    if report.get("ok"):
        return
    token = os.getenv("MOONCEN_BOT_TOKEN", "").strip()
    chat_id = os.getenv("MOONCEN_BOT_CHAT_ID", "").strip()
    if not token or not chat_id:
        return
    failed = [check for check in report.get("checks", []) if check.get("status") == "fail"]
    lines = [
        "MoonCen functional test failed",
        f"failed={len(failed)} total={report.get('summary', {}).get('total')}",
        f"base_url={report.get('base_url')}",
    ]
    if report_path:
        lines.append(f"report={report_path}")
    for check in failed[:5]:
        lines.append(f"- {check.get('name')}: {check.get('error')}")
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": "\n".join(lines)},
            timeout=timeout,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        status_text = f" status={status}" if status is not None else ""
        raise RuntimeError(f"Telegram notification failed type={type(exc).__name__}{status_text}") from None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run MoonCen production functional tests.")
    parser.add_argument("--base-url", default=public_base_url(), help="Public site base URL.")
    parser.add_argument("--internal-api-url", default=internal_api_url(), help="Internal API base URL.")
    parser.add_argument("--timeout", type=int, default=int(os.getenv("MOONCEN_FUNCTIONAL_TEST_TIMEOUT", "10")))
    parser.add_argument(
        "--report-dir",
        default=os.getenv("MOONCEN_FUNCTIONAL_TEST_REPORT_DIR", "logs/functional_tests"),
        help="Directory for JSON reports. Relative paths are resolved from the project root.",
    )
    parser.add_argument(
        "--min-active-courses",
        type=int,
        default=int(os.getenv("MOONCEN_FUNCTIONAL_TEST_MIN_ACTIVE_COURSES", "100")),
    )
    parser.add_argument(
        "--min-branches",
        type=int,
        default=int(os.getenv("MOONCEN_FUNCTIONAL_TEST_MIN_BRANCHES", "10")),
    )
    parser.add_argument("--no-db", action="store_true", help="Skip direct database checks.")
    parser.add_argument("--no-notify", action="store_true", help="Do not send Telegram failure notifications.")
    parser.add_argument("--json", action="store_true", help="Print the full JSON report to stdout.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report_dir = Path(args.report_dir)
    if not report_dir.is_absolute():
        report_dir = PROJECT_ROOT / report_dir

    ctx = FunctionalTestContext(args)
    started_monotonic = time.perf_counter()
    started_at = iso_now()
    results = [run_check(name, fn, required, ctx) for name, fn, required in CHECKS]
    finished_at = iso_now()
    failed_required = [result for result in results if result.required and result.status == "fail"]
    report = {
        "ok": not failed_required,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": round(time.perf_counter() - started_monotonic, 3),
        "base_url": args.base_url,
        "internal_api_url": args.internal_api_url,
        "summary": {
            "total": len(results),
            "passed": sum(1 for result in results if result.status == "pass"),
            "failed": sum(1 for result in results if result.status == "fail"),
            "skipped": sum(1 for result in results if result.status == "skip"),
            "required_failed": len(failed_required),
        },
        "checks": [
            {
                "name": result.name,
                "status": result.status,
                "required": result.required,
                "duration_ms": result.duration_ms,
                "details": result.details,
                "error": result.error,
                "trace": result.trace,
            }
            for result in results
        ],
    }
    report_path = write_report(report, report_dir)

    if not args.no_notify:
        try:
            notify_telegram(report, report_path, args.timeout)
        except Exception as exc:
            report["notification_error"] = redact_sensitive_text(exc)
            write_report(report, report_dir)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        status = "PASS" if report["ok"] else "FAIL"
        summary = report["summary"]
        print(
            f"functional_test={status} "
            f"passed={summary['passed']} failed={summary['failed']} "
            f"skipped={summary['skipped']} report={report_path}"
        )
        for result in results:
            suffix = f" error={result.error}" if result.error else ""
            print(f"{result.status.upper():4} {result.name} {result.duration_ms}ms{suffix}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
