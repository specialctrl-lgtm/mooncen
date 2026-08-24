"""
Apply the MoonCen database schema.

Usage:
    python DB/setup_db.py --mode migrate
    python DB/setup_db.py --mode fresh

`fresh` creates the canonical schema for a new database.
`migrate` updates an existing database to the current application schema.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from DB.db_utils import get_db_connection, get_db_cursor

DB_DIR = Path(__file__).resolve().parent
MIGRATIONS_DIR = DB_DIR / "migrations"

# Commit b104d2d made these already-released migrations transaction-resumable.
# Some databases correctly retain the checksums from the original reviewed
# release (01efe33). Accept only these exact old->current pairs; any further
# edit, unknown checksum, or reversed pair still fails closed.
KNOWN_CHECKSUM_TRANSITIONS = {
    "20260710_005_search_performance": (
        "f421ceb96dedbb3a8d45d5ef9483d86bb52720bfe2a017649202cb06f5347603",
        "e055bf97b6884ced4a81700e6c06c0befe5bcdcc7b1175e9b80098e4a7eaec62",
    ),
    "20260710_007_search_description_ngrams": (
        "85e00d6e33fbd2c1ba3e97ea9943b1e2f16227f1d1a46cfa16470035b5203967",
        "75202cdc65da6cfbab018c5ff7de8d628485ed177acae0d74f568495758cba4b",
    ),
    "20260710_008_search_long_description": (
        "b04fb976aa080c2423707ab77622c391576fc74d2df478b1dfd04716390d1003",
        "7a42a2e2ac8c7fe944821987c0878dfedfce109d3e4ba70b5afc7324f4133cf8",
    ),
    "20260710_010_oauth_identity_and_url_guards": (
        "5436157728b71095590ab9c59fb3584653963e5702ad1f94dd394f2b8ad72826",
        "7bbf2bcc46e8e651f612551190e4f81ba0a3937e72dceba60c87c60654f0cf5d",
    ),
    "20260710_011_user_course_url_guards": (
        "5eb2259587d308c15149bd73667bcf55affc6db451001180d56924d4a490688c",
        "0a07c3280e616194f35cd39dbbb46333144b4961a51bb3b37761346443a71d90",
    ),
    "20260710_012_auxiliary_url_guards": (
        "c2319d2f7c61504f2de51f828bbd396494df7a073a4564e63dfdba14b599a902",
        "2c05b4437fa932ad1722002afe68d2bfd5917ae365617160ef68e03f084b1e4f",
    ),
    "20260712_001_disable_false_positive_ansan_target": (
        "8e334c1903e0362b48ae2520006e15b2b9e500b34f1d05555eb096e69d7baf06",
        "194738bb34d8863cff7ac58a5c037548d93a2c22b9698ff4833376d583323ec1",
    ),
    "20260712_002_experience_scope_reconcile": (
        "d3ac5cfe386670716d853b05e3eebf6af91f5b4d3d3065a91e2d6885f20372fb",
        "e758993431554a20fc5fa19667a054b6cb5e558c0b03aab7585d58f265b8e1ee",
    ),
}

MIGRATOR_SEARCH_PATH_SQL = "SET SESSION search_path = public, pg_catalog"


def migration_checksum_is_accepted(version: str, recorded: str | None, current: str) -> bool:
    if recorded == current:
        return True
    return KNOWN_CHECKSUM_TRANSITIONS.get(version) == (recorded, current)


def read_sql(filename: str) -> str:
    return (DB_DIR / filename).read_text(encoding="utf-8")


def execute_sql(filename: str) -> None:
    sql = read_sql(filename)
    with get_db_cursor(dict_cursor=False) as cursor:
        # A role- or database-level search_path setting is applied after the
        # libpq startup options on PostgreSQL.  Do not let such a setting turn
        # an unqualified schema object (for example ``users``) into an attempt
        # to write pg_catalog.  setup_db.py is the privileged DDL path, so it
        # deliberately establishes the application schema on every pooled
        # connection before executing a canonical schema file.
        cursor.execute(MIGRATOR_SEARCH_PATH_SQL)
        cursor.execute(sql)


def execute_versioned_migrations() -> list[str]:
    """Apply immutable SQL migrations once, serialized by a session lock.

    Each migration and its ledger row are committed together.  Earlier,
    successfully applied migrations therefore remain durable when a later
    migration fails.
    """
    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    applied_now: list[str] = []
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        # Keep versioned migrations on the same explicit schema contract as
        # the canonical schema files above.  This cannot rely only on libpq
        # options because ALTER ROLE/DATABASE SET can override them.
        cursor.execute(MIGRATOR_SEARCH_PATH_SQL)
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
        cursor.execute("SELECT version, checksum FROM mooncen_schema_migrations")
        applied = {row[0]: row[1] for row in cursor.fetchall()}
        connection.commit()

        for path in migration_files:
            version = path.stem
            sql = path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            if version in applied:
                recorded_checksum = applied[version]
                if recorded_checksum and not migration_checksum_is_accepted(version, recorded_checksum, checksum):
                    raise RuntimeError(
                        f"Applied migration checksum mismatch: {path.name}. "
                        "Never edit an applied migration; add a new version."
                    )
                if not recorded_checksum:
                    cursor.execute(
                        "UPDATE mooncen_schema_migrations SET checksum = %s WHERE version = %s",
                        (checksum, version),
                    )
                    connection.commit()
                continue
            # A migration may use session-scoped SET rather than SET LOCAL.
            # Reset inherited values before every file, then provide bounded
            # defaults that the file can tighten for its own transaction.
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


def expected_migration_ledger() -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"Migration path is unsafe: {path.name}")
        sql = path.read_text(encoding="utf-8")
        records.append(
            {
                "version": path.stem,
                "checksum": hashlib.sha256(sql.encode("utf-8")).hexdigest(),
            }
        )
    if not records:
        raise RuntimeError("No versioned migrations were found")
    return records


def migration_ledger_digest(records: list[dict[str, str]]) -> str:
    encoded = json.dumps(
        records,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def versioned_migration_plan() -> dict[str, object]:
    """Read the migration ledger without creating, locking, or changing it."""

    expected = expected_migration_ledger()
    expected_by_version = {record["version"]: record["checksum"] for record in expected}
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute("SELECT to_regclass('public.mooncen_schema_migrations')")
        row = cursor.fetchone()
        if not row or row[0] is None:
            raise RuntimeError("mooncen_schema_migrations ledger is missing")
        cursor.execute(
            "SELECT version, checksum FROM mooncen_schema_migrations ORDER BY version"
        )
        applied_rows = cursor.fetchall()
        applied: dict[str, str] = {}
        for version, recorded_checksum in applied_rows:
            if version in applied:
                raise RuntimeError(f"Duplicate migration ledger version: {version}")
            if version not in expected_by_version:
                raise RuntimeError(f"Database contains an unknown migration: {version}")
            current_checksum = expected_by_version[version]
            if not recorded_checksum or not migration_checksum_is_accepted(
                version, recorded_checksum, current_checksum
            ):
                raise RuntimeError(f"Applied migration checksum mismatch: {version}")
            # Accepted historical checksum transitions normalize to the current
            # reviewed file so the expected and applied ledger digests converge.
            applied[version] = current_checksum
        pending = [
            record["version"] for record in expected if record["version"] not in applied
        ]
        normalized_applied = [
            {"version": record["version"], "checksum": applied[record["version"]]}
            for record in expected
            if record["version"] in applied
        ]
        return {
            "schema_version": 1,
            "current": not pending and len(applied) == len(expected),
            "pending": pending,
            "expected_count": len(expected),
            "applied_count": len(applied),
            "expected_ledger_sha256": migration_ledger_digest(expected),
            "applied_ledger_sha256": migration_ledger_digest(normalized_applied)
            if normalized_applied
            else hashlib.sha256(b"[]").hexdigest(),
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.rollback()
        connection.close()


def base_tables_exist() -> bool:
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT COUNT(*) AS count
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name IN ('branches', 'courses');
            """
        )
        return cursor.fetchone()["count"] == 2


