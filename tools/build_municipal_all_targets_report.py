from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping

import yaml
from psycopg2.extras import RealDictCursor

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from DB.db_utils import get_db_connection


REPORT_DIR = ROOT / "logs" / "municipal_crawler_reports"
DEFAULT_BASE_REPORT = (
    REPORT_DIR / "municipal_yaml_crawler_20260729_055032_539067_10924.yaml"
)
FIELD_NAMES = ("target", "fee", "date", "place", "category", "time")
FIELD_LABELS = {
    "target": "대상",
    "fee": "요금",
    "date": "날짜",
    "place": "장소",
    "category": "분야",
    "time": "시간",
}


def clean(value: Any) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def first_text(*values: Any) -> str:
    for value in values:
        text = clean(value)
        if text:
            return text
    return ""


def raw_value(raw_fields: Any, *keys: str) -> str:
    if not isinstance(raw_fields, Mapping):
        return ""
    return first_text(*(raw_fields.get(key) for key in keys))


def format_number(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        number = Decimal(str(value))
    except Exception:
        return clean(value)
    if number == number.to_integral_value():
        return f"{int(number):,}"
    return format(number.normalize(), "f")


def target_display(row: Mapping[str, Any]) -> str:
    age_range = ""
    minimum = row.get("target_min_age")
    maximum = row.get("target_max_age")
    if minimum is not None or maximum is not None:
        if minimum is not None and maximum is not None:
            age_range = f"{minimum}~{maximum}세"
        elif minimum is not None:
            age_range = f"{minimum}세 이상"
        else:
            age_range = f"{maximum}세 이하"
    tags = row.get("target_tags")
    tag_text = " ".join(clean(value) for value in tags or [] if clean(value))
    return first_text(
        row.get("target"),
        row.get("eligibility_raw"),
        row.get("target_age_group"),
        age_range,
        tag_text,
        raw_value(row.get("raw_fields"), "target", "eligibility"),
        "대상 별도 안내",
    )


def fee_display(row: Mapping[str, Any]) -> str:
    fee = row.get("fee")
    if fee is not None:
        amount = format_number(fee)
        return "무료" if amount in {"0", "0.0"} else f"{amount}원"
    source_fee = raw_value(
        row.get("raw_fields"),
        "fee",
        "fee_raw",
        "source_fee",
        "tuition",
        "price",
    )
    if source_fee:
        return source_fee
    material_fee = row.get("material_fee")
    if material_fee is not None:
        amount = format_number(material_fee)
        return "재료비 없음" if amount in {"0", "0.0"} else f"재료비 {amount}원"
    return first_text(
        raw_value(row.get("raw_fields"), "material_fee"),
        "요금 별도 안내",
    )


def date_display(row: Mapping[str, Any]) -> str:
    start = clean(row.get("start_date"))
    end = clean(row.get("end_date"))
    if start and end:
        return start if start == end else f"{start} ~ {end}"
    if start or end:
        return start or end
    source_period = raw_value(
        row.get("raw_fields"),
        "period",
        "event_period",
        "date",
    )
    if source_period:
        return source_period
    schedule_dates = row.get("schedule_dates")
    if isinstance(schedule_dates, list) and schedule_dates:
        first = clean(schedule_dates[0])
        last = clean(schedule_dates[-1])
        return first if first == last else f"{first} ~ {last}"
    return "날짜 별도 안내"


def place_display(row: Mapping[str, Any]) -> str:
    venue = first_text(
        row.get("venue_name"),
        raw_value(row.get("raw_fields"), "venue", "place", "location"),
    )
    venue_address = clean(row.get("venue_address"))
    if venue and venue_address and venue_address not in venue:
        return f"{venue} / {venue_address}"
    if venue or venue_address:
        return venue or venue_address
    branch = clean(row.get("branch_name"))
    branch_address = clean(row.get("branch_address"))
    if branch and branch_address and branch_address not in branch:
        return f"{branch} / {branch_address}"
    return branch or branch_address or "장소 별도 안내"


def category_display(row: Mapping[str, Any]) -> str:
    return first_text(
        row.get("category_raw"),
        row.get("domain_category"),
        row.get("collection_category"),
        row.get("program_type"),
        row.get("standard_category_label"),
        raw_value(row.get("raw_fields"), "category"),
        "분야 별도 안내",
    )


def time_display(row: Mapping[str, Any]) -> str:
    schedule = first_text(
        row.get("schedule_raw"),
        raw_value(row.get("raw_fields"), "schedule", "time", "hours"),
    )
    if schedule:
        return schedule
    days = row.get("schedule_days")
    day_text = " ".join(clean(value) for value in days or [] if clean(value))
    start = clean(row.get("schedule_time_start"))
    end = clean(row.get("schedule_time_end"))
    time_text = f"{start}~{end}" if start and end else start or end
    return " ".join(part for part in (day_text, time_text) if part) or "시간 별도 안내"


def display_fields(row: Mapping[str, Any]) -> dict[str, str]:
    return {
        "target": target_display(row),
        "fee": fee_display(row),
        "date": date_display(row),
        "place": place_display(row),
        "category": category_display(row),
        "time": time_display(row),
    }


def omission_flags(row: Mapping[str, Any], fields: Mapping[str, str]) -> dict[str, bool]:
    raw_fields = row.get("raw_fields")
    source_flags = {
        "target": "target_source_omission",
        "fee": "fee_source_omission",
        "date": "date_source_omission",
        "place": "venue_source_omission",
        "category": "category_source_omission",
        "time": "schedule_source_omission",
    }
    result: dict[str, bool] = {}
    for field, flag in source_flags.items():
        raw_flag = raw_fields.get(flag) if isinstance(raw_fields, Mapping) else None
        result[field] = (
            str(raw_flag).strip().lower() in {"1", "true", "yes"}
            or "별도 안내" in fields[field]
            or "미기재" in fields[field]
        )
    return result


def load_latest_reports(
    base_report_path: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    base = yaml.safe_load(base_report_path.read_text(encoding="utf-8")) or {}
    base_reports = base.get("reports") or []
    providers = {
        clean(report.get("provider")): report
        for report in base_reports
        if clean(report.get("provider"))
    }
    latest: dict[str, tuple[str, str, dict[str, Any]]] = {}
    for path in REPORT_DIR.glob("municipal_yaml_crawler_*.yaml"):
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        generated_at = clean(document.get("generated_at"))
        for report in document.get("reports") or []:
            provider = clean(report.get("provider"))
            if provider not in providers:
                continue
            candidate = (generated_at, path.name, report)
            if candidate[:2] > latest.get(provider, ("", "", {}))[:2]:
                latest[provider] = candidate
    reports = {provider: value[2] for provider, value in latest.items()}
    report_files = {provider: value[1] for provider, value in latest.items()}
    missing = sorted(set(providers) - set(reports))
    if missing:
        raise RuntimeError(f"providers without reports: {', '.join(missing)}")
    return reports, report_files


def fetch_active_courses(providers: Iterable[str]) -> list[dict[str, Any]]:
    provider_list = sorted(set(providers))
    connection = get_db_connection()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT
                    c.provider,
                    c.provider_course_id,
                    c.title,
                    c.target,
                    c.eligibility_raw,
                    c.target_age_group,
                    c.target_min_age,
                    c.target_max_age,
                    c.target_tags,
                    c.fee,
                    c.material_fee,
                    c.start_date,
                    c.end_date,
                    c.schedule_dates,
                    c.venue_name,
                    c.venue_address,
                    c.category_raw,
                    c.domain_category,
                    c.collection_category,
                    c.program_type,
                    c.standard_category_label,
                    c.schedule_raw,
                    c.schedule_time_start,
                    c.schedule_time_end,
                    c.schedule_days,
                    c.status,
                    c.raw_url,
                    c.raw_fields,
                    b.name AS branch_name,
                    b.address AS branch_address
                FROM courses c
                LEFT JOIN branches b ON b.id = c.branch_id
                WHERE c.is_active
                  AND c.provider = ANY(%s)
                ORDER BY c.provider, c.provider_course_id
                """,
                (provider_list,),
            )
            return [dict(row) for row in cursor.fetchall()]
    finally:
        connection.close()


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def markdown_table(provider_rows: list[dict[str, Any]]) -> str:
    lines = [
        "# 지자체 크롤러 전체 검증",
        "",
        "| provider | 대상 | 수집 | DB 활성 | 대상 | 요금 | 날짜 | 장소 | 분야 | 시간 | 결과 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in provider_rows:
        total = int(row["db_active_rows"])
        coverage = [
            f"{row[f'{field}_covered_rows']}/{total}"
            for field in FIELD_NAMES
        ]
        lines.append(
            "| {provider} | {name} | {collected} | {active} | {coverage} | {result} |".format(
                provider=row["provider"],
                name=str(row["name"]).replace("|", "/"),
                collected=row["collected_rows"],
                active=total,
                coverage=" | ".join(coverage),
                result=row["quality_status"],
            )
        )
    return "\n".join(lines) + "\n"


def build(base_report_path: Path, output_dir: Path) -> dict[str, Any]:
    reports, report_files = load_latest_reports(base_report_path)
    course_rows = fetch_active_courses(reports)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    exported_courses: list[dict[str, Any]] = []
    for row in course_rows:
        fields = display_fields(row)
        flags = omission_flags(row, fields)
        provider = clean(row.get("provider"))
        grouped[provider].append(
            {
                **row,
                "_display": fields,
                "_omission": flags,
            }
        )
        exported_courses.append(
            {
                "provider": provider,
                "provider_course_id": clean(row.get("provider_course_id")),
                "title": clean(row.get("title")),
                "대상": fields["target"],
                "요금": fields["fee"],
                "날짜": fields["date"],
                "장소": fields["place"],
                "분야": fields["category"],
                "시간": fields["time"],
                "상태": clean(row.get("status")),
                "원문_URL": clean(row.get("raw_url")),
            }
        )

    provider_rows: list[dict[str, Any]] = []
    for provider in sorted(reports):
        report = reports[provider]
        records = grouped.get(provider, [])
        active = len(records)
        coverage = {
            field: sum(bool(record["_display"][field]) for record in records)
            for field in FIELD_NAMES
        }
        omissions = {
            field: sum(record["_omission"][field] for record in records)
            for field in FIELD_NAMES
        }
        success = bool(report.get("success"))
        collected = int(report.get("collected") or 0)
        no_current = success and collected == 0
        complete = all(count == active for count in coverage.values())
        if not success:
            quality_status = "FAIL"
        elif not complete:
            quality_status = "FIELD_MISSING"
        elif no_current:
            quality_status = "PASS_NO_CURRENT_DATA"
        elif active == 0:
            quality_status = "DB_EMPTY"
        else:
            quality_status = "PASS"
        row = {
            "provider": provider,
            "name": clean(report.get("name")),
            "source_url": clean(report.get("url")),
            "latest_report": report_files[provider],
            "success": success,
            "collected_rows": collected,
            "saved_rows": int(report.get("saved") or 0),
            "db_active_rows": active,
            "no_current_data": no_current,
            "quality_status": quality_status,
            "parser": clean(report.get("parser")),
            "error": first_text(
                report.get("configured_collection_error"),
                report.get("error"),
            ),
        }
        for field in FIELD_NAMES:
            row[f"{field}_covered_rows"] = coverage[field]
            row[f"{field}_source_omission_rows"] = omissions[field]
        provider_rows.append(row)

    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"municipal_all_targets_audit_{timestamp}"
    provider_csv = output_dir / f"{stem}.csv"
    course_csv = output_dir / f"municipal_all_courses_required_fields_{timestamp}.csv"
    markdown = output_dir / f"{stem}.md"
    yaml_path = output_dir / f"{stem}.yaml"
    provider_fields = [
        "provider",
        "name",
        "source_url",
        "latest_report",
        "success",
        "collected_rows",
        "saved_rows",
        "db_active_rows",
        "no_current_data",
        *(f"{field}_covered_rows" for field in FIELD_NAMES),
        *(f"{field}_source_omission_rows" for field in FIELD_NAMES),
        "quality_status",
        "parser",
        "error",
    ]
    write_csv(provider_csv, provider_rows, list(provider_fields))
    write_csv(
        course_csv,
        exported_courses,
        [
            "provider",
            "provider_course_id",
            "title",
            "대상",
            "요금",
            "날짜",
            "장소",
            "분야",
            "시간",
            "상태",
            "원문_URL",
        ],
    )
    markdown.write_text(markdown_table(provider_rows), encoding="utf-8")
    field_missing = {
        field: sum(
            int(row["db_active_rows"]) - int(row[f"{field}_covered_rows"])
            for row in provider_rows
        )
        for field in FIELD_NAMES
    }
    source_omissions = {
        field: sum(int(row[f"{field}_source_omission_rows"]) for row in provider_rows)
        for field in FIELD_NAMES
    }
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "base_report": str(base_report_path.relative_to(ROOT)),
        "summary": {
            "targets": len(provider_rows),
            "success": sum(bool(row["success"]) for row in provider_rows),
            "failed": sum(not bool(row["success"]) for row in provider_rows),
            "providers_with_current_data": sum(
                int(row["db_active_rows"]) > 0 for row in provider_rows
            ),
            "providers_without_current_data": sum(
                bool(row["no_current_data"]) for row in provider_rows
            ),
            "collected_rows": sum(int(row["collected_rows"]) for row in provider_rows),
            "db_active_rows": len(exported_courses),
            "field_missing_rows": field_missing,
            "source_omission_rows": source_omissions,
            "quality_status_counts": dict(
                sorted(
                    {
                        status: sum(row["quality_status"] == status for row in provider_rows)
                        for status in {row["quality_status"] for row in provider_rows}
                    }.items()
                )
            ),
        },
        "artifacts": {
            "provider_csv": provider_csv.name,
            "course_csv": course_csv.name,
            "markdown": markdown.name,
        },
        "providers": provider_rows,
    }
    yaml_path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, width=160),
        encoding="utf-8",
    )
    return {
        "yaml": yaml_path,
        "provider_csv": provider_csv,
        "course_csv": course_csv,
        "markdown": markdown,
        "summary": payload["summary"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a 323-provider crawler audit and required-field course table."
    )
    parser.add_argument(
        "--base-report",
        type=Path,
        default=DEFAULT_BASE_REPORT,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPORT_DIR,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_report = args.base_report.resolve()
    output_dir = args.output_dir.resolve()
    result = build(base_report, output_dir)
    print(yaml.safe_dump({key: str(value) for key, value in result.items()}, allow_unicode=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
