from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ENV_PREFIX = "MOONCEN_SYNC_"
POLICY_VERSION = "mooncen-production-to-development-v1"
PLAN_PHASES = (
    "source_read_only_identity",
    "destination_identity_guard",
    "source_read_only_dump",
    "create_isolated_database",
    "restore_isolated_database",
    "sanitize_private_data",
    "verify_sanitization",
    "replace_development_database",
    "verify_replacement",
    "remove_previous_development_database",
)

# pg_dump receives these as argv entries, not through a shell. Known private and
# operational data never enters the archive. The post-restore SQL is a second,
# schema-aware line of defence for tables introduced after this policy version.
DUMP_EXCLUDED_DATA_PATTERNS = (
    "public.users",
    "public.oauth_accounts",
    "public.user_*",
    "public.notifications",
    "public.course_alerts",
    "public.search_logs",
    "public.ops_*",
    "public.crawler_run_log",
    "public.crawl_progress",
    "public.crawl_batches",
    "public.crawl_staging",
    "public.crawl_batch_apply_logs",
    "public.crawl_batch_validation_errors",
    "public.course_update_requests",
)

_PRODUCTION_NAME_MARKER = re.compile(
    r"(?:^|[._-])(production|prod|cloud|live)(?:$|[._-])",
    re.IGNORECASE,
)
_PRODUCTION_COMMENT_MARKER = re.compile(
    r"(?:mooncen\.)?environment\s*[:=]\s*(production|prod|cloud|live)\b",
    re.IGNORECASE,
)
_SAFE_RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")


IDENTITY_SQL = r"""
SELECT json_build_object(
    'database', current_database(),
    'user', current_user,
    'server_address', COALESCE(inet_server_addr()::text, ''),
    'server_port', COALESCE(inet_server_port(), 0),
    'cluster_catalog_fingerprint', COALESCE(
        (
            SELECT md5(string_agg(oid::text || ':' || datname, ',' ORDER BY oid))
            FROM pg_database
        ),
        ''
    ),
    'transaction_read_only', current_setting('transaction_read_only'),
    'role_is_superuser', COALESCE(
        (SELECT rolsuper FROM pg_roles WHERE rolname = current_user),
        false
    ),
    'role_can_create_database_objects', has_database_privilege(
        current_user,
        current_database(),
        'CREATE'
    ),
    'role_can_write_tables', EXISTS (
        SELECT 1
        FROM pg_class relation
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname NOT IN ('pg_catalog', 'information_schema')
          AND relation.relkind IN ('r', 'p')
          AND (
              has_table_privilege(current_user, relation.oid, 'INSERT')
              OR has_table_privilege(current_user, relation.oid, 'UPDATE')
              OR has_table_privilege(current_user, relation.oid, 'DELETE')
              OR has_table_privilege(current_user, relation.oid, 'TRUNCATE')
          )
    ),
    'environment_setting', COALESCE(current_setting('mooncen.environment', true), ''),
    'database_comment', COALESCE(
        shobj_description(
            (SELECT oid FROM pg_database WHERE datname = current_database()),
            'pg_database'
        ),
        ''
    )
)::text;
"""


SANITIZE_SQL = r"""
CREATE TEMP TABLE mooncen_sync_sanitized_tables (
    table_name text PRIMARY KEY,
    removed_rows bigint NOT NULL,
    remaining_rows bigint NOT NULL
) ON COMMIT DROP;

DO $mooncen_sync$
DECLARE
    target record;
    rows_before bigint;
    rows_after bigint;
BEGIN
    FOR target IN
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public'
          AND (
              tablename ~* '^ops_'
              OR tablename ~* '(^|_)(users?|oauth|sessions?|tokens?|notifications?|favorites?|audit_logs?)($|_)'
              OR tablename IN (
                  'course_alerts',
                  'search_logs',
                  'crawler_run_log',
                  'crawl_progress',
                  'crawl_batches',
                  'crawl_staging',
                  'crawl_batch_apply_logs',
                  'crawl_batch_validation_errors',
                  'course_update_requests'
              )
              OR tablename IN (
                  SELECT DISTINCT c.table_name
                  FROM information_schema.columns c
                  WHERE c.table_schema = 'public'
                    AND c.column_name ~* '^(email|password|password_hash|password_digest|token|access_token|refresh_token|session_token|fcm_token|api_key|client_secret|secret|session_id|user_id|provider_user_id|ip_address|cookie|authorization|phone|phone_number|mobile|full_name)$'
                    AND NOT (c.table_name = 'branches' AND c.column_name = 'phone')
              )
          )
        ORDER BY tablename
    LOOP
        EXECUTE format('SELECT count(*) FROM %I.%I', 'public', target.tablename)
            INTO rows_before;
        EXECUTE format(
            'TRUNCATE TABLE %I.%I RESTART IDENTITY CASCADE',
            'public',
            target.tablename
        );
        EXECUTE format('SELECT count(*) FROM %I.%I', 'public', target.tablename)
            INTO rows_after;
        INSERT INTO mooncen_sync_sanitized_tables(table_name, removed_rows, remaining_rows)
        VALUES (target.tablename, rows_before, rows_after);
    END LOOP;
END
$mooncen_sync$;

CREATE TEMP TABLE mooncen_sync_sensitive_residual (
    table_name text PRIMARY KEY,
    row_count bigint NOT NULL
) ON COMMIT DROP;

DO $mooncen_verify$
DECLARE
    target record;
    remaining bigint;
BEGIN
    FOR target IN
        SELECT DISTINCT c.table_name
        FROM information_schema.columns c
        WHERE c.table_schema = 'public'
          AND (
              c.table_name ~* '^ops_'
              OR c.table_name ~* '(^|_)(users?|oauth|sessions?|tokens?|notifications?|favorites?|audit_logs?)($|_)'
              OR c.column_name ~* '^(email|password|password_hash|password_digest|token|access_token|refresh_token|session_token|fcm_token|api_key|client_secret|secret|session_id|user_id|provider_user_id|ip_address|cookie|authorization|phone|phone_number|mobile|full_name)$'
          )
          AND NOT (c.table_name = 'branches' AND c.column_name = 'phone')
        ORDER BY c.table_name
    LOOP
        EXECUTE format('SELECT count(*) FROM %I.%I', 'public', target.table_name)
            INTO remaining;
        IF remaining > 0 THEN
            INSERT INTO mooncen_sync_sensitive_residual(table_name, row_count)
            VALUES (target.table_name, remaining);
        END IF;
    END LOOP;
END
$mooncen_verify$;

SELECT json_build_object(
    'policy_version', 'mooncen-production-to-development-v1',
    'tables', COALESCE(
        (
            SELECT json_agg(
                json_build_object(
                    'table', table_name,
                    'removed_rows', removed_rows,
                    'remaining_rows', remaining_rows
                )
                ORDER BY table_name
            )
            FROM mooncen_sync_sanitized_tables
        ),
        '[]'::json
    ),
    'remaining_sensitive_rows', COALESCE(
        (SELECT sum(row_count) FROM mooncen_sync_sensitive_residual),
        0
    ),
    'residual_tables', COALESCE(
        (
            SELECT json_agg(table_name ORDER BY table_name)
            FROM mooncen_sync_sensitive_residual
        ),
        '[]'::json
    )
)::text;
"""


