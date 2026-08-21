"""Install the distributed crawler contract into one confirmed staging DB."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2 import sql

from DB.connection_settings import is_local_database_host
from tools.preflight_distributed_crawler_control import (
    DATABASE_IDENTIFIER,
    PreflightError,
    _assert_managed_permission_groups_own_nothing,
    _crawler_acl_digest,
    _crawler_policy_digest,
    _port,
    _protected_environment,
    _required,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "DB" / "crawler_control_migrations" / "20260810_001_crawler_control_plane.sql"
DATABASE_MARKER = ROOT / "DB" / "crawler_control_database_marker.sql"
STAGING_CONTROL = ROOT / "DB" / "staging_control_plane.sql"
ROLES = ROOT / "DB" / "roles_body.sql"
INSTALL_RECEIPT_MIGRATION = (
    ROOT
    / "DB"
    / "crawler_control_migrations"
    / "20260812_001_install_receipt_consumption.sql"
)
RELEASE_ACTION_MIGRATION = (
    ROOT
    / "DB"
    / "crawler_control_migrations"
    / "20260812_002_release_action_requests.sql"
)
STUDIO_MIGRATION = (
    ROOT
    / "DB"
    / "crawler_control_migrations"
    / "20260812_003_crawler_studio.sql"
)
ROLLOUT_SNAPSHOT_MIGRATION = (
    ROOT
    / "DB"
    / "crawler_control_migrations"
    / "20260812_004_rollout_worker_snapshots.sql"
)
ATTEMPT_RELEASE_GENERATION_MIGRATION = (
    ROOT
    / "DB"
    / "crawler_control_migrations"
    / "20260812_005_attempt_release_generation.sql"
)
RELEASE_OPERATOR_APPROVAL_MIGRATION = (
    ROOT
    / "DB"
    / "crawler_control_migrations"
    / "20260812_006_release_operator_approvals.sql"
)
QUALITY_ENVIRONMENT_ISOLATION_MIGRATION = (
    ROOT
    / "DB"
    / "crawler_control_migrations"
    / "20260812_007_quality_environment_isolation.sql"
)
MIGRATION_VERSION = MIGRATION.stem
INSTALL_RECEIPT_MIGRATION_VERSION = INSTALL_RECEIPT_MIGRATION.stem
RELEASE_ACTION_MIGRATION_VERSION = RELEASE_ACTION_MIGRATION.stem
STUDIO_MIGRATION_VERSION = STUDIO_MIGRATION.stem
ROLLOUT_SNAPSHOT_MIGRATION_VERSION = ROLLOUT_SNAPSHOT_MIGRATION.stem
ATTEMPT_RELEASE_GENERATION_MIGRATION_VERSION = ATTEMPT_RELEASE_GENERATION_MIGRATION.stem
RELEASE_OPERATOR_APPROVAL_MIGRATION_VERSION = RELEASE_OPERATOR_APPROVAL_MIGRATION.stem
QUALITY_ENVIRONMENT_ISOLATION_MIGRATION_VERSION = QUALITY_ENVIRONMENT_ISOLATION_MIGRATION.stem
# Share the canonical migration lock with DB/setup_db.py.  A separate lock
# would let both installers observe a missing ledger row and execute the same
# migration concurrently.
ADVISORY_LOCK = "mooncen.schema_migrations"
ROOT_TRUST_HELPER = Path("/usr/local/libexec/mooncen-crawler-control-root-trust")
RECEIPT_ROOT = Path("/var/lib/mooncen-crawler-control-root-trust/receipts")
RECEIPT_FORMAT = "mooncen-crawler-control-backup-receipt-v1"
RELEASE_PRINCIPAL = "mooncen-crawler-control-release"
RECEIPT_PRINCIPAL = "mooncen-gen1db-backup-receipt"
MAX_RECEIPT_BYTES = 256 * 1024
MAX_SIGNATURE_BYTES = 16 * 1024
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
RELEASE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
STUDIO_CONTRACT_FUNCTION_PATTERN = re.compile(
    r"CREATE OR REPLACE FUNCTION crawler_studio_contract_is_valid\(\)"
    r".*?AS \$crawler_studio_contract\$(.*?)\$crawler_studio_contract\$;",
    re.DOTALL,
)


@dataclass(frozen=True)
class InstallReceiptClaim:
    receipt_sha256: str
    nonce: str
    deploy_commit: str
    archive_sha256: str
    tree_sha256: str
    release_id: str
    receipt_format: str
    node_role: str
    target_host: str
    database_host: str
    database_port: int
    database_name: str
    database_sslmode: str
    release_signer_principal: str
    receipt_signer_principal: str
    receipt_signature_sha256: str
    backup_attestation_sha256: str
    backup_attestation_key_id: str
    canonical_receipt: bytes
    issued_at: dt.datetime
    valid_until: dt.datetime


class SchemaInstallError(RuntimeError):
    pass


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SchemaInstallError(f"duplicate receipt JSON key: {key}")
        result[key] = value
    return result


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _receipt_timestamp(value: Any, label: str) -> dt.datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value
    ):
        raise SchemaInstallError(f"receipt {label} is invalid")
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError as exc:
        raise SchemaInstallError(f"receipt {label} is invalid") from exc


def _parse_install_receipt_claim(
    data: bytes,
    *,
    expected_nonce: str,
    expected_release_id: str,
    expected_commit: str,
    expected_archive_sha256: str,
    expected_tree_sha256: str,
    receipt_signature_sha256: str,
    now: dt.datetime,
) -> InstallReceiptClaim:
    if not data or len(data) > MAX_RECEIPT_BYTES:
        raise SchemaInstallError("install receipt size is invalid")
    try:
        document = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                SchemaInstallError(f"receipt contains non-finite number: {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SchemaInstallError("install receipt is not valid UTF-8 JSON") from exc
    if not isinstance(document, dict) or _canonical_json(document) != data:
        raise SchemaInstallError("install receipt is not canonical JSON")
    required = {
        "candidate",
        "format",
        "issued_at",
        "issuer",
        "nonce",
        "recovery_evidence",
        "release",
        "valid_until",
    }
    if set(document) != required or document.get("format") != RECEIPT_FORMAT:
        raise SchemaInstallError("install receipt structure or format is invalid")
    release = document.get("release")
    issuer = document.get("issuer")
    recovery = document.get("recovery_evidence")
    if not isinstance(release, dict) or not isinstance(issuer, dict) or not isinstance(recovery, dict):
        raise SchemaInstallError("install receipt identity objects are invalid")
    exact_release = {
        "archive_sha256": expected_archive_sha256,
        "deploy_commit": expected_commit,
        "node_role": "crawler-control",
        "release_id": expected_release_id,
        "signer_principal": RELEASE_PRINCIPAL,
        "target_host": "gen1db",
        "tree_sha256": expected_tree_sha256,
    }
    if release != exact_release or document.get("nonce") != expected_nonce:
        raise SchemaInstallError("install receipt is bound to another release or nonce")
    if issuer.get("signature_principal") != RECEIPT_PRINCIPAL:
        raise SchemaInstallError("install receipt signer principal is invalid")
    if (
        recovery.get("database_host") != "gen1db"
        or recovery.get("database_port") != 5432
        or recovery.get("database_name") != "mooncen_staging"
        or recovery.get("database_sslmode") != "verify-full"
    ):
        raise SchemaInstallError("install receipt database identity is invalid")
    backup_sha256 = recovery.get("attestation_sha256")
    if not isinstance(backup_sha256, str) or not SHA256_PATTERN.fullmatch(backup_sha256):
        raise SchemaInstallError("install receipt backup evidence digest is invalid")
    backup_key_id = recovery.get("attestation_key_id")
    if not isinstance(backup_key_id, str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", backup_key_id
    ):
        raise SchemaInstallError("install receipt backup evidence key id is invalid")
    if not SHA256_PATTERN.fullmatch(receipt_signature_sha256):
        raise SchemaInstallError("install receipt signature digest is invalid")
    issued_at = _receipt_timestamp(document.get("issued_at"), "issued_at")
    valid_until = _receipt_timestamp(document.get("valid_until"), "valid_until")
    current = now.astimezone(dt.timezone.utc)
    if not issued_at < valid_until or valid_until - issued_at > dt.timedelta(hours=24):
        raise SchemaInstallError("install receipt lifetime is invalid")
    if current < issued_at - dt.timedelta(minutes=5) or current >= valid_until:
        raise SchemaInstallError("install receipt is expired or not yet valid")
    return InstallReceiptClaim(
        receipt_sha256=hashlib.sha256(data).hexdigest(),
        nonce=expected_nonce,
        deploy_commit=expected_commit,
        archive_sha256=expected_archive_sha256,
        tree_sha256=expected_tree_sha256,
        release_id=expected_release_id,
        receipt_format=RECEIPT_FORMAT,
        node_role="crawler-control",
        target_host="gen1db",
        database_host="gen1db",
        database_port=5432,
        database_name="mooncen_staging",
        database_sslmode="verify-full",
        release_signer_principal=RELEASE_PRINCIPAL,
        receipt_signer_principal=RECEIPT_PRINCIPAL,
        receipt_signature_sha256=receipt_signature_sha256,
        backup_attestation_sha256=backup_sha256,
        backup_attestation_key_id=backup_key_id,
        canonical_receipt=data,
        issued_at=issued_at,
        valid_until=valid_until,
    )


def _read_root_receipt_file(path: Path, *, maximum: int, label: str) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise SchemaInstallError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o400
        or metadata.st_nlink != 1
        or not 0 < metadata.st_size <= maximum
    ):
        raise SchemaInstallError(f"{label} ownership, mode, or size is unsafe")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise SchemaInstallError(f"{label} changed while being opened")
        data = b""
        while len(data) <= maximum:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - len(data)))
            if not chunk:
                break
            data += chunk
        if len(data) != opened.st_size or len(data) > maximum:
            raise SchemaInstallError(f"{label} changed while being read")
        return data
    finally:
        os.close(descriptor)


def _verified_install_receipt(
    receipt_path: Path,
    signature_path: Path,
    *,
    nonce: str,
    release_id: str,
    commit: str,
    archive_sha256: str,
    tree_sha256: str,
) -> InstallReceiptClaim:
    expected_directory = RECEIPT_ROOT / nonce
    if (
        not SHA256_PATTERN.fullmatch(nonce)
        or not RELEASE_ID_PATTERN.fullmatch(release_id)
        or not COMMIT_PATTERN.fullmatch(commit)
        or not SHA256_PATTERN.fullmatch(archive_sha256)
        or not SHA256_PATTERN.fullmatch(tree_sha256)
        or receipt_path != expected_directory / "receipt.json"
        or signature_path != expected_directory / "receipt.json.sig"
    ):
        raise SchemaInstallError("install receipt path or expected release identity is invalid")
    before = _read_root_receipt_file(
        receipt_path, maximum=MAX_RECEIPT_BYTES, label="install receipt"
    )
    signature_bytes = _read_root_receipt_file(
        signature_path, maximum=MAX_SIGNATURE_BYTES, label="install receipt signature"
    )
    try:
        helper_metadata = ROOT_TRUST_HELPER.lstat()
    except OSError as exc:
        raise SchemaInstallError("fixed root trust helper is unavailable") from exc
    if (
        not stat.S_ISREG(helper_metadata.st_mode)
        or helper_metadata.st_uid != 0
        or stat.S_IMODE(helper_metadata.st_mode) != 0o755
        or helper_metadata.st_nlink != 1
    ):
        raise SchemaInstallError("fixed root trust helper is unsafe")
    command = [
        str(ROOT_TRUST_HELPER),
        "verify-receipt",
        "--release-id",
        release_id,
        "--expected-commit",
        commit,
        "--expected-archive-sha256",
        archive_sha256,
        "--expected-tree-sha256",
        tree_sha256,
        "--nonce",
        nonce,
        "--receipt",
        str(receipt_path),
        "--receipt-signature",
        str(signature_path),
    ]
    result = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
        check=False,
        timeout=20 * 60,
    )
    expected_proof = (
        "MOONCEN_CONTROL_BACKUP_RECEIPT_VERIFIED="
        f"{nonce}:{commit}:{tree_sha256}\n"
    ).encode("ascii")
    if result.returncode != 0 or result.stdout != expected_proof:
        raise SchemaInstallError("fixed root trust helper rejected the install receipt")
    after = _read_root_receipt_file(
        receipt_path, maximum=MAX_RECEIPT_BYTES, label="install receipt"
    )
    if after != before:
        raise SchemaInstallError("install receipt changed after signature verification")
    return _parse_install_receipt_claim(
        after,
        expected_nonce=nonce,
        expected_release_id=release_id,
        expected_commit=commit,
        expected_archive_sha256=archive_sha256,
        expected_tree_sha256=tree_sha256,
        receipt_signature_sha256=hashlib.sha256(signature_bytes).hexdigest(),
        now=dt.datetime.now(dt.timezone.utc),
    )


def _assert_application_owner_access(connection: Any, expected_owner: str) -> None:
    """Require effective ownership rights for every non-extension app object."""

    if not DATABASE_IDENTIFIER.fullmatch(expected_owner):
        raise SchemaInstallError("OPS_CRAWLER_SCHEMA_OBJECT_OWNER is not a safe role identifier")
    with connection.cursor() as cursor:
        _assert_managed_permission_groups_own_nothing(cursor)
        cursor.execute(
            """
            SELECT NOT rolcanlogin AND NOT rolsuper AND NOT rolcreaterole
                   AND NOT rolcreatedb AND NOT rolreplication AND NOT rolbypassrls
            FROM pg_roles
            WHERE rolname = %s
            """,
            (expected_owner,),
        )
        owner = cursor.fetchone()
        if not owner or owner[0] is not True:
            raise SchemaInstallError("staging object owner role is missing or unsafe")
        cursor.execute(
            """
            WITH application_objects AS (
                SELECT 'schema ' || namespace.nspname AS object_name,
                       namespace.nspowner AS owner_oid
                FROM pg_namespace namespace
                WHERE namespace.nspname IN ('public', 'crawl_staging')
                UNION ALL
                SELECT 'relation ' || namespace.nspname || '.' || relation.relname,
                       relation.relowner
                FROM pg_class relation
                JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname IN ('public', 'crawl_staging')
                  AND relation.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
                  AND NOT EXISTS (
                      SELECT 1
                      FROM pg_depend dependency
                      WHERE dependency.classid = 'pg_class'::regclass
                        AND dependency.objid = relation.oid
                        AND dependency.deptype = 'e'
                  )
                UNION ALL
                SELECT 'routine ' || namespace.nspname || '.' || procedure.proname,
                       procedure.proowner
                FROM pg_proc procedure
                JOIN pg_namespace namespace ON namespace.oid = procedure.pronamespace
                WHERE namespace.nspname IN ('public', 'crawl_staging')
                  AND procedure.prokind IN ('f', 'p')
                  AND NOT EXISTS (
                      SELECT 1
                      FROM pg_depend dependency
                      WHERE dependency.classid = 'pg_proc'::regclass
                        AND dependency.objid = procedure.oid
                        AND dependency.deptype = 'e'
                  )
            )
            SELECT owner.rolname, string_agg(object.object_name, ', ' ORDER BY object.object_name)
            FROM application_objects object
            JOIN pg_roles owner ON owner.oid = object.owner_oid
            WHERE NOT pg_has_role(current_user, object.owner_oid, 'USAGE')
            GROUP BY owner.rolname
            ORDER BY owner.rolname
            """
        )
        inaccessible = cursor.fetchall()
        if inaccessible:
            owners = ", ".join(str(row[0]) for row in inaccessible)
            raise SchemaInstallError(f"schema administrator cannot act as application object owners: {owners}")
        cursor.execute(
            """
            WITH application_owners AS (
                SELECT namespace.nspowner AS owner_oid
                FROM pg_namespace namespace
                WHERE namespace.nspname IN ('public', 'crawl_staging')
                UNION
                SELECT relation.relowner
                FROM pg_class relation
                JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname IN ('public', 'crawl_staging')
                  AND relation.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
                  AND NOT EXISTS (
                      SELECT 1 FROM pg_depend dependency
                      WHERE dependency.classid = 'pg_class'::regclass
                        AND dependency.objid = relation.oid
                        AND dependency.deptype = 'e'
                  )
                UNION
                SELECT procedure.proowner
                FROM pg_proc procedure
                JOIN pg_namespace namespace ON namespace.oid = procedure.pronamespace
                WHERE namespace.nspname IN ('public', 'crawl_staging')
                  AND procedure.prokind IN ('f', 'p')
                  AND NOT EXISTS (
                      SELECT 1 FROM pg_depend dependency
                      WHERE dependency.classid = 'pg_proc'::regclass
                        AND dependency.objid = procedure.oid
                        AND dependency.deptype = 'e'
                  )
            )
            SELECT array_agg(owner.rolname ORDER BY owner.rolname)
            FROM application_owners application_owner
            JOIN pg_roles owner ON owner.oid = application_owner.owner_oid
            WHERE application_owner.owner_oid <> %s::regrole
            """,
            (expected_owner,),
        )
        drifted_owners = cursor.fetchone()[0]
        if drifted_owners:
            raise SchemaInstallError(
                "non-extension staging objects have unexpected owners: "
                + ", ".join(str(owner) for owner in drifted_owners)
            )
        cursor.execute("SELECT pg_has_role(current_user, %s, 'USAGE')", (expected_owner,))
        if cursor.fetchone()[0] is not True:
            raise SchemaInstallError("schema administrator cannot SET ROLE to the staging object owner")
    connection.rollback()


def _connection_config(environment: dict[str, str]) -> dict[str, Any]:
    host = _required(environment, "OPS_CRAWLER_SCHEMA_DB_HOST")
    database = _required(environment, "OPS_CRAWLER_SCHEMA_DB_NAME")
    user = _required(environment, "OPS_CRAWLER_SCHEMA_DB_USER")
    if not DATABASE_IDENTIFIER.fullmatch(database) or not DATABASE_IDENTIFIER.fullmatch(user):
        raise SchemaInstallError("schema database and role names must be safe PostgreSQL identifiers")
    sslmode = environment.get("DB_SSLMODE", "prefer").strip().lower()
    if sslmode not in {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}:
        raise SchemaInstallError("DB_SSLMODE is invalid")
    if not is_local_database_host(host) and sslmode != "verify-full":
        raise SchemaInstallError("remote schema administration requires DB_SSLMODE=verify-full")
    connect_timeout_raw = environment.get("DB_CONNECT_TIMEOUT", "5").strip()
    try:
        connect_timeout = int(connect_timeout_raw)
    except ValueError as exc:
        raise SchemaInstallError("DB_CONNECT_TIMEOUT must be an integer") from exc
    if not 1 <= connect_timeout <= 60:
        raise SchemaInstallError("DB_CONNECT_TIMEOUT must be between 1 and 60")
    result: dict[str, Any] = {
        "host": host,
        "port": _port(environment, "OPS_CRAWLER_SCHEMA_DB_PORT"),
        "database": database,
        "user": user,
        "password": _required(environment, "OPS_CRAWLER_SCHEMA_DB_PASSWORD"),
        "connect_timeout": connect_timeout,
        "application_name": "mooncen-crawler-control-schema",
        "sslmode": sslmode,
    }
    for key, libpq_key in (
        ("DB_SSLROOTCERT", "sslrootcert"),
        ("DB_SSLCERT", "sslcert"),
        ("DB_SSLKEY", "sslkey"),
    ):
        value = environment.get(key, "").strip()
        if value:
            result[libpq_key] = value
    return result


def _pin_installer_search_path(connection: Any) -> None:
    """Pin and verify the installer namespace before its first catalog query."""

    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION search_path = public, pg_catalog")
            cursor.execute(
                "SELECT current_schemas(false) = ARRAY['public', 'pg_catalog']::name[]"
            )
            if cursor.fetchone()[0] is not True:
                raise SchemaInstallError("installer search_path could not be pinned")
    finally:
        connection.autocommit = False


def _read_contract_files() -> tuple[str, str, str, str, str, str, str, str]:
    try:
        migration = MIGRATION.read_text(encoding="utf-8")
        marker = DATABASE_MARKER.read_text(encoding="utf-8")
        staging = STAGING_CONTROL.read_text(encoding="utf-8")
        roles = ROLES.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SchemaInstallError("crawler control SQL contract is unavailable") from exc
    if not migration or not marker or not staging or not roles:
        raise SchemaInstallError("crawler control SQL contract contains an empty file")
    return (
        migration,
        marker,
        staging,
        roles,
        hashlib.sha256(migration.encode("utf-8")).hexdigest(),
        hashlib.sha256(marker.encode("utf-8")).hexdigest(),
        hashlib.sha256(staging.encode("utf-8")).hexdigest(),
        hashlib.sha256(roles.encode("utf-8")).hexdigest(),
    )


def _base_contract(connection: Any, expected_database: str, expected_owner: str) -> None:
    required = (
        "public.branches",
        "public.courses",
        "public.crawl_batches",
        "public.crawl_progress",
        "public.ops_agents",
        "public.ops_jobs",
        "public.mooncen_schema_migrations",
        "crawl_staging.branch_snapshots",
        "crawl_staging.course_snapshots",
    )
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT current_database(), current_user, rolsuper, rolcreaterole,
                   has_schema_privilege(current_user, 'public', 'CREATE')
            FROM pg_roles
            WHERE rolname = current_user
            """
        )
        identity = cursor.fetchone()
        if not identity or identity[0] != expected_database:
            raise SchemaInstallError("connected database differs from the confirmed staging database")
        if not identity[2] and not identity[3]:
            raise SchemaInstallError("schema installer requires a CREATEROLE-capable administrator")
        if identity[4] is not True:
            raise SchemaInstallError("schema installer cannot create objects in public")
        cursor.execute(
            "SELECT required.name, to_regclass(required.name) IS NOT NULL "
            "FROM unnest(%s::text[]) AS required(name)",
            (list(required),),
        )
        missing = [name for name, exists in cursor.fetchall() if not exists]
        cursor.execute("SELECT to_regprocedure('public.current_crawl_batch_id()') IS NOT NULL")
        if cursor.fetchone()[0] is not True:
            missing.append("public.current_crawl_batch_id()")
        if missing:
            raise SchemaInstallError(f"base staging schema is incomplete: {', '.join(missing)}")
        cursor.execute("SELECT to_regclass('public.ops_crawler_control_database_marker')")
        if cursor.fetchone()[0] is not None:
            cursor.execute(
                """
                SELECT count(*) = 1
                   AND bool_and(singleton IS TRUE)
                   AND bool_and(database_name = current_database()::name)
                FROM public.ops_crawler_control_database_marker
                """
            )
            if cursor.fetchone()[0] is not True:
                raise SchemaInstallError("crawler control database marker selects another database")
    connection.rollback()
    _assert_application_owner_access(connection, expected_owner)