def print_summary() -> None:
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_type IN ('BASE TABLE', 'VIEW')
            ORDER BY table_name;
            """
        )
        tables = [row["table_name"] for row in cursor.fetchall()]

        cursor.execute("SELECT COUNT(*) AS count FROM information_schema.columns WHERE table_name = 'courses';")
        course_columns = cursor.fetchone()["count"]

        cursor.execute("SELECT COUNT(*) AS count FROM information_schema.columns WHERE table_name = 'branches';")
        branch_columns = cursor.fetchone()["count"]

    print("Database schema is ready.")
    print(f"Tables/views: {', '.join(tables)}")
    print(f"branches columns: {branch_columns}")
    print(f"courses columns: {course_columns}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Set up the MoonCen PostgreSQL schema")
    parser.add_argument(
        "--mode",
        choices=["fresh", "migrate", "plan"],
        default="migrate",
        help=(
            "fresh creates missing objects, migrate updates the DB, and plan "
            "performs a read-only versioned-migration check"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit a canonical JSON document for --mode plan",
    )
    parser.add_argument(
        "--require-current",
        action="store_true",
        help="fail when --mode plan finds pending migrations",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.mode == "plan":
        plan = versioned_migration_plan()
        if args.json:
            print(
                json.dumps(
                    plan,
                    ensure_ascii=True,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        else:
            print(
                "Versioned migration plan: "
                f"applied={plan['applied_count']} expected={plan['expected_count']} "
                f"pending={len(plan['pending'])}."
            )
        if args.require_current and not plan["current"]:
            raise SystemExit(3)
        return

    if args.json or args.require_current:
        raise SystemExit("--json and --require-current are valid only with --mode plan")

    print("Applying auth_schema.sql...")
    execute_sql("auth_schema.sql")

    if args.mode == "fresh":
        print("Applying schema.sql...")
        execute_sql("schema.sql")
    else:
        if not base_tables_exist():
            print("Base tables are missing. Applying schema.sql before migration...")
            execute_sql("schema.sql")
        print("Applying migrate_current.sql...")
        execute_sql("migrate_current.sql")

    print("Applying generated service_group.sql contract...")
    execute_sql("service_group.sql")

    print("Applying primary staging metadata schema...")
    execute_sql("staging_primary_schema.sql")

    print("Applying ops monitoring schema...")
    execute_sql("migrate_ops_monitoring.sql")

    applied = execute_versioned_migrations()
    if applied:
        print(f"Applied versioned migrations: {', '.join(applied)}")
    else:
        print("Versioned migrations are up to date.")

    print_summary()


if __name__ == "__main__":
    main()