VERIFY_SANITIZATION_SQL = r"""
CREATE TEMP TABLE mooncen_sync_sensitive_residual (
    table_name text PRIMARY KEY,
    row_count bigint NOT NULL
) ON COMMIT DROP;

DO $mooncen_verify$
DECLARE
    target record;
    remaining bigint;
BEGIN
    FOR target IN
        SELECT DISTINCT c.table_name
        FROM information_schema.columns c
        WHERE c.table_schema = 'public'
          AND (
              c.table_name ~* '^ops_'
              OR c.table_name ~* '(^|_)(users?|oauth|sessions?|tokens?|notifications?|favorites?|audit_logs?)($|_)'
              OR c.column_name ~* '^(email|password|password_hash|password_digest|token|access_token|refresh_token|session_token|fcm_token|api_key|client_secret|secret|session_id|user_id|provider_user_id|ip_address|cookie|authorization|phone|phone_number|mobile|full_name)$'
          )
          AND NOT (c.table_name = 'branches' AND c.column_name = 'phone')
        ORDER BY c.table_name
    LOOP
        EXECUTE format('SELECT count(*) FROM %I.%I', 'public', target.table_name)
            INTO remaining;
        IF remaining > 0 THEN
            INSERT INTO mooncen_sync_sensitive_residual(table_name, row_count)
            VALUES (target.table_name, remaining);
        END IF;
    END LOOP;
END
$mooncen_verify$;

SELECT json_build_object(
    'policy_version', 'mooncen-production-to-development-v1',
    'remaining_sensitive_rows', COALESCE(
        (SELECT sum(row_count) FROM mooncen_sync_sensitive_residual),
        0
    ),
    'residual_tables', COALESCE(
        (
            SELECT json_agg(table_name ORDER BY table_name)
            FROM mooncen_sync_sensitive_residual
        ),
        '[]'::json
    )
)::text;
"""


RunCallable = Callable[..., subprocess.CompletedProcess[str]]


class SyncSafetyError(RuntimeError):
    """The requested source/destination combination is not safe."""


class ToolExecutionError(RuntimeError):
    def __init__(self, phase: str, tool: str, returncode: int | None) -> None:
        self.phase = phase
        self.tool = tool
        self.returncode = returncode
        status = "unavailable" if returncode is None else f"exit status {returncode}"
        super().__init__(f"{tool} failed during {phase} ({status})")


class SyncRunError(RuntimeError):
    def __init__(
        self,
        *,
        status: str,
        manifest_path: Path,
        error_type: str,
        tool_returncode: int | None = None,
    ) -> None:
        self.status = status
        self.manifest_path = manifest_path
        self.error_type = error_type
        self.tool_returncode = tool_returncode
        super().__init__(f"database synchronization {status}: {error_type}")


@dataclass(frozen=True)
class DatabaseEndpoint:
    host: str
    port: int
    database: str
    user: str
    password: str = field(repr=False)
    sslmode: str = "prefer"

    def fingerprint(self) -> str:
        material = f"{self.host.casefold()}\0{self.port}\0{self.database}".encode("utf-8")
        return hashlib.sha256(material).hexdigest()