def _recorded_checksum(connection: Any, version: str = MIGRATION_VERSION) -> str | None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT checksum FROM mooncen_schema_migrations WHERE version = %s",
            (version,),
        )
        row = cursor.fetchone()
    connection.rollback()
    return None if row is None else str(row[0] or "")


def _execute_roles(connection: Any, sql: str) -> None:
    connection.autocommit = True
    with connection.cursor() as cursor:
        cursor.execute(sql)
    connection.autocommit = False


def _install_receipt_migration() -> tuple[str, str]:
    try:
        migration = INSTALL_RECEIPT_MIGRATION.read_text(encoding="utf-8")
    except OSError as exc:
        raise SchemaInstallError("install receipt migration is unavailable") from exc
    return migration, hashlib.sha256(migration.encode("utf-8")).hexdigest()


def _release_action_migration() -> tuple[str, str]:
    try:
        migration = RELEASE_ACTION_MIGRATION.read_text(encoding="utf-8")
    except OSError as exc:
        raise SchemaInstallError("release action queue migration is unavailable") from exc
    return migration, hashlib.sha256(migration.encode("utf-8")).hexdigest()


def _studio_migration() -> tuple[str, str]:
    try:
        migration = STUDIO_MIGRATION.read_text(encoding="utf-8")
    except OSError as exc:
        raise SchemaInstallError("Crawler Studio migration is unavailable") from exc
    _studio_contract_source_sha256(migration)
    return migration, hashlib.sha256(migration.encode("utf-8")).hexdigest()


