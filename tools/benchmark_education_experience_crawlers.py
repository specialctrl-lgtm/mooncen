from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "config" / "generated_yaml_crawler_registry.yaml"
TARGET_DIR = ROOT / "config" / "crawl_targets"
REPORT_DIR = ROOT / "logs" / "municipal_crawler_reports"
OUT_DIR = ROOT / "logs" / "education_experience_crawler_benchmarks"

DEFAULT_PROVIDERS = [
    "MUNI_RESERVE_ANSAN_GO_KR_8236CAF0",
    "MUNI_RESERVE_ANSAN_GO_KR_02253999",
    "MUNI_RESERVE_ANSAN_GO_KR_5D6B8309",
]

DEFAULT_SOURCE_GROUPS = [
    "arts_culture",
    "museum_science",
    "arboretum_ecology",
    "public_reservation",
    "youth",
]

RUNNABLE_STATUSES = {"ready", "partial", "generated", "candidate"}
STATIC_PROVIDER_CRAWLERS = {
    "BUSAN_RESERVATION": "Crawler/Crawler_BusanReservation.py",
    "SEOUL_PUBLIC_SERVICE": "Crawler/Crawler_SeoulPublicService.py",
}

FIELD_GROUPS = {
    "core": ["title", "branch", "raw_url"],
    "important": ["period", "schedule_raw", "fee", "status", "target", "description"],
}


def load_registry() -> dict[str, dict[str, Any]]:
    data = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8")) or {}
    targets = data.get("targets") or []
    registry = {
        str(target.get("provider") or ""): target
        for target in targets
        if isinstance(target, dict) and target.get("provider")
    }
    for path in iter_target_files():
        data = load_yaml(path)
        for target in data.get("targets") or []:
            if not isinstance(target, dict):
                continue
            provider = clean(target.get("provider"))
            crawler = STATIC_PROVIDER_CRAWLERS.get(provider)
            if not crawler:
                continue
            registry[provider] = {
                **target,
                "provider": provider,
                "crawler": crawler,
                "source": target.get("source_group") or path.stem,
                "status": target.get("crawler_status") or target.get("status"),
                "target_status": target.get("crawler_status") or target.get("status"),
                "enabled": clean(target.get("crawler_status") or target.get("status")).lower() != "blocked",
                "disabled_reason": target.get("blocked_reason") if clean(target.get("crawler_status") or target.get("status")).lower() == "blocked" else "",
                "manual_stdout_report": True,
            }
    return registry


def clean(value: Any) -> str:
    return str(value or "").strip()


def iter_target_files() -> list[Path]:
    if not TARGET_DIR.exists():
        return []
    return sorted(path for path in TARGET_DIR.glob("*.yaml") if path.name != "index.yaml")


def load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def source_groups_to_files(source_groups: list[str]) -> set[str]:
    return {
        group if group.endswith(".yaml") else f"{group}.yaml"
        for group in source_groups
    }


def target_score(target: dict[str, Any]) -> float:
    quality = target.get("last_quality")
    if not isinstance(quality, dict):
        return 0.0
    try:
        return float(quality.get("score") or 0)
    except (TypeError, ValueError):
        return 0.0


def target_grade(target: dict[str, Any]) -> str:
    quality = target.get("last_quality")
    return clean(quality.get("grade")).upper() if isinstance(quality, dict) else ""


def target_collected(target: dict[str, Any]) -> int:
    quality = target.get("last_quality")
    if not isinstance(quality, dict):
        return 0
    try:
        return int(quality.get("collected") or 0)
    except (TypeError, ValueError):
        return 0


def is_no_current_quality(target: dict[str, Any]) -> bool:
    quality = target.get("last_quality")
    if not isinstance(quality, dict):
        return False
    return clean(quality.get("error_kind")).startswith("no_current_data")


