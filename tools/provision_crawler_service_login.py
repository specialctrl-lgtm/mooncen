"""Converge one crawler service LOGIN without exposing its password."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterator
from uuid import UUID

import psycopg2
from psycopg2 import sql
from psycopg2.extras import Json

from tools.ensure_crawler_control_schema import (
    SchemaInstallError,
    _assert_application_owner_access,
    _connection_config,
    _pin_installer_search_path,
)
from tools.preflight_distributed_crawler_control import (
    DATABASE_IDENTIFIER,
    PreflightError,
    _assert_managed_permission_groups_own_nothing,
    _port,
    _protected_environment,
    _required,
)
from tools.postgres_scram_verifier import (
    PasswordVerifierError,
    build_scram_sha_256_verifier,
    validate_service_password,
)


COMPONENT_CONTRACT = {
    "control": (
        "OPS_CRAWLER_CONTROL_DB_USER",
        "OPS_CRAWLER_CONTROL_DB_PASSWORD",
        "mooncen_crawler_control",
    ),
    "publisher": (
        "OPS_CRAWLER_PUBLISHER_DB_USER",
        "OPS_CRAWLER_PUBLISHER_DB_PASSWORD",
        "mooncen_crawler_publisher",
    ),
    "finalizer": (
        "OPS_CRAWLER_FINALIZER_DB_USER",
        "OPS_CRAWLER_FINALIZER_DB_PASSWORD",
        "mooncen_crawler_finalizer",
    ),
    "approver": (
        "OPS_CRAWLER_APPROVER_DB_USER",
        "OPS_CRAWLER_APPROVER_DB_PASSWORD",
        "mooncen_crawler_approver",
    ),
    "release_approver": (
        "OPS_CRAWLER_RELEASE_APPROVER_DB_USER",
        "OPS_CRAWLER_RELEASE_APPROVER_DB_PASSWORD",
        "mooncen_crawler_release_approver",
    ),
    "release_admin": (
        "OPS_CRAWLER_RELEASE_ADMIN_DB_USER",
        "OPS_CRAWLER_RELEASE_ADMIN_DB_PASSWORD",
        "mooncen_crawler_release_admin",
    ),
    "crawler_api": (
        "OPS_CRAWLER_API_DB_USER",
        "OPS_CRAWLER_API_DB_PASSWORD",
        "mooncen_crawler_api",
    ),
    "worker": (
        "OPS_QUEUE_DB_USER",
        "OPS_QUEUE_DB_PASSWORD",
        "mooncen_crawler_worker",
    ),
    "reporter": (
        "OPS_CRAWLER_REPORTER_DB_USER",
        "OPS_CRAWLER_REPORTER_DB_PASSWORD",
        "mooncen_crawler_reporter",
    ),
    "observer": (
        "OPS_CRAWLER_METRICS_DB_USER",
        "OPS_CRAWLER_METRICS_DB_PASSWORD",
        "mooncen_crawler_observer",
    ),
}
RESERVED_ROLES = frozenset(
    {
        "mooncen_api",
        "mooncen_crawler",
        "mooncen_crawler_control",
        "mooncen_crawler_publisher",
        "mooncen_crawler_finalizer",
        "mooncen_crawler_approver",
        "mooncen_crawler_release_approver",
        "mooncen_crawler_release_admin",
        "mooncen_crawler_api",
        "mooncen_crawler_worker",
        "mooncen_crawler_reporter",
        "mooncen_crawler_observer",
        "mooncen_applier",
        "mooncen_ai",
        "mooncen_check",
        "mooncen_readonly",
    }
)
MANAGED_ROLE_MARKER_PREFIX = "mooncen-managed-crawler-login:v1:"
WORKER_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
WORKER_HOSTNAME = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,62})(?:\.[a-z0-9](?:[a-z0-9-]{0,62}))*$"
)
CREDENTIAL_REGISTRY_DIRECTORY = Path("/etc/mooncen")
CREDENTIAL_FINGERPRINT_KEY = CREDENTIAL_REGISTRY_DIRECTORY / "crawler-login-fingerprint.key"
CREDENTIAL_FINGERPRINT_REGISTRY = (
    CREDENTIAL_REGISTRY_DIRECTORY / "crawler-login-password-fingerprints.json"
)
INSTALLER_LOCK_DIRECTORY = Path("/run/mooncen-distributed-crawler-control-install")
REGISTRY_VERSION = 1
ATOMIC_PAIR_COMPONENTS = frozenset({"worker", "reporter"})


class ServiceLoginError(RuntimeError):
    pass


@contextmanager
def _installer_lock() -> Iterator[None]:
    """Join the setup/enrollment flock or acquire the same lock standalone."""

    if os.name != "posix":
        yield
        return
    import fcntl

    try:
        metadata = INSTALLER_LOCK_DIRECTORY.lstat()
    except FileNotFoundError:
        INSTALLER_LOCK_DIRECTORY.mkdir(mode=0o700)
        metadata = INSTALLER_LOCK_DIRECTORY.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ServiceLoginError("installer lock directory is not root-owned mode 0700")

    inherited_raw = os.environ.get("MOONCEN_CRAWLER_INSTALL_LOCK_FD", "")
    if inherited_raw:
        try:
            inherited_fd = int(inherited_raw)
            inherited = os.fstat(inherited_fd)
        except (ValueError, OSError) as exc:
            raise ServiceLoginError("inherited installer lock descriptor is invalid") from exc
        if (inherited.st_dev, inherited.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise ServiceLoginError("inherited installer lock selects another object")
        try:
            fcntl.flock(inherited_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ServiceLoginError("inherited installer lock is not held exclusively") from exc
        # Do not unlock the shared open-file description: the parent shell
        # owns it for the full multi-component setup/enrollment transaction.
        yield
        return

    flags = os.O_RDONLY | os.O_CLOEXEC
    flags |= getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(INSTALLER_LOCK_DIRECTORY, flags)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ServiceLoginError("another crawler installer is running") from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _validate_registry_directory() -> None:
    if os.name != "posix":
        return
    if os.geteuid() != 0:
        raise ServiceLoginError("crawler credential registry must be managed as root")
    try:
        metadata = CREDENTIAL_REGISTRY_DIRECTORY.lstat()
    except OSError as exc:
        raise ServiceLoginError("crawler credential registry directory is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or metadata.st_mode & 0o022
    ):
        raise ServiceLoginError("crawler credential registry directory is unsafe")


def _atomic_write_root_file(path: Path, payload: bytes) -> None:
    _validate_registry_directory()
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=str(path.parent),
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        if os.name == "posix":
            os.fchown(descriptor, 0, 0)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def _read_root_file(path: Path) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ServiceLoginError(f"protected credential registry file is unavailable: {path}") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or (os.name == "posix" and (metadata.st_uid != 0 or metadata.st_gid != 0))
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise ServiceLoginError(f"protected credential registry file is unsafe: {path}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ServiceLoginError(f"protected credential registry file is unreadable: {path}") from exc


def _fingerprint_key() -> bytes:
    _validate_registry_directory()
    if not CREDENTIAL_FINGERPRINT_KEY.exists():
        _atomic_write_root_file(CREDENTIAL_FINGERPRINT_KEY, secrets.token_bytes(32))
    key = _read_root_file(CREDENTIAL_FINGERPRINT_KEY)
    if len(key) != 32:
        raise ServiceLoginError("crawler credential fingerprint key has invalid length")
    return key


def _empty_registry() -> dict[str, Any]:
    return {"version": REGISTRY_VERSION, "entries": {}}


def _load_credential_registry() -> dict[str, Any]:
    if not CREDENTIAL_FINGERPRINT_REGISTRY.exists():
        return _empty_registry()
    try:
        registry = json.loads(_read_root_file(CREDENTIAL_FINGERPRINT_REGISTRY).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ServiceLoginError("crawler credential fingerprint registry is invalid") from exc
    if registry.get("version") != REGISTRY_VERSION or not isinstance(registry.get("entries"), dict):
        raise ServiceLoginError("crawler credential fingerprint registry has an unknown format")
    for login, entry in registry["entries"].items():
        if (
            not isinstance(login, str)
            or not DATABASE_IDENTIFIER.fullmatch(login)
            or not isinstance(entry, dict)
            or entry.get("component") not in COMPONENT_CONTRACT
            or entry.get("state") not in {"active", "pending"}
            or not isinstance(entry.get("fingerprint"), str)
            or re.fullmatch(r"[0-9a-f]{64}", entry["fingerprint"]) is None
        ):
            raise ServiceLoginError("crawler credential fingerprint registry entry is invalid")
    return registry


def _write_credential_registry(registry: dict[str, Any]) -> None:
    payload = json.dumps(
        registry,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    _atomic_write_root_file(CREDENTIAL_FINGERPRINT_REGISTRY, payload)


def _reserve_credential_fingerprint(
    component: str,
    login: str,
    password: str,
) -> tuple[dict[str, Any], str]:
    key = _fingerprint_key()
    fingerprint = hmac.new(
        key,
        b"mooncen-crawler-login-password:v1\0" + password.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    registry = _load_credential_registry()
    previous = deepcopy(registry)
    entries = registry["entries"]
    for other_login, entry in entries.items():
        if entry["state"] == "pending" and (
            other_login != login or entry["fingerprint"] != fingerprint
        ):
            raise ServiceLoginError(
                f"credential registry has an unresolved pending transaction: {other_login}"
            )
        if other_login != login and hmac.compare_digest(entry["fingerprint"], fingerprint):
            raise ServiceLoginError(
                f"{component} password reuses another managed crawler credential"
            )
    current = entries.get(login)
    if current is not None:
        if current["component"] != component:
            raise ServiceLoginError("crawler login is registered to another component")
        if current["state"] == "pending" and not hmac.compare_digest(
            current["fingerprint"], fingerprint
        ):
            raise ServiceLoginError("crawler login has an unresolved password rotation")
    entries[login] = {
        "component": component,
        "fingerprint": fingerprint,
        "state": "pending",
    }
    _write_credential_registry(registry)
    return previous, fingerprint


def _activate_credential_fingerprint(component: str, login: str, fingerprint: str) -> None:
    registry = _load_credential_registry()
    expected = {
        "component": component,
        "fingerprint": fingerprint,
        "state": "pending",
    }
    if registry["entries"].get(login) != expected:
        raise ServiceLoginError("crawler credential registry reservation drifted before commit")
    registry["entries"][login]["state"] = "active"
    _write_credential_registry(registry)


def _managed_login_inventory(cursor: Any) -> dict[str, str]:
    cursor.execute(
        """
        SELECT role.rolname, shobj_description(role.oid, 'pg_authid')
        FROM pg_roles role
        WHERE role.rolcanlogin
          AND shobj_description(role.oid, 'pg_authid') LIKE %s
        ORDER BY role.rolname
        """,
        (f"{MANAGED_ROLE_MARKER_PREFIX}%",),
    )
    inventory: dict[str, str] = {}
    for managed_login, marker in cursor.fetchall():
        component = str(marker)[len(MANAGED_ROLE_MARKER_PREFIX) :]
        if component not in COMPONENT_CONTRACT:
            raise ServiceLoginError(f"managed login has an unknown component: {managed_login}")
        inventory[str(managed_login)] = component
    return inventory


def _verify_managed_login_registry(
    cursor: Any,
    *,
    allow_pending: bool = True,
    pending_login: str | None = None,
) -> None:
    """Refuse an older/untracked managed LOGIN before changing any password."""

    registry = _load_credential_registry()
    inventory = _managed_login_inventory(cursor)
    allowed_states = {"active", "pending"} if allow_pending else {"active"}
    for managed_login, component in inventory.items():
        entry = registry["entries"].get(managed_login)
        if (
            entry is None
            or entry.get("component") != component
            or entry.get("state") not in allowed_states
        ):
            raise ServiceLoginError(
                f"managed login is missing from the protected credential registry: {managed_login}"
            )
    unexpected = sorted(set(registry["entries"]) - set(inventory))
    if pending_login is not None:
        unexpected = [login for login in unexpected if login != pending_login]
    if unexpected:
        raise ServiceLoginError(
            f"credential registry contains a missing database login: {unexpected[0]}"
        )


def _agent_identity(environment: dict[str, str]) -> tuple[str, str, str, str]:
    runtime_environment = _required(environment, "ENVIRONMENT").lower()
    worker_key = _required(environment, "OPS_CRAWLER_WORKER_ID")
    hostname = _required(environment, "OPS_CRAWLER_WORKER_HOSTNAME").lower().rstrip(".")
    agent_text = _required(environment, "OPS_AGENT_ID")
    try:
        agent_id = UUID(agent_text)
    except ValueError as exc:
        raise ServiceLoginError("OPS_AGENT_ID must be a canonical non-nil UUID") from exc
    if (
        runtime_environment not in {"production", "staging"}
        or not WORKER_KEY.fullmatch(worker_key)
        or not WORKER_HOSTNAME.fullmatch(hostname)
        or str(agent_id) != agent_text
        or agent_id.int == 0
    ):
        raise ServiceLoginError("crawler agent id, key, hostname, or environment is invalid")
    return str(agent_id), worker_key, hostname, runtime_environment


def _converge_agent_binding(
    cursor: Any,
    *,
    component: str,
    login: str,
    agent_id: str,
    worker_key: str,
    hostname: str,
    environment: str,
) -> None:
    if component not in {"worker", "reporter"}:
        raise ServiceLoginError("only worker and reporter logins may bind to crawler agents")
    credential_hint = f"crawler-worker:{login}" if component == "worker" else None
    cursor.execute(
        """
        SELECT id::text, environment, hostname, credential_hint, status, maintenance_mode
        FROM ops_agents
        WHERE id = %s::uuid
           OR (environment = %s AND hostname = %s)
           OR (%s::text IS NOT NULL AND credential_hint = %s)
        FOR UPDATE
        """,
        (agent_id, environment, hostname, credential_hint, credential_hint),
    )
    rows = cursor.fetchall()
    if not rows:
        if component != "worker":
            raise ServiceLoginError("reporter requires an already enrolled worker agent")
        cursor.execute(
            """
            INSERT INTO ops_agents (
                id, name, hostname, environment, os_type, status,
                capabilities, credential_hint, maintenance_mode
            )
            VALUES (%s::uuid, %s, %s, %s, 'linux', 'unknown', %s, %s, false)
            """,
            (
                agent_id,
                f"{worker_key} distributed crawler",
                hostname,
                environment,
                Json(["crawler_worker"]),
                credential_hint,
            ),
        )
    else:
        expected_hint = credential_hint if component == "worker" else rows[0][3]
        expected = (agent_id, environment, hostname, expected_hint)
        if (
            len(rows) != 1
            or tuple(rows[0][:4]) != expected
            or rows[0][4] not in {"unknown", "healthy"}
        ):
            raise ServiceLoginError("crawler agent id, hostname, or credential binding collides")
        if rows[0][5] is not False:
            raise ServiceLoginError("crawler agent is in maintenance mode")

    if component == "reporter":
        cursor.execute(
            """
            SELECT binding.database_login,
                   agent.credential_hint = 'crawler-worker:' || binding.database_login
            FROM ops_crawler_agent_bindings binding
            JOIN ops_agents agent
              ON agent.id = binding.agent_id
             AND agent.environment = binding.environment
            WHERE binding.agent_id = %s::uuid
              AND binding.environment = %s
              AND binding.binding_type = 'worker'
            FOR UPDATE OF binding
            """,
            (agent_id, environment),
        )
        worker_bindings = cursor.fetchall()
        if len(worker_bindings) != 1 or worker_bindings[0][1] is not True:
            raise ServiceLoginError("reporter requires one valid worker binding for the same agent")

    cursor.execute(
        """
        SELECT agent_id::text, environment, binding_type, database_login
        FROM ops_crawler_agent_bindings
        WHERE (binding_type = %s AND database_login = %s)
           OR (agent_id = %s::uuid AND binding_type = %s)
        FOR UPDATE
        """,
        (component, login, agent_id, component),
    )
    bindings = cursor.fetchall()
    expected_binding = (agent_id, environment, component, login)
    if not bindings:
        cursor.execute(
            """
            INSERT INTO ops_crawler_agent_bindings (
                agent_id, environment, binding_type, database_login
            )
            VALUES (%s::uuid, %s, %s, %s)
            """,
            expected_binding,
        )
    elif len(bindings) != 1 or tuple(bindings[0]) != expected_binding:
        raise ServiceLoginError("crawler agent database-login binding collides")


def _converge_crawler_api_binding(
    cursor: Any,
    *,
    login: str,
    environment: str,
) -> None:
    cursor.execute(
        """
        SELECT database_login::text, environment
        FROM ops_crawler_api_bindings
        WHERE database_login = %s::name
        FOR UPDATE
        """,
        (login,),
    )
    rows = cursor.fetchall()
    expected = (login, environment)
    if not rows:
        cursor.execute(
            """
            INSERT INTO ops_crawler_api_bindings (database_login, environment)
            VALUES (%s::name, %s)
            """,
            expected,
        )
    elif len(rows) != 1 or tuple(rows[0]) != expected:
        raise ServiceLoginError("crawler API login is bound to a different environment")


def _converge_release_approver_binding(
    cursor: Any,
    *,
    login: str,
    environment: str,
) -> None:
    cursor.execute(
        """
        SELECT database_login::text, environment, enabled
        FROM ops_crawler_release_approver_bindings
        WHERE database_login = %s::name
        FOR UPDATE
        """,
        (login,),
    )
    rows = cursor.fetchall()
    expected = (login, environment, True)
    if not rows:
        cursor.execute(
            """
            INSERT INTO ops_crawler_release_approver_bindings (
                database_login, environment, enabled
            ) VALUES (%s::name, %s, TRUE)
            """,
            (login, environment),
        )
    elif len(rows) != 1 or tuple(rows[0]) != expected:
        raise ServiceLoginError("release approver login is bound to another environment")


def _converge_release_action_consumer_binding(
    cursor: Any,
    *,
    login: str,
    environment: str,
) -> None:
    cursor.execute(
        """
        SELECT database_login::text, environment, consumer_id, last_seen_at
        FROM ops_crawler_release_action_consumers
        WHERE database_login = %s::name OR environment = %s
        FOR UPDATE
        """,
        (login, environment),
    )
    rows = cursor.fetchall()
    if not rows:
        cursor.execute(
            """
            INSERT INTO ops_crawler_release_action_consumers (
                database_login, environment
            ) VALUES (%s::name, %s)
            """,
            (login, environment),
        )
    elif len(rows) != 1 or tuple(rows[0][:2]) != (login, environment):
        raise ServiceLoginError(
            "release action consumer binding collides or is already active"
        )


def _target_contract(component: str, environment: dict[str, str]) -> tuple[str, str, str]:
    user_key, password_key, group = COMPONENT_CONTRACT[component]
    user = _required(environment, user_key)
    password = _required(environment, password_key)
    if not DATABASE_IDENTIFIER.fullmatch(user) or user in RESERVED_ROLES:
        raise ServiceLoginError(f"{component} login name is invalid or reserved")
    try:
        validate_service_password(password, label=f"{component} login")
    except PasswordVerifierError as exc:
        raise ServiceLoginError(str(exc)) from exc
    return user, password, group


def _revoke_direct_privileges(cursor: Any, login: str) -> None:
    identifier = sql.Identifier(login)
    for schema in ("public", "crawl_staging"):
        cursor.execute("SELECT to_regnamespace(%s)", (schema,))
        if cursor.fetchone()[0] is None:
            continue
        schema_identifier = sql.Identifier(schema)
        cursor.execute(sql.SQL("REVOKE ALL PRIVILEGES ON SCHEMA {} FROM {}").format(schema_identifier, identifier))

    # Touch only objects that actually contain a direct ACL for this login.
    # A bulk ALL FUNCTIONS revoke also visits PostGIS extension objects owned
    # by postgres and would make a non-superuser staging owner fail needlessly.
    cursor.execute(
        """
        SELECT format(
            'REVOKE ALL PRIVILEGES ON %s %I.%I FROM %I',
            CASE WHEN relation.relkind = 'S' THEN 'SEQUENCE' ELSE 'TABLE' END,
            namespace.nspname,
            relation.relname,
            %s
        )
        FROM pg_class relation
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        CROSS JOIN LATERAL aclexplode(relation.relacl) privilege
        JOIN pg_roles grantee ON grantee.oid = privilege.grantee
        WHERE namespace.nspname IN ('public', 'crawl_staging')
          AND relation.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
          AND grantee.rolname = %s
        GROUP BY relation.relkind, namespace.nspname, relation.relname
        """,
        (login, login),
    )
    commands = [row[0] for row in cursor.fetchall()]
    cursor.execute(
        """
        SELECT format(
            'REVOKE ALL PRIVILEGES (%I) ON TABLE %I.%I FROM %I',
            attribute.attname,
            namespace.nspname,
            relation.relname,
            %s
        )
        FROM pg_attribute attribute
        JOIN pg_class relation ON relation.oid = attribute.attrelid
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        CROSS JOIN LATERAL aclexplode(attribute.attacl) privilege
        JOIN pg_roles grantee ON grantee.oid = privilege.grantee
        WHERE namespace.nspname IN ('public', 'crawl_staging')
          AND attribute.attnum > 0
          AND NOT attribute.attisdropped
          AND grantee.rolname = %s
        GROUP BY attribute.attname, namespace.nspname, relation.relname
        """,
        (login, login),
    )
    commands.extend(row[0] for row in cursor.fetchall())
    cursor.execute(
        """
        SELECT format(
            'REVOKE ALL PRIVILEGES ON %s %I.%I(%s) FROM %I',
            CASE WHEN procedure.prokind = 'p' THEN 'PROCEDURE' ELSE 'FUNCTION' END,
            namespace.nspname,
            procedure.proname,
            pg_get_function_identity_arguments(procedure.oid),
            %s
        )
        FROM pg_proc procedure
        JOIN pg_namespace namespace ON namespace.oid = procedure.pronamespace
        CROSS JOIN LATERAL aclexplode(procedure.proacl) privilege
        JOIN pg_roles grantee ON grantee.oid = privilege.grantee
        WHERE namespace.nspname IN ('public', 'crawl_staging')
          AND grantee.rolname = %s
        GROUP BY procedure.oid, procedure.prokind, namespace.nspname, procedure.proname
        """,
        (login, login),
    )
    commands.extend(row[0] for row in cursor.fetchall())
    for command in commands:
        cursor.execute(command)


def _has_direct_application_acl(cursor: Any, login: str, database: str) -> bool:
    cursor.execute(
        """
        WITH target AS (
            SELECT oid FROM pg_roles WHERE rolname = %s
        ), direct_acl AS (
            SELECT privilege.grantee
            FROM pg_database object
            CROSS JOIN LATERAL aclexplode(object.datacl) privilege
            WHERE object.datname = %s
            UNION ALL
            SELECT privilege.grantee
            FROM pg_namespace object
            CROSS JOIN LATERAL aclexplode(object.nspacl) privilege
            WHERE object.nspname IN ('public', 'crawl_staging')
            UNION ALL
            SELECT privilege.grantee
            FROM pg_class object
            JOIN pg_namespace namespace ON namespace.oid = object.relnamespace
            CROSS JOIN LATERAL aclexplode(object.relacl) privilege
            WHERE namespace.nspname IN ('public', 'crawl_staging')
            UNION ALL
            SELECT privilege.grantee
            FROM pg_attribute object
            JOIN pg_class relation ON relation.oid = object.attrelid
            JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
            CROSS JOIN LATERAL aclexplode(object.attacl) privilege
            WHERE namespace.nspname IN ('public', 'crawl_staging')
            UNION ALL
            SELECT privilege.grantee
            FROM pg_proc object
            JOIN pg_namespace namespace ON namespace.oid = object.pronamespace
            CROSS JOIN LATERAL aclexplode(object.proacl) privilege
            WHERE namespace.nspname IN ('public', 'crawl_staging')
        )
        SELECT EXISTS (
            SELECT 1
            FROM direct_acl privilege
            JOIN target ON target.oid = privilege.grantee
        )
        """,
        (login, database),
    )
    return cursor.fetchone()[0] is True


def _provision_service_login_locked(
    schema_environment_file: Path,
    service_environment_file: Path,
    *,
    component: str,
    confirmed_database: str,
) -> dict[str, str]:
    if component in ATOMIC_PAIR_COMPONENTS:
        raise ServiceLoginError(
            "NOT READY: worker and reporter credentials require one atomic pair "
            "transaction with active-rotation fencing"
        )
    try:
        schema_environment = _protected_environment(schema_environment_file, owner_only=True)
        service_environment = _protected_environment(service_environment_file, owner_only=True)
    except PreflightError as exc:
        raise ServiceLoginError(str(exc)) from exc
    admin_config = _connection_config(schema_environment)
    object_owner = _required(schema_environment, "OPS_CRAWLER_SCHEMA_OBJECT_OWNER")
    if admin_config["database"] != confirmed_database:
        raise ServiceLoginError("confirmed staging database differs from the schema administrator target")
    service_endpoint = (
        _required(service_environment, "OPS_CRAWLER_SHARED_DB_HOST"),
        _port(service_environment, "OPS_CRAWLER_SHARED_DB_PORT"),
        _required(service_environment, "OPS_CRAWLER_SHARED_DB_NAME"),
    )
    admin_endpoint = (admin_config["host"], admin_config["port"], admin_config["database"])
    if service_endpoint != admin_endpoint or service_endpoint[2] != confirmed_database:
        raise ServiceLoginError("service login and schema administrator must use one exact staging endpoint")
    if component == "worker":
        queue_endpoint = (
            _required(service_environment, "OPS_QUEUE_DB_HOST"),
            _port(service_environment, "OPS_QUEUE_DB_PORT"),
            _required(service_environment, "OPS_QUEUE_DB_NAME"),
        )
        staging_endpoint = (
            _required(service_environment, "CRAWL_STAGING_DB_HOST"),
            _port(service_environment, "CRAWL_STAGING_DB_PORT"),
            _required(service_environment, "CRAWL_STAGING_DB_NAME"),
        )
        if queue_endpoint != service_endpoint or staging_endpoint != service_endpoint:
            raise ServiceLoginError("worker queue, staging, and shared endpoints must be identical")
        if (
            _required(service_environment, "OPS_QUEUE_DB_USER")
            != _required(service_environment, "CRAWL_STAGING_DB_USER")
            or _required(service_environment, "OPS_QUEUE_DB_PASSWORD")
            != _required(service_environment, "CRAWL_STAGING_DB_PASSWORD")
        ):
            raise ServiceLoginError("worker queue and staging writes must use one credential")
        if service_environment.get("CRAWL_WRITE_MODE", "").strip().lower() != "staging":
            raise ServiceLoginError("worker provisioning requires CRAWL_WRITE_MODE=staging")
    login, password, permission_group = _target_contract(component, service_environment)
    connection_limit = (
        4 if component in {"observer", "crawler_api", "release_approver"} else 32
    )
    agent_identity = (
        _agent_identity(service_environment) if component in {"worker", "reporter"} else None
    )
    crawler_api_environment = None
    if component == "crawler_api":
        crawler_api_environment = _required(service_environment, "ENVIRONMENT").lower()
        if crawler_api_environment not in {"production", "staging"}:
            raise ServiceLoginError("crawler API ENVIRONMENT must be production or staging")
    release_approver_environment = None
    if component == "release_approver":
        release_approver_environment = _required(service_environment, "ENVIRONMENT").lower()
        if release_approver_environment not in {"production", "staging"}:
            raise ServiceLoginError(
                "release approver ENVIRONMENT must be production or staging"
            )
    release_admin_environment = None
    if component == "release_admin":
        release_admin_environment = _required(service_environment, "ENVIRONMENT").lower()
        if release_admin_environment not in {"production", "staging"}:
            raise ServiceLoginError(
                "release admin ENVIRONMENT must be production or staging"
            )
    if login == admin_config["user"]:
        raise ServiceLoginError("runtime service login cannot be the schema administrator")
    if password == admin_config["password"]:
        raise ServiceLoginError("runtime service password cannot reuse the schema administrator password")
    password_verifier = build_scram_sha_256_verifier(password)

    try:
        connection = psycopg2.connect(**admin_config)
    except Exception as exc:
        raise ServiceLoginError("cannot connect with the schema administrator") from exc
    registry_snapshot: dict[str, Any] | None = None
    credential_fingerprint: str | None = None
    commit_attempted = False
    try:
        _pin_installer_search_path(connection)
        _assert_application_owner_access(connection, object_owner)
        with connection.cursor() as ownership_cursor:
            _assert_managed_permission_groups_own_nothing(ownership_cursor)
        connection.rollback()
        registry_snapshot, credential_fingerprint = _reserve_credential_fingerprint(
            component,
            login,
            password,
        )
        with connection.cursor() as cursor:
            _verify_managed_login_registry(cursor, pending_login=login)
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))",
                ("mooncen.crawler.service_login", login),
            )
            cursor.execute(
                """
                SELECT NOT rolcanlogin AND NOT rolsuper AND NOT rolcreaterole
                       AND NOT rolcreatedb AND NOT rolreplication AND NOT rolbypassrls
                FROM pg_roles
                WHERE rolname = %s
                """,
                (permission_group,),
            )
            row = cursor.fetchone()
            if not row or row[0] is not True:
                raise ServiceLoginError(f"permission group is missing or unsafe: {permission_group}")
            if component == "release_approver":
                cursor.execute(
                    sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(object_owner))
                )
            cursor.execute(
                """
                SELECT member.rolname,
                       member.rolcanlogin
                           AND member.rolinherit
                           AND NOT member.rolsuper
                           AND NOT member.rolcreaterole
                           AND NOT member.rolcreatedb
                           AND NOT member.rolreplication
                           AND NOT member.rolbypassrls
                           AND member.rolconnlimit = %s
                           AND member.rolconfig IS NULL
                           AND NOT EXISTS (
                               SELECT 1 FROM pg_auth_members child_edge
                               WHERE child_edge.roleid = member.oid
                           ),
                       shobj_description(member.oid, 'pg_authid'),
                       membership.admin_option,
                       COALESCE((to_jsonb(membership)->>'inherit_option')::boolean, true),
                       COALESCE((to_jsonb(membership)->>'set_option')::boolean, true),
                       CASE
                           WHEN %s = 'worker' THEN EXISTS (
                               SELECT 1
                               FROM ops_crawler_agent_bindings binding
                               JOIN ops_agents agent
                                 ON agent.id = binding.agent_id
                                AND agent.environment = binding.environment
                               WHERE binding.binding_type = 'worker'
                                 AND binding.database_login = member.rolname
                                 AND agent.credential_hint = 'crawler-worker:' || member.rolname
                           )
                           WHEN %s = 'reporter' THEN EXISTS (
                               SELECT 1
                               FROM ops_crawler_agent_bindings binding
                               JOIN ops_agents agent
                                 ON agent.id = binding.agent_id
                                AND agent.environment = binding.environment
                               JOIN ops_crawler_agent_bindings worker_binding
                                 ON worker_binding.agent_id = binding.agent_id
                                AND worker_binding.environment = binding.environment
                                AND worker_binding.binding_type = 'worker'
                               WHERE binding.binding_type = 'reporter'
                                 AND binding.database_login = member.rolname
                                 AND agent.credential_hint =
                                     'crawler-worker:' || worker_binding.database_login
                           )
                            WHEN %s = 'crawler_api' THEN EXISTS (
                               SELECT 1
                               FROM ops_crawler_api_bindings api_binding
                               WHERE api_binding.database_login = member.rolname::name
                                 AND api_binding.environment IN (
                                     'production', 'staging', 'development'
                                  )
                            )
                            WHEN %s = 'release_approver' THEN EXISTS (
                                SELECT 1
                                FROM ops_crawler_release_approver_bindings binding
                                WHERE binding.database_login = member.rolname::name
                                  AND binding.environment IN (
                                      'production', 'staging', 'development'
                                  )
                                  AND binding.enabled IS TRUE
                            )
                            ELSE true
                       END
                FROM pg_auth_members membership
                JOIN pg_roles parent ON parent.oid = membership.roleid
                JOIN pg_roles member ON member.oid = membership.member
                WHERE parent.rolname = %s
                ORDER BY member.rolname
                """,
                (
                    connection_limit,
                    component,
                    component,
                    component,
                    component,
                    permission_group,
                ),
            )
            existing_members = cursor.fetchall()
            if component == "release_approver":
                cursor.execute("RESET ROLE")
            expected_marker = f"{MANAGED_ROLE_MARKER_PREFIX}{component}"
            if any(
                safe is not True
                or marker != expected_marker
                or admin_option is not False
                or inherit_option is not True
                or set_option is not True
                or (member_name != login and binding_valid is not True)
                for (
                    member_name, safe, marker, admin_option, inherit_option,
                    set_option, binding_valid,
                ) in existing_members
            ):
                raise ServiceLoginError(
                    f"permission group has an unmanaged or unsafe member: {permission_group}"
                )
            if component not in {"worker", "reporter", "crawler_api"} and any(
                member != login for member, *_ in existing_members
            ):
                raise ServiceLoginError(
                    f"permission group has an unexpected managed member: {permission_group}"
                )

            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (login,))
            if cursor.fetchone() is None:
                cursor.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(login)))
                cursor.execute(
                    sql.SQL("COMMENT ON ROLE {} IS %s").format(sql.Identifier(login)),
                    (f"{MANAGED_ROLE_MARKER_PREFIX}{component}",),
                )
            else:
                cursor.execute(
                    "SELECT shobj_description(oid, 'pg_authid') FROM pg_roles WHERE rolname = %s",
                    (login,),
                )
                marker = cursor.fetchone()[0]
                if marker != f"{MANAGED_ROLE_MARKER_PREFIX}{component}":
                    raise ServiceLoginError(
                        f"existing {component} login is unmarked or managed by another component"
                    )
                cursor.execute(
                    """
                    SELECT EXISTS (SELECT 1 FROM pg_database WHERE datdba = %s::regrole)
                        OR EXISTS (SELECT 1 FROM pg_namespace WHERE nspowner = %s::regrole)
                        OR EXISTS (SELECT 1 FROM pg_class WHERE relowner = %s::regrole)
                        OR EXISTS (SELECT 1 FROM pg_proc WHERE proowner = %s::regrole)
                        OR EXISTS (SELECT 1 FROM pg_type WHERE typowner = %s::regrole)
                        OR EXISTS (SELECT 1 FROM pg_extension WHERE extowner = %s::regrole)
                    """,
                    (login, login, login, login, login, login),
                )
                if cursor.fetchone()[0] is True:
                    raise ServiceLoginError(f"{component} login owns database objects")
            cursor.execute(
                sql.SQL(
                    "ALTER ROLE {} WITH LOGIN INHERIT NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT {} PASSWORD %s"
                ).format(sql.Identifier(login), sql.Literal(connection_limit)),
                (password_verifier,),
            )
            cursor.execute(sql.SQL("ALTER ROLE {} RESET ALL").format(sql.Identifier(login)))
            cursor.execute(
                sql.SQL("ALTER ROLE {} IN DATABASE {} RESET ALL").format(
                    sql.Identifier(login),
                    sql.Identifier(confirmed_database),
                )
            )
            if component == "observer":
                for setting, value in (
                    ("default_transaction_read_only", "on"),
                    ("statement_timeout", "5s"),
                    ("lock_timeout", "1s"),
                    ("idle_in_transaction_session_timeout", "10s"),
                ):
                    cursor.execute(
                        sql.SQL("ALTER ROLE {} IN DATABASE {} SET {} = %s").format(
                            sql.Identifier(login),
                            sql.Identifier(confirmed_database),
                            sql.Identifier(setting),
                        ),
                        (value,),
                    )
            cursor.execute(
                """
                SELECT parent.rolname
                FROM pg_auth_members membership
                JOIN pg_roles parent ON parent.oid = membership.roleid
                JOIN pg_roles member ON member.oid = membership.member
                WHERE member.rolname = %s
                """,
                (login,),
            )
            for (parent,) in cursor.fetchall():
                cursor.execute(
                    sql.SQL("REVOKE {} FROM {}").format(sql.Identifier(parent), sql.Identifier(login))
                )
            cursor.execute(
                """
                SELECT member.rolname
                FROM pg_auth_members membership
                JOIN pg_roles parent ON parent.oid = membership.roleid
                JOIN pg_roles member ON member.oid = membership.member
                WHERE parent.rolname = %s
                """,
                (login,),
            )
            for (member,) in cursor.fetchall():
                cursor.execute(
                    sql.SQL("REVOKE {} FROM {}").format(sql.Identifier(login), sql.Identifier(member))
                )
            _revoke_direct_privileges(cursor, login)
            cursor.execute(
                sql.SQL("REVOKE ALL PRIVILEGES ON DATABASE {} FROM {}").format(
                    sql.Identifier(confirmed_database),
                    sql.Identifier(login),
                )
            )
            cursor.execute(
                sql.SQL("GRANT {} TO {}").format(sql.Identifier(permission_group), sql.Identifier(login))
            )
            if agent_identity is not None:
                _converge_agent_binding(
                    cursor,
                    component=component,
                    login=login,
                    agent_id=agent_identity[0],
                    worker_key=agent_identity[1],
                    hostname=agent_identity[2],
                    environment=agent_identity[3],
                )
            if crawler_api_environment is not None:
                _converge_crawler_api_binding(
                    cursor,
                    login=login,
                    environment=crawler_api_environment,
                )
            if release_approver_environment is not None:
                cursor.execute(
                    sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(object_owner))
                )
                cursor.execute(
                    sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(object_owner))
                )
                _converge_release_approver_binding(
                    cursor,
                    login=login,
                    environment=release_approver_environment,
                )
                cursor.execute("RESET ROLE")
            if release_admin_environment is not None:
                cursor.execute(
                    sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(object_owner))
                )
                _converge_release_action_consumer_binding(
                    cursor,
                    login=login,
                    environment=release_admin_environment,
                )
                cursor.execute("RESET ROLE")
            if _has_direct_application_acl(cursor, login, confirmed_database):
                raise ServiceLoginError(f"{component} login retains a direct application ACL")
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT role.rolcanlogin
                       AND NOT role.rolsuper
                       AND NOT role.rolcreaterole
                       AND NOT role.rolcreatedb
                       AND NOT role.rolreplication
                       AND NOT role.rolbypassrls
                       AND role.rolconnlimit = %s
                       AND role.rolconfig IS NULL,
                       array_agg(parent.rolname ORDER BY parent.rolname)
                           FILTER (WHERE parent.rolname IS NOT NULL),
                       bool_or(membership.admin_option)
                           FILTER (WHERE parent.rolname IS NOT NULL),
                       bool_and(
                           COALESCE((to_jsonb(membership)->>'inherit_option')::boolean, true)
                       ) FILTER (WHERE parent.rolname IS NOT NULL),
                       bool_and(
                           COALESCE((to_jsonb(membership)->>'set_option')::boolean, true)
                       ) FILTER (WHERE parent.rolname IS NOT NULL),
                       NOT EXISTS (
                           SELECT 1
                           FROM pg_auth_members child_membership
                           WHERE child_membership.roleid = role.oid
                       )
                FROM pg_roles role
                LEFT JOIN pg_auth_members membership ON membership.member = role.oid
                LEFT JOIN pg_roles parent ON parent.oid = membership.roleid
                WHERE role.rolname = %s
                GROUP BY role.oid
                """,
                (connection_limit, login),
            )
            verified = cursor.fetchone()
            if (
                not verified
                or verified[0] is not True
                or verified[1] != [permission_group]
                or verified[2] is not False
                or verified[3] is not True
                or verified[4] is not True
                or verified[5] is not True
            ):
                raise ServiceLoginError(f"{component} login convergence verification failed")
            cursor.execute(
                """
                SELECT COALESCE(array_agg(config ORDER BY config), ARRAY[]::text[])
                FROM pg_db_role_setting setting
                JOIN pg_database database ON database.oid = setting.setdatabase
                CROSS JOIN LATERAL unnest(setting.setconfig) config
                WHERE setting.setrole = %s::regrole
                  AND database.datname = %s
                """,
                (login, confirmed_database),
            )
            database_settings = list(cursor.fetchone()[0])
            expected_settings = (
                [
                    "default_transaction_read_only=on",
                    "idle_in_transaction_session_timeout=10s",
                    "lock_timeout=1s",
                    "statement_timeout=5s",
                ]
                if component == "observer"
                else []
            )
            if database_settings != expected_settings:
                raise ServiceLoginError(f"{component} login database settings did not converge")
            if crawler_api_environment is not None:
                cursor.execute(
                    """
                    SELECT count(*) = 1
                           AND bool_and(environment = %s)
                    FROM ops_crawler_api_bindings
                    WHERE database_login = %s::name
                    """,
                    (crawler_api_environment, login),
                )
                if cursor.fetchone()[0] is not True:
                    raise ServiceLoginError("crawler API environment binding did not converge")
            if release_approver_environment is not None:
                cursor.execute(
                    """
                    SELECT count(*) = 1
                           AND bool_and(environment = %s AND enabled IS TRUE)
                    FROM ops_crawler_release_approver_bindings
                    WHERE database_login = %s::name
                    """,
                    (release_approver_environment, login),
                )
                if cursor.fetchone()[0] is not True:
                    raise ServiceLoginError(
                        "release approver environment binding did not converge"
                    )
                cursor.execute("RESET ROLE")
            if release_admin_environment is not None:
                cursor.execute(
                    sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(object_owner))
                )
                cursor.execute(
                    """
                    SELECT count(*) = 1 AND bool_and(environment = %s)
                    FROM ops_crawler_release_action_consumers
                    WHERE database_login = %s::name
                    """,
                    (release_admin_environment, login),
                )
                if cursor.fetchone()[0] is not True:
                    raise ServiceLoginError(
                        "release action consumer environment binding did not converge"
                    )
                cursor.execute("RESET ROLE")
        commit_attempted = True
        connection.commit()
        _activate_credential_fingerprint(component, login, credential_fingerprint)
    except Exception:
        connection.rollback()
        if registry_snapshot is not None and not commit_attempted:
            try:
                _write_credential_registry(registry_snapshot)
            except Exception:
                # The pending entry is deliberately safer than claiming the
                # old/new password is active after an uncertain filesystem
                # rollback. A reviewed rerun must reconcile it.
                pass
        raise
    finally:
        connection.close()
    return {"status": "ok", "component": component, "database": confirmed_database, "role": login}


