from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

import psycopg2
from dotenv import load_dotenv

from DB.course_upsert_guards import (
    deactivate_courses_missing_required_display_fields,
    repair_missing_schedule_raw,
)
from DB.connection_settings import database_connect_options


OUT_DIR = ROOT / "logs" / "course_data_quality"

REQUIRED_FIELDS = ("title", "schedule_raw", "raw_url", "branch", "status")
IMPORTANT_FIELDS = ("description", "target", "period_dates", "venue_name", "fee")


def clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def connect():
    load_dotenv(ROOT / ".env")
    host = os.getenv("DB_HOST", "localhost")
    return psycopg2.connect(
        host=host,
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "mooncen"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        **database_connect_options(host, "mooncen-course-quality-report"),
    )


def missing_fields(row: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if not clean(row.get("title")):
        missing.append("title")
    if not clean(row.get("schedule_raw")):
        missing.append("schedule_raw")
    if not clean(row.get("raw_url")):
        missing.append("raw_url")
    if not clean(row.get("branch_name")):
        missing.append("branch")
    if not clean(row.get("status")):
        missing.append("status")
    if not clean(row.get("description")):
        missing.append("description")
    if not clean(row.get("target")):
        missing.append("target")
    if row.get("start_date") is None and row.get("end_date") is None:
        missing.append("period_dates")
    if not clean(row.get("venue_name")):
        missing.append("venue_name")
    if row.get("fee") is None:
        missing.append("fee")
    return missing


def fetch_rows(conn, active_only: bool) -> list[dict[str, Any]]:
    active_filter = "WHERE c.is_active = TRUE" if active_only else ""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                c.id::text,
                c.provider,
                c.provider_course_id,
                c.is_active,
                COALESCE(b.name, '') AS branch_name,
                c.title,
                c.schedule_raw,
                c.start_date,
                c.end_date,
                c.target,
                c.status,
                c.description,
                c.venue_name,
                c.fee,
                c.raw_url,
                c.application_url,
                c.updated_at
            FROM courses c
            LEFT JOIN branches b ON b.id = c.branch_id
            {active_filter}
            ORDER BY c.provider, c.updated_at DESC NULLS LAST, c.id
            """
        )
        columns = [desc.name for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def build_report(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    issue_rows: list[dict[str, Any]] = []
    missing_counter: Counter[str] = Counter()
    provider_counter: Counter[str] = Counter()
    required_issue_count = 0

    for row in rows:
        fields = missing_fields(row)
        if not fields:
            continue
        missing_counter.update(fields)
        provider_counter[row["provider"]] += 1
        if any(field in REQUIRED_FIELDS for field in fields):
            required_issue_count += 1
        issue_rows.append(
            {
                "provider": row.get("provider"),
                "provider_course_id": row.get("provider_course_id"),
                "course_id": row.get("id"),
                "active": row.get("is_active"),
                "branch": row.get("branch_name"),
                "title": row.get("title"),
                "schedule_raw": row.get("schedule_raw"),
                "start_date": row.get("start_date"),
                "end_date": row.get("end_date"),
                "target": row.get("target"),
                "status": row.get("status"),
                "missing_fields": ",".join(fields),
                "raw_url": row.get("raw_url"),
                "application_url": row.get("application_url"),
                "updated_at": row.get("updated_at"),
            }
        )

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "scanned": len(rows),
        "issue_rows": len(issue_rows),
        "required_issue_rows": required_issue_count,
        "important_only_issue_rows": len(issue_rows) - required_issue_count,
        "missing_counts": dict(sorted(missing_counter.items())),
        "top_providers": dict(provider_counter.most_common(50)),
        "required_fields": REQUIRED_FIELDS,
        "important_fields": IMPORTANT_FIELDS,
    }
    return summary, issue_rows


def write_reports(summary: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[Path, Path, Path]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = OUT_DIR / f"course_data_quality_{stamp}.json"
    csv_path = OUT_DIR / f"course_data_quality_{stamp}.csv"
    md_path = OUT_DIR / f"course_data_quality_{stamp}.md"

    json_path.write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    fieldnames = [
        "provider",
        "provider_course_id",
        "course_id",
        "active",
        "branch",
        "title",
        "schedule_raw",
        "start_date",
        "end_date",
        "target",
        "status",
        "missing_fields",
        "raw_url",
        "application_url",
        "updated_at",
    ]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# Course Data Quality Report",
        "",
        f"- generated_at: `{summary['generated_at']}`",
        f"- scanned: `{summary['scanned']}`",
        f"- issue_rows: `{summary['issue_rows']}`",
        f"- required_issue_rows: `{summary['required_issue_rows']}`",
        f"- important_only_issue_rows: `{summary['important_only_issue_rows']}`",
        "",
        "## Missing Counts",
        "",
    ]
    for key, value in summary["missing_counts"].items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Top Providers", ""])
    for provider, value in summary["top_providers"].items():
        lines.append(f"- {provider}: `{value}`")
    lines.extend(["", "## Sample Rows", ""])
    lines.append("| provider | title | missing | url |")
    lines.append("| --- | --- | --- | --- |")
    for row in rows[:100]:
        title = clean(row.get("title")).replace("|", "\\|")[:80]
        missing = clean(row.get("missing_fields"))
        url = clean(row.get("raw_url") or row.get("application_url")).replace("|", "%7C")
        lines.append(f"| {row.get('provider')} | {title} | {missing} | {url} |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return json_path, csv_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit stored course data quality and output URL-linked reports.")
    parser.add_argument("--all", action="store_true", help="Include inactive courses. Default only scans active courses.")
    parser.add_argument("--apply-repairs", action="store_true", help="Repair schedule_raw from start/end dates before reporting.")
    parser.add_argument(
        "--deactivate-required-missing",
        action="store_true",
        help="Deactivate active courses still missing required display fields after repairs.",
    )
    args = parser.parse_args()

    conn = connect()
    try:
        if args.apply_repairs or args.deactivate_required_missing:
            with conn.cursor() as cur:
                repaired = repair_missing_schedule_raw(cur)
                deactivated = (
                    deactivate_courses_missing_required_display_fields(cur)
                    if args.deactivate_required_missing
                    else 0
                )
            conn.commit()
            print(f"repaired={repaired}")
            print(f"deactivated={deactivated}")

        rows = fetch_rows(conn, active_only=not args.all)
        summary, issue_rows = build_report(rows)
        json_path, csv_path, md_path = write_reports(summary, issue_rows)

        print(f"scanned={summary['scanned']}")
        print(f"issue_rows={summary['issue_rows']}")
        print(f"required_issue_rows={summary['required_issue_rows']}")
        print("missing_counts=" + json.dumps(summary["missing_counts"], ensure_ascii=False, sort_keys=True))
        print(f"json={json_path}")
        print(f"csv={csv_path}")
        print(f"md={md_path}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
