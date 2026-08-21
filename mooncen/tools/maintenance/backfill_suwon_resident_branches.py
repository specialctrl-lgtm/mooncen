from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_GeneratedYamlTargets import load_registry_targets, to_crawl_target
from Crawler.Crawler_MunicipalYaml import MunicipalDbWriter, clean_text, collect_from_url
from DB.course_upsert_guards import normalize_course_raw_url
from DB.db_utils import get_db_cursor


DEFAULT_PROVIDERS = {
    "MUNI_JANGAN_SUWON_GO_KR_D82A0EAE",
    "MUNI_PALDAL_SUWON_GO_KR_7F5BC8C6",
    "MUNI_PALDAL_SUWON_GO_KR_D78BD1B4",
}


def selected_targets(providers: set[str]) -> list[Any]:
    rows = []
    for item in load_registry_targets():
        provider = clean_text(item.get("provider")).upper()
        if provider in providers:
            rows.append(to_crawl_target(item))
    return rows


def update_course_branch(provider: str, raw_url: str, branch_id: str, branch_name: str, venue_name: str, dry_run: bool) -> int:
    raw_url = normalize_course_raw_url(raw_url)
    if not raw_url or (not branch_id and not dry_run):
        return 0
    legacy_raw_url_prefix = f"{raw_url}&mooncen_course_id="
    legacy_raw_url_query = f"{raw_url}?mooncen_course_id="
    with get_db_cursor() as cursor:
        if dry_run:
            cursor.execute(
                """
                SELECT COUNT(*) AS count
                FROM courses c
                JOIN branches b ON b.id = c.branch_id
                WHERE c.provider = %s
                  AND (
                        btrim(c.raw_url) = %s
                     OR btrim(c.raw_url) LIKE %s
                     OR btrim(c.raw_url) LIKE %s
                  )
                  AND b.name IS DISTINCT FROM %s
                """,
                (provider, raw_url, f"{legacy_raw_url_prefix}%", f"{legacy_raw_url_query}%", branch_name),
            )
            return int(cursor.fetchone()["count"])
        cursor.execute(
            """
            UPDATE courses
            SET branch_id = %s,
                venue_name = COALESCE(NULLIF(%s, ''), venue_name),
                raw_url = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE provider = %s
              AND (
                    btrim(raw_url) = %s
                 OR btrim(raw_url) LIKE %s
                 OR btrim(raw_url) LIKE %s
              )
              AND branch_id IS DISTINCT FROM %s
            """,
            (branch_id, venue_name, raw_url, provider, raw_url, f"{legacy_raw_url_prefix}%", f"{legacy_raw_url_query}%", branch_id),
        )
        return int(cursor.rowcount)


def backfill_provider(provider: str, max_pages: int, detail_limit: int, dry_run: bool) -> dict[str, Any]:
    targets = selected_targets({provider})
    if not targets:
        return {"provider": provider, "rows": 0, "updated": 0, "reason": "target_not_found"}
    target = targets[0]
    rows, parser, meta = collect_from_url(
        target,
        timeout=25,
        max_depth=1,
        max_pages=max_pages,
        detail_limit=detail_limit,
    )
    writer = MunicipalDbWriter(provider)
    updated = 0
    branches: set[str] = set()
    for row in rows:
        writer.normalize_branch_split_row(row)
        branch = writer.branch_info_from_row(row)
        branches.add(branch["name"])
        branch_id = "" if dry_run else writer.save_branch(
            branch["branch_code"],
            branch["name"],
            branch["address"],
            branch["phone"],
            branch["website_url"],
            branch["address_source"],
        )
        if dry_run:
            branch_id = "__dry_run__"
        updated += update_course_branch(
            provider,
            clean_text(row.get("raw_url")),
            branch_id,
            branch["name"],
            clean_text(row.get("venue_name") or branch["name"]),
            dry_run,
        )
    return {
        "provider": provider,
        "parser": parser,
        "rows": len(rows),
        "updated": updated,
        "branches": sorted(branches),
        "pages": meta.get("pages"),
        "dry_run": dry_run,
    }


def cleanup_stale_generic_rows(providers: set[str], dry_run: bool) -> int:
    provider_list = sorted(providers)
    with get_db_cursor() as cursor:
        if dry_run:
            cursor.execute(
                """
                SELECT COUNT(*) AS count
                FROM courses c
                JOIN branches b ON b.id = c.branch_id
                WHERE c.provider = ANY(%s)
                  AND c.is_active IS TRUE
                  AND b.name LIKE %s
                  AND (
                        c.title IN ('교육시간', '01 ~ .30')
                     OR c.raw_url LIKE %s
                     OR c.raw_fields::text LIKE %s
                  )
                """,
                (provider_list, "%수원시%", "%gorp_list.asp%", "%generic_table%"),
            )
            return int(cursor.fetchone()["count"])
        cursor.execute(
            """
            UPDATE courses c
            SET is_active = FALSE,
                removed_at = COALESCE(removed_at, CURRENT_TIMESTAMP),
                updated_at = CURRENT_TIMESTAMP
            FROM branches b
            WHERE b.id = c.branch_id
              AND c.provider = ANY(%s)
              AND c.is_active IS TRUE
              AND b.name LIKE %s
              AND (
                    c.title IN ('교육시간', '01 ~ .30')
                 OR c.raw_url LIKE %s
                 OR c.raw_fields::text LIKE %s
              )
            """,
            (provider_list, "%수원시%", "%gorp_list.asp%", "%generic_table%"),
        )
        return int(cursor.rowcount)


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill Suwon resident-autonomy course branches from dong names.")
    parser.add_argument("--provider", action="append", help="Provider to process. Defaults to known Suwon resident targets.")
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--detail-limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--cleanup-stale-generic", action="store_true", help="Deactivate old generic parser rows left under broad Suwon branches.")
    args = parser.parse_args()

    providers = {clean_text(value).upper() for value in args.provider or [] if clean_text(value)}
    if not providers:
        providers = set(DEFAULT_PROVIDERS)

    for provider in sorted(providers):
        result = backfill_provider(provider, args.max_pages, args.detail_limit, args.dry_run)
        print(
            f"{result['provider']} rows={result.get('rows', 0)} updated={result.get('updated', 0)} "
            f"parser={result.get('parser', '-')} pages={result.get('pages', '-')} dry_run={result.get('dry_run', args.dry_run)}"
        )
        branches = result.get("branches") or []
        if branches:
            print("  branches=" + ", ".join(branches[:12]) + (" ..." if len(branches) > 12 else ""))
        if result.get("reason"):
            print(f"  reason={result['reason']}")
    if args.cleanup_stale_generic:
        cleaned = cleanup_stale_generic_rows(providers, args.dry_run)
        print(f"cleanup_stale_generic updated={cleaned} dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
