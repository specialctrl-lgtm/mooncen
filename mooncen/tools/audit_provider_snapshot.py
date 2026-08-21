from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from psycopg2.extras import RealDictCursor


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from DB.db_utils import get_db_connection


def _json_default(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def audit_providers(
    providers: list[str],
    id_regex: str | None = None,
    include_branches: bool = False,
    include_scopes: bool = False,
) -> dict[str, Any]:
    connection = get_db_connection()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT provider,
                       COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE is_active) AS active,
                       COUNT(DISTINCT provider_course_id) FILTER (WHERE is_active) AS active_ids,
                       COUNT(DISTINCT mooncen_raw_url_fingerprint(raw_url))
                           FILTER (WHERE is_active) AS active_urls,
                       COUNT(*) FILTER (
                           WHERE is_active
                             AND NULLIF(BTRIM(COALESCE(application_url, '')), '') IS NOT NULL
                       ) AS application_urls,
                       COUNT(*) FILTER (
                           WHERE is_active
                             AND (
                                  NULLIF(BTRIM(COALESCE(title, '')), '') IS NULL
                               OR NULLIF(BTRIM(COALESCE(schedule_raw, '')), '') IS NULL
                             )
                       ) AS missing_required_display,
                       MAX(last_seen_at) FILTER (WHERE is_active) AS latest_seen
                  FROM courses
                 WHERE provider = ANY(%s)
                 GROUP BY provider
                 ORDER BY provider
                """,
                (providers,),
            )
            summary = [dict(row) for row in cursor.fetchall()]

            cursor.execute(
                """
                SELECT provider,
                       service_group,
                       collection_category,
                       domain_category,
                       COUNT(*) AS row_count
                  FROM courses
                 WHERE provider = ANY(%s)
                   AND is_active
                 GROUP BY 1, 2, 3, 4
                 ORDER BY provider, row_count DESC, service_group, collection_category, domain_category
                """,
                (providers,),
            )
            classifications = [dict(row) for row in cursor.fetchall()]

            scope_counts: list[dict[str, Any]] = []
            scope_contract: dict[str, Any] | None = None
            if include_scopes:
                from tools import ops_quality

                scope_names = tuple(ops_quality.PRODUCTION_COURSE_SCOPES)
                scope_columns = ",\n                       ".join(
                    "COUNT(*) FILTER (WHERE c.is_active AND "
                    f"({ops_quality.production_scope_predicate_sql(scope)})) AS {scope}"
                    for scope in scope_names
                )
                cursor.execute(
                    f"""
                    SELECT c.provider,
                           COUNT(*) FILTER (WHERE c.is_active) AS active,
                           {scope_columns}
                      FROM courses c
                     WHERE c.provider = ANY(%s)
                     GROUP BY c.provider
                     ORDER BY c.provider
                    """,
                    (providers,),
                )
                scope_counts = [dict(row) for row in cursor.fetchall()]
                scope_contract = {
                    "name": ops_quality.DEPLOYED_SCOPE_CONTRACT,
                    "courses_sha256": ops_quality.DEPLOYED_COURSES_SHA256,
                }

            branches: list[dict[str, Any]] = []
            if include_branches:
                cursor.execute(
                    """
                    SELECT c.provider,
                           COALESCE(b.name, '') AS branch,
                           COUNT(*) AS row_count
                      FROM courses c
                      LEFT JOIN branches b ON b.id = c.branch_id
                     WHERE c.provider = ANY(%s)
                       AND c.is_active
                     GROUP BY c.provider, b.name
                     ORDER BY c.provider, row_count DESC, b.name
                    """,
                    (providers,),
                )
                branches = [dict(row) for row in cursor.fetchall()]

            regex_counts: list[dict[str, Any]] = []
            if id_regex:
                cursor.execute(
                    """
                    SELECT provider,
                           COUNT(*) FILTER (WHERE provider_course_id ~ %s) AS matching_ids,
                           COUNT(*) FILTER (WHERE NOT provider_course_id ~ %s) AS nonmatching_ids
                      FROM courses
                     WHERE provider = ANY(%s)
                       AND is_active
                     GROUP BY provider
                     ORDER BY provider
                    """,
                    (id_regex, id_regex, providers),
                )
                regex_counts = [dict(row) for row in cursor.fetchall()]

        return {
            "providers": providers,
            "summary": summary,
            "classifications": classifications,
            "scope_contract": scope_contract,
            "scope_counts": scope_counts,
            "branches": branches,
            "id_regex": id_regex,
            "id_regex_counts": regex_counts,
        }
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only DB audit for one or more crawler providers.")
    parser.add_argument("providers", nargs="+", help="Provider identifiers to audit.")
    parser.add_argument("--id-regex", help="Optional PostgreSQL regex for active provider_course_id values.")
    parser.add_argument(
        "--include-branches",
        action="store_true",
        help="Include active row counts grouped by the persisted branch name.",
    )
    parser.add_argument(
        "--include-scopes",
        action="store_true",
        help="Include active counts partitioned by the pinned production scope contract.",
    )
    args = parser.parse_args()
    payload = audit_providers(
        args.providers,
        args.id_regex,
        args.include_branches,
        args.include_scopes,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
