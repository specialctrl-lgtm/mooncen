from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from psycopg2.extras import RealDictCursor


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from DB.db_utils import get_db_connection


SEOUL = ZoneInfo("Asia/Seoul")
DEFAULT_OUTPUT_ROOT = ROOT / "logs" / "collection_statistics"
COURSE_FIELDS = [
    "course_id",
    "provider",
    "branch",
    "title",
    "target",
    "fee",
    "date",
    "place",
    "category",
    "time",
    "reception_start",
    "reception_end",
    "reception_period",
    "major_category",
    "table_type",
    "status",
    "program_type",
    "service_group",
    "collection_type",
    "address",
    "url",
    "active",
    "last_seen_at",
]


def clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def iso_date(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return clean(value)[:10]


def iso_datetime(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return clean(value)


def date_range(start: Any, end: Any) -> str:
    start_text = iso_date(start)
    end_text = iso_date(end)
    if start_text and end_text and start_text != end_text:
        return f"{start_text} ~ {end_text}"
    return start_text or end_text


def table_type(service_group: Any) -> tuple[str, str]:
    value = clean(service_group)
    if value == "문화센터":
        return "문화센터", "culture_center"
    if value == "체험":
        return "체험", "experience"
    if value == "공공강좌":
        return "교육", "education"
    return "기타", "unknown"


def normalize_course(row: dict[str, Any]) -> dict[str, Any]:
    major_category, type_code = table_type(row.get("service_group"))
    branch = clean(row.get("branch_name"))
    venue_name = clean(row.get("venue_name"))
    address = clean(row.get("venue_address")) or clean(row.get("branch_address"))
    category = (
        clean(row.get("standard_category_label"))
        or clean(row.get("category_raw"))
        or clean(row.get("domain_category"))
        or clean(row.get("service_group"))
    )
    schedule = clean(row.get("schedule_raw"))
    if not schedule:
        start_time = clean(row.get("schedule_time_start"))
        end_time = clean(row.get("schedule_time_end"))
        schedule = f"{start_time}~{end_time}" if start_time and end_time else start_time or end_time

    fee = row.get("fee")
    fee_text = "" if fee is None else str(fee)
    return {
        "course_id": clean(row.get("course_id")),
        "provider": clean(row.get("provider")),
        "branch": branch,
        "title": clean(row.get("title")),
        "target": clean(row.get("target")),
        "fee": fee_text,
        "date": date_range(row.get("start_date"), row.get("end_date")),
        "place": venue_name or branch,
        "category": category,
        "time": schedule,
        "reception_start": iso_date(row.get("apply_start")),
        "reception_end": iso_date(row.get("apply_end")),
        "reception_period": date_range(row.get("apply_start"), row.get("apply_end")),
        "major_category": major_category,
        "table_type": type_code,
        "status": clean(row.get("status")),
        "program_type": clean(row.get("program_type")),
        "service_group": clean(row.get("service_group")),
        "collection_type": clean(row.get("collection_type")),
        "address": address,
        "url": clean(row.get("application_url")) or clean(row.get("raw_url")),
        "active": bool(row.get("is_active")),
        "last_seen_at": iso_datetime(row.get("last_seen_at")),
    }


def fetch_courses(*, include_inactive: bool) -> list[dict[str, Any]]:
    active_filter = "" if include_inactive else "WHERE c.is_active IS TRUE"
    query = f"""
        SELECT c.id::text AS course_id,
               c.provider,
               c.title,
               c.target,
               c.fee,
               c.start_date,
               c.end_date,
               c.schedule_raw,
               c.schedule_time_start,
               c.schedule_time_end,
               c.apply_start,
               c.apply_end,
               c.status,
               c.program_type,
               c.service_group,
               c.collection_type,
               c.category_raw,
               c.domain_category,
               c.standard_category_label,
               c.venue_name,
               c.venue_address,
               c.application_url,
               c.raw_url,
               c.is_active,
               c.last_seen_at,
               b.name AS branch_name,
               b.address AS branch_address
          FROM courses c
          LEFT JOIN branches b ON b.id = c.branch_id
          {active_filter}
         ORDER BY c.provider, b.name, c.apply_start NULLS LAST, c.title, c.id
    """
    connection = get_db_connection()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query)
            return [dict(row) for row in cursor.fetchall()]
    finally:
        connection.close()


def _counter_rows(
    counter: Counter[tuple[str, ...]],
    headers: list[str],
) -> list[dict[str, Any]]:
    return [
        dict(zip(headers, key, strict=True), count=count)
        for key, count in sorted(counter.items())
    ]


def build_statistics(
    rows: Iterable[dict[str, Any]],
    *,
    from_date: date | None = None,
    to_date: date | None = None,
) -> dict[str, list[dict[str, Any]]]:
    yearly: Counter[tuple[str, ...]] = Counter()
    monthly: Counter[tuple[str, ...]] = Counter()
    weekly: Counter[tuple[str, ...]] = Counter()
    major: Counter[tuple[str, ...]] = Counter()
    category: Counter[tuple[str, ...]] = Counter()
    provider: Counter[tuple[str, ...]] = Counter()
    table_types: Counter[tuple[str, ...]] = Counter()
    monthly_category: Counter[tuple[str, ...]] = Counter()
    monthly_provider: Counter[tuple[str, ...]] = Counter()

    for row in rows:
        reception_text = clean(row.get("reception_start"))
        if not reception_text:
            continue
        reception_date = date.fromisoformat(reception_text)
        if from_date and reception_date < from_date:
            continue
        if to_date and reception_date > to_date:
            continue

        year = reception_date.strftime("%Y")
        month = reception_date.strftime("%Y-%m")
        iso_year, iso_week, _ = reception_date.isocalendar()
        week_start = reception_date - timedelta(days=reception_date.weekday())
        week_end = week_start + timedelta(days=6)
        week = f"{iso_year}-W{iso_week:02d}"
        major_name = clean(row.get("major_category")) or "기타"
        category_name = clean(row.get("category")) or "미분류"
        provider_name = clean(row.get("provider")) or "UNKNOWN"
        type_name = clean(row.get("table_type")) or "unknown"

        yearly[(year,)] += 1
        monthly[(month,)] += 1
        weekly[(week, week_start.isoformat(), week_end.isoformat())] += 1
        major[(major_name,)] += 1
        category[(category_name,)] += 1
        provider[(provider_name,)] += 1
        table_types[(type_name, major_name)] += 1
        monthly_category[(month, category_name)] += 1
        monthly_provider[(month, provider_name)] += 1

    return {
        "yearly": _counter_rows(yearly, ["reception_year"]),
        "monthly": _counter_rows(monthly, ["reception_month"]),
        "weekly": _counter_rows(weekly, ["iso_week", "week_start", "week_end"]),
        "major_category": _counter_rows(major, ["major_category"]),
        "category": _counter_rows(category, ["category"]),
        "provider": _counter_rows(provider, ["provider"]),
        "table_type": _counter_rows(table_types, ["table_type", "major_category"]),
        "monthly_category": _counter_rows(
            monthly_category,
            ["reception_month", "category"],
        ),
        "monthly_provider": _counter_rows(
            monthly_provider,
            ["reception_month", "provider"],
        ),
    }


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    headers = fieldnames or (list(rows[0]) if rows else [])
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        if not headers:
            return
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def write_report(
    raw_rows: list[dict[str, Any]],
    *,
    output_root: Path,
    include_inactive: bool,
    from_date: date | None,
    to_date: date | None,
) -> tuple[Path, dict[str, Any]]:
    generated_at = datetime.now(SEOUL)
    output_dir = output_root / f"collection_statistics_{generated_at:%Y%m%d_%H%M%S}"
    output_dir.mkdir(parents=True, exist_ok=False)

    rows = [normalize_course(row) for row in raw_rows]
    statistics = build_statistics(rows, from_date=from_date, to_date=to_date)
    with_reception = sum(bool(row["reception_start"]) for row in rows)
    complete_required = sum(
        all(str(row[field]).strip() for field in ("target", "fee", "date", "place", "category", "time"))
        for row in rows
    )
    summary = {
        "generated_at": generated_at.isoformat(),
        "statistics_date_basis": "apply_start",
        "scope": "all" if include_inactive else "active_only",
        "statistics_from_date": from_date.isoformat() if from_date else None,
        "statistics_to_date": to_date.isoformat() if to_date else None,
        "course_rows": len(rows),
        "reception_start_rows": with_reception,
        "missing_reception_start_rows": len(rows) - with_reception,
        "required_six_fields_complete_rows": complete_required,
        "required_six_fields_incomplete_rows": len(rows) - complete_required,
        "files": {},
    }

    files = {
        "courses": ("courses.csv", rows, COURSE_FIELDS),
        "yearly": ("statistics_yearly.csv", statistics["yearly"], None),
        "monthly": ("statistics_monthly.csv", statistics["monthly"], None),
        "weekly": ("statistics_weekly.csv", statistics["weekly"], None),
        "major_category": (
            "statistics_major_category.csv",
            statistics["major_category"],
            None,
        ),
        "category": ("statistics_category.csv", statistics["category"], None),
        "provider": ("statistics_provider.csv", statistics["provider"], None),
        "table_type": ("statistics_table_type.csv", statistics["table_type"], None),
        "monthly_category": (
            "statistics_monthly_category.csv",
            statistics["monthly_category"],
            None,
        ),
        "monthly_provider": (
            "statistics_monthly_provider.csv",
            statistics["monthly_provider"],
            None,
        ),
    }
    for key, (filename, file_rows, headers) in files.items():
        write_csv(output_dir / filename, file_rows, headers)
        summary["files"][key] = filename

    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "README.md").write_text(
        "\n".join(
            [
                "# 수집 데이터 및 접수일 기준 통계",
                "",
                f"- 생성 시각: {summary['generated_at']}",
                f"- 대상 행: {summary['course_rows']:,}",
                f"- 접수 시작일 보유: {summary['reception_start_rows']:,}",
                f"- 접수 시작일 누락: {summary['missing_reception_start_rows']:,}",
                f"- 필수 6개 필드 완전: {summary['required_six_fields_complete_rows']:,}",
                "- 연·월·주 통계 기준: `apply_start`",
                "- CSV 인코딩: UTF-8 BOM",
                "",
            ]
        ),
        encoding="utf-8",
    )

    latest_dir = output_root / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    for source in output_dir.iterdir():
        if source.is_file():
            shutil.copy2(source, latest_dir / source.name)
    return output_dir, summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export collected courses and reception-date statistics.",
    )
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--include-inactive", action="store_true")
    parser.add_argument("--from-date", type=date.fromisoformat)
    parser.add_argument("--to-date", type=date.fromisoformat)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.from_date and args.to_date and args.from_date > args.to_date:
        raise SystemExit("--from-date must be on or before --to-date")
    raw_rows = fetch_courses(include_inactive=args.include_inactive)
    output_dir, summary = write_report(
        raw_rows,
        output_root=args.output_root,
        include_inactive=args.include_inactive,
        from_date=args.from_date,
        to_date=args.to_date,
    )
    print(
        json.dumps(
            {
                "output_dir": str(output_dir),
                **summary,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