def _rollout_snapshot_migration() -> tuple[str, str]:
    try:
        migration = ROLLOUT_SNAPSHOT_MIGRATION.read_text(encoding="utf-8")
    except OSError as exc:
        raise SchemaInstallError("rollout worker snapshot migration is unavailable") from exc
    return migration, hashlib.sha256(migration.encode("utf-8")).hexdigest()


def _attempt_release_generation_migration() -> tuple[str, str]:
    try:
        migration = ATTEMPT_RELEASE_GENERATION_MIGRATION.read_text(encoding="utf-8")
    except OSError as exc:
        raise SchemaInstallError("attempt release generation migration is unavailable") from exc
    return migration, hashlib.sha256(migration.encode("utf-8")).hexdigest()


def _release_operator_approval_migration() -> tuple[str, str]:
    try:
        migration = RELEASE_OPERATOR_APPROVAL_MIGRATION.read_text(encoding="utf-8")
    except OSError as exc:
        raise SchemaInstallError("release operator approval migration is unavailable") from exc
    _release_approval_function_source_sha256(migration)
    return migration, hashlib.sha256(migration.encode("utf-8")).hexdigest()


def _quality_environment_isolation_migration() -> tuple[str, str]:
    try:
        migration = QUALITY_ENVIRONMENT_ISOLATION_MIGRATION.read_text(encoding="utf-8")
    except OSError as exc:
        raise SchemaInstallError("quality environment isolation migration is unavailable") from exc
    return migration, hashlib.sha256(migration.encode("utf-8")).hexdigest()


def _normalized_routine_source(source: str) -> str:
    return source.replace("\r\n", "\n").replace("\r", "\n").strip()


def _studio_contract_source_sha256(migration: str) -> str:
    matches = STUDIO_CONTRACT_FUNCTION_PATTERN.findall(migration)
    if len(matches) != 1:
        raise SchemaInstallError("Crawler Studio live contract function is not exact")
    return hashlib.sha256(
        _normalized_routine_source(matches[0]).encode("utf-8")
    ).hexdigest()


