from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from DB.db_utils import get_db_cursor
from service_group import infer_service_group
from tools.standard_category_mapper import (
    MOJIBAKE_HARD_MARKERS,
    MOJIBAKE_SOFT_MARKERS,
)


TARGET_DIR = ROOT / "config" / "crawl_targets"
DEFAULT_STATEMENT_TIMEOUT_MS = 120_000
MIN_STATEMENT_TIMEOUT_MS = 15_000
MAX_STATEMENT_TIMEOUT_MS = 300_000
_CATEGORY_HARD_DAMAGE_MARKERS = "".join(MOJIBAKE_HARD_MARKERS)
_CATEGORY_SOFT_DAMAGE_MARKERS = "".join(MOJIBAKE_SOFT_MARKERS)


def clean(value: Any) -> str:
    return str(value or "").strip()


def corrupted_category_sql(column: str) -> str:
    value = f"btrim(COALESCE({column}, ''))"
    return f"""(
        (length({value}) - length(replace({value}, '?', ''))) >= 2
        OR (
            length({value})
            - length(regexp_replace({value}, '[{_CATEGORY_HARD_DAMAGE_MARKERS}]', '', 'g'))
        ) >= 1
        OR (
            length({value})
            - length(regexp_replace({value}, '[{_CATEGORY_SOFT_DAMAGE_MARKERS}]', '', 'g'))
        ) >= 2
    )"""


def provider_repair_condition(values: dict[str, str]) -> str:
    conditions: list[str] = []
    for field in ("collection_category", "domain_category"):
        if clean(values.get(field)):
            conditions.append(
                f"(NULLIF(btrim({field}), '') IS NULL OR {corrupted_category_sql(field)})"
            )
    return "(" + " OR ".join(conditions) + ")" if conditions else ""


def load_provider_metadata(target_dir: Path) -> dict[str, dict[str, str]]:
    metadata: dict[str, dict[str, str]] = {}
    for path in sorted(target_dir.glob("*.yaml")):
        if path.name == "index.yaml":
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for row in data.get("targets") or []:
            if not isinstance(row, dict):
                continue
            provider = clean(row.get("provider")).upper()
            if not provider:
                continue
            values = {
                "collection_category": clean(row.get("collection_category") or row.get("domain_category")),
                "domain_category": clean(row.get("domain_category")),
                "source_group": clean(row.get("source_group")),
                "operator_type": clean(row.get("operator_type")),
                "service_group": clean(row.get("service_group")),
                "collection_type": clean(row.get("collection_type")),
            }
            values["service_group"] = values["service_group"] or infer_service_group(
                provider=provider,
                collection_category=values.get("collection_category"),
                domain_category=values.get("domain_category"),
                source_group=values.get("source_group"),
                operator_type=values.get("operator_type"),
                branch_name=row.get("branch") or row.get("name"),
                raw_url=row.get("url") or row.get("list_url") or row.get("base_url"),
            )
            values = {key: value for key, value in values.items() if value}
            if not values:
                continue
            existing = metadata.get(provider)
            if not existing or row.get("crawler_status") == "ready":
                metadata[provider] = values
    return metadata


def backfill(
    metadata: dict[str, dict[str, str]],
    dry_run: bool,
    statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS,
) -> tuple[int, int]:
    if not MIN_STATEMENT_TIMEOUT_MS <= statement_timeout_ms <= MAX_STATEMENT_TIMEOUT_MS:
        raise ValueError("statement_timeout_ms is outside the safe bound")
    matched_providers = 0
    updated_courses = 0
    with get_db_cursor() as cursor:
        cursor.execute(
            "SELECT set_config('statement_timeout', %s, true)",
            (f"{statement_timeout_ms}ms",),
        )
        for provider, values in metadata.items():
            repair_condition = provider_repair_condition(values)
            if not repair_condition:
                continue
            cursor.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM courses
                WHERE provider = %s
                  AND {repair_condition}
                """,
                (provider,),
            )
            count = int(cursor.fetchone()["count"])
            if count <= 0:
                continue
            matched_providers += 1
            updated_courses += count
            if dry_run:
                continue
            cursor.execute(
                f"""
                UPDATE courses
                SET
                    collection_category = COALESCE(NULLIF(%(collection_category)s, ''), collection_category),
                    domain_category = COALESCE(NULLIF(%(domain_category)s, ''), domain_category)
                WHERE provider = %(provider)s
                  AND {repair_condition}
                """,
                {
                    "provider": provider,
                    "collection_category": values.get("collection_category", ""),
                    "domain_category": values.get("domain_category", ""),
                },
            )
    return matched_providers, updated_courses


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill course category metadata from config/crawl_targets/*.yaml")
    parser.add_argument("--target-dir", type=Path, default=TARGET_DIR)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--statement-timeout-ms",
        type=int,
        default=DEFAULT_STATEMENT_TIMEOUT_MS,
    )
    args = parser.parse_args()
    if not MIN_STATEMENT_TIMEOUT_MS <= args.statement_timeout_ms <= MAX_STATEMENT_TIMEOUT_MS:
        parser.error(
            f"--statement-timeout-ms must be between "
            f"{MIN_STATEMENT_TIMEOUT_MS} and {MAX_STATEMENT_TIMEOUT_MS}"
        )
    return args


def main() -> int:
    args = parse_args()
    metadata = load_provider_metadata(args.target_dir)
    matched_providers, updated_courses = backfill(
        metadata,
        args.dry_run,
        args.statement_timeout_ms,
    )
    print(f"target_dir={args.target_dir}")
    print(f"metadata_providers={len(metadata)}")
    print(f"matched_providers={matched_providers}")
    print(f"{'would_update' if args.dry_run else 'updated'}_courses={updated_courses}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
