from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

import psycopg2
import yaml
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from DB.connection_settings import database_connect_options


TARGET_DIR = ROOT / "config" / "crawl_targets"
FACILITY_REGISTRY = ROOT / "config" / "facility_registry_crawl_targets.yaml"
CRAWLER_REPORT_DIR = ROOT / "logs" / "municipal_crawler_reports"
OUTPUT_DIR = ROOT / "logs" / "target_collection_audit"

DEFAULT_SOURCE_GROUPS = ("museum_science", "library")
EXCLUDED_TARGET_FILES = {"deprecated.yaml", "index.yaml"}
FACILITY_CATEGORIES = {
    "문화기반시설/공공도서관",
    "문화기반시설/국립도서관",
    "문화기반시설/미술관",
    "문화기반시설/박물관",
}
RUNNABLE_STATUSES = {"ready", "partial", "generated", "candidate"}
REPORT_TIMEZONE = ZoneInfo("Asia/Seoul")
FIELD_KEYS = ("target", "fee", "date", "place", "category", "time")
FIELD_LABELS = {
    "target": "대상",
    "fee": "요금",
    "date": "날짜",
    "place": "장소",
    "category": "분야",
    "time": "시간",
}
COLLECTION_STATUS_LABELS = {
    "collected": "수집",
    "collected_field_gap": "수집/필드부족",
    "collected_recent_failure": "수집/최근실패",
    "validated_not_persisted": "수집검증/미저장",
    "stale": "수집/오래됨",
    "no_current_data": "현재자료없음",
    "failed": "수집실패",
    "not_collected": "미수집",
    "blocked": "수집불가",
}
FACILITY_STATUS_LABELS = {
    "collected": "수집",
    "configured_no_active": "대상연결/활성자료없음",
    "site_connected_unverified": "사이트연결/지점미확인",
    "crawler_target_needed": "수집대상필요",
    "url_discovery_needed": "URL탐색필요",
}
SHARED_HOSTING_DOMAINS = {
    "blog.daum.net",
    "blog.naver.com",
    "cafe.daum.net",
    "cafe.naver.com",
    "facebook.com",
    "instagram.com",
    "sites.google.com",
}
INSTITUTION_NAME_SUFFIXES = (
    "도서관",
    "미술관",
    "박물관",
    "과학관",
    "문화관",
    "문화회관",
)
SCOPE_INSTITUTION_KEYWORDS = ("도서관", "박물관", "미술관", "과학관")


def clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def normalized_name(value: Any) -> str:
    text = unicodedata.normalize("NFKC", clean(value)).casefold()
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def canonical_url_key(value: Any) -> str:
    raw = clean(value)
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    host = (parsed.hostname or "").casefold()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return ""
    path = re.sub(r"/+", "/", unquote(parsed.path or "/")).rstrip("/")
    return f"{host}{path if path and path != '/' else ''}"


def canonical_site_key(value: Any) -> str:
    key = canonical_url_key(value)
    if not key:
        return ""
    host, separator, path = key.partition("/")
    if host not in SHARED_HOSTING_DOMAINS or not separator:
        return host
    account = path.split("/", 1)[0]
    return f"{host}/{account}" if account else host


