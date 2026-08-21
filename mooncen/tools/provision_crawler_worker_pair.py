"""Atomically enroll one distributed crawler worker/reporter credential pair.

This is a dormant, root-operated database interface.  It deliberately is not
called by the worker enrollment shell until that shell is installed from an
independently authenticated worker bootstrap.  The database portion uses one
transaction for both LOGINs and both server-side agent bindings.  Passwords
are never written to stdout; the only output containing them is a root-owned,
mode-0600, one-time envelope activated after the database commit.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import stat
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2 import sql

from tools.ensure_crawler_control_schema import (
    SchemaInstallError,
    _assert_application_owner_access,
    _connection_config,
    _pin_installer_search_path,
)
from tools.preflight_distributed_crawler_control import (
    PreflightError,
    _assert_managed_permission_groups_own_nothing,
    _port,
    _protected_environment,
    _required,
)
from tools.postgres_scram_verifier import build_scram_sha_256_verifier
from tools.provision_crawler_service_login import (
    COMPONENT_CONTRACT,
    MANAGED_ROLE_MARKER_PREFIX,
    RESERVED_ROLES,
    ServiceLoginError,
    _agent_identity,
    _converge_agent_binding,
    _fingerprint_key,
    _has_direct_application_acl,
    _installer_lock,
    _load_credential_registry,
    _managed_login_inventory,
    _revoke_direct_privileges,
    _target_contract,
    _write_credential_registry,
)


ENVELOPE_FORMAT = "mooncen-crawler-worker-pair-envelope-v1"
PAIR_LOCK_NAMESPACE = "mooncen.crawler.worker_reporter_pair"
RECENT_HEARTBEAT_SECONDS = 600
PAIR_COMPONENTS = ("worker", "reporter")
REQUIRED_RLS_TABLES = frozenset(
    {
        "crawl_progress",
        "crawler_run_log",
        "ops_agents",
        "ops_crawler_release_reports",
        "ops_crawler_runs",
        "ops_crawler_task_attempts",
        "ops_crawler_task_observations",
        "ops_crawler_worker_desired_state",
        "ops_job_logs",
        "ops_jobs",
    }
)
REQUIRED_RLS_POLICIES = frozenset(
    {
        ("ops_agents", "crawler_worker_agent_scope"),
        ("ops_crawler_release_reports", "crawler_worker_release_report_scope"),
        ("ops_crawler_task_attempts", "crawler_worker_attempt_scope"),
        ("ops_crawler_task_observations", "crawler_worker_observation_scope"),
        ("ops_crawler_worker_desired_state", "crawler_runtime_desired_state_scope"),
        ("ops_jobs", "crawler_worker_job_scope"),
    }
)


class WorkerPairEnrollmentError(RuntimeError):
    """Raised when paired enrollment cannot safely converge."""


@dataclass(frozen=True)
class PairContract:
    database: str
    endpoint: tuple[str, int, str]
    agent_id: str
    worker_key: str
    hostname: str
    environment: str
    worker_login: str
    worker_password: str
    reporter_login: str
    reporter_password: str


def _pair_contract(
    schema_environment: dict[str, str],
    worker_environment: dict[str, str],
    reporter_environment: dict[str, str],
    *,
    confirmed_database: str,
) -> tuple[PairContract, dict[str, Any], str]:
    admin_config = _connection_config(schema_environment)
    object_owner = _required(schema_environment, "OPS_CRAWLER_SCHEMA_OBJECT_OWNER")
    if admin_config["database"] != confirmed_database:
        raise WorkerPairEnrollmentError(
            "confirmed staging database differs from the schema administrator target"
        )
    admin_endpoint = (
        admin_config["host"],
        admin_config["port"],
        admin_config["database"],
    )

    def shared_endpoint(environment: dict[str, str]) -> tuple[str, int, str]:
        return (
            _required(environment, "OPS_CRAWLER_SHARED_DB_HOST"),
            _port(environment, "OPS_CRAWLER_SHARED_DB_PORT"),
            _required(environment, "OPS_CRAWLER_SHARED_DB_NAME"),
        )

    worker_endpoint = shared_endpoint(worker_environment)
    reporter_endpoint = shared_endpoint(reporter_environment)
    queue_endpoint = (
        _required(worker_environment, "OPS_QUEUE_DB_HOST"),
        _port(worker_environment, "OPS_QUEUE_DB_PORT"),
        _required(worker_environment, "OPS_QUEUE_DB_NAME"),
    )
    staging_endpoint = (
        _required(worker_environment, "CRAWL_STAGING_DB_HOST"),
        _port(worker_environment, "CRAWL_STAGING_DB_PORT"),
        _required(worker_environment, "CRAWL_STAGING_DB_NAME"),
    )
    if not (
        admin_endpoint
        == worker_endpoint
        == reporter_endpoint
        == queue_endpoint
        == staging_endpoint
    ):
        raise WorkerPairEnrollmentError(
            "schema, worker queue/staging, and reporter endpoints must match exactly"
        )
    if worker_environment.get("CRAWL_WRITE_MODE", "").strip() != "staging":
        raise WorkerPairEnrollmentError("worker enrollment requires CRAWL_WRITE_MODE=staging")

    worker_identity = _agent_identity(worker_environment)
    reporter_identity = _agent_identity(reporter_environment)
    if worker_identity != reporter_identity:
        raise WorkerPairEnrollmentError(
            "worker and reporter must bind the same UUID, worker key, hostname, and environment"
        )
    agent_id, worker_key, hostname, runtime_environment = worker_identity
    for source in (worker_environment, reporter_environment):
        if (
            source.get("OPS_AGENT_ID") != agent_id
            or source.get("OPS_CRAWLER_WORKER_ID") != worker_key
            or source.get("OPS_CRAWLER_WORKER_HOSTNAME") != hostname
            or source.get("ENVIRONMENT") != runtime_environment
        ):
            raise WorkerPairEnrollmentError(
                "agent UUID must be canonical and hostname/environment must be exact lowercase values"
            )

    worker_login, worker_password, _ = _target_contract("worker", worker_environment)
    reporter_login, reporter_password, _ = _target_contract(
        "reporter", reporter_environment
    )
    if (
        _required(worker_environment, "CRAWL_STAGING_DB_USER") != worker_login
        or _required(worker_environment, "CRAWL_STAGING_DB_PASSWORD") != worker_password
    ):
        raise WorkerPairEnrollmentError(
            "worker queue and staging settings must use the same credential"
        )
    if worker_login == reporter_login or hmac.compare_digest(
        worker_password, reporter_password
    ):
        raise WorkerPairEnrollmentError("worker and reporter credentials must be distinct")
    if worker_login == admin_config["user"] or reporter_login == admin_config["user"]:
        raise WorkerPairEnrollmentError("runtime LOGINs must differ from the schema administrator")
    if any(
        hmac.compare_digest(password, admin_config["password"])
        for password in (worker_password, reporter_password)
    ):
        raise WorkerPairEnrollmentError(
            "runtime passwords must differ from the schema administrator password"
        )
    for environment in (worker_environment, reporter_environment):
        sslmode = environment.get("DB_SSLMODE", "").strip().lower()
        if sslmode != "verify-full":
            raise WorkerPairEnrollmentError("paired enrollment requires verify-full TLS")

    return (
        PairContract(
            database=confirmed_database,
            endpoint=admin_endpoint,
            agent_id=agent_id,
            worker_key=worker_key,
            hostname=hostname,
            environment=runtime_environment,
            worker_login=worker_login,
            worker_password=worker_password,
            reporter_login=reporter_login,
            reporter_password=reporter_password,
        ),
        admin_config,
        object_owner,
    )


def _credential_fingerprint(key: bytes, password: str) -> str:
    return hmac.new(
        key,
        b"mooncen-crawler-login-password:v1\0" + password.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


def _reserve_pair_registry(contract: PairContract) -> tuple[dict[str, Any], dict[str, str]]:
    key = _fingerprint_key()
    fingerprints = {
        "worker": _credential_fingerprint(key, contract.worker_password),
        "reporter": _credential_fingerprint(key, contract.reporter_password),
    }
    if hmac.compare_digest(fingerprints["worker"], fingerprints["reporter"]):
        raise WorkerPairEnrollmentError("worker and reporter password fingerprints collide")
    registry = _load_credential_registry()
    previous = deepcopy(registry)
    entries = registry["entries"]
    pair_logins = {contract.worker_login, contract.reporter_login}
    present_pair_logins = pair_logins.intersection(entries)
    if present_pair_logins and present_pair_logins != pair_logins:
        raise WorkerPairEnrollmentError(
            "credential registry contains only one member of the worker/reporter pair"
        )
    for other_login, entry in entries.items():
        if entry["state"] == "pending" and other_login not in pair_logins:
            raise WorkerPairEnrollmentError(
                f"credential registry has an unresolved pending transaction: {other_login}"
            )
        for component, fingerprint in fingerprints.items():
            target_login = getattr(contract, f"{component}_login")
            if other_login != target_login and hmac.compare_digest(
                entry["fingerprint"], fingerprint
            ):
                raise WorkerPairEnrollmentError(
                    f"{component} password reuses another managed crawler credential"
                )
    for component in PAIR_COMPONENTS:
        login = getattr(contract, f"{component}_login")
        current = entries.get(login)
        if current is not None and current.get("component") != component:
            raise WorkerPairEnrollmentError(
                f"{component} LOGIN is registered to another component"
            )
        if (
            current is not None
            and current.get("state") == "pending"
            and not hmac.compare_digest(
                str(current.get("fingerprint", "")), fingerprints[component]
            )
        ):
            raise WorkerPairEnrollmentError(
                "credential registry has an unresolved paired password rotation"
            )
        entries[login] = {
            "component": component,
            "fingerprint": fingerprints[component],
            "state": "pending",
        }
    # Both reservations are one canonical, atomic filesystem replacement.
    _write_credential_registry(registry)
    return previous, fingerprints


def _activate_pair_registry(contract: PairContract, fingerprints: dict[str, str]) -> None:
    registry = _load_credential_registry()
    for component in PAIR_COMPONENTS:
        login = getattr(contract, f"{component}_login")
        expected = {
            "component": component,
            "fingerprint": fingerprints[component],
            "state": "pending",
        }
        if registry["entries"].get(login) != expected:
            raise WorkerPairEnrollmentError(
                "paired credential registry reservation drifted before activation"
            )
    for component in PAIR_COMPONENTS:
        login = getattr(contract, f"{component}_login")
        registry["entries"][login]["state"] = "active"
    # Never expose a state where only one member of the pair is active.
    _write_credential_registry(registry)


def _validate_envelope_target(path: Path) -> None:
    if not path.is_absolute() or path.exists() or path.is_symlink():
        raise WorkerPairEnrollmentError(
            "one-time secret envelope must be a new absolute non-symlink path"
        )
    try:
        metadata = path.parent.lstat()
    except OSError as exc:
        raise WorkerPairEnrollmentError("secret envelope directory is unavailable") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or (os.name == "posix" and (metadata.st_uid != 0 or metadata.st_gid != 0))
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise WorkerPairEnrollmentError(
            "secret envelope directory must be root-owned mode 0700"
        )


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short write while staging credential envelope")
        view = view[written:]


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise WorkerPairEnrollmentError("secret envelope parent is not a directory")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _stage_secret_envelope(path: Path, contract: PairContract) -> Path:
    _validate_envelope_target(path)
    payload = (
        json.dumps(
            {
                "agent_id": contract.agent_id,
                "database": contract.database,
                "environment": contract.environment,
                "format": ENVELOPE_FORMAT,
                "hostname": contract.hostname,
                "reporter": {
                    "login": contract.reporter_login,
                    "password": contract.reporter_password,
                },
                "worker": {
                    "login": contract.worker_login,
                    "password": contract.worker_password,
                },
                "worker_key": contract.worker_key,
            },
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.pending-", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        if os.name == "posix":
            os.fchown(descriptor, 0, 0)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
    except Exception:
        os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise
    else:
        os.close(descriptor)
    try:
        _fsync_directory(path.parent)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _activate_secret_envelope(temporary: Path, target: Path) -> None:
    _validate_envelope_target(target)
    os.replace(temporary, target)
    _fsync_directory(target.parent)


def _assert_rls_contract(cursor: Any) -> None:
    cursor.execute(
        """
        SELECT relation.relname
        FROM pg_class relation
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public'
          AND relation.relname = ANY(%s::text[])
          AND relation.relkind IN ('r', 'p')
          AND relation.relrowsecurity
        ORDER BY relation.relname
        """,
        (sorted(REQUIRED_RLS_TABLES),),
    )
    if {str(row[0]) for row in cursor.fetchall()} != REQUIRED_RLS_TABLES:
        raise WorkerPairEnrollmentError("required crawler worker RLS tables are missing or disabled")
    cursor.execute(
        """
        SELECT tablename, policyname
        FROM pg_policies
        WHERE schemaname = 'public'
          AND (tablename, policyname) IN (
              ('ops_agents', 'crawler_worker_agent_scope'),
              ('ops_crawler_release_reports', 'crawler_worker_release_report_scope'),
              ('ops_crawler_task_attempts', 'crawler_worker_attempt_scope'),
              ('ops_crawler_task_observations', 'crawler_worker_observation_scope'),
              ('ops_crawler_worker_desired_state', 'crawler_runtime_desired_state_scope'),
              ('ops_jobs', 'crawler_worker_job_scope')
          )
        ORDER BY tablename, policyname
        """
    )
    if {(str(row[0]), str(row[1])) for row in cursor.fetchall()} != REQUIRED_RLS_POLICIES:
        raise WorkerPairEnrollmentError("required crawler worker RLS policies are incomplete")


def _pair_existing_state(cursor: Any, contract: PairContract) -> str:
    cursor.execute(
        "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s::text[]) ORDER BY rolname",
        (sorted((contract.worker_login, contract.reporter_login)),),
    )
    roles = {str(row[0]) for row in cursor.fetchall()}
    cursor.execute(
        """
        SELECT agent_id::text, environment, binding_type, database_login
        FROM ops_crawler_agent_bindings
        WHERE database_login = ANY(%s::text[])
           OR agent_id = %s::uuid
        ORDER BY binding_type, database_login
        FOR UPDATE
        """,
        (sorted((contract.worker_login, contract.reporter_login)), contract.agent_id),
    )
    bindings = [tuple(str(value) for value in row) for row in cursor.fetchall()]
    cursor.execute(
        """
        SELECT id::text, environment, hostname, credential_hint, status,
               maintenance_mode, last_seen_at, name, capabilities
        FROM ops_agents
        WHERE id = %s::uuid
           OR (environment = %s AND hostname = %s)
           OR credential_hint = %s
        FOR UPDATE
        """,
        (
            contract.agent_id,
            contract.environment,
            contract.hostname,
            f"crawler-worker:{contract.worker_login}",
        ),
    )
    agents = cursor.fetchall()
    cursor.execute(
        """
        SELECT environment, worker_key, agent_id::text
        FROM ops_crawler_worker_desired_state
        WHERE (environment = %s AND worker_key = %s)
           OR agent_id = %s::uuid
        FOR UPDATE
        """,
        (contract.environment, contract.worker_key, contract.agent_id),
    )
    if any(
        tuple(str(value) for value in row)
        != (contract.environment, contract.worker_key, contract.agent_id)
        for row in cursor.fetchall()
    ):
        raise WorkerPairEnrollmentError("worker key is bound to a different desired-state agent")
    if not roles and not bindings and not agents:
        return "initial"
    expected_roles = {contract.worker_login, contract.reporter_login}
    expected_bindings = {
        (contract.agent_id, contract.environment, "worker", contract.worker_login),
        (contract.agent_id, contract.environment, "reporter", contract.reporter_login),
    }
    if roles != expected_roles or set(bindings) != expected_bindings or len(agents) != 1:
        raise WorkerPairEnrollmentError("worker/reporter pair is partial, collided, or unbound")
    agent = agents[0]
    if (
        tuple(str(value) for value in agent[:4])
        != (
            contract.agent_id,
            contract.environment,
            contract.hostname,
            f"crawler-worker:{contract.worker_login}",
        )
        or agent[5] is not False
        or agent[7] != f"{contract.worker_key} distributed crawler"
        or list(agent[8]) != ["crawler_worker"]
    ):
        raise WorkerPairEnrollmentError("existing crawler agent binding differs from the request")
    return "rotation"


def _assert_rotation_is_idle(cursor: Any, contract: PairContract) -> None:
    cursor.execute(
        """
        SELECT
            agent.status = 'healthy',
            agent.last_seen_at IS NOT NULL
                AND agent.last_seen_at >= clock_timestamp() - (%s * interval '1 second'),
            EXISTS (
                SELECT 1 FROM ops_jobs job
                WHERE job.agent_id = agent.id
                  AND job.status IN ('assigned', 'running')
                  AND job.lease_token IS NOT NULL
                  AND job.leased_until > clock_timestamp()
            ),
            EXISTS (
                SELECT 1 FROM ops_crawler_task_attempts attempt
                WHERE attempt.agent_id = agent.id AND attempt.status = 'running'
            ),
            EXISTS (
                SELECT 1 FROM ops_crawler_worker_desired_state desired
                WHERE desired.agent_id = agent.id
                  AND desired.environment = agent.environment
                  AND desired.desired_status IN ('active', 'draining')
            ),
            EXISTS (
                SELECT 1 FROM pg_stat_activity activity
                WHERE activity.usename = ANY(%s::text[])
                  AND activity.pid <> pg_backend_pid()
            )
        FROM ops_agents agent
        WHERE agent.id = %s::uuid
        FOR UPDATE
        """,
        (
            RECENT_HEARTBEAT_SECONDS,
            sorted((contract.worker_login, contract.reporter_login)),
            contract.agent_id,
        ),
    )
    state = cursor.fetchone()
    if not state or any(value is True for value in state):
        raise WorkerPairEnrollmentError(
            "credential rotation is fenced by healthy/recent heartbeat, active lease, "
            "running attempt, active desired state, or open runtime session"
        )


def _assert_registry_matches_database(
    cursor: Any, contract: PairContract, *, allow_pair_pending_without_roles: bool
) -> None:
    registry = _load_credential_registry()
    inventory = _managed_login_inventory(cursor)
    pair = {
        contract.worker_login: "worker",
        contract.reporter_login: "reporter",
    }
    for login, component in inventory.items():
        entry = registry["entries"].get(login)
        if (
            entry is None
            or entry.get("component") != component
            or entry.get("state") not in {"active", "pending"}
        ):
            raise WorkerPairEnrollmentError(
                f"managed LOGIN is missing from the credential registry: {login}"
            )
    allowed_extra = set(pair) if allow_pair_pending_without_roles else set()
    unexpected = set(registry["entries"]) - set(inventory) - allowed_extra
    if unexpected:
        raise WorkerPairEnrollmentError(
            f"credential registry contains a missing database LOGIN: {sorted(unexpected)[0]}"
        )
    for login, component in pair.items():
        entry = registry["entries"].get(login)
        if entry is None or entry.get("component") != component or entry.get("state") != "pending":
            raise WorkerPairEnrollmentError("paired credential registry is not pending")


def _converge_login(
    cursor: Any,
    *,
    component: str,
    login: str,
    verifier: str,
    database: str,
) -> None:
    if component not in PAIR_COMPONENTS or login in RESERVED_ROLES:
        raise WorkerPairEnrollmentError("unsafe worker/reporter LOGIN contract")
    permission_group = COMPONENT_CONTRACT[component][2]
    cursor.execute(
        """
        SELECT NOT rolcanlogin AND NOT rolsuper AND NOT rolcreaterole
               AND NOT rolcreatedb AND NOT rolreplication AND NOT rolbypassrls
        FROM pg_roles WHERE rolname = %s
        """,
        (permission_group,),
    )
    group = cursor.fetchone()
    if not group or group[0] is not True:
        raise WorkerPairEnrollmentError(f"permission group is missing or unsafe: {permission_group}")
    cursor.execute("SELECT shobj_description(oid, 'pg_authid') FROM pg_roles WHERE rolname = %s", (login,))
    existing = cursor.fetchone()
    marker = f"{MANAGED_ROLE_MARKER_PREFIX}{component}"
    if existing is None:
        cursor.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(login)))
        cursor.execute(sql.SQL("COMMENT ON ROLE {} IS %s").format(sql.Identifier(login)), (marker,))
    elif existing[0] != marker:
        raise WorkerPairEnrollmentError(f"existing {component} LOGIN has an invalid marker")
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
        raise WorkerPairEnrollmentError(f"{component} LOGIN owns database objects")
    cursor.execute(
        sql.SQL(
            "ALTER ROLE {} WITH LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 32 PASSWORD %s"
        ).format(sql.Identifier(login)),
        (verifier,),
    )
    cursor.execute(sql.SQL("ALTER ROLE {} RESET ALL").format(sql.Identifier(login)))
    cursor.execute(
        sql.SQL("ALTER ROLE {} IN DATABASE {} RESET ALL").format(
            sql.Identifier(login), sql.Identifier(database)
        )
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
            sql.SQL("REVOKE {} FROM {}").format(
                sql.Identifier(parent), sql.Identifier(login)
            )
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
            sql.SQL("REVOKE {} FROM {}").format(
                sql.Identifier(login), sql.Identifier(member)
            )
        )
    _revoke_direct_privileges(cursor, login)
    cursor.execute(
        sql.SQL("REVOKE ALL PRIVILEGES ON DATABASE {} FROM {}").format(
            sql.Identifier(database), sql.Identifier(login)
        )
    )
    cursor.execute(
        sql.SQL("GRANT {} TO {}").format(
            sql.Identifier(permission_group), sql.Identifier(login)
        )
    )
    if _has_direct_application_acl(cursor, login, database):
        raise WorkerPairEnrollmentError(f"{component} LOGIN retains a direct application ACL")


def _verify_pair(cursor: Any, contract: PairContract) -> None:
    for component in PAIR_COMPONENTS:
        login = getattr(contract, f"{component}_login")
        permission_group = COMPONENT_CONTRACT[component][2]
        cursor.execute(
            """
            SELECT role.rolcanlogin AND role.rolinherit
                       AND NOT role.rolsuper AND NOT role.rolcreaterole
                       AND NOT role.rolcreatedb AND NOT role.rolreplication
                       AND NOT role.rolbypassrls AND role.rolconnlimit = 32
                       AND role.rolconfig IS NULL,
                   array_agg(parent.rolname ORDER BY parent.rolname)
                       FILTER (WHERE parent.rolname IS NOT NULL),
                   bool_or(membership.admin_option)
                       FILTER (WHERE parent.rolname IS NOT NULL),
                   NOT EXISTS (
                       SELECT 1 FROM pg_auth_members child
                       WHERE child.roleid = role.oid
                   )
            FROM pg_roles role
            LEFT JOIN pg_auth_members membership ON membership.member = role.oid
            LEFT JOIN pg_roles parent ON parent.oid = membership.roleid
            WHERE role.rolname = %s
            GROUP BY role.oid
            """,
            (login,),
        )
        verified = cursor.fetchone()
        if (
            not verified
            or verified[0] is not True
            or verified[1] != [permission_group]
            or verified[2] is not False
            or verified[3] is not True
        ):
            raise WorkerPairEnrollmentError(f"{component} LOGIN membership verification failed")
        if _has_direct_application_acl(cursor, login, contract.database):
            raise WorkerPairEnrollmentError(f"{component} LOGIN retains a direct application ACL")
    cursor.execute(
        """
        SELECT agent.id::text, agent.environment, agent.hostname, agent.credential_hint,
               agent.name,
               array_agg(binding.binding_type || ':' || binding.database_login
                         ORDER BY binding.binding_type)
        FROM ops_agents agent
        JOIN ops_crawler_agent_bindings binding
          ON binding.agent_id = agent.id AND binding.environment = agent.environment
        WHERE agent.id = %s::uuid
        GROUP BY agent.id
        """,
        (contract.agent_id,),
    )
    row = cursor.fetchone()
    if not row or tuple(row[:4]) != (
        contract.agent_id,
        contract.environment,
        contract.hostname,
        f"crawler-worker:{contract.worker_login}",
    ) or row[4] != f"{contract.worker_key} distributed crawler" or list(row[5]) != [
        f"reporter:{contract.reporter_login}",
        f"worker:{contract.worker_login}",
    ]:
        raise WorkerPairEnrollmentError("paired agent identity/binding verification failed")


def _database_pair_transaction(
    connection: Any,
    contract: PairContract,
    *,
    object_owner: str,
    worker_verifier: str,
    reporter_verifier: str,
) -> None:
    _pin_installer_search_path(connection)
    _assert_application_owner_access(connection, object_owner)
    connection.set_session(isolation_level="SERIALIZABLE", readonly=False, autocommit=False)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))",
            (PAIR_LOCK_NAMESPACE, f"{contract.environment}:{contract.worker_key}"),
        )
        _assert_managed_permission_groups_own_nothing(cursor)
        _assert_rls_contract(cursor)
        _assert_registry_matches_database(
            cursor, contract, allow_pair_pending_without_roles=True
        )
        state = _pair_existing_state(cursor, contract)
        if state == "rotation":
            _assert_rotation_is_idle(cursor, contract)
        _converge_login(
            cursor,
            component="worker",
            login=contract.worker_login,
            verifier=worker_verifier,
            database=contract.database,
        )
        _converge_login(
            cursor,
            component="reporter",
            login=contract.reporter_login,
            verifier=reporter_verifier,
            database=contract.database,
        )
        _converge_agent_binding(
            cursor,
            component="worker",
            login=contract.worker_login,
            agent_id=contract.agent_id,
            worker_key=contract.worker_key,
            hostname=contract.hostname,
            environment=contract.environment,
        )
        _converge_agent_binding(
            cursor,
            component="reporter",
            login=contract.reporter_login,
            agent_id=contract.agent_id,
            worker_key=contract.worker_key,
            hostname=contract.hostname,
            environment=contract.environment,
        )
        _verify_pair(cursor, contract)
        _assert_registry_matches_database(
            cursor, contract, allow_pair_pending_without_roles=False
        )


def provision_worker_reporter_pair(
    schema_environment_file: Path,
    worker_environment_file: Path,
    reporter_environment_file: Path,
    *,
    confirmed_database: str,
    secret_envelope: Path,
) -> dict[str, str]:
    try:
        schema_environment = _protected_environment(schema_environment_file, owner_only=True)
        worker_environment = _protected_environment(worker_environment_file, owner_only=True)
        reporter_environment = _protected_environment(reporter_environment_file, owner_only=True)
    except PreflightError as exc:
        raise WorkerPairEnrollmentError(str(exc)) from exc
    contract, admin_config, object_owner = _pair_contract(
        schema_environment,
        worker_environment,
        reporter_environment,
        confirmed_database=confirmed_database,
    )
    worker_verifier = build_scram_sha_256_verifier(contract.worker_password)
    reporter_verifier = build_scram_sha_256_verifier(contract.reporter_password)
    if hmac.compare_digest(worker_verifier, reporter_verifier):
        raise WorkerPairEnrollmentError("worker and reporter SCRAM verifiers must be distinct")

    with _installer_lock():
        registry_snapshot, fingerprints = _reserve_pair_registry(contract)
        staged_envelope: Path | None = None
        connection: Any | None = None
        commit_attempted = False
        try:
            staged_envelope = _stage_secret_envelope(secret_envelope, contract)
            connection = psycopg2.connect(**admin_config)
            _database_pair_transaction(
                connection,
                contract,
                object_owner=object_owner,
                worker_verifier=worker_verifier,
                reporter_verifier=reporter_verifier,
            )
            # Exactly one commit makes the two LOGINs, memberships and bindings
            # visible together. No per-component commit is permitted here.
            commit_attempted = True
            connection.commit()
            _activate_secret_envelope(staged_envelope, secret_envelope)
            staged_envelope = None
            _activate_pair_registry(contract, fingerprints)
        except Exception:
            if connection is not None:
                connection.rollback()
            if not commit_attempted:
                if staged_envelope is not None:
                    staged_envelope.unlink(missing_ok=True)
                    _fsync_directory(secret_envelope.parent)
                try:
                    _write_credential_registry(registry_snapshot)
                except Exception:
                    # Pending pair state is safer than claiming either old or
                    # new password active after uncertain local recovery.
                    pass
            # Once commit was attempted, retain both the pending registry and
            # staged envelope. The blocked shell cannot consume either; an
            # operator must reconcile the uncertain commit as one pair.
            raise
        finally:
            if connection is not None:
                connection.close()
    return {
        "agent_id": contract.agent_id,
        "database": contract.database,
        "environment": contract.environment,
        "secret_envelope": str(secret_envelope),
        "status": "paired",
        "worker_key": contract.worker_key,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Atomically provision one crawler worker/reporter LOGIN pair"
    )
    parser.add_argument("--schema-env", required=True, type=Path)
    parser.add_argument("--worker-env", required=True, type=Path)
    parser.add_argument("--reporter-env", required=True, type=Path)
    parser.add_argument("--confirm-staging-database", required=True)
    parser.add_argument("--secret-envelope", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = provision_worker_reporter_pair(
            args.schema_env,
            args.worker_env,
            args.reporter_env,
            confirmed_database=args.confirm_staging_database,
            secret_envelope=args.secret_envelope,
        )
    except (SchemaInstallError, ServiceLoginError, PreflightError, WorkerPairEnrollmentError) as exc:
        parser.exit(78, f"crawler worker/reporter pair enrollment failed: {exc}\n")
    except psycopg2.Error:
        parser.exit(70, "crawler worker/reporter pair enrollment failed: PostgreSQL operation failed\n")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
