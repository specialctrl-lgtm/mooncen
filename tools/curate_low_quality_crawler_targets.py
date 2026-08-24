from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
TARGET_DIR = ROOT / "config" / "crawl_targets"
OUT_DIR = ROOT / "logs" / "crawler_dev_reports"

DEFAULT_GRADES = {"D", "ERROR", "NO_DATA"}
RUNNABLE_STATUSES = {"ready", "partial", "generated", "candidate"}


def clean(value: Any) -> str:
    return str(value or "").strip()


def iter_target_files() -> list[Path]:
    if TARGET_DIR.exists():
        return sorted(path for path in TARGET_DIR.glob("*.yaml") if path.name != "index.yaml")
    legacy = ROOT / "config" / "collected_yaml_crawl_targets.yaml"
    return [legacy] if legacy.exists() else []


def load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def target_quality(target: dict[str, Any]) -> dict[str, Any]:
    quality = target.get("last_quality")
    return quality if isinstance(quality, dict) else {}


def is_low_quality_target(
    target: dict[str, Any],
    *,
    max_score: float,
    grades: set[str],
    include_zero_collection: bool,
) -> bool:
    status = clean(target.get("crawler_status") or target.get("status")).lower()
    if status not in RUNNABLE_STATUSES or status.startswith("duplicate_url:"):
        return False
    quality = target_quality(target)
    if not quality:
        return False
    grade = clean(quality.get("grade")).upper()
    score_raw = quality.get("score")
    try:
        score = float(score_raw)
    except (TypeError, ValueError):
        score = 0.0
    collected = int(quality.get("collected") or 0)
    if include_zero_collection and collected <= 0:
        return True
    return grade in grades or score < max_score


def collect_candidates(args: argparse.Namespace) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    grades = {grade.upper() for grade in args.grades}
    provider_filter = {provider.upper() for provider in (args.providers or [])}
    for path in iter_target_files():
        data = load_yaml(path)
        targets = data.get("targets") or []
        for index, target in enumerate(targets):
            if not isinstance(target, dict):
                continue
            provider = clean(target.get("provider")).upper()
            if provider_filter and provider not in provider_filter:
                continue
            if not is_low_quality_target(
                target,
                max_score=args.max_score,
                grades=grades,
                include_zero_collection=args.include_zero_collection,
            ):
                continue
            quality = target_quality(target)
            rows.append(
                {
                    "file": str(path.relative_to(ROOT)).replace("\\", "/"),
                    "index": index,
                    "provider": clean(target.get("provider")),
                    "name": clean(target.get("name")),
                    "status": clean(target.get("crawler_status") or target.get("status")),
                    "grade": clean(quality.get("grade")).upper(),
                    "score": quality.get("score", ""),
                    "collected": quality.get("collected", ""),
                    "parser": clean(quality.get("parser")),
                    "error_kind": clean(quality.get("error_kind")),
                    "url": clean(target.get("url") or target.get("list_url") or target.get("base_url")),
                }
            )
    return sorted(rows, key=lambda row: (float(row["score"] or 0), int(row["collected"] or 0), row["provider"]))


def apply_exclusions(rows: list[dict[str, Any]], reason: str) -> int:
    by_file: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_file.setdefault(row["file"], []).append(row)

    updated = 0
    now = datetime.now().isoformat(timespec="seconds")
    for rel_file, file_rows in by_file.items():
        path = ROOT / rel_file
        data = load_yaml(path)
        targets = data.get("targets") or []
        for row in file_rows:
            index = int(row["index"])
            if index < 0 or index >= len(targets) or not isinstance(targets[index], dict):
                continue
            target = targets[index]
            target["crawler_status"] = "blocked"
            target["blocked_reason"] = reason
            target["excluded_at"] = now
            updated += 1
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=140), encoding="utf-8")
    return updated


def write_outputs(rows: list[dict[str, Any]], applied: bool) -> tuple[Path, Path]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = OUT_DIR / f"low_quality_crawler_exclusions_{stamp}.csv"
    md_path = OUT_DIR / f"low_quality_crawler_exclusions_{stamp}.md"
    fields = ["provider", "grade", "score", "collected", "status", "parser", "error_kind", "url", "file"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})

    lines = [
        "# Low Quality Crawler Exclusion Candidates",
        "",
        f"- generated_at: {datetime.now().isoformat(timespec='seconds')}",
        f"- applied: {str(applied).lower()}",
        "",
        "| provider | grade | score | rows | status | parser | url |",
        "| --- | --- | ---: | ---: | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| {provider} | {grade} | {score} | {collected} | {status} | {parser} | {url} |".format(
                provider=row["provider"],
                grade=row["grade"],
                score=row["score"],
                collected=row["collected"],
                status=row["status"],
                parser=row["parser"],
                url=row["url"].replace("|", "/"),
            )
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return csv_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="List or block crawler targets that stayed low quality.")
    parser.add_argument("--max-score", type=float, default=35.0)
    parser.add_argument("--grades", nargs="*", default=sorted(DEFAULT_GRADES))
    parser.add_argument("--include-zero-collection", action="store_true", default=True)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--providers", nargs="*", help="Limit curation to specific providers.")
    parser.add_argument("--apply", action="store_true", help="Mark selected targets as blocked in config/crawl_targets YAML files.")
    parser.add_argument("--reason", default="low_quality_unresolved")
    args = parser.parse_args()

    rows = collect_candidates(args)[: max(0, args.limit)]
    updated = apply_exclusions(rows, args.reason) if args.apply and rows else 0
    csv_path, md_path = write_outputs(rows, args.apply)

    print("| provider | grade | score | rows | status | url |")
    print("| --- | --- | ---: | ---: | --- | --- |")
    for row in rows:
        print(f"| {row['provider']} | {row['grade']} | {row['score']} | {row['collected']} | {row['status']} | {row['url']} |")
    print()
    print(f"candidates={len(rows)}")
    if args.apply:
        print(f"blocked={updated}")
    print(f"csv={csv_path.relative_to(ROOT).as_posix()}")
    print(f"markdown={md_path.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
