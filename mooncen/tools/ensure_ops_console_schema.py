from __future__ import annotations

import hashlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from DB.db_utils import get_db_connection


REQUIRED_MIGRATIONS = (
    "20260724_001_preserve_course_freshness_on_view",
    "20260725_001_ops_console_core",
    "20260803_001_ops_service_host",
    "20260804_001_ops_job_partial_success",
    "20260806_001_ops_deployment_agent_registration",
    "20260806_002_ops_deployment_worker_read_access",
    "20260807_001_ops_active_deployment_target",
    "20260807_002_ops_deployment_api_cancel_access",
    "20260807_003_ops_deployment_target_key_contract",
    "20260819_001_ops_container_deployment_pipeline",
)


def ensure_schema() -> list[str]:
    """Apply only the immutable migrations required by the standalone console.

    This is intentionally narrow so a restored development database can regain
    the Ops schema even when an unrelated historical migration checksum needs a
    separate repository repair. Existing rows are still checksum-verified.
    """
    applied_now: list[str] = []
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("SET LOCAL statement_timeout = '30s'")
        cursor.execute("SELECT pg_advisory_lock(hashtext('mooncen.schema_migrations'))")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS mooncen_schema_migrations (
                version TEXT PRIMARY KEY,
                checksum TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute("ALTER TABLE mooncen_schema_migrations ADD COLUMN IF NOT EXISTS checksum TEXT")
        connection.commit()
        for version in REQUIRED_MIGRATIONS:
            path = ROOT / "DB" / "migrations" / f"{version}.sql"
            sql = path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            cursor.execute(
                "SELECT checksum FROM mooncen_schema_migrations WHERE version = %s",
                (version,),
            )
            row = cursor.fetchone()
            if row:
                if row[0] != checksum:
                    raise RuntimeError(f"Required Ops migration checksum mismatch: {path.name}")
                continue
            cursor.execute("RESET lock_timeout; RESET statement_timeout")
            cursor.execute("SET LOCAL lock_timeout = '5s'; SET LOCAL statement_timeout = '30min'")
            cursor.execute(sql)
            cursor.execute(
                "INSERT INTO mooncen_schema_migrations(version, checksum) VALUES (%s, %s)",
                (version, checksum),
            )
            connection.commit()
            applied_now.append(version)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return applied_now


def main() -> int:
    applied = ensure_schema()
    print("Ops schema ready.")
    if applied:
        print("Applied:", ", ".join(applied))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
