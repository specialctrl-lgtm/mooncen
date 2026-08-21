from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from psycopg2.extras import execute_batch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from DB.db_utils import get_db_cursor
from ai_processor import AIProcessor


DEFAULT_SOURCES = ("", "ai", "rule_fallback")


def fetch_rows(
    limit: int | None,
    provider: str | None,
    include_all_sources: bool,
) -> list[dict[str, Any]]:
    where = ["COALESCE(is_active, TRUE) = TRUE"]
    params: list[Any] = []

    if provider:
        where.append("provider = %s")
        params.append(provider.upper())

    if not include_all_sources:
        where.append(
            """
            (
                ai_title_result IS NULL
                OR COALESCE(ai_title_processed, FALSE) = FALSE
                OR COALESCE(ai_title_result->>'source', '') = ANY(%s)
            )
            """
        )
        params.append(list(DEFAULT_SOURCES))

    limit_sql = "LIMIT %s" if limit else ""
    if limit:
        params.append(limit)

    with get_db_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT id, provider, title, title_raw, title_prefix_removed, target,
                   target_age_group, target_min_age, target_max_age,
                   target_with_parent, target_tags, target_age_is_explicit,
                   description, category_raw, schedule_raw,
                   COALESCE(ai_title_processed, FALSE) AS ai_title_processed,
                   COALESCE(ai_title_result, '{{}}'::jsonb) AS ai_title_result
            FROM courses
            WHERE {" AND ".join(where)}
            ORDER BY updated_at NULLS FIRST, created_at NULLS FIRST, id
            {limit_sql}
            """,
            params,
        )
        return [dict(row) for row in cursor.fetchall()]


def normalize_result(processor: AIProcessor, row: dict[str, Any]) -> dict[str, Any] | None:
    result = processor.analyze_title(row)
    if not result:
        return None
    metadata = dict(result.get("ai_title_result") or {})
    metadata["backfill"] = "rule_title_metadata"
    result["ai_title_result"] = metadata
    return result


def needs_update(row: dict[str, Any], result: dict[str, Any]) -> bool:
    metadata = row.get("ai_title_result") or {}
    source = metadata.get("source") if isinstance(metadata, dict) else None
    if source in DEFAULT_SOURCES or not row.get("ai_title_processed"):
        return True

    if result.get("clear_target_text") and str(row.get("target") or "").strip():
        return True

    comparable = (
        ("title", "title"),
        ("target", "target"),
        ("target_age_group", "target_age_group"),
        ("target_min_age", "target_min_age"),
        ("target_max_age", "target_max_age"),
        ("target_with_parent", "target_with_parent"),
    )
    for row_key, result_key in comparable:
        if row.get(row_key) != result.get(result_key):
            return True
    return False


def update_rows(results: list[tuple[str, dict[str, Any]]], dry_run: bool) -> None:
    if dry_run or not results:
        return

    payloads = []
    for course_id, result in results:
        payloads.append(
            {
                "id": str(course_id),
                "title": result["title"],
                "target": result.get("target"),
                "target_age_group": result.get("target_age_group"),
                "target_min_age": result.get("target_min_age"),
                "target_max_age": result.get("target_max_age"),
                "target_with_parent": result.get("target_with_parent", False),
                "target_tags": result.get("target_tags") or [],
                "title_prefix_removed": result.get("title_prefix_removed"),
                "ai_title_confidence": result.get("ai_title_confidence"),
                "clear_target_age_bounds": bool(result.get("clear_target_age_bounds")),
                "clear_target_text": bool(result.get("clear_target_text")),
                "ai_title_result": json.dumps(result.get("ai_title_result") or {}, ensure_ascii=False),
            }
        )

    with get_db_cursor() as cursor:
        execute_batch(
            cursor,
            """
            UPDATE courses
            SET
                title = %(title)s,
                target = CASE
                    WHEN COALESCE(target_age_is_explicit, FALSE)
                     AND (target_min_age IS NOT NULL OR target_max_age IS NOT NULL)
                     AND (target_min_age IS NULL OR target_max_age IS NULL OR target_min_age <= target_max_age)
                    THEN target
                    WHEN %(clear_target_text)s THEN NULL
                    WHEN %(target)s IS NOT NULL
                    THEN %(target)s
                    ELSE COALESCE(NULLIF(btrim(target), ''), %(target)s)
                END,
                target_age_group = CASE
                    WHEN COALESCE(target_age_is_explicit, FALSE)
                     AND (target_min_age IS NOT NULL OR target_max_age IS NOT NULL)
                     AND (target_min_age IS NULL OR target_max_age IS NULL OR target_min_age <= target_max_age)
                    THEN target_age_group
                    WHEN %(target)s IS NOT NULL
                     AND (%(target_min_age)s IS NOT NULL OR %(target_max_age)s IS NOT NULL)
                    THEN %(target_age_group)s
                    WHEN target_min_age IS NOT NULL
                     AND target_max_age IS NOT NULL
                     AND target_min_age > target_max_age
                    THEN %(target_age_group)s
                    WHEN %(clear_target_text)s AND %(target_age_group)s IS NULL THEN NULL
                    WHEN %(target_age_group)s IS NOT NULL
                     AND (
                        %(target_min_age)s IS NOT NULL
                        OR %(target_max_age)s IS NOT NULL
                        OR target_age_group IS NULL
                        OR %(clear_target_age_bounds)s
                     )
                    THEN %(target_age_group)s
                    WHEN target_min_age IS NOT NULL OR target_max_age IS NOT NULL
                    THEN target_age_group
                    ELSE COALESCE(%(target_age_group)s, target_age_group)
                END,
                target_min_age = CASE
                    WHEN COALESCE(target_age_is_explicit, FALSE)
                     AND (target_min_age IS NOT NULL OR target_max_age IS NOT NULL)
                     AND (target_min_age IS NULL OR target_max_age IS NULL OR target_min_age <= target_max_age)
                    THEN target_min_age
                    WHEN %(target)s IS NOT NULL
                     AND (%(target_min_age)s IS NOT NULL OR %(target_max_age)s IS NOT NULL)
                    THEN %(target_min_age)s
                    WHEN target_min_age IS NOT NULL
                     AND target_max_age IS NOT NULL
                     AND target_min_age > target_max_age
                    THEN %(target_min_age)s
                    WHEN %(clear_target_age_bounds)s THEN NULL
                    WHEN %(clear_target_text)s AND %(target_min_age)s IS NULL THEN NULL
                    ELSE COALESCE(%(target_min_age)s, target_min_age)
                END,
                target_max_age = CASE
                    WHEN COALESCE(target_age_is_explicit, FALSE)
                     AND (target_min_age IS NOT NULL OR target_max_age IS NOT NULL)
                     AND (target_min_age IS NULL OR target_max_age IS NULL OR target_min_age <= target_max_age)
                    THEN target_max_age
                    WHEN %(target)s IS NOT NULL
                     AND (%(target_min_age)s IS NOT NULL OR %(target_max_age)s IS NOT NULL)
                    THEN %(target_max_age)s
                    WHEN target_min_age IS NOT NULL
                     AND target_max_age IS NOT NULL
                     AND target_min_age > target_max_age
                    THEN %(target_max_age)s
                    WHEN %(clear_target_age_bounds)s THEN NULL
                    WHEN %(clear_target_text)s AND %(target_max_age)s IS NULL THEN NULL
                    ELSE COALESCE(%(target_max_age)s, target_max_age)
                END,
                target_with_parent = CASE
                    WHEN %(clear_target_text)s THEN %(target_with_parent)s
                    ELSE COALESCE(target_with_parent, FALSE) OR %(target_with_parent)s
                END,
                target_tags = CASE
                    WHEN %(clear_target_text)s THEN %(target_tags)s
                    WHEN COALESCE(array_length(%(target_tags)s::text[], 1), 0) > 0
                     AND (
                        %(target)s IS NOT NULL
                        OR %(target_age_group)s IS NOT NULL
                        OR %(target_min_age)s IS NOT NULL
                        OR %(target_max_age)s IS NOT NULL
                     )
                    THEN %(target_tags)s
                    WHEN COALESCE(array_length(target_tags, 1), 0) > 0
                    THEN target_tags
                    ELSE %(target_tags)s
                END,
                title_prefix_removed = COALESCE(%(title_prefix_removed)s, title_prefix_removed),
                ai_title_processed = TRUE,
                ai_title_confidence = %(ai_title_confidence)s,
                ai_title_result = %(ai_title_result)s::jsonb,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = CAST(%(id)s AS uuid)
            """,
            payloads,
            page_size=200,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill clean title and target age metadata with deterministic rules.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--provider", default=None)
    parser.add_argument("--include-all-sources", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    processor = AIProcessor()
    processor.model = None
    processor.provider = None

    rows = fetch_rows(args.limit, args.provider, args.include_all_sources)
    updates: list[tuple[str, dict[str, Any]]] = []
    samples: list[dict[str, Any]] = []

    for row in rows:
        result = normalize_result(processor, row)
        if not result or not needs_update(row, result):
            continue
        updates.append((str(row["id"]), result))
        if len(samples) < 20:
            metadata = row.get("ai_title_result") or {}
            samples.append(
                {
                    "id": str(row["id"]),
                    "provider": row.get("provider"),
                    "source": metadata.get("source") if isinstance(metadata, dict) else None,
                    "title_before": row.get("title"),
                    "title_after": result.get("title"),
                    "target_before": row.get("target"),
                    "target_after": result.get("target"),
                    "clear_target_text": bool(result.get("clear_target_text")),
                    "age_after": [
                        result.get("target_age_group"),
                        result.get("target_min_age"),
                        result.get("target_max_age"),
                    ],
                }
            )

    update_rows(updates, args.dry_run)

    print(
        json.dumps(
            {
                "scanned": len(rows),
                "matched_updates": len(updates),
                "dry_run": args.dry_run,
                "provider": args.provider,
                "include_all_sources": args.include_all_sources,
                "samples": samples,
            },
            ensure_ascii=False,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