@dataclass(frozen=True)
class SyncConfig:
    source: DatabaseEndpoint
    destination: DatabaseEndpoint
    administrator: DatabaseEndpoint
    destination_environment: str
    binary_directory: Path | None = None


@dataclass(frozen=True)
class ToolPaths:
    psql: str
    pg_dump: str
    pg_restore: str


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _run_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def _bounded_text(value: str, *, field_name: str, maximum: int = 255) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > maximum or any(ord(char) < 32 for char in normalized):
        raise SyncSafetyError(f"{field_name} is missing or invalid")
    return normalized


def _port(value: str, *, field_name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise SyncSafetyError(f"{field_name} must be an integer") from exc
    if not 1 <= parsed <= 65535:
        raise SyncSafetyError(f"{field_name} must be between 1 and 65535")
    return parsed


def _endpoint_from_env(
    env: Mapping[str, str],
    role: str,
    *,
    require_password: bool,
    fallback: DatabaseEndpoint | None = None,
) -> DatabaseEndpoint:
    prefix = f"{ENV_PREFIX}{role}_"

    def setting(name: str, fallback_value: str = "") -> str:
        return str(env.get(f"{prefix}{name}", fallback_value))

    fallback_port = str(fallback.port) if fallback else "5432"
    password = setting("PASSWORD", fallback.password if fallback else "")
    if require_password and not password:
        raise SyncSafetyError(f"{prefix}PASSWORD is required outside plan mode")
    return DatabaseEndpoint(
        host=_bounded_text(
            setting("HOST", fallback.host if fallback else ""),
            field_name=f"{prefix}HOST",
        ),
        port=_port(setting("PORT", fallback_port), field_name=f"{prefix}PORT"),
        database=_bounded_text(
            setting("DATABASE", fallback.database if fallback else ""),
            field_name=f"{prefix}DATABASE",
        ),
        user=_bounded_text(
            setting("USER", fallback.user if fallback else ""),
            field_name=f"{prefix}USER",
        ),
        password=password,
        sslmode=_bounded_text(
            setting("SSLMODE", fallback.sslmode if fallback else "prefer"),
            field_name=f"{prefix}SSLMODE",
            maximum=32,
        ),
    )


def load_config(env: Mapping[str, str], *, mode: str) -> SyncConfig:
    if mode not in {"plan", "dry-run", "execute"}:
        raise SyncSafetyError("sync mode is invalid")
    for name in env:
        if name.startswith(ENV_PREFIX) and (name.endswith("_DSN") or name.endswith("_URL")):
            raise SyncSafetyError("DSN and URL inputs are not supported; use discrete settings")
    require_password = mode != "plan"
    source = _endpoint_from_env(env, "SOURCE", require_password=require_password)
    destination = _endpoint_from_env(env, "DEST", require_password=require_password)
    admin_database = str(env.get(f"{ENV_PREFIX}DEST_ADMIN_DATABASE", "postgres"))
    administrator_fallback = DatabaseEndpoint(
        host=destination.host,
        port=destination.port,
        database=admin_database,
        user=destination.user,
        password=destination.password,
        sslmode=destination.sslmode,
    )
    administrator = _endpoint_from_env(
        env,
        "DEST_ADMIN",
        require_password=require_password,
        fallback=administrator_fallback,
    )
    destination_environment = str(env.get(f"{ENV_PREFIX}DEST_ENVIRONMENT", "")).strip().casefold()
    binary_value = str(env.get(f"{ENV_PREFIX}PG_BIN", "")).strip()
    config = SyncConfig(
        source=source,
        destination=destination,
        administrator=administrator,
        destination_environment=destination_environment,
        binary_directory=Path(binary_value) if binary_value else None,
    )
    validate_offline_safety(config)
    return config


def _normalized_host(host: str) -> str:
    value = host.strip().rstrip(".").casefold()
    if value in {"localhost", "127.0.0.1", "::1"}:
        return "localhost"
    return value


def _contains_production_name_marker(value: str) -> bool:
    return bool(_PRODUCTION_NAME_MARKER.search(value.strip()))


def validate_offline_safety(config: SyncConfig) -> None:
    if config.destination_environment != "development":
        raise SyncSafetyError("destination environment must be exactly 'development'")
    for value in (
        config.destination.host,
        config.destination.database,
        config.destination.user,
        config.administrator.database,
        config.administrator.user,
    ):
        if _contains_production_name_marker(value):
            raise SyncSafetyError("destination has a production or cloud marker")
    if (
        _normalized_host(config.source.host) == _normalized_host(config.destination.host)
        and config.source.port == config.destination.port
    ):
        raise SyncSafetyError("source and destination must not share a database server")
    if (
        _normalized_host(config.administrator.host) != _normalized_host(config.destination.host)
        or config.administrator.port != config.destination.port
    ):
        raise SyncSafetyError("destination administrator must use the destination server")
    if config.administrator.database == config.destination.database:
        raise SyncSafetyError("administrator database must differ from the replaced database")
    if config.source.sslmode not in {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}:
        raise SyncSafetyError("source SSL mode is invalid")
    if config.destination.sslmode not in {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}:
        raise SyncSafetyError("destination SSL mode is invalid")
    if config.administrator.sslmode not in {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}:
        raise SyncSafetyError("destination administrator SSL mode is invalid")


def validate_confirmation(config: SyncConfig, confirmation: str | None) -> None:
    if confirmation is None or confirmation != config.destination.database:
        raise SyncSafetyError(
            "execute mode requires --confirm-development-replace with the exact destination database name"
        )


def _resolved_addresses(host: str) -> set[str]:
    try:
        return {item[4][0] for item in socket.getaddrinfo(host, None)}
    except OSError:
        return set()


def _identity_has_production_marker(identity: Mapping[str, Any]) -> bool:
    environment = str(identity.get("environment_setting") or "").strip().casefold()
    if environment in {"production", "prod", "cloud", "live"}:
        return True
    return bool(_PRODUCTION_COMMENT_MARKER.search(str(identity.get("database_comment") or "")))


def validate_live_identities(
    config: SyncConfig,
    source_identity: Mapping[str, Any],
    destination_identity: Mapping[str, Any],
) -> None:
    if str(source_identity.get("transaction_read_only") or "").casefold() not in {"on", "true"}:
        raise SyncSafetyError("source preflight session is not read-only")
    required_role_guards = (
        source_identity.get("role_is_superuser"),
        source_identity.get("role_can_create_database_objects"),
        source_identity.get("role_can_write_tables"),
    )
    if any(value is None for value in required_role_guards):
        raise SyncSafetyError("source role privilege preflight is incomplete")
    if any(bool(value) for value in required_role_guards):
        raise SyncSafetyError("source login is not a dedicated read-only role")
    if _identity_has_production_marker(destination_identity):
        raise SyncSafetyError("destination database has a production environment marker")
    source_address = str(source_identity.get("server_address") or "").strip()
    destination_address = str(destination_identity.get("server_address") or "").strip()
    source_cluster = str(source_identity.get("cluster_catalog_fingerprint") or "").strip()
    destination_cluster = str(destination_identity.get("cluster_catalog_fingerprint") or "").strip()
    if source_cluster and destination_cluster and source_cluster == destination_cluster:
        raise SyncSafetyError("source and destination are the same PostgreSQL cluster")
    source_port = int(source_identity.get("server_port") or config.source.port)
    destination_port = int(destination_identity.get("server_port") or config.destination.port)
    if source_address and destination_address:
        if source_address == destination_address and source_port == destination_port:
            raise SyncSafetyError("source and destination resolve to the same database server")
    elif source_port == destination_port:
        source_addresses = _resolved_addresses(config.source.host)
        destination_addresses = _resolved_addresses(config.destination.host)
        if source_addresses and source_addresses.intersection(destination_addresses):
            raise SyncSafetyError("source and destination DNS resolve to the same database server")
    if str(source_identity.get("database") or "") != config.source.database:
        raise SyncSafetyError("source identity does not match configured database")
    if str(destination_identity.get("database") or "") != config.destination.database:
        raise SyncSafetyError("destination identity does not match configured database")


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    details = path.lstat()
    if not stat.S_ISDIR(details.st_mode) or path.is_symlink():
        raise SyncSafetyError("manifest directory must be a regular directory")
    if os.name == "posix" and stat.S_IMODE(details.st_mode) & 0o077:
        raise SyncSafetyError("manifest directory must have mode 0700")


class ManifestWriter:
    def __init__(self, directory: Path, *, run_id: str, mode: str, config: SyncConfig) -> None:
        if not _SAFE_RUN_ID.fullmatch(run_id):
            raise SyncSafetyError("run identifier is invalid")
        _private_directory(directory)
        self.path = directory / f"production-to-development-{run_id}.json"
        self.document: dict[str, Any] = {
            "schema_version": 1,
            "operation": "production-to-development-sync",
            "policy_version": POLICY_VERSION,
            "run_id": run_id,
            "mode": mode,
            "status": "running",
            "started_at": _utc_now(),
            "finished_at": None,
            "source": {"connection_fingerprint": config.source.fingerprint(), "read_only": True},
            "destination": {
                "connection_fingerprint": config.destination.fingerprint(),
                "environment": "development",
            },
            "safety": {
                "same_server_forbidden": True,
                "production_destination_markers_forbidden": True,
                "confirmation_required": mode == "execute",
                "confirmation_verified": False,
                "known_private_data_excluded_from_dump": True,
                "post_restore_sanitization_required": True,
            },
            "planned_phases": list(PLAN_PHASES),
            "phases": [],
        }
        self.write()

    def write(self) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
                descriptor = -1
                json.dump(self.document, output, ensure_ascii=False, indent=2, sort_keys=True)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            if os.name == "posix":
                self.path.chmod(0o600)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)

    def phase_started(self, name: str, tool: str) -> dict[str, Any]:
        phase = {"name": name, "tool": tool, "status": "running", "started_at": _utc_now()}
        self.document["phases"].append(phase)
        self.write()
        return phase

    def phase_finished(
        self,
        phase: dict[str, Any],
        *,
        status: str,
        returncode: int | None,
        duration_ms: int,
    ) -> None:
        phase.update(
            {
                "status": status,
                "returncode": returncode,
                "duration_ms": max(duration_ms, 0),
                "finished_at": _utc_now(),
            }
        )
        self.write()

    def finish(self, status: str, **fields: Any) -> None:
        self.document.update(fields)
        self.document["status"] = status
        self.document["finished_at"] = _utc_now()
        self.write()