def providers_from_target_files(args: argparse.Namespace) -> list[str]:
    selected_files = source_groups_to_files(args.source_groups or DEFAULT_SOURCE_GROUPS)
    grades = {grade.upper() for grade in args.low_quality_grades}
    rows: list[tuple[float, int, str]] = []
    for path in iter_target_files():
        if path.name not in selected_files:
            continue
        data = load_yaml(path)
        for target in data.get("targets") or []:
            if not isinstance(target, dict):
                continue
            status = clean(target.get("crawler_status") or target.get("status")).lower()
            provider = clean(target.get("provider"))
            if not provider or status not in RUNNABLE_STATUSES:
                continue
            quality = target.get("last_quality")
            if args.low_quality_only:
                if not isinstance(quality, dict):
                    continue
                if is_no_current_quality(target) and not args.include_no_current:
                    continue
                score = target_score(target)
                grade_value = target_grade(target)
                if target_collected(target) > 0 and grade_value not in grades and score >= args.max_score:
                    continue
            rows.append((target_score(target), target_collected(target), provider))
    rows.sort(key=lambda row: (row[0], row[1], row[2]))
    providers = [provider for _, _, provider in rows]
    if args.limit and args.limit > 0:
        providers = providers[: args.limit]
    return providers


def pct(count: int, total: int) -> float:
    return round((count / total) * 100, 1) if total else 0.0


def field_pct(fields: dict[str, Any], names: list[str], total: int) -> float:
    if not total:
        return 0.0
    values = [pct(int(fields.get(name) or 0), total) for name in names]
    return round(sum(values) / len(values), 1) if values else 0.0


def grade(row: dict[str, Any]) -> str:
    if not row["runnable"]:
        return "SKIP"
    if row.get("no_current"):
        return "NO_DATA"
    if row["reason"]:
        return "ERROR"
    if row["collected"] <= 0:
        return "NO_DATA"
    if row["core_pct"] >= 100 and row["important_pct"] >= 60:
        return "A"
    if row["core_pct"] >= 100 and row["important_pct"] >= 35:
        return "B"
    if row["core_pct"] >= 100:
        return "C"
    return "D"


def latest_report_after(started_at: float) -> Path | None:
    if not REPORT_DIR.exists():
        return None
    candidates = [
        path
        for path in REPORT_DIR.glob("municipal_yaml_crawler_*.yaml")
        if path.stat().st_mtime >= started_at - 1
    ]
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def parse_report(path: Path, provider: str) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    reports = data.get("reports") or []
    for item in reports:
        if isinstance(item, dict) and item.get("provider") == provider:
            return item
    return {}


def runnable_target(target: dict[str, Any]) -> tuple[bool, str]:
    enabled = bool(target.get("enabled", True))
    reason = str(target.get("disabled_reason") or "").strip()
    status = str(target.get("status") or target.get("target_status") or "").strip()
    crawler = ROOT / str(target.get("crawler") or "")
    if not enabled:
        return False, reason or status or "disabled"
    if status in {"blocked", "needs_discovery", "needs_parser"}:
        return False, reason or status
    if not crawler.exists():
        return False, f"crawler file not found: {target.get('crawler')}"
    return True, ""


def build_command(target: dict[str, Any], args: argparse.Namespace) -> list[str]:
    crawler = ROOT / str(target.get("crawler"))
    return [
        sys.executable,
        "-X",
        "utf8",
        str(crawler),
        "--per-target-limit",
        str(args.per_target_limit),
        "--max-pages",
        str(args.max_pages),
        "--detail-limit",
        str(args.detail_limit),
        "--timeout",
        str(args.request_timeout),
    ]


def parse_manual_stdout(stdout: str, row: dict[str, Any], elapsed: float) -> bool:
    summary = re.search(
        r"provider=(?P<provider>\S+)\s+rows=(?P<rows>\d+)\s+saved=(?P<saved>\d+)\s+parser=(?P<parser>\S+)\s+pages=(?P<pages>\d+)\s+detail=(?P<detail>\d+)",
        stdout,
    )
    if not summary:
        return False
    collected = int(summary.group("rows"))
    fields: dict[str, int] = {}
    field_line = re.search(r"field_counts\s+(.+)", stdout)
    if field_line:
        for key, value in re.findall(r"([A-Za-z_]+)=(\d+)", field_line.group(1)):
            fields[key] = int(value)
    field_totals = {
        "title": fields.get("title", 0),
        "branch": fields.get("branch", 0),
        "raw_url": collected,
        "period": fields.get("period", 0),
        "schedule_raw": fields.get("schedule_raw", 0),
        "fee": fields.get("fee", fields.get("fee_raw", 0)),
        "status": fields.get("status", 0),
        "target": fields.get("target", 0),
        "description": fields.get("description", 0),
    }
    row.update(
        {
            "collected": collected,
            "rows_per_second": round(collected / elapsed, 2) if elapsed > 0 else 0.0,
            "pages": int(summary.group("pages")),
            "detail_pages": int(summary.group("detail")),
            "parser": summary.group("parser"),
            "core_pct": field_pct(field_totals, FIELD_GROUPS["core"], collected),
            "important_pct": field_pct(field_totals, FIELD_GROUPS["important"], collected),
            "period_pct": pct(field_totals["period"], collected),
            "schedule_pct": pct(field_totals["schedule_raw"], collected),
            "fee_pct": pct(field_totals["fee"], collected),
            "status_pct": pct(field_totals["status"], collected),
            "target_pct": pct(field_totals["target"], collected),
            "description_pct": pct(field_totals["description"], collected),
            "reason": "",
            "no_current": False,
        }
    )
    row["grade"] = grade(row)
    return True


