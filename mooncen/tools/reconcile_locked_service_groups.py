from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from DB.db_utils import get_db_cursor
from service_group import SERVICE_GROUP_PUBLIC_COURSE


PROVIDER_RE = re.compile(r"^[A-Z0-9_]{2,50}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit or reconcile crawler rows whose locked service group was overwritten."
    )
    parser.add_argument("--provider", action="append", required=True)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    providers = list(dict.fromkeys(str(value).strip().upper() for value in args.provider))
    if not providers or len(providers) > 100 or any(not PROVIDER_RE.fullmatch(value) for value in providers):
        raise SystemExit("invalid bounded provider list")

    predicate = """
        provider = ANY(%s)
        AND raw_fields->>'service_group_policy' = 'locked'
        AND raw_fields->>'service_group' = %s
        AND service_group IS DISTINCT FROM %s
    """
    with get_db_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT provider, provider_course_id, title, service_group
            FROM courses
            WHERE {predicate}
            ORDER BY provider, provider_course_id
            """,
            (providers, SERVICE_GROUP_PUBLIC_COURSE, SERVICE_GROUP_PUBLIC_COURSE),
        )
        mismatches = [dict(row) for row in cursor.fetchall()]
        corrected: list[dict[str, object]] = []
        if args.apply and mismatches:
            cursor.execute(
                f"""
                UPDATE courses
                SET service_group = %s
                WHERE {predicate}
                RETURNING provider, provider_course_id, title, service_group
                """,
                (
                    SERVICE_GROUP_PUBLIC_COURSE,
                    providers,
                    SERVICE_GROUP_PUBLIC_COURSE,
                    SERVICE_GROUP_PUBLIC_COURSE,
                ),
            )
            corrected = [dict(row) for row in cursor.fetchall()]

    print(
        json.dumps(
            {
                "providers": providers,
                "mismatch_count": len(mismatches),
                "corrected_count": len(corrected),
                "rows": corrected if args.apply else mismatches,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
