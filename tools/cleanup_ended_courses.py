#!/usr/bin/env python3
"""Apply date-based course lifecycle cleanup to the production primary DB."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from DB.course_lifecycle import apply_ended_course_lifecycle
from tools.apply_staging_batch import (
    acquire_primary_apply_lock,
    connect,
    db_config,
)


def cleanup_ended_courses(grace_days: int = 7) -> dict[str, int]:
    primary_config = db_config("PRIMARY", os.getenv("PRIMARY_DB_NAME", "mooncen"))
    connection = connect(primary_config)
    connection.autocommit = False
    try:
        acquire_primary_apply_lock(connection)
        with connection.cursor() as cursor:
            result = apply_ended_course_lifecycle(
                grace_days=grace_days,
                cursor=cursor,
            )
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> int:
    result = cleanup_ended_courses()
    print(
        json.dumps(
            {
                "status": "SUCCESS",
                "closed": int(result["closed"]),
                "deactivated": int(result["deactivated"]),
                "grace_days": 7,
                "database": "primary",
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