def _tool_path(directory: Path | None, name: str) -> str:
    if directory is not None:
        candidates = [directory / name]
        if os.name == "nt":
            candidates.insert(0, directory / f"{name}.exe")
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        raise SyncSafetyError(f"required PostgreSQL client tool is unavailable: {name}")
    resolved = shutil.which(name)
    if not resolved:
        raise SyncSafetyError(f"required PostgreSQL client tool is unavailable: {name}")
    return resolved


def resolve_tools(config: SyncConfig) -> ToolPaths:
    return ToolPaths(
        psql=_tool_path(config.binary_directory, "psql"),
        pg_dump=_tool_path(config.binary_directory, "pg_dump"),
        pg_restore=_tool_path(config.binary_directory, "pg_restore"),
    )


def _libpq_environment(
    endpoint: DatabaseEndpoint,
    *,
    database: str | None = None,
    read_only: bool,
    application_name: str,
) -> dict[str, str]:
    child = dict(os.environ)
    sensitive_fragments = (
        "PASSWORD",
        "TOKEN",
        "SECRET",
        "API_KEY",
        "PRIVATE_KEY",
        "DATABASE_URL",
        "DSN",
    )
    for name in tuple(child):
        if name.startswith((ENV_PREFIX, "DB_", "PG")) or any(
            fragment in name.upper() for fragment in sensitive_fragments
        ):
            child.pop(name, None)
    child.update(
        {
            "PGHOST": endpoint.host,
            "PGPORT": str(endpoint.port),
            "PGDATABASE": database or endpoint.database,
            "PGUSER": endpoint.user,
            "PGPASSWORD": endpoint.password,
            "PGSSLMODE": endpoint.sslmode,
            "PGAPPNAME": application_name,
            "PGCLIENTENCODING": "UTF8",
        }
    )
    if read_only:
        child["PGOPTIONS"] = "-c default_transaction_read_only=on"
    return child