def _release_approval_function_source_sha256(migration: str) -> dict[str, str]:
    reviewed: dict[str, str] = {}
    for function_name in (
        "crawler_release_approval_catalog_is_valid",
        "crawler_release_approval_contract_is_valid",
        "crawler_release_action_runtime_is_ready",
    ):
        matches = re.findall(
            rf"CREATE OR REPLACE FUNCTION {function_name}\([^)]*\)"
            rf".*?AS \$\$(.*?)\$\$;",
            migration,
            re.DOTALL,
        )
        if len(matches) != 1:
            raise SchemaInstallError(
                f"crawler release approval function is not exact: {function_name}"
            )
        reviewed[function_name] = hashlib.sha256(
            _normalized_routine_source(matches[0]).encode("utf-8")
        ).hexdigest()
    return reviewed


def _prepare_install_receipt_ledger(
    cursor: Any,
    *,
    object_owner: str,
) -> str:
    """Create or validate the ledger inside the caller's transaction.

    The caller must already hold ``ADVISORY_LOCK``.  No commit or rollback is
    performed here.
    """

    if not DATABASE_IDENTIFIER.fullmatch(object_owner):
        raise SchemaInstallError("receipt ledger owner role is invalid")
    migration, checksum = _install_receipt_migration()
    cursor.execute(
        "SELECT checksum FROM public.mooncen_schema_migrations WHERE version = %s",
        (INSTALL_RECEIPT_MIGRATION_VERSION,),
    )
    row = cursor.fetchone()
    cursor.execute(
        "SELECT to_regclass('public.ops_crawler_control_install_receipt_consumptions')"
    )
    ledger_exists = cursor.fetchone()[0] is not None
    if row is None and ledger_exists:
        raise SchemaInstallError("unledgered crawler-control receipt table already exists")
    if row is not None and str(row[0]) != checksum:
        raise SchemaInstallError("install receipt migration checksum differs")
    if row is not None and not ledger_exists:
        raise SchemaInstallError("ledgered crawler-control receipt table is missing")
    if row is None:
        cursor.execute(sql.SQL("SET LOCAL ROLE {} ").format(sql.Identifier(object_owner)))
        cursor.execute(migration)
        cursor.execute(
            "INSERT INTO public.mooncen_schema_migrations(version, checksum) VALUES (%s, %s)",
            (INSTALL_RECEIPT_MIGRATION_VERSION, checksum),
        )
    else:
        cursor.execute(sql.SQL("SET LOCAL ROLE {} ").format(sql.Identifier(object_owner)))
    cursor.execute(
        """
        SELECT table_class.relowner = %s::regrole,
               table_class.relrowsecurity IS FALSE,
               table_class.relforcerowsecurity IS FALSE,
               count(DISTINCT constraint_row.conname) FILTER (
                   WHERE constraint_row.conname IN (
                       'ops_crawler_control_install_receipt_consumptions_pkey',
                       'ux_crawler_install_receipt_nonce',
                       'chk_crawler_install_receipt_sha256',
                       'chk_crawler_install_receipt_nonce',
                       'chk_crawler_install_receipt_commit',
                       'chk_crawler_install_receipt_archive_sha256',
                       'chk_crawler_install_receipt_tree_sha256',
                       'chk_crawler_install_receipt_release_id',
                       'chk_crawler_install_receipt_backup_sha256',
                       'chk_crawler_install_receipt_release_principal',
                       'chk_crawler_install_receipt_backup_principal',
                       'chk_crawler_install_receipt_signature_sha256',
                       'chk_crawler_install_receipt_backup_key_id',
                       'chk_crawler_install_receipt_format',
                       'chk_crawler_install_receipt_target',
                       'chk_crawler_install_receipt_canonical_size',
                       'chk_crawler_install_receipt_lifetime',
                       'ux_crawler_install_receipt_release_id'
                   )
               ) = 18,
               NOT EXISTS (
                   SELECT 1
                   FROM aclexplode(
                       COALESCE(
                           table_class.relacl,
                           acldefault('r', table_class.relowner)
                       )
                   ) acl_row
                   WHERE acl_row.grantee <> table_class.relowner
               )
        FROM pg_class table_class
        JOIN pg_namespace namespace_row ON namespace_row.oid = table_class.relnamespace
        LEFT JOIN pg_constraint constraint_row ON constraint_row.conrelid = table_class.oid
        WHERE namespace_row.nspname = 'public'
          AND table_class.relname = 'ops_crawler_control_install_receipt_consumptions'
        GROUP BY table_class.relowner, table_class.relrowsecurity, table_class.relforcerowsecurity
        """,
        (object_owner,),
    )
    contract = cursor.fetchone()
    if contract != (True, True, True, True, True):
        raise SchemaInstallError("install receipt ledger ownership, constraints, or ACL drifted")
    cursor.execute("RESET ROLE")
    return checksum


def _insert_install_receipt(
    cursor: Any,
    claim: InstallReceiptClaim,
    *,
    object_owner: str,
    schema_user: str,
) -> None:
    """Insert the receipt as the final mutation before the caller commits."""

    if not DATABASE_IDENTIFIER.fullmatch(object_owner) or not DATABASE_IDENTIFIER.fullmatch(
        schema_user
    ):
        raise SchemaInstallError("receipt ledger owner or consumer role is invalid")
    cursor.execute(sql.SQL("SET LOCAL ROLE {} ").format(sql.Identifier(object_owner)))
    try:
        cursor.execute(
            """
        INSERT INTO public.ops_crawler_control_install_receipt_consumptions (
            receipt_sha256, nonce, deploy_commit, archive_sha256, tree_sha256,
            release_id, receipt_format, node_role, target_host,
            database_host, database_port, database_name, database_sslmode,
            release_signer_principal, receipt_signer_principal,
            receipt_signature_sha256, backup_attestation_sha256,
            backup_attestation_key_id, canonical_receipt,
            receipt_issued_at, receipt_valid_until, consumed_at, consumed_by
        )
        SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
               %s, %s, %s, %s, %s, %s, %s, %s, transaction_timestamp(), %s::name
        WHERE %s <= transaction_timestamp()
          AND transaction_timestamp() < %s
        RETURNING receipt_sha256
        """,
            (
                claim.receipt_sha256,
                claim.nonce,
                claim.deploy_commit,
                claim.archive_sha256,
                claim.tree_sha256,
                claim.release_id,
                claim.receipt_format,
                claim.node_role,
                claim.target_host,
                claim.database_host,
                claim.database_port,
                claim.database_name,
                claim.database_sslmode,
                claim.release_signer_principal,
                claim.receipt_signer_principal,
                claim.receipt_signature_sha256,
                claim.backup_attestation_sha256,
                claim.backup_attestation_key_id,
                psycopg2.Binary(claim.canonical_receipt),
                claim.issued_at,
                claim.valid_until,
                schema_user,
                claim.issued_at,
                claim.valid_until,
            ),
        )
    except psycopg2.IntegrityError as exc:
        raise SchemaInstallError(
            "install receipt nonce, digest, or release was already consumed"
        ) from exc
    consumed = cursor.fetchone()
    if consumed != (claim.receipt_sha256,):
        raise SchemaInstallError(
            "install receipt is expired or not yet valid according to the database clock"
        )
    cursor.execute("RESET ROLE")


def _consume_install_receipt(
    cursor: Any,
    claim: InstallReceiptClaim,
    *,
    object_owner: str,
    schema_user: str,
) -> str:
    """Testable prepare+insert primitive; the coordinator controls commit."""

    checksum = _prepare_install_receipt_ledger(cursor, object_owner=object_owner)
    _insert_install_receipt(
        cursor,
        claim,
        object_owner=object_owner,
        schema_user=schema_user,
    )
    return checksum