def url_keys_overlap(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    left_host, _, left_path = left.partition("/")
    right_host, _, right_path = right.partition("/")
    if left_host != right_host or not left_path or not right_path:
        return False
    return left_path.startswith(f"{right_path}/") or right_path.startswith(f"{left_path}/")


def institution_names_overlap(left: Any, right: Any) -> bool:
    left_key = normalized_name(left)
    right_key = normalized_name(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    shorter, longer = sorted((left_key, right_key), key=len)
    if len(shorter) < 5:
        return False
    if not any(shorter.endswith(normalized_name(suffix)) for suffix in INSTITUTION_NAME_SUFFIXES):
        return False
    return longer.endswith(shorter)


def percentage(count: int, total: int) -> float:
    return round((count / total) * 100, 1) if total else 0.0


def iter_values(value: Any) -> Iterable[str]:
    if isinstance(value, (list, tuple, set)):
        for item in value:
            text = clean(item)
            if text:
                yield text
        return
    text = clean(value)
    if text:
        yield text


def target_urls(target: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for key in ("url", "main_url", "base_url", "list_url", "list_urls", "homepage_urls"):
        values.update(iter_values(target.get(key)))
    return values


def is_duplicate_target_alias(target: dict[str, Any]) -> bool:
    status = clean(target.get("crawler_status") or target.get("status")).lower()
    return bool(
        clean(target.get("duplicate_of"))
        or status.startswith("duplicate")
        or clean(target.get("collection_type")).lower() == "duplicate"
    )


def target_matches_related_scope(target: dict[str, Any]) -> bool:
    text = " ".join(
        clean(target.get(key))
        for key in ("name", "branch", "provider_organizer")
    )
    return any(keyword in text for keyword in SCOPE_INSTITUTION_KEYWORDS)


def load_operational_targets(
    source_groups: Iterable[str],
    *,
    include_related_targets: bool = True,
) -> tuple[dict[str, dict[str, Any]], int]:
    providers: dict[str, dict[str, Any]] = {}
    target_rows = 0
    selected_filenames = {
        source_group if source_group.endswith(".yaml") else f"{source_group}.yaml"
        for source_group in source_groups
    }
    for filename in selected_filenames:
        path = TARGET_DIR / filename
        if not path.exists():
            raise FileNotFoundError(f"Target registry not found: {path}")
    candidate_paths = (
        sorted(
            path
            for path in TARGET_DIR.glob("*.yaml")
            if path.name not in EXCLUDED_TARGET_FILES
        )
        if include_related_targets
        else sorted(TARGET_DIR / filename for filename in selected_filenames)
    )
    for path in candidate_paths:
        filename = path.name
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for target in document.get("targets") or []:
            if not isinstance(target, dict):
                continue
            if is_duplicate_target_alias(target):
                continue
            if filename not in selected_filenames and not target_matches_related_scope(
                target
            ):
                continue
            provider = clean(target.get("provider")).upper()
            if not provider:
                continue
            target_rows += 1
            row = providers.setdefault(
                provider,
                {
                    "provider": provider,
                    "names": set(),
                    "branches": set(),
                    "urls": set(),
                    "source_groups": set(),
                    "categories": set(),
                    "registry_statuses": set(),
                    "target_count": 0,
                    "last_quality": [],
                },
            )
            row["target_count"] += 1
            row["names"].update(iter_values(target.get("name")))
            row["branches"].update(iter_values(target.get("branch")))
            row["urls"].update(target_urls(target))
            row["source_groups"].add(clean(target.get("source_group")) or Path(filename).stem)
            row["categories"].update(
                filter(
                    None,
                    (
                        clean(target.get("collection_category")),
                        clean(target.get("domain_category")),
                    ),
                )
            )
            row["registry_statuses"].add(
                clean(target.get("crawler_status") or target.get("status")).lower() or "unknown"
            )
            quality = target.get("last_quality")
            if isinstance(quality, dict):
                row["last_quality"].append(quality)
    return providers, target_rows


def connect():
    load_dotenv(ROOT / ".env")
    host = os.getenv("DB_HOST", "localhost")
    return psycopg2.connect(
        host=host,
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "mooncen"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        cursor_factory=RealDictCursor,
        **database_connect_options(host, "mooncen-target-collection-audit"),
    )


def fetch_provider_stats(connection, providers: list[str]) -> dict[str, dict[str, Any]]:
    if not providers:
        return {}
    query = """
        SELECT
            c.provider,
            COUNT(*) AS active_courses,
            COUNT(DISTINCT c.branch_id) AS branch_count,
            COUNT(DISTINCT NULLIF(btrim(c.venue_name), '')) AS venue_count,
            COUNT(*) FILTER (
                WHERE NULLIF(btrim(c.target), '') IS NOT NULL
                   OR NULLIF(btrim(c.target_age_group), '') IS NOT NULL
                   OR c.target_min_age IS NOT NULL
                   OR c.target_max_age IS NOT NULL
                   OR NULLIF(btrim(c.raw_fields->>'target'), '') IS NOT NULL
            ) AS target_count,
            COUNT(*) FILTER (
                WHERE c.fee IS NOT NULL
                   OR NULLIF(
                        btrim(
                            COALESCE(
                                c.raw_fields->>'fee',
                                c.raw_fields->>'fee_raw',
                                c.raw_fields->>'source_fee',
                                ''
                            )
                        ),
                        ''
                      ) IS NOT NULL
            ) AS fee_count,
            COUNT(*) FILTER (
                WHERE c.start_date IS NOT NULL
                   OR c.end_date IS NOT NULL
                   OR COALESCE(c.schedule_raw, '') ~ '(상시|연중)'
                   OR NULLIF(
                        btrim(COALESCE(c.raw_fields->>'period', c.raw_fields->>'date', '')),
                        ''
                      ) IS NOT NULL
            ) AS date_count,
            COUNT(*) FILTER (
                WHERE NULLIF(btrim(c.venue_name), '') IS NOT NULL
                   OR NULLIF(btrim(c.venue_address), '') IS NOT NULL
                   OR NULLIF(btrim(b.name), '') IS NOT NULL
                   OR NULLIF(
                        btrim(
                            COALESCE(
                                c.raw_fields->>'venue_name',
                                c.raw_fields->>'place',
                                c.raw_fields->>'room',
                                c.raw_fields->>'location',
                                ''
                            )
                        ),
                        ''
                      ) IS NOT NULL
            ) AS place_count,
            COUNT(*) FILTER (
                WHERE NULLIF(btrim(c.standard_category_label), '') IS NOT NULL
                   OR NULLIF(btrim(c.category_raw), '') IS NOT NULL
                   OR NULLIF(btrim(c.collection_category), '') IS NOT NULL
                   OR NULLIF(btrim(c.domain_category), '') IS NOT NULL
                   OR NULLIF(btrim(c.service_group), '') IS NOT NULL
                   OR NULLIF(btrim(c.raw_fields->>'category'), '') IS NOT NULL
            ) AS category_count,
            COUNT(*) FILTER (
                WHERE (c.schedule_time_start IS NOT NULL AND c.schedule_time_start <> TIME '00:00')
                   OR (c.schedule_time_end IS NOT NULL AND c.schedule_time_end <> TIME '00:00')
                   OR regexp_replace(COALESCE(c.schedule_raw, ''), '00:00', '', 'g')
                      ~ '([0-1]?[0-9]|2[0-3]):[0-5][0-9]'
                   OR NULLIF(
                        btrim(COALESCE(c.raw_fields->>'schedule', c.raw_fields->>'time', '')),
                        ''
                      ) IS NOT NULL
            ) AS time_count,
            COUNT(*) FILTER (
                WHERE NULLIF(btrim(c.venue_name), '') IS NOT NULL
            ) AS detailed_venue_count,
            MAX(c.last_seen_at) AS latest_seen_at,
            MAX(c.updated_at) AS latest_updated_at
        FROM courses c
        LEFT JOIN branches b ON b.id = c.branch_id
        WHERE c.is_active IS TRUE
          AND c.provider = ANY(%s)
        GROUP BY c.provider
    """
    with connection.cursor() as cursor:
        cursor.execute(query, (providers,))
        return {str(row["provider"]): dict(row) for row in cursor.fetchall()}


def fetch_provider_branches(connection, providers: list[str]) -> dict[str, list[dict[str, Any]]]:
    if not providers:
        return {}
    query = """
        SELECT
            c.provider,
            b.name,
            b.address,
            COUNT(*) AS active_courses
        FROM courses c
        JOIN branches b ON b.id = c.branch_id
        WHERE c.is_active IS TRUE
          AND c.provider = ANY(%s)
        GROUP BY c.provider, b.name, b.address
        ORDER BY c.provider, COUNT(*) DESC, b.name
    """
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with connection.cursor() as cursor:
        cursor.execute(query, (providers,))
        for row in cursor.fetchall():
            grouped[str(row["provider"])].append(dict(row))
    return dict(grouped)


def crawler_run_reports(
    rows: Iterable[dict[str, Any]],
    providers: set[str],
) -> dict[str, dict[str, Any]]:
    provider_keys = {clean(provider).upper() for provider in providers if clean(provider)}
    reports: dict[str, dict[str, Any]] = {}
    for row in rows:
        provider = clean(row.get("target_key")).upper()
        if provider not in provider_keys:
            continue

        status = clean(row.get("status")).lower()
        error_type = clean(row.get("error_type"))
        error_message = clean(row.get("error_message"))
        if error_type and error_message:
            error = f"{error_type}: {error_message}"
        else:
            error = error_type or error_message
        if not error and status != "success":
            error = {
                "running": "crawler run is still running",
                "stopped": "crawler run was stopped",
                "skipped": "crawler run was skipped",
            }.get(status, f"crawler run status is {status or 'unknown'}")

        generated_at = row.get("ended_at") or row.get("started_at")
        if isinstance(generated_at, datetime):
            generated_at = generated_at.isoformat()
        else:
            generated_at = clean(generated_at)
        run_id = row.get("id")
        report = {
            "provider": provider,
            "success": status == "success",
            "collected": int(row.get("collected_count") or 0),
            "saved": int(row.get("inserted_count") or 0)
            + int(row.get("updated_count") or 0),
            "error": error,
            "run_status": status,
            "source_type": clean(row.get("source_type")),
            "crawler_name": clean(row.get("crawler_name")),
            "report_path": f"crawler_run_log:{run_id}",
            "report_generated_at": generated_at,
            "evidence_type": "crawler_run_log",
        }
        current = reports.get(provider)
        if current is None:
            reports[provider] = report
            continue
        current_at = report_generated_datetime(current)
        candidate_at = report_generated_datetime(report)
        if candidate_at is not None and (
            current_at is None or candidate_at > current_at
        ):
            reports[provider] = report
    return reports


def fetch_latest_crawler_run_reports(
    connection,
    providers: list[str],
) -> dict[str, dict[str, Any]]:
    if not providers:
        return {}
    query = """
        SELECT DISTINCT ON (upper(btrim(target_key)))
            id,
            target_key,
            source_type,
            crawler_name,
            status,
            started_at,
            ended_at,
            collected_count,
            inserted_count,
            updated_count,
            error_type,
            error_message
        FROM crawler_run_log
        WHERE upper(btrim(target_key)) = ANY(%s)
        ORDER BY
            upper(btrim(target_key)),
            COALESCE(ended_at, started_at) DESC,
            id DESC
    """
    with connection.cursor() as cursor:
        cursor.execute(query, (providers,))
        return crawler_run_reports(cursor.fetchall(), set(providers))


def load_latest_crawler_reports(providers: set[str], scan_limit: int) -> dict[str, dict[str, Any]]:
    if not CRAWLER_REPORT_DIR.exists() or not providers or scan_limit <= 0:
        return {}
    paths = sorted(
        CRAWLER_REPORT_DIR.glob("municipal_yaml_crawler_*.yaml"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:scan_limit]
    reports: dict[str, dict[str, Any]] = {}
    for path in paths:
        if providers.issubset(reports):
            break
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        generated_at = clean(document.get("generated_at"))
        for item in document.get("reports") or []:
            if not isinstance(item, dict):
                continue
            provider = clean(item.get("provider")).upper()
            if provider not in providers or provider in reports:
                continue
            reports[provider] = {
                **item,
                "report_path": str(path.relative_to(ROOT)).replace("\\", "/"),
                "report_generated_at": generated_at,
            }
    return reports


def all_target_source_groups() -> tuple[str, ...]:
    return tuple(
        path.stem
        for path in sorted(TARGET_DIR.glob("*.yaml"))
        if path.name not in EXCLUDED_TARGET_FILES
    )


def operational_validation_reports(
    entries: Iterable[dict[str, Any]],
    providers: set[str],
) -> dict[str, dict[str, Any]]:
    reports: dict[str, dict[str, Any]] = {}
    for entry in entries:
        provider = clean(entry.get("provider")).upper()
        if provider not in providers:
            continue
        outcome = clean(entry.get("validation_outcome"))
        row_count = int(entry.get("row_count") or 0)
        no_current_data = bool(entry.get("no_current_data"))
        reports[provider] = {
            "provider": provider,
            "success": True,
            "collected": row_count,
            "saved": 0,
            "no_current_data": no_current_data,
            "no_current_reason": (
                "operational validation found no current data"
                if no_current_data
                else ""
            ),
            "parser": clean(entry.get("parser")),
            "validation_outcome": outcome,
            "report_path": "config/municipal_integrated_reservation_operational.yaml",
            "report_generated_at": clean(entry.get("validated_at")),
            "evidence_type": "municipal_operational_validation",
        }
    return reports


def load_operational_validation_reports(
    providers: set[str],
) -> dict[str, dict[str, Any]]:
    if not providers:
        return {}
    from Crawler.Crawler_MunicipalIntegratedReservation import (
        load_operational_entries,
    )

    return operational_validation_reports(load_operational_entries(), providers)


def merge_latest_reports(
    *report_sets: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for reports in report_sets:
        for provider, report in reports.items():
            current = merged.get(provider)
            if current is None:
                merged[provider] = report
                continue
            current_at = report_generated_datetime(current)
            candidate_at = report_generated_datetime(report)
            if candidate_at is not None and (
                current_at is None or candidate_at > current_at
            ):
                merged[provider] = report
    return merged


def last_quality_error(target: dict[str, Any]) -> str:
    errors = [
        clean(item.get("error_kind") or item.get("error"))
        for item in target.get("last_quality") or []
        if isinstance(item, dict)
    ]
    return next((error for error in errors if error), "")


def report_error(report: dict[str, Any] | None) -> str:
    if not report:
        return ""
    return clean(
        report.get("error")
        or report.get("configured_collection_error")
        or report.get("main_discovery_error")
    )


def report_is_no_current(report: dict[str, Any] | None) -> bool:
    if not report:
        return False
    return bool(report.get("no_current_data") or clean(report.get("no_current_reason")))


def report_generated_datetime(report: dict[str, Any] | None) -> datetime | None:
    if not report:
        return None
    raw = clean(report.get("report_generated_at"))
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=REPORT_TIMEZONE)
    return value.astimezone(timezone.utc)


def effective_report_error(
    report: dict[str, Any] | None,
    latest_seen_at: datetime | None,
) -> str:
    error = report_error(report)
    if not error or latest_seen_at is None:
        return error
    if clean(report.get("evidence_type")) == "crawler_run_log":
        return error
    report_at = report_generated_datetime(report)
    if report_at is None:
        return error
    latest = latest_seen_at
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    if report_at < latest.astimezone(timezone.utc):
        return ""
    return error


def derive_collection_status(
    target: dict[str, Any],
    stats: dict[str, Any],
    report: dict[str, Any] | None,
    *,
    stale_before: datetime,
) -> str:
    active = int(stats.get("active_courses") or 0)
    registry_statuses = set(target.get("registry_statuses") or ())
    latest = stats.get("latest_seen_at") or stats.get("latest_updated_at")
    error = effective_report_error(report, latest)
    if active:
        if error:
            return "collected_recent_failure"
        if latest and latest < stale_before:
            return "stale"
        if any(int(stats.get(f"{field}_count") or 0) < active for field in FIELD_KEYS):
            return "collected_field_gap"
        return "collected"
    if (
        report
        and report.get("success") is True
        and int(report.get("collected") or 0) > 0
    ):
        return "validated_not_persisted"
    if report_is_no_current(report) or last_quality_error(target).startswith("no_current_data"):
        return "no_current_data"
    if registry_statuses and not registry_statuses.intersection(RUNNABLE_STATUSES):
        return "blocked"
    if error:
        return "failed"
    return "not_collected"


def display_name(target: dict[str, Any]) -> str:
    names = sorted(target.get("names") or ())
    return names[0] if names else target["provider"]


def build_operational_rows(
    targets: dict[str, dict[str, Any]],
    stats_by_provider: dict[str, dict[str, Any]],
    branches_by_provider: dict[str, list[dict[str, Any]]],
    reports_by_provider: dict[str, dict[str, Any]],
    *,
    stale_days: int,
) -> list[dict[str, Any]]:
    stale_before = datetime.now(timezone.utc) - timedelta(days=stale_days)
    rows: list[dict[str, Any]] = []
    for provider, target in sorted(targets.items()):
        stats = stats_by_provider.get(provider, {})
        report = reports_by_provider.get(provider)
        active = int(stats.get("active_courses") or 0)
        status = derive_collection_status(target, stats, report, stale_before=stale_before)
        branches = branches_by_provider.get(provider, [])
        field_percentages = {
            f"{field}_pct": percentage(int(stats.get(f"{field}_count") or 0), active)
            for field in FIELD_KEYS
        }
        missing_fields = (
            [
                FIELD_LABELS[field]
                for field in FIELD_KEYS
                if int(stats.get(f"{field}_count") or 0) < active
            ]
            if active
            else []
        )
        latest = stats.get("latest_seen_at") or stats.get("latest_updated_at")
        error = effective_report_error(report, latest) or last_quality_error(target)
        rows.append(
            {
                "provider": provider,
                "name": display_name(target),
                "source_groups": ",".join(sorted(target.get("source_groups") or ())),
                "categories": ",".join(sorted(target.get("categories") or ())),
                "registry_status": ",".join(sorted(target.get("registry_statuses") or ())),
                "collection_status": status,
                "collection_status_label": COLLECTION_STATUS_LABELS[status],
                "active_courses": active,
                "branch_count": int(stats.get("branch_count") or 0),
                "venue_count": int(stats.get("venue_count") or 0),
                "target_count": int(target.get("target_count") or 0),
                **field_percentages,
                "detailed_venue_pct": percentage(
                    int(stats.get("detailed_venue_count") or 0), active
                ),
                "missing_fields": ",".join(missing_fields),
                "multi_location": len(branches) > 1,
                "branch_names": " | ".join(clean(item.get("name")) for item in branches[:20]),
                "latest_seen_at": latest.isoformat() if latest else "",
                "last_run_success": bool(report.get("success")) if report else None,
                "last_run_collected": int(report.get("collected") or 0) if report else None,
                "last_run_error": error,
                "last_report": clean(report.get("report_path")) if report else "",
                "url": sorted(target.get("urls") or ("",))[0],
            }
        )
    return rows


def load_facility_rows() -> list[dict[str, Any]]:
    if not FACILITY_REGISTRY.exists():
        return []
    document = yaml.safe_load(FACILITY_REGISTRY.read_text(encoding="utf-8")) or {}
    return [
        item
        for item in document.get("targets") or []
        if isinstance(item, dict) and clean(item.get("category")) in FACILITY_CATEGORIES
    ]


def facility_urls(facility: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for key in ("url", "homepage_urls"):
        values.update(iter_values(facility.get(key)))
    return values


def build_match_indexes(
    targets: dict[str, dict[str, Any]],
    branches_by_provider: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    name_index: dict[str, set[str]] = defaultdict(set)
    branch_index: dict[str, set[str]] = defaultdict(set)
    url_index: list[tuple[str, str]] = []
    site_index: dict[str, set[str]] = defaultdict(set)
    branch_candidates: list[tuple[str, str]] = []
    for provider, target in targets.items():
        for value in set(target.get("names") or ()) | set(target.get("branches") or ()):
            key = normalized_name(value)
            if key:
                name_index[key].add(provider)
        for value in target.get("urls") or ():
            key = canonical_url_key(value)
            if key:
                url_index.append((key, provider))
            site_key = canonical_site_key(value)
            if site_key:
                site_index[site_key].add(provider)
        for branch in branches_by_provider.get(provider, []):
            key = normalized_name(branch.get("name"))
            if key:
                branch_index[key].add(provider)
                branch_candidates.append((key, provider))
    return {
        "name_index": dict(name_index),
        "url_index": url_index,
        "branch_index": dict(branch_index),
        "site_index": dict(site_index),
        "branch_candidates": branch_candidates,
    }


def match_facility(
    facility: dict[str, Any],
    *,
    targets: dict[str, dict[str, Any]],
    indexes: dict[str, Any],
) -> tuple[set[str], str]:
    provider = clean(facility.get("provider")).upper()
    if provider in targets:
        return {provider}, "provider"

    urls = facility_urls(facility)
    facility_keys = {canonical_url_key(value) for value in urls}
    facility_keys.discard("")
    facility_sites = {canonical_site_key(value) for value in urls}
    facility_sites.discard("")
    site_matches = {
        candidate_provider
        for site_key in facility_sites
        for candidate_provider in indexes["site_index"].get(site_key, ())
    }

    name_key = normalized_name(facility.get("name") or facility.get("branch"))
    branch_matches = set(indexes["branch_index"].get(name_key, ()))
    if facility_sites:
        branch_matches.intersection_update(site_matches)
    if branch_matches:
        return branch_matches, "active_branch_name"

    if site_matches and name_key:
        fuzzy_branch_matches = {
            candidate_provider
            for branch_key, candidate_provider in indexes["branch_candidates"]
            if candidate_provider in site_matches
            and institution_names_overlap(name_key, branch_key)
        }
        if fuzzy_branch_matches:
            return fuzzy_branch_matches, "active_branch_name_fuzzy"

    name_matches = set(indexes["name_index"].get(name_key, ()))
    if facility_sites:
        name_matches.intersection_update(site_matches)
    if name_matches:
        return name_matches, "target_name"

    url_matches = {
        candidate_provider
        for facility_key in facility_keys
        for candidate_key, candidate_provider in indexes["url_index"]
        if url_keys_overlap(facility_key, candidate_key)
    }
    if url_matches:
        return url_matches, "url_path"

    if site_matches:
        return site_matches, "site_key"
    return set(), ""


def build_facility_coverage_rows(
    facilities: list[dict[str, Any]],
    targets: dict[str, dict[str, Any]],
    stats_by_provider: dict[str, dict[str, Any]],
    branches_by_provider: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    indexes = build_match_indexes(targets, branches_by_provider)
    rows: list[dict[str, Any]] = []
    for facility in facilities:
        matches, method = match_facility(
            facility,
            targets=targets,
            indexes=indexes,
        )
        active = sum(
            int(stats_by_provider.get(provider, {}).get("active_courses") or 0)
            for provider in matches
        )
        urls = facility_urls(facility)
        site_keys = sorted(
            filter(None, (canonical_site_key(value) for value in urls))
        )
        if active and method != "site_key":
            status = "collected"
        elif active and method == "site_key":
            status = "site_connected_unverified"
        elif matches:
            status = "configured_no_active"
        elif urls:
            status = "crawler_target_needed"
        else:
            status = "url_discovery_needed"
        rows.append(
            {
                "facility_provider": clean(facility.get("provider")),
                "category": clean(facility.get("category")),
                "name": clean(facility.get("name") or facility.get("branch")),
                "region": clean(facility.get("region")),
                "address": clean(facility.get("address")),
                "url": clean(facility.get("url")),
                "site_key": site_keys[0] if site_keys else "",
                "coverage_status": status,
                "coverage_status_label": FACILITY_STATUS_LABELS[status],
                "matched_providers": ",".join(sorted(matches)),
                "match_method": method,
                "matched_active_courses": active,
            }
        )
    return rows


def build_site_group_rows(facility_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in facility_rows:
        site_key = clean(row.get("site_key"))
        if site_key:
            grouped[site_key].append(row)

    rows: list[dict[str, Any]] = []
    for site_key, facilities in grouped.items():
        statuses = Counter(clean(item.get("coverage_status")) for item in facilities)
        matched_providers = sorted(
            {
                provider
                for item in facilities
                for provider in clean(item.get("matched_providers")).split(",")
                if provider
            }
        )
        unresolved_count = len(facilities) - statuses.get("collected", 0)
        if unresolved_count == 0:
            action = "covered"
        elif matched_providers:
            action = "expand_existing_collector"
        elif len(facilities) > 1:
            action = "build_multi_facility_collector"
        else:
            action = "build_single_facility_collector"
        rows.append(
            {
                "site_key": site_key,
                "facility_count": len(facilities),
                "collected_count": statuses.get("collected", 0),
                "site_connected_unverified_count": statuses.get(
                    "site_connected_unverified", 0
                ),
                "configured_no_active_count": statuses.get("configured_no_active", 0),
                "crawler_target_needed_count": statuses.get("crawler_target_needed", 0),
                "unresolved_count": unresolved_count,
                "action": action,
                "matched_providers": ",".join(matched_providers),
                "categories": ",".join(
                    sorted({clean(item.get("category")) for item in facilities})
                ),
                "regions": ",".join(
                    sorted({clean(item.get("region")) for item in facilities})
                ),
                "sample_facilities": " | ".join(
                    clean(item.get("name")) for item in facilities[:8]
                ),
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            -int(row["unresolved_count"]),
            -int(row["facility_count"]),
            str(row["site_key"]),
        ),
    )


def summarize(
    operational_rows: list[dict[str, Any]],
    facility_rows: list[dict[str, Any]],
    *,
    target_entries: int,
    site_group_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    operational_statuses = Counter(row["collection_status"] for row in operational_rows)
    field_gaps = {
        field: sum(
            1
            for row in operational_rows
            if int(row["active_courses"]) > 0 and float(row[f"{field}_pct"]) < 100
        )
        for field in FIELD_KEYS
    }
    facility_statuses = Counter(row["coverage_status"] for row in facility_rows)
    facility_categories: dict[str, Counter[str]] = defaultdict(Counter)
    for row in facility_rows:
        facility_categories[row["category"]][row["coverage_status"]] += 1
    site_groups = site_group_rows or []
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "target_entries": target_entries,
        "operational_providers": len(operational_rows),
        "operational_statuses": dict(sorted(operational_statuses.items())),
        "providers_with_field_gaps": field_gaps,
        "facility_master_rows": len(facility_rows),
        "facility_statuses": dict(sorted(facility_statuses.items())),
        "facility_categories": {
            category: dict(sorted(statuses.items()))
            for category, statuses in sorted(facility_categories.items())
        },
        "site_groups": {
            "total": len(site_groups),
            "multi_facility": sum(
                int(row["facility_count"]) > 1 for row in site_groups
            ),
            "existing_collector_expansion": sum(
                row["action"] == "expand_existing_collector" for row in site_groups
            ),
            "new_multi_facility_collector": sum(
                row["action"] == "build_multi_facility_collector"
                for row in site_groups
            ),
        },
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def markdown_report(
    summary: dict[str, Any],
    operational_rows: list[dict[str, Any]],
    facility_rows: list[dict[str, Any]],
    site_group_rows: list[dict[str, Any]],
) -> str:
    lines = [
        "# 전체 크롤러 대상 수집 감사",
        "",
        f"- 생성: `{summary['generated_at']}`",
        f"- 운영 대상: `{summary['target_entries']}`개 URL 항목 / `{summary['operational_providers']}`개 공급자",
        f"- 전국 시설 원장: `{summary['facility_master_rows']}`개",
        "",
        "## 운영 대상 요약",
        "",
        "| 상태 | 공급자 수 |",
        "| --- | ---: |",
    ]
    for status, count in summary["operational_statuses"].items():
        lines.append(f"| {COLLECTION_STATUS_LABELS.get(status, status)} | {count} |")
    lines.extend(
        [
            "",
            "## 필수 필드 부족 공급자",
            "",
            "| 필드 | 100% 미만 공급자 수 |",
            "| --- | ---: |",
        ]
    )
    for field, count in summary["providers_with_field_gaps"].items():
        lines.append(f"| {FIELD_LABELS[field]} | {count} |")
    lines.extend(
        [
            "",
            "## 운영 대상 상세",
            "",
            "| 공급자 | 이름 | 상태 | 활성 | 지점 | 대상 | 요금 | 날짜 | 장소 | 분야 | 시간 | 누락 |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in operational_rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["provider"],
                    row["name"].replace("|", "\\|"),
                    row["collection_status_label"],
                    str(row["active_courses"]),
                    str(row["branch_count"]),
                    f"{row['target_pct']}%",
                    f"{row['fee_pct']}%",
                    f"{row['date_pct']}%",
                    f"{row['place_pct']}%",
                    f"{row['category_pct']}%",
                    f"{row['time_pct']}%",
                    row["missing_fields"] or "-",
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 전국 시설 원장 요약",
            "",
            "| 분류 | 수집 | 사이트연결/지점미확인 | 대상연결/활성자료없음 | 수집대상필요 | URL탐색필요 |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for category, statuses in summary["facility_categories"].items():
        lines.append(
            f"| {category} | {statuses.get('collected', 0)} | "
            f"{statuses.get('site_connected_unverified', 0)} | "
            f"{statuses.get('configured_no_active', 0)} | "
            f"{statuses.get('crawler_target_needed', 0)} | "
            f"{statuses.get('url_discovery_needed', 0)} |"
        )
    prioritized_sites = [
        row for row in site_group_rows if int(row["unresolved_count"]) > 0
    ]
    lines.extend(
        [
            "",
            "## 공용 사이트 우선순위",
            "",
            "| 사이트 | 시설 | 수집 | 지점미확인 | 신규대상 | 조치 | 연결 공급자 | 예시 |",
            "| --- | ---: | ---: | ---: | ---: | --- | --- | --- |",
        ]
    )
    for row in prioritized_sites[:100]:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["site_key"],
                    str(row["facility_count"]),
                    str(row["collected_count"]),
                    str(row["site_connected_unverified_count"]),
                    str(row["crawler_target_needed_count"]),
                    row["action"],
                    row["matched_providers"] or "-",
                    row["sample_facilities"].replace("|", "\\|"),
                ]
            )
            + " |"
        )
    unresolved = [
        row
        for row in facility_rows
        if row["coverage_status"]
        in {
            "configured_no_active",
            "site_connected_unverified",
            "crawler_target_needed",
            "url_discovery_needed",
        }
    ]
    lines.extend(
        [
            "",
            "## 미수집 시설 예시",
            "",
            "| 분류 | 시설 | 지역 | 상태 | 연결 공급자 | URL |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in unresolved[:100]:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["category"],
                    row["name"].replace("|", "\\|"),
                    row["region"].replace("|", "\\|"),
                    row["coverage_status_label"],
                    row["matched_providers"] or "-",
                    row["url"].replace("|", "%7C"),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "전체 시설별 결과는 같은 시각에 생성된 `facility_collection_coverage_*.csv`, "
            "사이트별 결과는 `facility_site_groups_*.csv`를 확인한다.",
            "",
        ]
    )
    return "\n".join(lines)


def copy_latest(path: Path, latest_name: str) -> Path:
    latest = path.parent / latest_name
    shutil.copyfile(path, latest)
    return latest


def write_outputs(
    summary: dict[str, Any],
    operational_rows: list[dict[str, Any]],
    facility_rows: list[dict[str, Any]],
    site_group_rows: list[dict[str, Any]],
    output_dir: Path,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"target_collection_audit_{stamp}.json"
    csv_path = output_dir / f"target_collection_audit_{stamp}.csv"
    facility_csv_path = output_dir / f"facility_collection_coverage_{stamp}.csv"
    site_group_csv_path = output_dir / f"facility_site_groups_{stamp}.csv"
    markdown_path = output_dir / f"target_collection_audit_{stamp}.md"
    json_path.write_text(
        json.dumps(
            {
                "summary": summary,
                "operational_targets": operational_rows,
                "facility_coverage": facility_rows,
                "facility_site_groups": site_group_rows,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    write_csv(csv_path, operational_rows)
    write_csv(facility_csv_path, facility_rows)
    write_csv(site_group_csv_path, site_group_rows)
    markdown_path.write_text(
        markdown_report(summary, operational_rows, facility_rows, site_group_rows),
        encoding="utf-8",
    )
    paths = {
        "json": json_path,
        "csv": csv_path,
        "facility_csv": facility_csv_path,
        "site_group_csv": site_group_csv_path,
        "markdown": markdown_path,
    }
    for key, path in list(paths.items()):
        latest_name = {
            "json": "target_collection_audit_latest.json",
            "csv": "target_collection_audit_latest.csv",
            "facility_csv": "facility_collection_coverage_latest.csv",
            "site_group_csv": "facility_site_groups_latest.csv",
            "markdown": "target_collection_audit_latest.md",
        }[key]
        paths[f"{key}_latest"] = copy_latest(path, latest_name)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit crawler target coverage and required field completeness."
    )
    parser.add_argument(
        "--source-groups",
        nargs="+",
        default=list(DEFAULT_SOURCE_GROUPS),
        help="Primary crawl_targets YAML stems; related institution targets in other files are included.",
    )
    parser.add_argument(
        "--all-targets",
        action="store_true",
        help="Audit every non-deprecated crawl_targets YAML file.",
    )
    parser.add_argument(
        "--selected-files-only",
        action="store_true",
        help="Do not include related institution targets found in other crawl target files.",
    )
    parser.add_argument("--stale-days", type=int, default=30)
    parser.add_argument(
        "--report-scan-limit",
        type=int,
        default=400,
        help="Maximum recent municipal crawler reports to inspect.",
    )
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    if args.stale_days < 1:
        parser.error("--stale-days must be at least 1")
    if args.report_scan_limit < 0:
        parser.error("--report-scan-limit cannot be negative")

    source_groups = (
        all_target_source_groups()
        if args.all_targets
        else tuple(args.source_groups)
    )
    targets, target_entries = load_operational_targets(
        source_groups,
        include_related_targets=not (args.selected_files_only or args.all_targets),
    )
    providers = sorted(targets)
    with connect() as connection:
        stats_by_provider = fetch_provider_stats(connection, providers)
        branches_by_provider = fetch_provider_branches(connection, providers)
        crawler_runs_by_provider = fetch_latest_crawler_run_reports(
            connection,
            providers,
        )
    reports_by_provider = merge_latest_reports(
        load_operational_validation_reports(set(providers)),
        load_latest_crawler_reports(set(providers), args.report_scan_limit),
        crawler_runs_by_provider,
    )
    operational_rows = build_operational_rows(
        targets,
        stats_by_provider,
        branches_by_provider,
        reports_by_provider,
        stale_days=args.stale_days,
    )
    facilities = load_facility_rows()
    facility_rows = build_facility_coverage_rows(
        facilities,
        targets,
        stats_by_provider,
        branches_by_provider,
    )
    site_group_rows = build_site_group_rows(facility_rows)
    summary = summarize(
        operational_rows,
        facility_rows,
        target_entries=target_entries,
        site_group_rows=site_group_rows,
    )
    paths = write_outputs(
        summary,
        operational_rows,
        facility_rows,
        site_group_rows,
        args.output_dir,
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for key in ("markdown", "csv", "facility_csv", "site_group_csv", "json"):
        print(f"{key}={paths[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