def _run_command(
    command: Sequence[str],
    *,
    endpoint: DatabaseEndpoint,
    database: str | None,
    read_only: bool,
    application_name: str,
    phase_name: str,
    tool_name: str,
    manifest: ManifestWriter,
    run_func: RunCallable,
) -> subprocess.CompletedProcess[str]:
    phase = manifest.phase_started(phase_name, tool_name)
    started = time.monotonic()
    try:
        completed = run_func(
            list(command),
            env=_libpq_environment(
                endpoint,
                database=database,
                read_only=read_only,
                application_name=application_name,
            ),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
        )
    except OSError as exc:
        manifest.phase_finished(
            phase,
            status="failed",
            returncode=None,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        raise ToolExecutionError(phase_name, tool_name, None) from exc
    status = "succeeded" if completed.returncode == 0 else "failed"
    manifest.phase_finished(
        phase,
        status=status,
        returncode=completed.returncode,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    if completed.returncode != 0:
        raise ToolExecutionError(phase_name, tool_name, completed.returncode)
    return completed


def _parse_json_output(output: str, *, phase: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        candidate = line.strip()
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise SyncSafetyError(f"{phase} returned no structured result")


def _psql_json(
    *,
    endpoint: DatabaseEndpoint,
    database: str,
    sql: str,
    read_only: bool,
    phase_name: str,
    manifest: ManifestWriter,
    tools: ToolPaths,
    run_func: RunCallable,
) -> dict[str, Any]:
    completed = _run_command(
        [
            tools.psql,
            "-X",
            "--quiet",
            "--no-align",
            "--tuples-only",
            "--set=ON_ERROR_STOP=1",
            "--dbname",
            database,
            "--command",
            sql,
        ],
        endpoint=endpoint,
        database=database,
        read_only=read_only,
        application_name=f"mooncen-sync-{phase_name[:32]}",
        phase_name=phase_name,
        tool_name="psql",
        manifest=manifest,
        run_func=run_func,
    )
    return _parse_json_output(completed.stdout or "", phase=phase_name)


def _psql_action(
    *,
    endpoint: DatabaseEndpoint,
    database: str,
    sql: str,
    phase_name: str,
    manifest: ManifestWriter,
    tools: ToolPaths,
    run_func: RunCallable,
) -> None:
    _run_command(
        [
            tools.psql,
            "-X",
            "--quiet",
            "--set=ON_ERROR_STOP=1",
            "--dbname",
            database,
            "--command",
            sql,
        ],
        endpoint=endpoint,
        database=database,
        read_only=False,
        application_name=f"mooncen-sync-{phase_name[:32]}",
        phase_name=phase_name,
        tool_name="psql",
        manifest=manifest,
        run_func=run_func,
    )


def _quote_identifier(value: str) -> str:
    if not value or "\x00" in value or len(value.encode("utf-8")) > 63:
        raise SyncSafetyError("database identifier is invalid")
    return '"' + value.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    if "\x00" in value:
        raise SyncSafetyError("database name is invalid")
    return "'" + value.replace("'", "''") + "'"


def _database_exists_sql(names: Sequence[str]) -> str:
    pairs = []
    for name in names:
        pairs.extend((_quote_literal(name), _quote_literal(name)))
    values = ", ".join(f"({pairs[index]}, {pairs[index + 1]})" for index in range(0, len(pairs), 2))
    return (
        "SELECT json_build_object('databases', COALESCE(json_object_agg(requested.name, "
        "(db.datname IS NOT NULL)), '{}'::json))::text "
        f"FROM (VALUES {values}) AS requested(name, lookup_name) "
        "LEFT JOIN pg_database db ON db.datname = requested.lookup_name;"
    )


def _terminate_sql(database: str) -> str:
    literal = _quote_literal(database)
    return (
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        f"WHERE datname = {literal} AND pid <> pg_backend_pid();"
    )


def _rename_sql(old: str, new: str) -> str:
    return f"ALTER DATABASE {_quote_identifier(old)} RENAME TO {_quote_identifier(new)};"


def _drop_sql(database: str) -> str:
    return f"DROP DATABASE IF EXISTS {_quote_identifier(database)};"


def _archive_metadata(path: Path) -> dict[str, Any]:
    details = path.lstat()
    if not stat.S_ISREG(details.st_mode) or path.is_symlink() or details.st_size <= 0:
        raise SyncSafetyError("pg_dump did not create a valid private archive")
    if os.name == "posix":
        path.chmod(0o600)
    digest = hashlib.sha256()
    with path.open("rb") as archive:
        for chunk in iter(lambda: archive.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"sha256": digest.hexdigest(), "bytes": details.st_size}


def _validate_sanitization(result: Mapping[str, Any]) -> None:
    if result.get("policy_version") != POLICY_VERSION:
        raise SyncSafetyError("sanitization policy result is invalid")
    try:
        remaining = int(result.get("remaining_sensitive_rows", -1))
    except (TypeError, ValueError) as exc:
        raise SyncSafetyError("sanitization result is invalid") from exc
    if remaining != 0 or result.get("residual_tables") not in ([], None):
        raise SyncSafetyError("private account, token, session, or audit data remains")


def _database_action(
    sql: str,
    *,
    phase_name: str,
    config: SyncConfig,
    manifest: ManifestWriter,
    tools: ToolPaths,
    run_func: RunCallable,
) -> None:
    _psql_action(
        endpoint=config.administrator,
        database=config.administrator.database,
        sql=sql,
        phase_name=phase_name,
        manifest=manifest,
        tools=tools,
        run_func=run_func,
    )


def _drop_database(
    name: str,
    *,
    phase_prefix: str,
    config: SyncConfig,
    manifest: ManifestWriter,
    tools: ToolPaths,
    run_func: RunCallable,
) -> None:
    _database_action(
        _terminate_sql(name),
        phase_name=f"{phase_prefix}_terminate",
        config=config,
        manifest=manifest,
        tools=tools,
        run_func=run_func,
    )
    _database_action(
        _drop_sql(name),
        phase_name=f"{phase_prefix}_drop",
        config=config,
        manifest=manifest,
        tools=tools,
        run_func=run_func,
    )


def _execute_sync(
    *,
    config: SyncConfig,
    run_id: str,
    manifest: ManifestWriter,
    tools: ToolPaths,
    run_func: RunCallable,
    temporary_root: Path | None,
) -> dict[str, Any]:
    transient_name = f"mooncen_sync_{run_id[-8:]}"
    previous_name = f"mooncen_previous_{run_id[-8:]}"
    failed_name = f"mooncen_failed_{run_id[-8:]}"
    transient_created = False
    destination_renamed = False
    transient_promoted = False
    archive_metadata: dict[str, Any] = {}
    operation_failed = False
    temporary = tempfile.TemporaryDirectory(prefix="mooncen-prod-to-dev-", dir=temporary_root)
    temporary_path = Path(temporary.name)
    if os.name == "posix":
        temporary_path.chmod(0o700)
    archive = temporary_path / "production.dump"
    cleanup_errors: list[str] = []
    try:
        dump_command = [
            tools.pg_dump,
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--file",
            str(archive),
            "--dbname",
            config.source.database,
        ]
        for pattern in DUMP_EXCLUDED_DATA_PATTERNS:
            dump_command.extend(("--exclude-table-data", pattern))
        _run_command(
            dump_command,
            endpoint=config.source,
            database=config.source.database,
            read_only=True,
            application_name="mooncen-sync-source-readonly-dump",
            phase_name="source_read_only_dump",
            tool_name="pg_dump",
            manifest=manifest,
            run_func=run_func,
        )
        archive_metadata = _archive_metadata(archive)
        manifest.document["archive"] = archive_metadata
        manifest.write()

        existence = _psql_json(
            endpoint=config.administrator,
            database=config.administrator.database,
            sql=_database_exists_sql((transient_name, previous_name, failed_name)),
            read_only=True,
            phase_name="isolated_name_preflight",
            manifest=manifest,
            tools=tools,
            run_func=run_func,
        )
        if any(bool(value) for value in dict(existence.get("databases") or {}).values()):
            raise SyncSafetyError("isolated database names already exist; refusing cleanup ambiguity")

        create_sql = (
            f"CREATE DATABASE {_quote_identifier(transient_name)} "
            f"WITH TEMPLATE template0 OWNER {_quote_identifier(config.destination.user)};"
        )
        _database_action(
            create_sql,
            phase_name="create_isolated_database",
            config=config,
            manifest=manifest,
            tools=tools,
            run_func=run_func,
        )
        transient_created = True

        _run_command(
            [
                tools.pg_restore,
                "--exit-on-error",
                "--single-transaction",
                "--no-owner",
                "--no-privileges",
                "--dbname",
                transient_name,
                str(archive),
            ],
            endpoint=config.destination,
            database=transient_name,
            read_only=False,
            application_name="mooncen-sync-isolated-restore",
            phase_name="restore_isolated_database",
            tool_name="pg_restore",
            manifest=manifest,
            run_func=run_func,
        )

        sanitization = _psql_json(
            endpoint=config.destination,
            database=transient_name,
            sql=SANITIZE_SQL,
            read_only=False,
            phase_name="sanitize_private_data",
            manifest=manifest,
            tools=tools,
            run_func=run_func,
        )
        _validate_sanitization(sanitization)
        manifest.document["sanitization"] = sanitization
        manifest.write()

        verification = _psql_json(
            endpoint=config.destination,
            database=transient_name,
            sql=VERIFY_SANITIZATION_SQL,
            read_only=True,
            phase_name="verify_sanitization",
            manifest=manifest,
            tools=tools,
            run_func=run_func,
        )
        _validate_sanitization(verification)
        marker = "mooncen.environment=development; sanitized=true; production_credentials=false"
        _psql_action(
            endpoint=config.destination,
            database=transient_name,
            sql=f"COMMENT ON DATABASE {_quote_identifier(transient_name)} IS {_quote_literal(marker)};",
            phase_name="mark_isolated_database_development",
            manifest=manifest,
            tools=tools,
            run_func=run_func,
        )

        _database_action(
            _terminate_sql(config.destination.database),
            phase_name="replace_development_database_terminate",
            config=config,
            manifest=manifest,
            tools=tools,
            run_func=run_func,
        )
        _database_action(
            _rename_sql(config.destination.database, previous_name),
            phase_name="replace_development_database_preserve_previous",
            config=config,
            manifest=manifest,
            tools=tools,
            run_func=run_func,
        )
        destination_renamed = True
        _database_action(
            _rename_sql(transient_name, config.destination.database),
            phase_name="replace_development_database_activate",
            config=config,
            manifest=manifest,
            tools=tools,
            run_func=run_func,
        )
        transient_created = False
        transient_promoted = True

        post_verification = _psql_json(
            endpoint=config.destination,
            database=config.destination.database,
            sql=VERIFY_SANITIZATION_SQL,
            read_only=True,
            phase_name="verify_replacement",
            manifest=manifest,
            tools=tools,
            run_func=run_func,
        )
        _validate_sanitization(post_verification)
        replacement_identity = _psql_json(
            endpoint=config.destination,
            database=config.destination.database,
            sql=IDENTITY_SQL,
            read_only=True,
            phase_name="verify_development_marker",
            manifest=manifest,
            tools=tools,
            run_func=run_func,
        )
        if _identity_has_production_marker(replacement_identity):
            raise SyncSafetyError("replacement database retained a production marker")
        if "mooncen.environment=development" not in str(replacement_identity.get("database_comment") or ""):
            raise SyncSafetyError("replacement database is missing the development marker")

        _drop_database(
            previous_name,
            phase_prefix="remove_previous_development_database",
            config=config,
            manifest=manifest,
            tools=tools,
            run_func=run_func,
        )
        destination_renamed = False
        transient_promoted = False
        return {
            "archive": archive_metadata,
            "sanitization": sanitization,
            "post_swap_verification": post_verification,
            "cleanup_completed": True,
        }
    except Exception:
        operation_failed = True
        if destination_renamed:
            try:
                if transient_promoted:
                    _database_action(
                        _terminate_sql(config.destination.database),
                        phase_name="rollback_terminate_failed_replacement",
                        config=config,
                        manifest=manifest,
                        tools=tools,
                        run_func=run_func,
                    )
                    _database_action(
                        _rename_sql(config.destination.database, failed_name),
                        phase_name="rollback_preserve_failed_replacement",
                        config=config,
                        manifest=manifest,
                        tools=tools,
                        run_func=run_func,
                    )
                _database_action(
                    _rename_sql(previous_name, config.destination.database),
                    phase_name="rollback_restore_development_database",
                    config=config,
                    manifest=manifest,
                    tools=tools,
                    run_func=run_func,
                )
                destination_renamed = False
                if transient_promoted:
                    _drop_database(
                        failed_name,
                        phase_prefix="rollback_remove_failed_replacement",
                        config=config,
                        manifest=manifest,
                        tools=tools,
                        run_func=run_func,
                    )
                    transient_promoted = False
            except Exception as rollback_error:
                cleanup_errors.append(type(rollback_error).__name__)
                manifest.document["recovery_required"] = {
                    "destination_database": config.destination.database,
                    "previous_database": previous_name,
                    "failed_replacement_database": failed_name if transient_promoted else None,
                }
                manifest.write()
        if transient_created:
            try:
                _drop_database(
                    transient_name,
                    phase_prefix="cleanup_isolated_database",
                    config=config,
                    manifest=manifest,
                    tools=tools,
                    run_func=run_func,
                )
                transient_created = False
            except Exception as cleanup_error:
                cleanup_errors.append(type(cleanup_error).__name__)
                recovery = manifest.document.setdefault("recovery_required", {})
                recovery["isolated_database"] = transient_name
        if cleanup_errors:
            manifest.document["cleanup_errors"] = cleanup_errors
            manifest.write()
        raise
    finally:
        try:
            archive.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            temporary.cleanup()
        except OSError as cleanup_error:
            cleanup_errors.append(type(cleanup_error).__name__)
        if temporary_path.exists():
            cleanup_errors.append("PrivateTemporaryDirectoryCleanupError")
            recovery = manifest.document.setdefault("recovery_required", {})
            recovery["temporary_directory"] = str(temporary_path)
        if cleanup_errors:
            manifest.document["cleanup_errors"] = cleanup_errors
            manifest.write()
        if temporary_path.exists() and not operation_failed:
            raise SyncSafetyError("private temporary archive cleanup failed")


def synchronize(
    *,
    config: SyncConfig,
    mode: str,
    confirmation: str | None,
    manifest_directory: Path,
    temporary_root: Path | None = None,
    tools: ToolPaths | None = None,
    run_func: RunCallable = subprocess.run,
    run_id: str | None = None,
) -> dict[str, Any]:
    actual_run_id = run_id or _run_id()
    manifest = ManifestWriter(manifest_directory, run_id=actual_run_id, mode=mode, config=config)
    try:
        validate_offline_safety(config)
        if mode == "execute":
            validate_confirmation(config, confirmation)
            manifest.document["safety"]["confirmation_verified"] = True
            manifest.write()
        if mode == "plan":
            manifest.finish("planned")
            return {"status": "planned", "run_id": actual_run_id, "manifest": str(manifest.path)}
        actual_tools = tools or resolve_tools(config)
        source_identity = _psql_json(
            endpoint=config.source,
            database=config.source.database,
            sql=IDENTITY_SQL,
            read_only=True,
            phase_name="source_read_only_identity",
            manifest=manifest,
            tools=actual_tools,
            run_func=run_func,
        )
        destination_identity = _psql_json(
            endpoint=config.destination,
            database=config.destination.database,
            sql=IDENTITY_SQL,
            read_only=True,
            phase_name="destination_identity_guard",
            manifest=manifest,
            tools=actual_tools,
            run_func=run_func,
        )
        validate_live_identities(config, source_identity, destination_identity)
        manifest.document["live_identity"] = {
            "source_server_fingerprint": hashlib.sha256(
                f"{source_identity.get('server_address')}:{source_identity.get('server_port')}".encode()
            ).hexdigest(),
            "destination_server_fingerprint": hashlib.sha256(
                f"{destination_identity.get('server_address')}:{destination_identity.get('server_port')}".encode()
            ).hexdigest(),
            "source_read_only": True,
            "destination_production_marker": False,
        }
        manifest.write()
        if mode == "dry-run":
            manifest.finish("validated")
            return {"status": "validated", "run_id": actual_run_id, "manifest": str(manifest.path)}
        details = _execute_sync(
            config=config,
            run_id=actual_run_id,
            manifest=manifest,
            tools=actual_tools,
            run_func=run_func,
            temporary_root=temporary_root,
        )
        manifest.finish("succeeded", result=details)
        return {"status": "succeeded", "run_id": actual_run_id, "manifest": str(manifest.path)}
    except Exception as exc:
        status = "refused" if isinstance(exc, SyncSafetyError) else "failed"
        if manifest.document.get("recovery_required"):
            status = "recovery_required"
        manifest.finish(
            status,
            failure={"error_type": type(exc).__name__, "safe_message": str(exc)},
        )
        raise SyncRunError(
            status=status,
            manifest_path=manifest.path,
            error_type=type(exc).__name__,
            tool_returncode=exc.returncode if isinstance(exc, ToolExecutionError) else None,
        ) from exc


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Safely replace a development PostgreSQL database with a sanitized production snapshot",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true", help="write an offline, non-connecting plan")
    mode.add_argument("--dry-run", action="store_true", help="run read-only identity guards without dumping")
    mode.add_argument("--execute", action="store_true", help="perform the guarded replacement")
    parser.add_argument(
        "--confirm-development-replace",
        metavar="DATABASE",
        help="exact development database name; required only with --execute",
    )
    parser.add_argument(
        "--manifest-directory",
        type=Path,
        default=Path(".mooncen-runtime") / "production-to-development-manifests",
    )
    parser.add_argument("--temporary-directory", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    mode = "execute" if args.execute else "dry-run" if args.dry_run else "plan"
    try:
        config = load_config(os.environ, mode=mode)
        result = synchronize(
            config=config,
            mode=mode,
            confirmation=args.confirm_development_replace,
            manifest_directory=args.manifest_directory,
            temporary_root=args.temporary_directory,
        )
    except SyncRunError as exc:
        print(
            json.dumps(
                {
                    "status": exc.status,
                    "error_type": exc.error_type,
                    "manifest": str(exc.manifest_path),
                },
                ensure_ascii=False,
            )
        )
        if exc.status == "refused":
            return 2
        if exc.tool_returncode is not None and 1 <= exc.tool_returncode <= 125:
            return exc.tool_returncode
        return 1
    except SyncSafetyError as exc:
        print(json.dumps({"status": "refused", "error_type": type(exc).__name__}))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