def _post_contract(
    connection: Any,
    checksum: str,
    release_action_checksum: str,
    studio_checksum: str,
    rollout_snapshot_checksum: str,
    attempt_release_generation_checksum: str,
    release_operator_approval_checksum: str,
    quality_environment_isolation_checksum: str,
    staging_version: str,
    staging_checksum: str,
    marker_version: str,
    marker_checksum: str,
    roles_version: str,
    roles_checksum: str,
    *,
    expected_owner: str | None = None,
    rollback: bool = True,
) -> None:
    required = (
        "public.ops_crawler_batches",
        "public.ops_crawler_batch_tasks",
        "public.ops_crawler_task_attempts",
        "public.ops_crawler_task_observations",
        "public.ops_crawler_release_artifacts",
        "public.ops_crawler_release_rollouts",
        "public.ops_crawler_worker_desired_state",
        "public.ops_crawler_rollout_worker_snapshots",
        "public.ops_crawler_agent_bindings",
        "public.ops_crawler_release_reports",
        "public.ops_crawler_api_bindings",
        "public.ops_crawler_release_action_requests",
        "public.ops_crawler_release_approver_bindings",
        "public.ops_crawler_release_action_approvals",
        "public.ops_crawler_release_action_consumers",
        "public.ops_crawler_release_policy_contract",
        "public.ops_crawler_studio_provider_paths",
        "public.ops_crawler_studio_drafts",
        "public.ops_crawler_studio_revisions",
        "public.ops_crawler_studio_reviews",
        "public.ops_crawler_control_database_marker",
        "public.ops_crawler_control_install_receipt_consumptions",
        "crawl_staging.fenced_branch_snapshots",
        "crawl_staging.fenced_course_snapshots",
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT required.name, to_regclass(required.name) IS NOT NULL "
            "FROM unnest(%s::text[]) AS required(name)",
            (list(required),),
        )
        missing = [name for name, exists in cursor.fetchall() if not exists]
        cursor.execute(
            """
            SELECT to_regprocedure('public.enforce_current_crawler_lease()') IS NOT NULL,
                   to_regprocedure('public.capture_fenced_crawler_snapshot()') IS NOT NULL,
                   to_regprocedure('public.current_crawler_api_environment()') IS NOT NULL,
                   to_regprocedure(
                       'public.approve_crawler_release_action(uuid,text,text,text,integer)'
                   ) IS NOT NULL,
                   to_regprocedure(
                       'public.preview_crawler_release_action_for_approval(uuid,text)'
                   ) IS NOT NULL,
                   to_regprocedure(
                       'public.crawler_release_approval_contract_is_valid(text)'
                   ) IS NOT NULL,
                   to_regprocedure(
                       'public.crawler_release_approval_catalog_is_valid()'
                   ) IS NOT NULL,
                   to_regprocedure(
                       'public.crawler_release_action_runtime_is_ready(text)'
                   ) IS NOT NULL,
                   EXISTS (
                       SELECT 1 FROM pg_roles
                       WHERE rolname = 'mooncen_crawler_worker'
                         AND NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls
                   ),
                   EXISTS (
                       SELECT 1 FROM pg_roles
                       WHERE rolname = 'mooncen_crawler_release_approver'
                         AND NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls
                   ),
                   EXISTS (
                       SELECT 1 FROM pg_roles
                       WHERE rolname = 'mooncen_crawler_control'
                         AND NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls
                   ),
                   EXISTS (
                       SELECT 1 FROM pg_roles
                       WHERE rolname = 'mooncen_crawler_api'
                         AND NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls
                   ),
                   (SELECT checksum = %s FROM mooncen_schema_migrations WHERE version = %s)
                   AND (SELECT checksum = %s FROM mooncen_schema_migrations WHERE version = %s)
                   AND (SELECT checksum = %s FROM mooncen_schema_migrations WHERE version = %s)
                   AND (SELECT checksum = %s FROM mooncen_schema_migrations WHERE version = %s)
                   AND (SELECT checksum = %s FROM mooncen_schema_migrations WHERE version = %s)
                   AND (SELECT checksum = %s FROM mooncen_schema_migrations WHERE version = %s)
                   AND (SELECT checksum = %s FROM mooncen_schema_migrations WHERE version = %s)
                   AND (SELECT checksum = %s FROM mooncen_schema_migrations WHERE version = %s)
                   AND (SELECT checksum = %s FROM mooncen_schema_migrations WHERE version = %s)
                   AND (SELECT checksum = %s FROM mooncen_schema_migrations WHERE version = %s)
            """,
            (
                checksum,
                MIGRATION_VERSION,
                release_action_checksum,
                RELEASE_ACTION_MIGRATION_VERSION,
                studio_checksum,
                STUDIO_MIGRATION_VERSION,
                rollout_snapshot_checksum,
                ROLLOUT_SNAPSHOT_MIGRATION_VERSION,
                attempt_release_generation_checksum,
                ATTEMPT_RELEASE_GENERATION_MIGRATION_VERSION,
                release_operator_approval_checksum,
                RELEASE_OPERATOR_APPROVAL_MIGRATION_VERSION,
                quality_environment_isolation_checksum,
                QUALITY_ENVIRONMENT_ISOLATION_MIGRATION_VERSION,
                staging_checksum,
                staging_version,
                marker_checksum,
                marker_version,
                roles_checksum,
                roles_version,
            ),
        )
        functions_and_role = cursor.fetchone()
        if missing or functions_and_role != (
            True, True, True, True, True, True, True, True, True, True, True,
            True, True,
        ):
            detail = ", ".join(missing) if missing else "function, role, or checksum contract"
            raise SchemaInstallError(f"installed crawler control schema failed verification: {detail}")
        release_approval_migration, _ = _release_operator_approval_migration()
        expected_approval_sources = _release_approval_function_source_sha256(
            release_approval_migration
        )
        cursor.execute(
            """
            SELECT procedure.proname, procedure.prosrc
            FROM pg_proc procedure
            JOIN pg_namespace namespace_row
              ON namespace_row.oid = procedure.pronamespace
            WHERE namespace_row.nspname = 'public'
              AND procedure.proname = ANY(%s::text[])
            ORDER BY procedure.proname
            """,
            (sorted(expected_approval_sources),),
        )
        live_approval_sources = cursor.fetchall()
        if len(live_approval_sources) != len(expected_approval_sources) or any(
            hashlib.sha256(
                _normalized_routine_source(str(source)).encode("utf-8")
            ).hexdigest()
            != expected_approval_sources.get(str(name))
            for name, source in live_approval_sources
        ):
            raise SchemaInstallError(
                "crawler release approval function identity has drifted"
            )
        if expected_owner is not None:
            cursor.execute(
                sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(expected_owner))
            )
        cursor.execute("SELECT public.crawler_release_approval_catalog_is_valid()")
        if cursor.fetchone() != (True,):
            raise SchemaInstallError(
                "live crawler release approval catalog contract has drifted"
            )
        if expected_owner is not None:
            cursor.execute("RESET ROLE")
        studio_migration, _ = _studio_migration()
        expected_studio_contract_sha256 = _studio_contract_source_sha256(
            studio_migration
        )
        cursor.execute(
            """
            SELECT procedure.prosrc,
                   procedure.prokind = 'f'
                   AND procedure.pronargs = 0
                   AND procedure.prorettype = 'boolean'::regtype
                   AND procedure.provolatile = 's'
                   AND procedure.proparallel = 'u'
                   AND NOT procedure.prosecdef
                   AND NOT procedure.proleakproof
                   AND language_row.lanname = 'plpgsql'
                   AND procedure.proconfig =
                       ARRAY['search_path=pg_catalog, public']::text[]
                   AND procedure.proowner = public_namespace.nspowner
                   AND NOT owner.rolcanlogin
                   AND NOT owner.rolsuper
                   AND NOT owner.rolcreaterole
                   AND NOT owner.rolcreatedb
                   AND NOT owner.rolreplication
                   AND NOT owner.rolbypassrls
            FROM pg_proc procedure
            JOIN pg_namespace namespace_row
              ON namespace_row.oid = procedure.pronamespace
            JOIN pg_namespace public_namespace
              ON public_namespace.nspname = 'public'
            JOIN pg_language language_row ON language_row.oid = procedure.prolang
            JOIN pg_roles owner ON owner.oid = procedure.proowner
            WHERE namespace_row.nspname = 'public'
              AND procedure.proname = 'crawler_studio_contract_is_valid'
              AND procedure.pronargs = 0
            """
        )
        studio_contract = cursor.fetchone()
        if (
            not studio_contract
            or hashlib.sha256(
                _normalized_routine_source(str(studio_contract[0])).encode("utf-8")
            ).hexdigest()
            != expected_studio_contract_sha256
            or studio_contract[1] is not True
        ):
            raise SchemaInstallError("live Crawler Studio catalog contract has drifted")
        cursor.execute("SELECT public.crawler_studio_contract_is_valid()")
        if cursor.fetchone() != (True,):
            raise SchemaInstallError("live Crawler Studio catalog contract has drifted")
        if expected_owner is not None:
            if not DATABASE_IDENTIFIER.fullmatch(expected_owner):
                raise SchemaInstallError("Crawler Studio expected owner is invalid")
            cursor.execute(
                """
                SELECT count(*) = 5
                       AND bool_and(owner.rolname = %s)
                FROM (
                    SELECT table_row.relowner AS owner_oid
                    FROM pg_class table_row
                    WHERE table_row.oid IN (
                        'public.ops_crawler_studio_provider_paths'::regclass,
                        'public.ops_crawler_studio_drafts'::regclass,
                        'public.ops_crawler_studio_revisions'::regclass,
                        'public.ops_crawler_studio_reviews'::regclass
                    )
                    UNION ALL
                    SELECT procedure.proowner
                    FROM pg_proc procedure
                    WHERE procedure.oid = to_regprocedure(
                        'public.crawler_studio_contract_is_valid()'
                    )
                ) object_owner
                JOIN pg_roles owner ON owner.oid = object_owner.owner_oid
                """,
                (expected_owner,),
            )
            if cursor.fetchone()[0] is not True:
                raise SchemaInstallError("Crawler Studio object ownership has drifted")
        cursor.execute(
            """
            SELECT count(*) = 1
               AND bool_and(singleton IS TRUE)
               AND bool_and(database_name = current_database()::name)
            FROM public.ops_crawler_control_database_marker
            """
        )
        if cursor.fetchone()[0] is not True:
            raise SchemaInstallError("crawler control database marker contract has drifted")
        cursor.execute(
            """
            SELECT crawler_index.indisunique
                   AND crawler_index.indisvalid
                   AND crawler_index.indisready
                   AND crawler_index.indislive
                   AND crawler_index.indimmediate
                   AND NOT crawler_index.indisprimary
                   AND NOT crawler_index.indisexclusion
                   AND crawler_index.indexprs IS NULL
                   AND crawler_index.indnkeyatts = 1
                   AND crawler_index.indnatts = 1
                   AND crawler_index.indkey::text = lease_token.attnum::text
                   AND access_method.amname = 'btree'
                   AND regexp_replace(
                       pg_get_expr(crawler_index.indpred, crawler_index.indrelid),
                       '\\s+', ' ', 'g'
                   ) IN ('(lease_token IS NOT NULL)', 'lease_token IS NOT NULL')
            FROM pg_index crawler_index
            JOIN pg_class index_relation ON index_relation.oid = crawler_index.indexrelid
            JOIN pg_namespace index_namespace ON index_namespace.oid = index_relation.relnamespace
            JOIN pg_class table_relation ON table_relation.oid = crawler_index.indrelid
            JOIN pg_namespace table_namespace ON table_namespace.oid = table_relation.relnamespace
            JOIN pg_attribute lease_token
              ON lease_token.attrelid = table_relation.oid
             AND lease_token.attname = 'lease_token'
             AND NOT lease_token.attisdropped
            JOIN pg_am access_method ON access_method.oid = index_relation.relam
            WHERE index_namespace.nspname = 'public'
              AND index_relation.relname = 'ux_ops_jobs_active_lease_token'
              AND table_namespace.nspname = 'public'
              AND table_relation.relname = 'ops_jobs'
            """
        )
        active_lease_index = cursor.fetchone()
        if not active_lease_index or active_lease_index[0] is not True:
            raise SchemaInstallError("active crawler lease token unique index definition has drifted")
        policy_digest = _crawler_policy_digest(cursor)
        policy_version = (
            f"{MIGRATION_VERSION}_policies_{staging_checksum[:12]}_{policy_digest[:12]}"
        )
        cursor.execute(
            """
            SELECT checksum = %s
            FROM mooncen_schema_migrations
            WHERE version = %s
            """,
            (policy_digest, policy_version),
        )
        policy_marker = cursor.fetchone()
        if not policy_marker or policy_marker[0] is not True:
            raise SchemaInstallError("live crawler RLS policy digest differs from the applied contract")
        acl_digest = _crawler_acl_digest(cursor)
        acl_version = f"{MIGRATION_VERSION}_acls_{roles_checksum[:12]}_{acl_digest[:12]}"
        cursor.execute(
            """
            SELECT checksum = %s
            FROM mooncen_schema_migrations
            WHERE version = %s
            """,
            (acl_digest, acl_version),
        )
        acl_marker = cursor.fetchone()
        if not acl_marker or acl_marker[0] is not True:
            raise SchemaInstallError("live crawler permission-group ACL digest has drifted")
    if rollback:
        connection.rollback()


