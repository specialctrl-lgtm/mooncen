from __future__ import annotations

import argparse
import csv
import glob
import os
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "logs" / "municipal_crawler_reports"
OUT_DIR = ROOT / "logs" / "municipal_crawler_quality"

CORE_FIELDS = ["title", "branch", "raw_url"]
IMPORTANT_FIELDS = ["period", "schedule_raw", "fee", "status", "target", "description"]
ALL_FIELDS = CORE_FIELDS + IMPORTANT_FIELDS


def latest_report() -> Path:
    files = glob.glob(str(REPORT_DIR / "municipal_yaml_crawler_*.yaml"))
    if not files:
        raise FileNotFoundError("No municipal crawler report found")
    return Path(max(files, key=os.path.getmtime))


def pct(value: int, total: int) -> float:
    return round((value / total) * 100, 1) if total else 0.0


def grade(row: dict) -> str:
    if row["error"]:
        return "ERROR"
    if row["collected"] <= 0:
        return "NO_DATA"
    if row["core_pct"] >= 100 and row["important_pct"] >= 60:
        return "A"
    if row["core_pct"] >= 100 and row["important_pct"] >= 35:
        return "B"
    if row["core_pct"] >= 100 and row["important_pct"] >= 15:
        return "C"
    return "D"


def build_rows(report: dict) -> list[dict]:
    rows = []
    for item in report.get("reports", []):
        fields = item.get("fields") or {}
        collected = int(item.get("collected") or 0)
        core_present = sum(1 for field in CORE_FIELDS if fields.get(field, 0) >= collected and collected > 0)
        important_present = sum(pct(fields.get(field, 0), collected) for field in IMPORTANT_FIELDS)
        row = {
            "provider": item.get("provider", ""),
            "name": item.get("name", ""),
            "url": item.get("url", ""),
            "success": bool(item.get("success")),
            "parser": item.get("parser", ""),
            "collected": collected,
            "saved": int(item.get("saved") or 0),
            "pages": int(item.get("pages") or 0),
            "detail_pages": int(item.get("detail_pages") or 0),
            "pagination_detected": bool(item.get("pagination_detected")),
            "discovered_links": int(item.get("discovered_links") or 0),
            "error": item.get("error", ""),
            "core_pct": round((core_present / len(CORE_FIELDS)) * 100, 1) if collected else 0.0,
            "important_pct": round(important_present / len(IMPORTANT_FIELDS), 1) if collected else 0.0,
        }
        for field in ALL_FIELDS:
            row[field] = int(fields.get(field) or 0)
            row[f"{field}_pct"] = pct(row[field], collected)
        row["grade"] = grade(row)
        rows.append(row)
    return rows


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "grade",
        "provider",
        "name",
        "parser",
        "collected",
        "pages",
        "detail_pages",
        "pagination_detected",
        "discovered_links",
        "core_pct",
        "important_pct",
        "title_pct",
        "branch_pct",
        "raw_url_pct",
        "period_pct",
        "schedule_raw_pct",
        "fee_pct",
        "status_pct",
        "target_pct",
        "description_pct",
        "error",
        "url",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def markdown_table(rows: list[dict], limit: int) -> str:
    headers = [
        "grade",
        "provider",
        "rows",
        "parser",
        "pages",
        "detail",
        "pg",
        "core",
        "important",
        "period",
        "schedule",
        "fee",
        "status",
        "target",
        "desc",
    ]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows[:limit]:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["grade"]),
                    str(row["provider"]),
                    str(row["collected"]),
                    str(row["parser"]),
                    str(row["pages"]),
                    str(row["detail_pages"]),
                    "Y" if row["pagination_detected"] else "N",
                    f"{row['core_pct']}%",
                    f"{row['important_pct']}%",
                    f"{row['period_pct']}%",
                    f"{row['schedule_raw_pct']}%",
                    f"{row['fee_pct']}%",
                    f"{row['status_pct']}%",
                    f"{row['target_pct']}%",
                    f"{row['description_pct']}%",
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build quality tables from municipal crawler report")
    parser.add_argument("--report")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    report_path = Path(args.report) if args.report else latest_report()
    if not report_path.is_absolute():
        report_path = ROOT / report_path
    report = yaml.safe_load(report_path.read_text(encoding="utf-8"))
    rows = build_rows(report)
    rows_sorted = sorted(rows, key=lambda row: (row["grade"], -row["important_pct"], -row["collected"], row["provider"]))
    good = [row for row in rows if row["grade"] in {"A", "B"}]
    weak = [row for row in rows if row["grade"] in {"C", "D", "NO_DATA", "ERROR"}]
    top = sorted(rows, key=lambda row: (-row["important_pct"], -row["collected"], row["provider"]))
    bottom = sorted(rows, key=lambda row: (row["grade"] != "ERROR", row["important_pct"], row["collected"], row["provider"]))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUT_DIR / (report_path.stem + "_quality.csv")
    md_path = OUT_DIR / (report_path.stem + "_quality.md")
    write_csv(rows_sorted, csv_path)

    summary = {
        "report": str(report_path.relative_to(ROOT)).replace("\\", "/"),
        "targets": len(rows),
        "good_ab": len(good),
        "weak": len(weak),
        "grades": {grade_name: sum(1 for row in rows if row["grade"] == grade_name) for grade_name in ["A", "B", "C", "D", "NO_DATA", "ERROR"]},
        "collected": sum(row["collected"] for row in rows),
        "field_totals": {field: sum(row[field] for row in rows) for field in ALL_FIELDS},
    }
    md = [
        "# Municipal Crawler Quality",
        "",
        "## Summary",
        "```yaml",
        yaml.safe_dump(summary, allow_unicode=True, sort_keys=False).strip(),
        "```",
        "",
        "## Top Quality",
        markdown_table(top, args.limit),
        "",
        "## Weak Or Failed",
        markdown_table(bottom, args.limit),
    ]
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"csv={csv_path}")
    print(f"markdown={md_path}")
    print(yaml.safe_dump(summary, allow_unicode=True, sort_keys=False))
    print("== Top Quality ==")
    print(markdown_table(top, args.limit))
    print("\n== Weak Or Failed ==")
    print(markdown_table(bottom, args.limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