def provision_service_login(
    schema_environment_file: Path,
    service_environment_file: Path,
    *,
    component: str,
    confirmed_database: str,
) -> dict[str, str]:
    if component in ATOMIC_PAIR_COMPONENTS:
        raise ServiceLoginError(
            "NOT READY: worker and reporter credentials require one atomic pair "
            "transaction with active-rotation fencing"
        )
    with _installer_lock():
        return _provision_service_login_locked(
            schema_environment_file,
            service_environment_file,
            component=component,
            confirmed_database=confirmed_database,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Provision one crawler service database login")
    parser.add_argument("--schema-env", required=True, type=Path)
    parser.add_argument("--service-env", required=True, type=Path)
    parser.add_argument("--component", required=True, choices=sorted(COMPONENT_CONTRACT))
    parser.add_argument("--confirm-staging-database", required=True)
    args = parser.parse_args(argv)
    try:
        result = provision_service_login(
            args.schema_env,
            args.service_env,
            component=args.component,
            confirmed_database=args.confirm_staging_database,
        )
    except (SchemaInstallError, ServiceLoginError, PreflightError) as exc:
        parser.exit(78, f"crawler service login provisioning failed: {exc}\n")
    except psycopg2.Error:
        parser.exit(70, "crawler service login provisioning failed: PostgreSQL operation failed\n")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
