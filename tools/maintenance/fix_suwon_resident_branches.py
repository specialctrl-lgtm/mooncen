from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_GeneratedYamlTargets import load_registry_targets, to_crawl_target
from Crawler.Crawler_MunicipalYaml import CrawlTarget, MunicipalDbWriter, collect_from_url
from DB.course_upsert_guards import delete_empty_branches_for_provider, normalize_course_raw_url
from DB.db_utils import get_db_cursor
from utils import clean_text


SUWON_RESIDENT_HOSTS = {
    "jangan.suwon.go.kr",
    "ksun.suwon.go.kr",
    "paldal.suwon.go.kr",
    "ytedu.suwon.go.kr",
}
SUWON_LEARNING_HOST = "learning.suwon.go.kr"
SUWON_LEARNING_BRANCH = "수원시 평생학습관"


def is_suwon_target(target: dict[str, Any]) -> bool:
    url = clean_text(target.get("url") or target.get("list_url") or target.get("base_url"))
    provider = clean_text(target.get("provider")).upper()
    return (
        any(host in url for host in SUWON_RESIDENT_HOSTS | {SUWON_LEARNING_HOST})
        or provider.startswith("MUNI_LEARNING_SUWON_GO_KR_")
        or provider.startswith("MUNI_JANGAN_SUWON_GO_KR_")
        or provider.startswith("MUNI_PALDAL_SUWON_GO_KR_")
    )


def is_resident_autonomy_target(target: CrawlTarget) -> bool:
    return any(host in clean_text(target.url) for host in SUWON_RESIDENT_HOSTS)


def suwon_room_scope(url: Any) -> str:
    query = parse_qs(urlparse(clean_text(url)).query)
    return clean_text((query.get("sbd_room") or [""])[0])


def can_apply_provider_branch_fallback(target: CrawlTarget, raw_url: Any) -> bool:
    if target.provider.startswith("MUNI_LEARNING_SUWON_GO_KR_"):
        return True
    if not is_resident_autonomy_target(target):
        return False
    target_room = suwon_room_scope(target.url)
    course_room = suwon_room_scope(raw_url)
    return bool(target_room and course_room and target_room == course_room)


def branch_is_broad(writer: MunicipalDbWriter, value: Any) -> bool:
    text = clean_text(value)
    return writer.is_broad_branch_name(text) or text.startswith("경기도 수원시") or text.startswith("수원시")


def collect_provider_rows(target: CrawlTarget, max_pages: int, detail_limit: int, timeout: int) -> list[dict[str, Any]]:
    rows, parser, meta = collect_from_url(
        target,
        timeout=timeout,
        max_pages=max_pages,
        detail_limit=detail_limit,
    )
    print(
        f"collected provider={target.provider} rows={len(rows)} parser={parser} "
        f"pages={meta.get('pages')} detail={meta.get('detail_pages')}"
    )
    return rows


def ensure_branch(writer: MunicipalDbWriter, row: dict[str, Any], dry_run: bool) -> tuple[str, dict[str, str]]:
    writer.normalize_branch_split_row(row)
    branch = writer.branch_info_from_row(row)
    if dry_run:
        return "", branch
    branch_id = writer.save_branch(
        branch["branch_code"],
        branch["name"],
        branch["address"],
        branch["phone"],
        branch["website_url"],
        branch["address_source"],
    )
    return branch_id or "", branch


def fetch_provider_courses(provider: str) -> list[dict[str, Any]]:
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT c.id, c.raw_url, c.branch_id, c.venue_name, b.name AS branch_name
              FROM courses c
              JOIN branches b ON b.id = c.branch_id
             WHERE c.provider = %s
            """,
            (provider,),
        )
        return list(cursor.fetchall())


def update_course_branch(
    *,
    course_id: str,
    branch_id: str,
    venue_name: str,
    venue_address: str,
    raw_url: str,
) -> None:
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            UPDATE courses
               SET branch_id = %s,
                   venue_name = COALESCE(NULLIF(%s, ''), venue_name),
                   venue_address = COALESCE(NULLIF(%s, ''), venue_address),
                   raw_url = COALESCE(NULLIF(%s, ''), raw_url),
                   updated_at = CURRENT_TIMESTAMP
             WHERE id = %s
            """,
            (branch_id, venue_name, venue_address, raw_url, course_id),
        )