def _assert_include_safe_roles(roles: str) -> None:
    if re.search(r"(?im)^\s*(?:BEGIN|COMMIT|ROLLBACK|START\s+TRANSACTION)\s*;\s*$", roles):
        raise SchemaInstallError("roles body must not own a transaction boundary")


def _atomic_recorded_checksum(cursor: Any, version: str) -> str | None:
    cursor.execute(
        "SELECT checksum FROM public.mooncen_schema_migrations WHERE version = %s",
        (version,),
    )
    row = cursor.fetchone()
    return None if row is None else str(row[0] or "")


def _apply_control_contract_atomically(
    connection: Any,
    *,
    confirmed_database: str,
    object_owner: str,
    schema_user: str,
    claim: InstallReceiptClaim,
    migration: str,
    release_action_migration: str = "",
    studio_migration: str = "",
    rollout_snapshot_migration: str = "",
    attempt_release_generation_migration: str = "",
    release_operator_approval_migration: str = "",
    quality_environment_isolation_migration: str = "",
    marker: str,
    staging: str,
    roles: str,
    checksum: str,
    release_action_checksum: str = "",
    studio_checksum: str = "",
    rollout_snapshot_checksum: str = "",
    attempt_release_generation_checksum: str = "",
    release_operator_approval_checksum: str = "",
    quality_environment_isolation_checksum: str = "",
    marker_checksum: str,
    staging_checksum: str,
    roles_checksum: str,
) -> dict[str, Any]:
    """Apply the complete control contract and receipt with exactly one commit."""

    if not release_action_migration:
        release_action_migration, default_release_action_checksum = _release_action_migration()
        release_action_checksum = release_action_checksum or default_release_action_checksum
    if not studio_migration:
        studio_migration, default_studio_checksum = _studio_migration()
        studio_checksum = studio_checksum or default_studio_checksum
    if not rollout_snapshot_migration:
        rollout_snapshot_migration, default_rollout_snapshot_checksum = (
            _rollout_snapshot_migration()
        )
        rollout_snapshot_checksum = (
            rollout_snapshot_checksum or default_rollout_snapshot_checksum
        )
    if not attempt_release_generation_migration:
        (
            attempt_release_generation_migration,
            default_attempt_release_generation_checksum,
        ) = _attempt_release_generation_migration()
        attempt_release_generation_checksum = (
            attempt_release_generation_checksum
            or default_attempt_release_generation_checksum
        )
    if not release_operator_approval_migration:
        (
            release_operator_approval_migration,
            default_release_operator_approval_checksum,
        ) = _release_operator_approval_migration()
        release_operator_approval_checksum = (
            release_operator_approval_checksum
            or default_release_operator_approval_checksum
        )
    if not quality_environment_isolation_migration:
        (
            quality_environment_isolation_migration,
            default_quality_environment_isolation_checksum,
        ) = _quality_environment_isolation_migration()
        quality_environment_isolation_checksum = (
            quality_environment_isolation_checksum
            or default_quality_environment_isolation_checksum
        )
    _assert_include_safe_roles(roles)
    marker_version = f"{MIGRATION_VERSION}_marker_{marker_checksum[:16]}"
    staging_version = f"{MIGRATION_VERSION}_staging_{staging_checksum[:16]}"
    roles_version = f"{MIGRATION_VERSION}_roles_{roles_checksum[:16]}"
    connection.rollback()
    connection.set_session(isolation_level="SERIALIZABLE", readonly=False, autocommit=False)
    try:
        with connection.cursor() as cursor:
            # The xact lock is the first statement in the write transaction.
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (ADVISORY_LOCK,))
            cursor.execute("SET LOCAL lock_timeout = '5s'; SET LOCAL statement_timeout = '30min'")
            cursor.execute("SELECT current_database(), session_user")
            identity = cursor.fetchone()
            if identity != (confirmed_database, schema_user):
                raise SchemaInstallError("atomic installer database or session role changed")

            recorded = _atomic_recorded_checksum(cursor, MIGRATION_VERSION)
            release_action_recorded = _atomic_recorded_checksum(
                cursor, RELEASE_ACTION_MIGRATION_VERSION
            )
            studio_recorded = _atomic_recorded_checksum(cursor, STUDIO_MIGRATION_VERSION)
            rollout_snapshot_recorded = _atomic_recorded_checksum(
                cursor, ROLLOUT_SNAPSHOT_MIGRATION_VERSION
            )
            attempt_release_generation_recorded = _atomic_recorded_checksum(
                cursor, ATTEMPT_RELEASE_GENERATION_MIGRATION_VERSION
            )
            release_operator_approval_recorded = _atomic_recorded_checksum(
                cursor, RELEASE_OPERATOR_APPROVAL_MIGRATION_VERSION
            )
            quality_environment_isolation_recorded = _atomic_recorded_checksum(
                cursor, QUALITY_ENVIRONMENT_ISOLATION_MIGRATION_VERSION
            )
            if recorded and recorded != checksum:
                raise SchemaInstallError("applied crawler control migration checksum differs")
            if release_action_recorded and release_action_recorded != release_action_checksum:
                raise SchemaInstallError("applied release action queue migration checksum differs")
            if studio_recorded and studio_recorded != studio_checksum:
                raise SchemaInstallError("applied Crawler Studio migration checksum differs")
            if (
                rollout_snapshot_recorded
                and rollout_snapshot_recorded != rollout_snapshot_checksum
            ):
                raise SchemaInstallError(
                    "applied rollout worker snapshot migration checksum differs"
                )
            if (
                release_operator_approval_recorded
                and release_operator_approval_recorded
                != release_operator_approval_checksum
            ):
                raise SchemaInstallError(
                    "applied release operator approval migration checksum differs"
                )
            if (
                quality_environment_isolation_recorded
                and quality_environment_isolation_recorded
                != quality_environment_isolation_checksum
            ):
                raise SchemaInstallError(
                    "applied quality environment isolation migration checksum differs"
                )
            if (
                attempt_release_generation_recorded
                and attempt_release_generation_recorded
                != attempt_release_generation_checksum
            ):
                raise SchemaInstallError(
                    "applied attempt release generation migration checksum differs"
                )
            # Pass 1 creates/converges all NOLOGIN permission groups. The body
            # is include-safe and has no BEGIN/COMMIT of its own.
            cursor.execute(roles)
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_crawler_worker'")
            if cursor.fetchone() is None:
                raise SchemaInstallError("roles body did not bootstrap mooncen_crawler_worker")
            _prepare_install_receipt_ledger(cursor, object_owner=object_owner)

            cursor.execute(sql.SQL("SET LOCAL ROLE {} ").format(sql.Identifier(object_owner)))
            cursor.execute(marker)
            if not recorded:
                cursor.execute(migration)
            elif recorded != checksum:
                raise SchemaInstallError("crawler control migration changed under the lock")
            if not release_action_recorded:
                cursor.execute(release_action_migration)
            if not studio_recorded:
                cursor.execute(studio_migration)
            if not rollout_snapshot_recorded:
                cursor.execute(rollout_snapshot_migration)
            if not attempt_release_generation_recorded:
                cursor.execute(attempt_release_generation_migration)
            if not release_operator_approval_recorded:
                cursor.execute(release_operator_approval_migration)
            if not quality_environment_isolation_recorded:
                cursor.execute(quality_environment_isolation_migration)
            cursor.execute(staging)
            policy_digest = _crawler_policy_digest(cursor)
            policy_version = (
                f"{MIGRATION_VERSION}_policies_{staging_checksum[:12]}_{policy_digest[:12]}"
            )
            for version, digest in (
                (MIGRATION_VERSION, checksum),
                (RELEASE_ACTION_MIGRATION_VERSION, release_action_checksum),
                (STUDIO_MIGRATION_VERSION, studio_checksum),
                (ROLLOUT_SNAPSHOT_MIGRATION_VERSION, rollout_snapshot_checksum),
                (
                    ATTEMPT_RELEASE_GENERATION_MIGRATION_VERSION,
                    attempt_release_generation_checksum,
                ),
                (
                    RELEASE_OPERATOR_APPROVAL_MIGRATION_VERSION,
                    release_operator_approval_checksum,
                ),
                (
                    QUALITY_ENVIRONMENT_ISOLATION_MIGRATION_VERSION,
                    quality_environment_isolation_checksum,
                ),
                (staging_version, staging_checksum),
                (marker_version, marker_checksum),
                (policy_version, policy_digest),
            ):
                cursor.execute(
                    """
                    INSERT INTO public.mooncen_schema_migrations(version, checksum)
                    VALUES (%s, %s)
                    ON CONFLICT (version) DO NOTHING
                    """,
                    (version, digest),
                )
            cursor.execute("RESET ROLE")

            # Pass 2 converges grants after every control object exists and
            # explicitly removes all runtime access to the receipt ledger.
            cursor.execute(roles)
            cursor.execute(sql.SQL("SET LOCAL ROLE {} ").format(sql.Identifier(object_owner)))
            cursor.execute(
                """
                INSERT INTO public.mooncen_schema_migrations(version, checksum)
                VALUES (%s, %s)
                ON CONFLICT (version) DO NOTHING
                """,
                (roles_version, roles_checksum),
            )
            acl_digest = _crawler_acl_digest(cursor)
            acl_version = f"{MIGRATION_VERSION}_acls_{roles_checksum[:12]}_{acl_digest[:12]}"
            cursor.execute(
                """
                INSERT INTO public.mooncen_schema_migrations(version, checksum)
                VALUES (%s, %s)
                ON CONFLICT (version) DO NOTHING
                """,
                (acl_version, acl_digest),
            )
            cursor.execute("RESET ROLE")

        _post_contract(
            connection,
            checksum,
            release_action_checksum,
            studio_checksum,
            rollout_snapshot_checksum,
            attempt_release_generation_checksum,
            release_operator_approval_checksum,
            quality_environment_isolation_checksum,
            staging_version,
            staging_checksum,
            marker_version,
            marker_checksum,
            roles_version,
            roles_checksum,
            expected_owner=object_owner,
            rollback=False,
        )
        with connection.cursor() as cursor:
            # Revalidate ledger ACL after the broad roles pass. Receipt insert
            # is deliberately the final mutation before the only commit.
            _prepare_install_receipt_ledger(cursor, object_owner=object_owner)
            _insert_install_receipt(
                cursor,
                claim,
                object_owner=object_owner,
                schema_user=schema_user,
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return {
        "status": "ok",
        "mode": "apply",
        "database": confirmed_database,
        "migration": MIGRATION_VERSION,
        "checksum": checksum,
        "object_owner": object_owner,
        "receipt_sha256": claim.receipt_sha256,
    }


def ensure_schema(
    environment_file: Path,
    confirmed_database: str,
    *,
    dry_run: bool,
    require_applied: bool = False,
    install_receipt_claim: InstallReceiptClaim | None = None,
) -> dict[str, Any]:
    try:
        environment = _protected_environment(environment_file, owner_only=True)
    except PreflightError as exc:
        raise SchemaInstallError(str(exc)) from exc
    config = _connection_config(environment)
    object_owner = _required(environment, "OPS_CRAWLER_SCHEMA_OBJECT_OWNER")
    if confirmed_database != config["database"] or not DATABASE_IDENTIFIER.fullmatch(confirmed_database):
        raise SchemaInstallError("--confirm-staging-database must exactly match the configured database")
    (
        migration,
        marker,
        staging,
        roles,
        checksum,
        marker_checksum,
        staging_checksum,
        roles_checksum,
    ) = _read_contract_files()
    release_action_migration, release_action_checksum = _release_action_migration()
    studio_migration, studio_checksum = _studio_migration()
    rollout_snapshot_migration, rollout_snapshot_checksum = (
        _rollout_snapshot_migration()
    )
    (
        attempt_release_generation_migration,
        attempt_release_generation_checksum,
    ) = _attempt_release_generation_migration()
    (
        release_operator_approval_migration,
        release_operator_approval_checksum,
    ) = _release_operator_approval_migration()
    (
        quality_environment_isolation_migration,
        quality_environment_isolation_checksum,
    ) = _quality_environment_isolation_migration()
    marker_version = f"{MIGRATION_VERSION}_marker_{marker_checksum[:16]}"
    staging_version = f"{MIGRATION_VERSION}_staging_{staging_checksum[:16]}"
    roles_version = f"{MIGRATION_VERSION}_roles_{roles_checksum[:16]}"
    try:
        connection = psycopg2.connect(**config)
    except Exception as exc:
        raise SchemaInstallError("cannot connect to the confirmed staging database") from exc
    try:
        _pin_installer_search_path(connection)
        _base_contract(connection, confirmed_database, object_owner)
        recorded = _recorded_checksum(connection)
        release_action_recorded = _recorded_checksum(
            connection, RELEASE_ACTION_MIGRATION_VERSION
        )
        studio_recorded = _recorded_checksum(connection, STUDIO_MIGRATION_VERSION)
        rollout_snapshot_recorded = _recorded_checksum(
            connection, ROLLOUT_SNAPSHOT_MIGRATION_VERSION
        )
        attempt_release_generation_recorded = _recorded_checksum(
            connection, ATTEMPT_RELEASE_GENERATION_MIGRATION_VERSION
        )
        release_operator_approval_recorded = _recorded_checksum(
            connection, RELEASE_OPERATOR_APPROVAL_MIGRATION_VERSION
        )
        quality_environment_isolation_recorded = _recorded_checksum(
            connection, QUALITY_ENVIRONMENT_ISOLATION_MIGRATION_VERSION
        )
        staging_recorded = _recorded_checksum(connection, staging_version)
        marker_recorded = _recorded_checksum(connection, marker_version)
        roles_recorded = _recorded_checksum(connection, roles_version)
        if recorded and recorded != checksum:
            raise SchemaInstallError("applied crawler control migration checksum differs from this release")
        if dry_run:
            contract_applied = (
                recorded
                and release_action_recorded == release_action_checksum
                and studio_recorded == studio_checksum
                and rollout_snapshot_recorded == rollout_snapshot_checksum
                and attempt_release_generation_recorded
                == attempt_release_generation_checksum
                and release_operator_approval_recorded
                == release_operator_approval_checksum
                and quality_environment_isolation_recorded
                == quality_environment_isolation_checksum
                and marker_recorded == marker_checksum
                and staging_recorded == staging_checksum
                and roles_recorded == roles_checksum
            )
            if require_applied and not contract_applied:
                raise SchemaInstallError(
                    "exact crawler control contract is not applied; run central setup first"
                )
            if contract_applied:
                _post_contract(
                    connection,
                    checksum,
                    release_action_checksum,
                    studio_checksum,
                    rollout_snapshot_checksum,
                    attempt_release_generation_checksum,
                    release_operator_approval_checksum,
                    quality_environment_isolation_checksum,
                    staging_version,
                    staging_checksum,
                    marker_version,
                    marker_checksum,
                    roles_version,
                    roles_checksum,
                    expected_owner=object_owner,
                )
            return {
                "status": "ready",
                "mode": "dry-run",
                "database": confirmed_database,
                "migration": MIGRATION_VERSION,
                "migration_state": "applied" if recorded else "pending",
                "contract_state": (
                    "applied"
                    if contract_applied
                    else "pending"
                ),
                "checksum": checksum,
                "object_owner": object_owner,
            }

        # The one-commit database coordinator is implemented below, but it is
        # not yet authorized: activation and installation do not share one
        # root lock/stable release inode, and cluster-wide role DDL is not yet
        # serialized against role installers connected to another database.
        # Keep direct --apply fail-closed until both host-level seams are proven.
        raise SchemaInstallError(
            "NOT READY: install receipt atomic DB apply awaits shared root/release "
            "and cluster-wide role-DDL locks"
        )

        if install_receipt_claim is None:
            raise SchemaInstallError("release-bound install receipt claim is required")
        return _apply_control_contract_atomically(
            connection,
            confirmed_database=confirmed_database,
            object_owner=object_owner,
            schema_user=config["user"],
            claim=install_receipt_claim,
            migration=migration,
            release_action_migration=release_action_migration,
            studio_migration=studio_migration,
            rollout_snapshot_migration=rollout_snapshot_migration,
            attempt_release_generation_migration=attempt_release_generation_migration,
            release_operator_approval_migration=release_operator_approval_migration,
            quality_environment_isolation_migration=quality_environment_isolation_migration,
            marker=marker,
            staging=staging,
            roles=roles,
            checksum=checksum,
            release_action_checksum=release_action_checksum,
            studio_checksum=studio_checksum,
            rollout_snapshot_checksum=rollout_snapshot_checksum,
            attempt_release_generation_checksum=attempt_release_generation_checksum,
            release_operator_approval_checksum=release_operator_approval_checksum,
            quality_environment_isolation_checksum=quality_environment_isolation_checksum,
            marker_checksum=marker_checksum,
            staging_checksum=staging_checksum,
            roles_checksum=roles_checksum,
        )

        # DORMANT LEGACY REFERENCE: unreachable both through the NOT READY
        # exception and through the atomic return above. It is not an
        # authorized apply path and will be deleted when the host locks land.
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(hashtext(%s))", (ADVISORY_LOCK,))
        connection.autocommit = False

        # First convergence bootstraps mooncen_crawler_worker.  The staging
        # trigger contract refuses to install without that server-side role.
        _execute_roles(connection, roles)
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = 'mooncen_crawler_worker'")
            if cursor.fetchone() is None:
                raise SchemaInstallError("roles.sql did not bootstrap mooncen_crawler_worker")
        connection.rollback()

        recorded = _recorded_checksum(connection)
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '5s'; SET LOCAL statement_timeout = '30min'")
            cursor.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(object_owner)))
            cursor.execute(marker)
            if not recorded:
                cursor.execute(migration)
            elif recorded != checksum:
                raise SchemaInstallError("crawler control migration changed while the installer was running")
            # The migration's new grants and every mandatory worker/RLS fence
            # commit together. A crash can never leave a ledgered migration
            # whose staging security contract was not installed.
            cursor.execute(staging)
            policy_digest = _crawler_policy_digest(cursor)
            policy_version = (
                f"{MIGRATION_VERSION}_policies_{staging_checksum[:12]}_{policy_digest[:12]}"
            )
            if not recorded:
                cursor.execute(
                    "INSERT INTO mooncen_schema_migrations(version, checksum) VALUES (%s, %s)",
                    (MIGRATION_VERSION, checksum),
                )
            cursor.execute(
                """
                INSERT INTO mooncen_schema_migrations(version, checksum)
                VALUES (%s, %s)
                ON CONFLICT (version) DO NOTHING
                """,
                (staging_version, staging_checksum),
            )
            cursor.execute(
                """
                INSERT INTO mooncen_schema_migrations(version, checksum)
                VALUES (%s, %s)
                ON CONFLICT (version) DO NOTHING
                """,
                (marker_version, marker_checksum),
            )
            cursor.execute(
                """
                INSERT INTO mooncen_schema_migrations(version, checksum)
                VALUES (%s, %s)
                ON CONFLICT (version) DO NOTHING
                """,
                (policy_version, policy_digest),
            )
        connection.commit()

        # Re-run after every object exists so grants converge atomically.
        _execute_roles(connection, roles)
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(object_owner)))
            cursor.execute(
                """
                INSERT INTO mooncen_schema_migrations(version, checksum)
                VALUES (%s, %s)
                ON CONFLICT (version) DO NOTHING
                """,
                (roles_version, roles_checksum),
            )
            acl_digest = _crawler_acl_digest(cursor)
            acl_version = f"{MIGRATION_VERSION}_acls_{roles_checksum[:12]}_{acl_digest[:12]}"
            cursor.execute(
                """
                INSERT INTO mooncen_schema_migrations(version, checksum)
                VALUES (%s, %s)
                ON CONFLICT (version) DO NOTHING
                """,
                (acl_version, acl_digest),
            )
        connection.commit()
        _assert_application_owner_access(connection, object_owner)
        _post_contract(
            connection,
            checksum,
            release_action_checksum,
            studio_checksum,
            rollout_snapshot_checksum,
            attempt_release_generation_checksum,
            release_operator_approval_checksum,
            quality_environment_isolation_checksum,
            staging_version,
            staging_checksum,
            marker_version,
            marker_checksum,
            roles_version,
            roles_checksum,
            expected_owner=object_owner,
        )
        return {
            "status": "ok",
            "mode": "apply",
            "database": confirmed_database,
            "migration": MIGRATION_VERSION,
            "checksum": checksum,
            "object_owner": object_owner,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        try:
            connection.autocommit = True
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(hashtext(%s))", (ADVISORY_LOCK,))
        except Exception:
            pass
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install the crawler control contract on a staging database")
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--confirm-staging-database", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--install-receipt", type=Path)
    parser.add_argument("--install-receipt-signature", type=Path)
    parser.add_argument("--install-receipt-nonce")
    parser.add_argument("--release-id")
    parser.add_argument("--expected-commit")
    parser.add_argument("--expected-archive-sha256")
    parser.add_argument("--expected-tree-sha256")
    parser.add_argument(
        "--require-applied",
        action="store_true",
        help="With --dry-run, fail unless this release's full contract is already applied.",
    )
    args = parser.parse_args(argv)
    if args.require_applied and not args.dry_run:
        parser.error("--require-applied is valid only with --dry-run")
    receipt_arguments = (
        args.install_receipt,
        args.install_receipt_signature,
        args.install_receipt_nonce,
        args.release_id,
        args.expected_commit,
        args.expected_archive_sha256,
        args.expected_tree_sha256,
    )
    if args.apply and any(value is None for value in receipt_arguments):
        parser.error("--apply requires the complete release-bound install receipt identity")
    if args.dry_run and any(value is not None for value in receipt_arguments):
        parser.error("install receipt arguments are valid only with --apply")
    try:
        install_receipt_claim = None
        if args.apply:
            install_receipt_claim = _verified_install_receipt(
                args.install_receipt,
                args.install_receipt_signature,
                nonce=args.install_receipt_nonce,
                release_id=args.release_id,
                commit=args.expected_commit,
                archive_sha256=args.expected_archive_sha256,
                tree_sha256=args.expected_tree_sha256,
            )
        result = ensure_schema(
            args.env_file,
            args.confirm_staging_database,
            dry_run=args.dry_run,
            require_applied=args.require_applied,
            install_receipt_claim=install_receipt_claim,
        )
    except (SchemaInstallError, PreflightError) as exc:
        parser.exit(78, f"crawler control schema installation failed: {exc}\n")
    except psycopg2.Error:
        parser.exit(70, "crawler control schema installation failed: PostgreSQL operation failed\n")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
