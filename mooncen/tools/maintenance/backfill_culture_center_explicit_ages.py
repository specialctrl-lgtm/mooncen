from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from DB.db_utils import get_db_cursor
from data_parser import TargetParser, parse_crawler_target
from target_cleaner import extract_target_text
from utils import clean_text


CULTURE_CENTER_PROVIDERS = {
    "HOMEPLUS",
    "EMART",
    "LOTTE",
    "HYUNDAI_DEPT",
    "GALLERIA",
    "AK_PLAZA",
    "ELAND_RETAIL",
    "SHINSEGAE_ACADEMY",
    "LOTTE_MART",
}

LOTTE_MEANINGLESS_TARGETS = {"1인강좌", "2인강좌", "1인", "2인", "강좌", "수강"}


def compact_parts(*values: object) -> str:
    return " ".join(text for text in (clean_text(value) for value in values) if text)


def source_text_for_row(row: dict[str, Any]) -> str:
    provider = str(row.get("provider") or "").upper()
    raw_title = clean_text(row.get("title_raw"))
    title = clean_text(row.get("title"))
    target = clean_text(row.get("target"))
    category_raw = clean_text(row.get("category_raw"))

    explicit_target = extract_target_text(raw_title) or extract_target_text(title) or target
    if provider == "LOTTE":
        meaningful = target and target not in LOTTE_MEANINGLESS_TARGETS
        if meaningful:
            return target
        return compact_parts(title, row.get("description"))

    return compact_parts(raw_title, title, explicit_target, row.get("eligibility_raw"), category_raw)


def build_update(row: dict[str, Any], parser: TargetParser) -> dict[str, Any] | None:
    parsed = parse_crawler_target(source_text_for_row(row), parser)
    if not parsed.get("age_is_explicit"):
        return None
    return {
        "id": row["id"],
        "target_age_group": parsed.get("age_group"),
        "target_min_age": parsed.get("min_age"),
        "target_max_age": parsed.get("max_age"),
        "target_with_parent": parsed.get("with_parent", False),
        "target_tags": parsed.get("tags") or [],
        "target_age_is_explicit": True,
    }


def main() -> int:
    arg_parser = argparse.ArgumentParser(
        description="Backfill explicit culture-center target ages and mark them as crawler-explicit."
    )
    arg_parser.add_argument("--dry-run", action="store_true")
    arg_parser.add_argument("--limit", type=int, default=0)
    arg_parser.add_argument("--sample", type=int, default=20)
    args = arg_parser.parse_args()

    parser = TargetParser()
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT id, provider, title, title_raw, target, category_raw, eligibility_raw,
                   description, target_age_group, target_min_age, target_max_age,
                   target_with_parent, target_tags, target_age_is_explicit
            FROM courses
            WHERE provider = ANY(%s)
              AND COALESCE(is_active, TRUE) = TRUE
            ORDER BY provider, updated_at DESC, id
            """,
            (sorted(CULTURE_CENTER_PROVIDERS),),
        )
        rows = [dict(row) for row in cursor.fetchall()]

        updates: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for row in rows:
            update = build_update(row, parser)
            if not update:
                continue
            changed = any(row.get(key) != update.get(key) for key in update if key != "id")
            if changed:
                updates.append((row, update))
                if args.limit and len(updates) >= args.limit:
                    break

        by_provider = Counter(row["provider"] for row, _ in updates)
        print(f"scanned={len(rows)} updates={len(updates)} dry_run={args.dry_run}")
        for provider, count in sorted(by_provider.items()):
            print(f"{provider}: {count}")
        for before, after in updates[: args.sample]:
            print(
                f"- {before['provider']} | {before.get('title')} | "
                f"age {before.get('target_min_age')}~{before.get('target_max_age')} -> "
                f"{after.get('target_min_age')}~{after.get('target_max_age')} | "
                f"group {before.get('target_age_group')} -> {after.get('target_age_group')}"
            )

        if args.dry_run or not updates:
            return 0

        for _, update in updates:
            cursor.execute(
                """
                UPDATE courses
                SET target_age_group = %(target_age_group)s,
                    target_min_age = %(target_min_age)s,
                    target_max_age = %(target_max_age)s,
                    target_with_parent = %(target_with_parent)s,
                    target_tags = %(target_tags)s,
                    target_age_is_explicit = %(target_age_is_explicit)s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %(id)s
                """,
                update,
            )
        print(f"updated={len(updates)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
