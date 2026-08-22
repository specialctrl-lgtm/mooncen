from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from psycopg2.extras import execute_batch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from DB.db_utils import get_db_cursor
from tools.category_age_patterns import CULTURE_CENTER_PROVIDERS, build_category_age_updates


REPORT_DIR = os.path.join(PROJECT_ROOT, "logs", "category_age_audits")


def _parse_providers(values: list[str] | None) -> list[str]:
    if not values:
        return sorted(CULTURE_CENTER_PROVIDERS)
    providers: list[str] = []
    for value in values:
        providers.extend(part.strip().upper() for part in value.split(",") if part.strip())
    return list(dict.fromkeys(providers))


def _row_identity(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id")),
        "provider": row.get("provider"),
        "title": row.get("title"),
        "target": row.get("target"),
        "category_raw": row.get("category_raw"),
        "raw_url": row.get("raw_url"),
    }


def _fetch_rows(providers: list[str], include_inactive: bool, limit: int | None) -> list[dict[str, Any]]:
    params: list[Any] = [providers]
    where = ["provider = ANY(%s)"]
    if not include_inactive:
        where.append("is_active IS TRUE")
    limit_sql = ""
    if limit:
        limit_sql = " LIMIT %s"
        params.append(limit)

    sql = f"""
        SELECT id, provider, title, target, eligibility_raw, description, category_raw,
               collection_category, domain_category, target_age_group,
               target_min_age, target_max_age, raw_url
        FROM courses
        WHERE {' AND '.join(where)}
        ORDER BY provider, title, id
        {limit_sql}
    """
    with get_db_cursor() as cursor:
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]


def _apply_updates(changes: list[dict[str, Any]]) -> int:
    if not changes:
        return 0
    rows = []
    for change in changes:
        updates = change["updates"]
        rows.append(
            {
                "id": change["id"],
                "collection_category": updates.get("collection_category"),
                "domain_category": updates.get("domain_category"),
                "target_age_group": updates.get("target_age_group"),
                "target_min_age": updates.get("target_min_age"),
                "target_max_age": updates.get("target_max_age"),
                "set_collection_category": "collection_category" in updates,
                "set_domain_category": "domain_category" in updates,
                "set_target_age_group": "target_age_group" in updates,
                "set_target_min_age": "target_min_age" in updates,
                "set_target_max_age": "target_max_age" in updates,
            }
        )

    with get_db_cursor() as cursor:
        execute_batch(
            cursor,
            """
            UPDATE courses
            SET collection_category = CASE WHEN %(set_collection_category)s THEN %(collection_category)s ELSE collection_category END,
                domain_category = CASE WHEN %(set_domain_category)s THEN %(domain_category)s ELSE domain_category END,
                target_age_group = CASE WHEN %(set_target_age_group)s THEN %(target_age_group)s ELSE target_age_group END,
                target_min_age = CASE WHEN %(set_target_min_age)s THEN %(target_min_age)s ELSE target_min_age END,
                target_max_age = CASE WHEN %(set_target_max_age)s THEN %(target_max_age)s ELSE target_max_age END,
                updated_at = NOW()
            WHERE id = %(id)s
            """,
            rows,
            page_size=200,
        )
    return len(rows)


def _write_report(report: dict[str, Any]) -> str:
    os.makedirs(REPORT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    mode = "apply" if report["apply"] else "dry_run"
    path = os.path.join(REPORT_DIR, f"category_age_patterns_{mode}_{stamp}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, default=str)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit/apply category and target-age pattern fixes.")
    parser.add_argument("--provider", action="append", help="Provider code or comma-separated provider codes.")
    parser.add_argument("--limit", type=int, help="Maximum rows to scan.")
    parser.add_argument("--include-inactive", action="store_true", help="Scan inactive rows too.")
    parser.add_argument("--apply", action="store_true", help="Apply DB updates. Default is dry-run.")
    args = parser.parse_args()

    providers = _parse_providers(args.provider)
    rows = _fetch_rows(providers, args.include_inactive, args.limit)

    reason_counts: Counter[str] = Counter()
    provider_counts: dict[str, Counter[str]] = defaultdict(Counter)
    changes: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []

    for row in rows:
        decision = build_category_age_updates(row)
        if not decision.updates:
            continue
        for reason in decision.reasons:
            reason_counts[reason] += 1
            provider_counts[str(row.get("provider"))][reason] += 1
        change = {
            "id": row["id"],
            "identity": _row_identity(row),
            "updates": decision.updates,
            "reasons": decision.reasons,
        }
        changes.append(change)
        if len(samples) < 30:
            samples.append(change)

    applied = _apply_updates(changes) if args.apply else 0
    report = {
        "apply": args.apply,
        "providers": providers,
        "include_inactive": args.include_inactive,
        "scanned": len(rows),
        "changed": len(changes),
        "applied": applied,
        "reason_counts": dict(reason_counts),
        "provider_counts": {provider: dict(counts) for provider, counts in provider_counts.items()},
        "samples": samples,
    }
    report_path = _write_report(report)

    print(f"mode={'apply' if args.apply else 'dry-run'} providers={','.join(providers)} scanned={len(rows)} changed={len(changes)} applied={applied}")
    print("reason_counts=" + json.dumps(dict(reason_counts), ensure_ascii=False, sort_keys=True))
    print(f"report={report_path}")


if __name__ == "__main__":
    main()