def fix_provider(target: CrawlTarget, *, max_pages: int, detail_limit: int, timeout: int, dry_run: bool) -> dict[str, int]:
    writer = MunicipalDbWriter(target.provider)
    rows = collect_provider_rows(target, max_pages=max_pages, detail_limit=detail_limit, timeout=timeout)
    row_by_raw_url: dict[str, dict[str, Any]] = {}
    branch_counter: Counter[str] = Counter()
    branch_rows: dict[str, dict[str, Any]] = {}

    for row in rows:
        writer.normalize_branch_split_row(row)
        branch_name = clean_text(row.get("branch"))
        if branch_name:
            branch_counter[branch_name] += 1
            branch_rows.setdefault(branch_name, row)
        raw_url = normalize_course_raw_url(row.get("raw_url"))
        if raw_url:
            row_by_raw_url[raw_url] = row

    if target.provider.startswith("MUNI_LEARNING_SUWON_GO_KR_") and not branch_counter:
        branch_counter[SUWON_LEARNING_BRANCH] = 1
        branch_rows[SUWON_LEARNING_BRANCH] = {
            "provider": target.provider,
            "branch": SUWON_LEARNING_BRANCH,
            "venue_name": SUWON_LEARNING_BRANCH,
            "raw_url": target.url,
        }

    single_branch_name = ""
    if len(branch_counter) == 1:
        single_branch_name = next(iter(branch_counter))
    elif target.provider.startswith("MUNI_LEARNING_SUWON_GO_KR_"):
        single_branch_name = SUWON_LEARNING_BRANCH

    courses = fetch_provider_courses(target.provider)
    courses_by_raw_url: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for course in courses:
        normalized_raw_url = normalize_course_raw_url(course.get("raw_url"))
        if normalized_raw_url:
            courses_by_raw_url[normalized_raw_url].append(course)

    branch_id_cache: dict[str, tuple[str, dict[str, str]]] = {}
    checked = 0
    updated = 0
    matched_by_url = 0
    matched_by_provider_branch = 0

    def branch_for_row(row: dict[str, Any]) -> tuple[str, dict[str, str]]:
        branch_key = clean_text(row.get("branch")) or clean_text(row.get("venue_name")) or target.branch
        if branch_key not in branch_id_cache:
            branch_id_cache[branch_key] = ensure_branch(writer, row, dry_run)
        return branch_id_cache[branch_key]

    for raw_url, row in row_by_raw_url.items():
        matched_courses = courses_by_raw_url.get(raw_url, [])
        if not matched_courses:
            continue
        branch_id, branch = branch_for_row(row)
        for course in matched_courses:
            checked += 1
            if not branch_is_broad(writer, course.get("branch_name")) and clean_text(course.get("branch_name")) == branch["name"]:
                continue
            matched_by_url += 1
            updated += 1
            print(f"raw_url match: {target.provider} {course.get('branch_name')} -> {branch['name']} | {raw_url}")
            if not dry_run and branch_id:
                update_course_branch(
                    course_id=str(course["id"]),
                    branch_id=branch_id,
                    venue_name=branch["name"],
                    venue_address=branch["address"],
                    raw_url=raw_url,
                )

    if single_branch_name:
        row = dict(branch_rows.get(single_branch_name) or {})
        row.setdefault("provider", target.provider)
        row["branch"] = single_branch_name
        row.setdefault("venue_name", single_branch_name)
        branch_id, branch = branch_for_row(row)
        for course in courses:
            if not branch_is_broad(writer, course.get("branch_name")):
                continue
            checked += 1
            normalized_raw_url = normalize_course_raw_url(course.get("raw_url"))
            if normalized_raw_url in row_by_raw_url:
                continue
            if not can_apply_provider_branch_fallback(target, normalized_raw_url or course.get("raw_url")):
                continue
            matched_by_provider_branch += 1
            updated += 1
            print(
                f"provider branch fallback: {target.provider} {course.get('branch_name')} -> "
                f"{branch['name']} | {normalized_raw_url or course.get('raw_url') or '-'}"
            )
            if not dry_run and branch_id:
                update_course_branch(
                    course_id=str(course["id"]),
                    branch_id=branch_id,
                    venue_name=branch["name"],
                    venue_address=branch["address"],
                    raw_url=normalized_raw_url,
                )

    if not dry_run and updated:
        with get_db_cursor() as cursor:
            delete_empty_branches_for_provider(cursor, target.provider)

    return {
        "rows": len(rows),
        "courses": len(courses),
        "checked": checked,
        "updated": updated,
        "matched_by_url": matched_by_url,
        "matched_by_provider_branch": matched_by_provider_branch,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Reassign Suwon education course branches from city-level rows to actual centers.")
    parser.add_argument("--provider", action="append", help="Limit to a provider. Can be passed multiple times.")
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--detail-limit", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    provider_filter = {provider.upper() for provider in args.provider or []}
    targets = []
    for row in load_registry_targets():
        if not is_suwon_target(row):
            continue
        target = to_crawl_target(row)
        if provider_filter and target.provider.upper() not in provider_filter:
            continue
        targets.append(target)

    totals = Counter()
    for target in targets:
        stats = fix_provider(
            target,
            max_pages=args.max_pages,
            detail_limit=args.detail_limit,
            timeout=args.timeout,
            dry_run=args.dry_run,
        )
        totals.update(stats)
        print(f"stats provider={target.provider} {stats}")

    print(f"total {dict(totals)} dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
