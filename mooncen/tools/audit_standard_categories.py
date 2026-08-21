from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from DB.db_utils import get_db_cursor
from tools.standard_category_mapper import classify_standard_category


REPORT_DIR = PROJECT_ROOT / "logs" / "standard_category_audits"
CULTURE_CENTER_PROVIDERS = (
    "HOMEPLUS",
    "EMART",
    "LOTTE",
    "HYUNDAI_DEPT",
    "GALLERIA",
    "AK_PLAZA",
    "ELAND_RETAIL",
    "SHINSEGAE_ACADEMY",
    "LOTTE_MART",
)


def fetch_rows(provider: str | None, limit: int | None, include_inactive: bool) -> list[dict[str, Any]]:
    where = []
    params: list[Any] = []
    if not include_inactive:
        where.append("is_active IS TRUE")
    if provider:
        providers = [part.strip().upper() for part in provider.split(",") if part.strip()]
        where.append("provider = ANY(%s)")
        params.append(providers)
    limit_sql = ""
    if limit:
        limit_sql = " LIMIT %s"
        params.append(limit)
    sql = f"""
        SELECT id, provider, title, title_raw, category_raw, collection_category,
               domain_category, source_group, program_type, description, raw_url
        FROM courses
        {'WHERE ' + ' AND '.join(where) if where else ''}
        ORDER BY provider, title, id
        {limit_sql}
    """
    with get_db_cursor() as cursor:
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]


def write_reports(report: dict[str, Any], rows: list[dict[str, Any]], report_prefix: str) -> tuple[Path, Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = REPORT_DIR / f"{report_prefix}_{stamp}.json"
    csv_path = REPORT_DIR / f"{report_prefix}_{stamp}.csv"
    md_path = REPORT_DIR / f"{report_prefix}_{stamp}.md"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "provider",
                "standard_key",
                "standard_label",
                "confidence",
                "matched_terms",
                "title",
                "category_raw",
                "collection_category",
                "domain_category",
                "raw_url",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# Standard Category Audit",
        "",
        f"- scanned: `{report['scanned']}`",
        f"- classified: `{report['classified']}`",
        f"- uncategorized: `{report['uncategorized']}`",
        "",
        "## By Standard Category",
        "",
        "| category | count |",
        "| --- | ---: |",
    ]
    for label, count in report["by_label"].items():
        lines.append(f"| {label} | {count} |")
    lines.extend(["", "## Top Unclassified Raw Categories", "", "| raw | count |", "| --- | ---: |"])
    for raw, count in report["top_unclassified_raw"].items():
        lines.append(f"| {raw} | {count} |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return json_path, csv_path, md_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit standard course category rules against current DB rows.")
    parser.add_argument("--provider", help="Optional provider code or comma-separated provider codes.")
    parser.add_argument("--culture-centers", action="store_true", help="Shortcut for all culture-center providers.")
    parser.add_argument("--config", help="Optional category YAML path. Defaults to config/standard_categories.yaml.")
    parser.add_argument("--limit", type=int, help="Maximum rows to scan.")
    parser.add_argument("--include-inactive", action="store_true")
    args = parser.parse_args()

    provider = ",".join(CULTURE_CENTER_PROVIDERS) if args.culture_centers else args.provider
    rows = fetch_rows(provider, args.limit, args.include_inactive)
    by_key: Counter[str] = Counter()
    by_label: Counter[str] = Counter()
    by_provider: dict[str, Counter[str]] = defaultdict(Counter)
    unclassified_raw: Counter[str] = Counter()
    low_confidence: list[dict[str, Any]] = []
    csv_rows: list[dict[str, Any]] = []

    for row in rows:
        result = classify_standard_category(row, args.config)
        by_key[result.key] += 1
        by_label[result.label] += 1
        by_provider[str(row.get("provider") or "")][result.label] += 1
        if result.key == "uncategorized":
            raw = str(row.get("category_raw") or "<missing>").strip() or "<missing>"
            unclassified_raw[raw] += 1
        if result.confidence < 0.7 and len(low_confidence) < 80:
            low_confidence.append(
                {
                    "provider": row.get("provider"),
                    "title": row.get("title"),
                    "category_raw": row.get("category_raw"),
                    "standard_label": result.label,
                    "confidence": result.confidence,
                    "matched_terms": result.matched_terms,
                    "raw_url": row.get("raw_url"),
                }
            )
        csv_rows.append(
            {
                "provider": row.get("provider"),
                "standard_key": result.key,
                "standard_label": result.label,
                "confidence": result.confidence,
                "matched_terms": ", ".join(result.matched_terms),
                "title": row.get("title"),
                "category_raw": row.get("category_raw"),
                "collection_category": row.get("collection_category"),
                "domain_category": row.get("domain_category"),
                "raw_url": row.get("raw_url"),
            }
        )

    report = {
        "scanned": len(rows),
        "classified": len(rows) - by_key.get("uncategorized", 0),
        "uncategorized": by_key.get("uncategorized", 0),
        "by_key": dict(by_key.most_common()),
        "by_label": dict(by_label.most_common()),
        "by_provider": {provider: dict(counter.most_common()) for provider, counter in by_provider.items()},
        "top_unclassified_raw": dict(unclassified_raw.most_common(50)),
        "low_confidence_samples": low_confidence,
        "config": args.config or "config/standard_categories.yaml",
        "provider_filter": provider,
    }
    report_prefix = "culture_center_category_audit" if args.culture_centers else "standard_category_audit"
    json_path, csv_path, md_path = write_reports(report, csv_rows, report_prefix)
    print(
        f"scanned={report['scanned']} classified={report['classified']} "
        f"uncategorized={report['uncategorized']}"
    )
    print("by_label=" + json.dumps(report["by_label"], ensure_ascii=False))
    print(f"json={json_path}")
    print(f"csv={csv_path}")
    print(f"md={md_path}")


if __name__ == "__main__":
    main()