def benchmark_provider(provider: str, target: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    runnable, skip_reason = runnable_target(target)
    row = {
        "provider": provider,
        "name": target.get("name", ""),
        "category": target.get("source", ""),
        "status": target.get("status") or target.get("target_status") or "",
        "runnable": runnable,
        "grade": "SKIP",
        "elapsed_seconds": 0.0,
        "collected": 0,
        "rows_per_second": 0.0,
        "pages": 0,
        "detail_pages": 0,
        "parser": "",
        "core_pct": 0.0,
        "important_pct": 0.0,
        "period_pct": 0.0,
        "schedule_pct": 0.0,
        "fee_pct": 0.0,
        "status_pct": 0.0,
        "target_pct": 0.0,
        "description_pct": 0.0,
        "report": "",
        "reason": skip_reason,
        "no_current": False,
        "url": target.get("url", ""),
    }
    if not runnable:
        return row

    command = build_command(target, args)
    started = time.time()
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=args.process_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        row["elapsed_seconds"] = round(time.time() - started, 2)
        row["reason"] = f"process timeout after {args.process_timeout}s"
        row["grade"] = grade(row)
        row["stdout_tail"] = (exc.stdout or "")[-500:]
        row["stderr_tail"] = (exc.stderr or "")[-500:]
        return row

    elapsed = time.time() - started
    row["elapsed_seconds"] = round(elapsed, 2)
    report_path = latest_report_after(started)
    if completed.returncode != 0:
        row["reason"] = f"exit_code={completed.returncode}: {(completed.stderr or completed.stdout)[-500:].strip()}"
        row["grade"] = grade(row)
        return row
    if target.get("manual_stdout_report"):
        if parse_manual_stdout(completed.stdout or "", row, elapsed):
            return row
        row["reason"] = "manual crawler completed but stdout could not be parsed"
        row["grade"] = grade(row)
        return row
    if not report_path:
        row["reason"] = "crawler completed but report file was not created"
        row["grade"] = grade(row)
        return row

    item = parse_report(report_path, provider)
    if not item:
        row["reason"] = f"report did not include provider: {report_path.name}"
        row["grade"] = grade(row)
        return row

    fields = item.get("fields") or {}
    collected = int(item.get("collected") or 0)
    item_error = str(item.get("error") or "").strip()
    no_current_reason = str(item.get("no_current_reason") or "").strip() if collected <= 0 else ""
    row.update(
        {
            "collected": collected,
            "rows_per_second": round(collected / elapsed, 2) if elapsed > 0 else 0.0,
            "pages": int(item.get("pages") or 0),
            "detail_pages": int(item.get("detail_pages") or 0),
            "parser": item.get("parser", ""),
            "core_pct": field_pct(fields, FIELD_GROUPS["core"], collected),
            "important_pct": field_pct(fields, FIELD_GROUPS["important"], collected),
            "period_pct": pct(int(fields.get("period") or 0), collected),
            "schedule_pct": pct(int(fields.get("schedule_raw") or 0), collected),
            "fee_pct": pct(int(fields.get("fee") or 0), collected),
            "status_pct": pct(int(fields.get("status") or 0), collected),
            "target_pct": pct(int(fields.get("target") or 0), collected),
            "description_pct": pct(int(fields.get("description") or 0), collected),
            "report": str(report_path.relative_to(ROOT)).replace("\\", "/"),
            "reason": item_error or no_current_reason,
            "no_current": bool(no_current_reason and not item_error),
        }
    )
    row["grade"] = grade(row)
    return row


def markdown_table(rows: list[dict[str, Any]]) -> str:
    headers = [
        "provider",
        "run",
        "grade",
        "elapsed_s",
        "rows",
        "rows/s",
        "pages",
        "detail",
        "parser",
        "core",
        "important",
        "reason",
    ]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["provider"]),
                    "Y" if row["runnable"] else "N",
                    str(row["grade"]),
                    str(row["elapsed_seconds"]),
                    str(row["collected"]),
                    str(row["rows_per_second"]),
                    str(row["pages"]),
                    str(row["detail_pages"]),
                    str(row["parser"]),
                    f"{row['core_pct']}%",
                    f"{row['important_pct']}%",
                    str(row["reason"]).replace("|", "/")[:160],
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def write_outputs(rows: list[dict[str, Any]]) -> tuple[Path, Path, Path]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = OUT_DIR / f"education_experience_crawler_benchmark_{timestamp}.csv"
    json_path = OUT_DIR / f"education_experience_crawler_benchmark_{timestamp}.json"
    md_path = OUT_DIR / f"education_experience_crawler_benchmark_{timestamp}.md"
    fieldnames = [
        "provider",
        "name",
        "status",
        "runnable",
        "grade",
        "elapsed_seconds",
        "collected",
        "rows_per_second",
        "pages",
        "detail_pages",
        "parser",
        "core_pct",
        "important_pct",
        "period_pct",
        "schedule_pct",
        "fee_pct",
        "status_pct",
        "target_pct",
        "description_pct",
        "report",
        "reason",
        "url",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(
        "\n".join(
            [
                "# Education/Experience Crawler Benchmark",
                "",
                f"- generated_at: {datetime.now().isoformat(timespec='seconds')}",
                f"- registry: {REGISTRY_PATH.relative_to(ROOT).as_posix()}",
                "",
                markdown_table(rows),
                "",
            ]
        ),
        encoding="utf-8",
    )
    return csv_path, json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark education/experience generated crawlers")
    parser.add_argument("--providers", nargs="*", default=DEFAULT_PROVIDERS)
    parser.add_argument(
        "--source-groups",
        nargs="*",
        help="Target YAML source groups to select providers from, e.g. arts_culture museum_science public_reservation youth.",
    )
    parser.add_argument("--low-quality-only", action="store_true", help="Select low quality targets from source groups instead of using default providers.")
    parser.add_argument("--include-no-current", action="store_true", help="Include targets whose last quality is no_current_data.")
    parser.add_argument("--max-score", type=float, default=60.0)
    parser.add_argument("--low-quality-grades", nargs="*", default=["D", "F", "ERROR", "NO_DATA"])
    parser.add_argument("--limit", type=int, default=0, help="Limit selected providers when using --low-quality-only.")
    parser.add_argument("--per-target-limit", type=int, default=0)
    parser.add_argument("--max-pages", type=int, default=2)
    parser.add_argument("--detail-limit", type=int, default=0)
    parser.add_argument("--request-timeout", type=int, default=30)
    parser.add_argument("--process-timeout", type=int, default=180)
    args = parser.parse_args()

    registry = load_registry()
    if args.low_quality_only or args.source_groups:
        args.providers = providers_from_target_files(args)
    rows: list[dict[str, Any]] = []
    for provider in args.providers:
        target = registry.get(provider)
        if not target:
            rows.append(
                {
                    "provider": provider,
                    "name": "",
                    "category": "",
                    "status": "",
                    "runnable": False,
                    "grade": "SKIP",
                    "elapsed_seconds": 0.0,
                    "collected": 0,
                    "rows_per_second": 0.0,
                    "pages": 0,
                    "detail_pages": 0,
                    "parser": "",
                    "core_pct": 0.0,
                    "important_pct": 0.0,
                    "period_pct": 0.0,
                    "schedule_pct": 0.0,
                    "fee_pct": 0.0,
                    "status_pct": 0.0,
                    "target_pct": 0.0,
                    "description_pct": 0.0,
                    "report": "",
                    "reason": "provider not found in generated YAML registry",
                    "url": "",
                }
            )
            continue
        rows.append(benchmark_provider(provider, target, args))

    csv_path, json_path, md_path = write_outputs(rows)
    print(markdown_table(rows))
    print()
    print(f"csv={csv_path.relative_to(ROOT).as_posix()}")
    print(f"json={json_path.relative_to(ROOT).as_posix()}")
    print(f"markdown={md_path.relative_to(ROOT).as_posix()}")
    return 1 if any(row["runnable"] and row["grade"] == "ERROR" for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
