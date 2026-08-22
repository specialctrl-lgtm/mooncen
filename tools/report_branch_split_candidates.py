from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parents[1]))

from DB.db_utils import get_db_cursor
from utils import clean_text


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "logs" / "crawler_dev_reports"

INSTITUTION_TOKENS = (
    "도서관",
    "복지관",
    "문화센터",
    "체육",
    "수영장",
    "센터",
    "학습관",
    "교육관",
    "박물관",
    "미술관",
    "과학관",
    "청소년",
    "수목원",
    "생태",
    "공원",
    "이음터",
)

BROAD_TOKENS = (
    "통합예약",
    "예약시스템",
    "예약포털",
    "공공서비스",
    "평생학습",
    "교육포털",
    "대표 홈페이지",
    "홈페이지",
    "시청",
    "군청",
    "구청",
)


def is_broad_branch_name(value: Any, provider: str = "") -> bool:
    text = clean_text(value)
    if not text:
        return True
    if provider and text.upper() == clean_text(provider).upper():
        return True
    if any(token in text for token in BROAD_TOKENS):
        return True
    if any(token in text for token in INSTITUTION_TOKENS):
        return False
    return bool(re.fullmatch(r"(?:[가-힣]+(?:특별자치도|특별시|광역시|도)\s*)?[가-힣]+(?:시|군|구)", text))


def risk_level(row: dict[str, Any]) -> str:
    if int(row["missing_branch"] or 0) > 0:
        return "HIGH"
    if int(row["active_branches"] or 0) <= 1 and bool(row["sample_branch_broad"]):
        return "HIGH"
    if int(row["active_branches"] or 0) <= 1 and int(row["active_courses"] or 0) >= 50:
        return "REVIEW"
    return "LOW"


def load_rows(min_active: int, limit: int) -> list[dict[str, Any]]:
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            WITH stats AS (
                SELECT c.provider,
                       COALESCE(NULLIF(c.collection_category,''), NULLIF(c.domain_category,''), '미분류') AS category,
                       COUNT(*) FILTER (WHERE c.is_active) AS active_courses,
                       COUNT(DISTINCT c.branch_id) FILTER (WHERE c.is_active) AS active_branches,
                       COUNT(*) FILTER (WHERE c.is_active AND c.branch_id IS NULL) AS missing_branch,
                       COALESCE(MIN(b.name) FILTER (WHERE c.is_active), '') AS sample_branch,
                       COALESCE(MIN(c.venue_name) FILTER (WHERE c.is_active AND c.venue_name IS NOT NULL AND btrim(c.venue_name) <> ''), '') AS sample_venue,
                       COALESCE(MIN(c.venue_address) FILTER (WHERE c.is_active AND c.venue_address IS NOT NULL AND btrim(c.venue_address) <> ''), '') AS sample_address,
                       COALESCE(MIN(c.title) FILTER (WHERE c.is_active), '') AS sample_title
                FROM courses c
                LEFT JOIN branches b ON b.id = c.branch_id
                GROUP BY c.provider, COALESCE(NULLIF(c.collection_category,''), NULLIF(c.domain_category,''), '미분류')
            )
            SELECT provider, category, active_courses, active_branches, missing_branch,
                   sample_branch, sample_venue, sample_address, sample_title
            FROM stats
            WHERE active_courses >= %s
              AND (missing_branch > 0 OR active_branches <= 1)
            ORDER BY active_courses DESC, provider
            LIMIT %s
            """,
            (min_active, limit),
        )
        rows = [dict(row) for row in cursor.fetchall()]

    for row in rows:
        row["sample_branch_broad"] = is_broad_branch_name(row.get("sample_branch"), row.get("provider"))
        row["risk"] = risk_level(row)
        if row["missing_branch"]:
            row["reason"] = "branch_id missing"
        elif row["sample_branch_broad"]:
            row["reason"] = "broad branch name"
        else:
            row["reason"] = "single branch; verify if real single site"
    return rows


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    fields = [
        "risk",
        "provider",
        "category",
        "active_courses",
        "active_branches",
        "missing_branch",
        "sample_branch",
        "sample_venue",
        "sample_address",
        "sample_title",
        "reason",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def write_markdown(rows: list[dict[str, Any]], path: Path) -> None:
    headers = ["risk", "provider", "category", "courses", "branches", "sample_branch", "sample_venue", "reason"]
    lines = [
        "# Branch split candidates",
        "",
        f"Generated at: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        values = [
            row.get("risk", ""),
            row.get("provider", ""),
            row.get("category", ""),
            str(row.get("active_courses", "")),
            str(row.get("active_branches", "")),
            clean_text(row.get("sample_branch"))[:40],
            clean_text(row.get("sample_venue"))[:40],
            row.get("reason", ""),
        ]
        lines.append("| " + " | ".join(value.replace("|", "/") for value in values) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def print_table(rows: list[dict[str, Any]]) -> None:
    print("| risk | provider | category | courses | branches | sample_branch | sample_venue | reason |")
    print("| --- | --- | --- | ---: | ---: | --- | --- | --- |")
    for row in rows:
        print(
            "| {risk} | {provider} | {category} | {active_courses} | {active_branches} | {sample_branch} | {sample_venue} | {reason} |".format(
                risk=row.get("risk", ""),
                provider=row.get("provider", ""),
                category=row.get("category", ""),
                active_courses=row.get("active_courses", ""),
                active_branches=row.get("active_branches", ""),
                sample_branch=clean_text(row.get("sample_branch"))[:30].replace("|", "/"),
                sample_venue=clean_text(row.get("sample_venue"))[:30].replace("|", "/"),
                reason=row.get("reason", ""),
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Report providers that may need branch-level crawler splitting.")
    parser.add_argument("--min-active", type=int, default=20)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    rows = load_rows(args.min_active, args.limit)
    print_table(rows)

    if args.write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_path = OUT_DIR / f"branch_split_candidates_{stamp}.csv"
        md_path = OUT_DIR / f"branch_split_candidates_{stamp}.md"
        write_csv(rows, csv_path)
        write_markdown(rows, md_path)
        print(f"\ncsv={csv_path}")
        print(f"markdown={md_path}")


if __name__ == "__main__":
    main()
