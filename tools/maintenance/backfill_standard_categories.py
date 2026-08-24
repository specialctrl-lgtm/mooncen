from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from psycopg2.extras import execute_values

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from DB.db_utils import get_db_cursor
from tools.standard_category_mapper import classify_standard_category


CULTURE_CENTER_PROVIDERS = {
    "HOMEPLUS",
    "LOTTE",
    "EMART",
    "HYUNDAI_DEPT",
    "GALLERIA",
    "AK_PLAZA",
    "ELAND_RETAIL",
    "SHINSEGAE_ACADEMY",
    "LOTTE_MART",
}
CULTURE_CENTER_CATEGORY_NAMES = {"문화센터", "문화 센터"}
CULTURE_CENTER_STANDARD_CATEGORY_CONFIG = str(ROOT / "config" / "culture_center_standard_categories.yaml")
REQUIRED_COURSE_COLUMNS = frozenset(
    {"standard_category_key", "standard_category_label"}
)


def is_culture_center_course(row: dict[str, Any]) -> bool:
    provider = str(row.get("provider") or "").strip().upper()
    if provider in CULTURE_CENTER_PROVIDERS:
        return True
    values = (
        row.get("service_group"),
        row.get("collection_category"),
        row.get("domain_category"),
    )
    return any(str(value or "").strip() in CULTURE_CENTER_CATEGORY_NAMES for value in values)


def standard_category_values(row: dict[str, Any]) -> tuple[str, str]:
    config_path = CULTURE_CENTER_STANDARD_CATEGORY_CONFIG if is_culture_center_course(row) else None
    result = classify_standard_category(
        {
            "title": row.get("title"),
            "title_raw": row.get("title_raw"),
            "category_raw": row.get("category_raw"),
            "collection_category": row.get("collection_category"),
            "domain_category": row.get("domain_category"),
            "source_group": row.get("source_group"),
            "program_type": row.get("program_type"),
            "description": row.get("description"),
        },
        config_path,
    )
    return result.key, result.label


def ensure_columns() -> None:
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'courses'
              AND column_name IN %s
            """,
            (tuple(sorted(REQUIRED_COURSE_COLUMNS)),),
        )
        present = {str(row["column_name"]) for row in cursor.fetchall()}
    missing = sorted(REQUIRED_COURSE_COLUMNS - present)
    if missing:
        raise RuntimeError(
            "courses schema is missing standard category columns: "
            + ", ".join(missing)
        )


def fetch_batch(limit: int, include_inactive: bool, force: bool, last_id: str | None) -> list[dict[str, Any]]:
    where = []
    params: list[Any] = []
    if not include_inactive:
        where.append("is_active IS TRUE")
    if not force:
        where.append("(standard_category_key IS NULL OR standard_category_label IS NULL)")
    if last_id:
        where.append("id > %s::uuid")
        params.append(last_id)
    params.append(limit)
    sql = f"""
        SELECT id, provider, title, title_raw, category_raw, collection_category,
               domain_category, source_group, program_type, description, service_group
        FROM courses
        {'WHERE ' + ' AND '.join(where) if where else ''}
        ORDER BY id
        LIMIT %s
    """
    with get_db_cursor() as cursor:
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]


def update_batch(rows: list[tuple[str, str, str]]) -> int:
    if not rows:
        return 0
    with get_db_cursor(dict_cursor=False) as cursor:
        execute_values(
            cursor,
            """
            UPDATE courses AS c
            SET standard_category_key = v.standard_category_key,
                standard_category_label = v.standard_category_label
            FROM (VALUES %s) AS v(id, standard_category_key, standard_category_label)
            WHERE c.id = v.id::uuid
            """,
            rows,
            page_size=len(rows),
        )
        return cursor.rowcount


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill standard category columns on courses.")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--include-inactive", action="store_true")
    parser.add_argument("--force", action="store_true", help="Recompute rows even when category columns are already filled.")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ensure_columns()
    total_seen = 0
    total_updated = 0
    by_label: Counter[str] = Counter()
    last_id: str | None = None

    while True:
        rows = fetch_batch(args.batch_size, args.include_inactive, args.force, last_id)
        if not rows:
            break
        updates: list[tuple[str, str, str]] = []
        for row in rows:
            standard_key, standard_label = standard_category_values(row)
            row_id = str(row["id"])
            updates.append((row_id, standard_key, standard_label))
            by_label[standard_label] += 1
            last_id = row_id
        total_seen += len(rows)
        if not args.dry_run:
            total_updated += update_batch(updates)
        else:
            total_updated += len(updates)
        print(f"processed={total_seen} {'would_update' if args.dry_run else 'updated'}={total_updated}")

    print(f"scanned={total_seen}")
    print(f"{'would_update' if args.dry_run else 'updated'}={total_updated}")
    print("by_label=" + ", ".join(f"{label}:{count}" for label, count in by_label.most_common()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
