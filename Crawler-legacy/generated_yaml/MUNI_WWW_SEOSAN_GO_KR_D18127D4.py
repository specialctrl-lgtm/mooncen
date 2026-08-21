from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


PROVIDER = "MUNI_WWW_SEOSAN_GO_KR_D18127D4"
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_SeosanReservation import (
    SeosanDbWriter,
    detail_links_from_list,
    fetch_soup,
    parse_detail,
    parse_list_status,
    session,
)
from DB.course_lifecycle import mark_stale_courses, utc_now


BASE_URL = "https://total.seosan.go.kr"
LIST_URLS = (
    f"{BASE_URL}/total/selectEdcAtrCourseListU.do?key=2&searchInsttCode=01",
    f"{BASE_URL}/total/selectEdcAtrCourseListU.do?key=186&searchInsttCode=01&cl1No=25",
    f"{BASE_URL}/total/selectEdcAtrCourseListU.do?key=187&searchInsttCode=01&cl1No=26",
    f"{BASE_URL}/total/selectEdcAtrCourseListU.do?key=506&searchInsttCode=01&cl1No=345",
    f"{BASE_URL}/total/selectEdcAtrCourseListU.do?key=606&searchInsttCode=01&cl1No=565",
)
NAME = "\uc11c\uc0b0\uc2dc \ud3c9\uc0dd\ud559\uc2b5\uad00"
BRANCH = "\uc11c\uc0b0\uc2dc \ud3c9\uc0dd\ud559\uc2b5\uad00"
CATEGORY = "\ud3c9\uc0dd\ud559\uc2b5"
REPORT_DIR = ROOT / "logs" / "municipal_crawler_reports"


def field_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    keys = [
        "title",
        "branch",
        "branch_code",
        "address",
        "venue_name",
        "venue_address",
        "raw_url",
        "status",
        "fee",
        "schedule_raw",
        "period",
        "target",
        "description",
    ]
    return {key: sum(1 for row in rows if row.get(key)) for key in keys}


def collect(limit: int | None, timeout: int, max_pages: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    client = session()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    pages = 0
    detail_pages = 0

    for list_url in LIST_URLS:
        if pages >= max_pages or (limit is not None and len(rows) >= limit):
            break
        soup = fetch_soup(client, list_url, timeout)
        pages += 1
        statuses = parse_list_status(soup)
        for detail_url in detail_links_from_list(soup, list_url):
            if detail_url in seen:
                continue
            seen.add(detail_url)
            if limit is not None and len(rows) >= limit:
                break
            row = parse_detail(client, detail_url, timeout)
            row["provider"] = PROVIDER
            row["branch"] = row.get("branch") or BRANCH
            row["category"] = CATEGORY
            row["status"] = statuses.get(detail_url, row.get("status") or "OPEN")
            rows.append({key: value for key, value in row.items() if value})
            detail_pages += 1

    return rows, {
        "pages": pages,
        "detail_pages": detail_pages,
        "discovered_links": len(seen),
        "reservation_discovery_links": sum(1 for row in rows if row.get("application_url")),
        "reservation_fallback_pages": 0,
        "pagination_detected": pages > 1,
        "recursion_depth": 0,
    }


def write_quality_report(rows: list[dict[str, Any]], meta: dict[str, Any], saved: int, error: str = "") -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    fields = field_counts(rows)
    report = {
        "provider": PROVIDER,
        "name": NAME,
        "url": LIST_URLS[0],
        "success": bool(rows) and not error,
        "collected": len(rows),
        "saved": saved,
        "pages": meta.get("pages", 0),
        "detail_pages": meta.get("detail_pages", 0),
        "discovered_links": meta.get("discovered_links", 0),
        "reservation_discovery_links": meta.get("reservation_discovery_links", 0),
        "reservation_fallback_pages": 0,
        "pagination_detected": meta.get("pagination_detected", False),
        "recursion_depth": 0,
        "no_current_data": False,
        "no_current_reason": "",
        "parser": "seosan_reservation_detail",
        "error": error,
        "fields": fields,
        "samples": [
            {
                "title": row.get("title"),
                "branch": row.get("branch"),
                "status": row.get("status"),
                "raw_url": row.get("raw_url"),
            }
            for row in rows[:3]
        ],
        "grade": "A" if rows and not error else "ERROR",
    }
    data = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "reports": [report],
    }
    path = REPORT_DIR / f"municipal_yaml_crawler_{datetime.now().strftime('%Y%m%d_%H%M%S')}.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
    return path


def run() -> int:
    parser = argparse.ArgumentParser(description="Seosan lifelong-learning courses from integrated reservation")
    parser.add_argument("--limit", "--per-target-limit", dest="limit", type=int, default=None)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument("--max-depth", type=int, default=0)
    parser.add_argument("--detail-limit", type=int, default=30)
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--mark-stale", action="store_true")
    args = parser.parse_args()

    saved = 0
    error = ""
    try:
        rows, meta = collect(args.limit, args.timeout, args.max_pages)
        if args.save_db and rows:
            writer = SeosanDbWriter(PROVIDER)
            saved = writer.save_rows(rows)
            if args.mark_stale and saved > 0:
                mark_stale_courses(PROVIDER, utc_now())
    except Exception as exc:  # noqa: BLE001 - report runtime failures to recursive loop.
        rows = []
        meta = {}
        error = f"{type(exc).__name__}: {exc}"

    report_path = write_quality_report(rows, meta, saved, error)
    quality = {
        "provider": PROVIDER,
        "collected": len(rows),
        "saved": saved,
        "parser": "seosan_reservation_detail",
        "field_counts": field_counts(rows),
        "error": error,
        "grade": "A" if rows and not error else "ERROR",
    }
    print(json.dumps(quality, ensure_ascii=False))
    print(f"report={report_path}")
    return 0 if rows and not error else 1


if __name__ == "__main__":
    raise SystemExit(run())
