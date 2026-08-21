#!/usr/bin/env python3
"""Install the narrow PostgreSQL SCRAM contract used by containers and Ops.

Ubuntu's default local peer rule cannot authenticate a process in a container
as a PostgreSQL LOGIN role. The remote deployment worker also arrives through
a fixed TLS loopback tunnel and must not reuse its credential for another
database. This helper inserts three local container rules plus an exact worker
allow/reject fence before every fallback, proves the effective configuration
and positive/negative logins, and restores the previous bytes if any post-write
check fails. Passwords are accepted only on stdin and never printed or placed
in argv.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import pwd
import re
import signal
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence, cast


HBA_PATH = Path("/etc/postgresql/16/main/pg_hba.conf")
SOCKET_DIRECTORY = Path("/var/run/postgresql")
SOCKET_PATH = SOCKET_DIRECTORY / ".s.PGSQL.5432"
LOCK_PATH = Path("/run/lock/mooncen-postgresql-hba.lock")
RUNUSER = "/usr/sbin/runuser"
PSQL = "/usr/bin/psql"
BEGIN_MARKER = "# BEGIN MOONCEN CONTAINER LOCAL SCRAM"
END_MARKER = "# END MOONCEN CONTAINER LOCAL SCRAM"
IDENTIFIER_PATTERN = re.compile(r"\A[a-z_][a-z0-9_]{0,62}\Z")
PASSWORD_PATTERN = re.compile(r"\A[A-Za-z0-9._!@%+=,:/-]{16,256}\Z")
MAX_HBA_BYTES = 1024 * 1024
MAX_STDIN_BYTES = 2048
LOCAL_ROLE_COUNT = 3


class HbaContractError(RuntimeError):
    """Raised when the local PostgreSQL authentication contract is unsafe."""


@dataclass(frozen=True)
class Contract:
    database: str
    roles: tuple[str, str, str, str]

    @property
    def local_roles(self) -> tuple[str, str, str]:
        return cast(tuple[str, str, str], self.roles[:LOCAL_ROLE_COUNT])

    @property
    def worker_role(self) -> str:
        return self.roles[LOCAL_ROLE_COUNT]


def _validated_identifier(value: str, label: str) -> str:
    if IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise HbaContractError(f"{label} is invalid")
    return value


def _canonical_rule(database: str, role: str) -> str:
    return f"local\t{database}\t{role}\tscram-sha-256"


def _canonical_block(contract: Contract) -> list[str]:
    return [
        BEGIN_MARKER,
        *(_canonical_rule(contract.database, role) for role in contract.local_roles),
        f"local\tall\t{contract.worker_role}\treject",
        (
            f"hostssl\t{contract.database}\t{contract.worker_role}"
            "\t127.0.0.1/32\tscram-sha-256"
        ),
        f"host\tall\t{contract.worker_role}\t0.0.0.0/0\treject",
        f"host\tall\t{contract.worker_role}\t::/0\treject",
        END_MARKER,
    ]


def _validate_previous_managed_block(lines: list[str]) -> None:
    if len(lines) != 7:
        raise HbaContractError("managed PostgreSQL HBA block is not canonical")
    parsed = tuple(_tokens(line) for line in lines)
    local = parsed[:3]
    local_worker, worker_allow, worker_ipv4_reject, worker_ipv6_reject = parsed[3:]
    if (
        any(
            len(fields) != 4
            or fields[0] != "local"
            or IDENTIFIER_PATTERN.fullmatch(fields[1]) is None
            or IDENTIFIER_PATTERN.fullmatch(fields[2]) is None
            or fields[3] != "scram-sha-256"
            for fields in local
        )
        or len({fields[1] for fields in local}) != 1
        or len({fields[2] for fields in local}) != 3
        or local_worker[:2] != ("local", "all")
        or len(local_worker) != 4
        or IDENTIFIER_PATTERN.fullmatch(local_worker[2]) is None
        or local_worker[3] != "reject"
        or worker_allow
        != (
            "hostssl",
            local[0][1],
            local_worker[2],
            "127.0.0.1/32",
            "scram-sha-256",
        )
        or worker_ipv4_reject
        != ("host", "all", local_worker[2], "0.0.0.0/0", "reject")
        or worker_ipv6_reject
        != ("host", "all", local_worker[2], "::/0", "reject")
        or local_worker[2] in {fields[2] for fields in local}
    ):
        raise HbaContractError("managed PostgreSQL HBA block is not canonical")


def _tokens(line: str) -> tuple[str, ...]:
    content = line.split("#", 1)[0].strip()
    return tuple(content.split()) if content else ()


def render_hba(previous: bytes, contract: Contract) -> bytes:
    """Return a canonical, minimally changed HBA file for ``contract``."""

    if len(previous) > MAX_HBA_BYTES or b"\0" in previous:
        raise HbaContractError("PostgreSQL HBA file is invalid")
    try:
        text = previous.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HbaContractError("PostgreSQL HBA file is not UTF-8") from exc
    if not text.endswith("\n"):
        raise HbaContractError("PostgreSQL HBA file is not newline terminated")

    lines = text.splitlines()
    begin_indexes = [index for index, line in enumerate(lines) if line == BEGIN_MARKER]
    end_indexes = [index for index, line in enumerate(lines) if line == END_MARKER]
    if len(begin_indexes) != len(end_indexes) or len(begin_indexes) > 1:
        raise HbaContractError("managed PostgreSQL HBA markers are malformed")
    if begin_indexes:
        begin = begin_indexes[0]
        end = end_indexes[0]
        if end != begin + 8:
            raise HbaContractError("managed PostgreSQL HBA block is not canonical")
        _validate_previous_managed_block(lines[begin + 1 : end])
        del lines[begin : end + 1]

    fallback_indexes = [
        index
        for index, line in enumerate(lines)
        if _tokens(line) == ("local", "all", "all", "peer")
    ]
    if len(fallback_indexes) != 1:
        raise HbaContractError("PostgreSQL local peer fallback is not unique")
    fallback = fallback_indexes[0]
    postgres_peer = ("local", "all", "postgres", "peer")
    seen_postgres_peer = False
    for line in lines[:fallback]:
        fields = _tokens(line)
        if not fields:
            continue
        if fields == postgres_peer and not seen_postgres_peer:
            seen_postgres_peer = True
            continue
        if (
            fields[0] == "local"
            or fields[0].startswith("host")
            or fields[0].startswith("include")
        ):
            raise HbaContractError(
                "an earlier PostgreSQL HBA rule could shadow the managed contract"
            )
    if not seen_postgres_peer:
        raise HbaContractError("PostgreSQL postgres peer rule is missing or out of order")

    block = _canonical_block(contract)
    lines[fallback:fallback] = block
    return ("\n".join(lines) + "\n").encode("utf-8")


def _postgres_uid_gid() -> tuple[int, int]:
    try:
        account = pwd.getpwnam("postgres")
    except KeyError as exc:
        raise HbaContractError("PostgreSQL service account is unavailable") from exc
    return account.pw_uid, account.pw_gid


def _require_root() -> None:
    if os.geteuid() != 0:
        raise HbaContractError("this helper must run as root")


def _require_safe_parent(path: Path, postgres_uid: int) -> None:
    current = Path("/")
    for part in path.absolute().parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise HbaContractError("PostgreSQL configuration path is unavailable") from exc
        if current.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
            raise HbaContractError("PostgreSQL configuration path is unsafe")
        if metadata.st_uid not in {0, postgres_uid} or stat.S_IMODE(metadata.st_mode) & 0o022:
            raise HbaContractError("PostgreSQL configuration parent metadata is unsafe")


def _require_hba_file(postgres_uid: int, postgres_gid: int) -> os.stat_result:
    _require_safe_parent(HBA_PATH.parent, postgres_uid)
    try:
        metadata = HBA_PATH.lstat()
    except OSError as exc:
        raise HbaContractError("PostgreSQL HBA file is unavailable") from exc
    if (
        HBA_PATH.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != postgres_uid
        or metadata.st_gid != postgres_gid
        or stat.S_IMODE(metadata.st_mode) != 0o640
        or metadata.st_size > MAX_HBA_BYTES
    ):
        raise HbaContractError("PostgreSQL HBA file metadata is unsafe")
    return metadata


def _postgres_query(query: str) -> str:
    environment = {
        "HOME": "/var/lib/postgresql",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
    }
    try:
        result = subprocess.run(
            (
                RUNUSER,
                "-u",
                "postgres",
                "--",
                PSQL,
                "-X",
                "--no-password",
                "--set=ON_ERROR_STOP=1",
                "-At",
                "-d",
                "postgres",
                "-c",
                query,
            ),
            cwd="/",
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HbaContractError("PostgreSQL control query failed") from exc
    if result.returncode != 0 or len(result.stdout) > MAX_HBA_BYTES:
        raise HbaContractError("PostgreSQL control query failed")
    try:
        return result.stdout.decode("utf-8").rstrip("\n")
    except UnicodeDecodeError as exc:
        raise HbaContractError("PostgreSQL control query returned invalid output") from exc


def _read_server_settings() -> tuple[str, str, str, str]:
    values = _postgres_query(
        "SELECT current_setting('server_version_num');"
        "SELECT current_setting('hba_file');"
        "SELECT current_setting('unix_socket_directories');"
        "SELECT current_setting('unix_socket_permissions');"
    ).splitlines()
    if len(values) != 4:
        raise HbaContractError("PostgreSQL server settings are invalid")
    return values[0], values[1], values[2], values[3]


def _require_socket_contract(postgres_uid: int, postgres_gid: int) -> None:
    try:
        directory = SOCKET_DIRECTORY.lstat()
        socket = SOCKET_PATH.lstat()
    except OSError as exc:
        raise HbaContractError("PostgreSQL Unix socket is unavailable") from exc
    if (
        SOCKET_DIRECTORY.is_symlink()
        or not stat.S_ISDIR(directory.st_mode)
        or directory.st_uid != postgres_uid
        or directory.st_gid != postgres_gid
        or stat.S_IMODE(directory.st_mode) != 0o2775
        or SOCKET_PATH.is_symlink()
        or not stat.S_ISSOCK(socket.st_mode)
        or socket.st_uid != postgres_uid
        or socket.st_gid != postgres_gid
        or stat.S_IMODE(socket.st_mode) != 0o777
    ):
        raise HbaContractError("PostgreSQL Unix socket metadata is unsafe")


def _atomic_write_hba(content: bytes, postgres_uid: int, postgres_gid: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".mooncen-pg-hba.", dir=str(HBA_PATH.parent)
    )
    temporary = Path(temporary_name)
    try:
        os.fchown(descriptor, postgres_uid, postgres_gid)
        os.fchmod(descriptor, 0o640)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, HBA_PATH)
        parent_fd = os.open(HBA_PATH.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise


def _reload() -> None:
    if _postgres_query("SELECT pg_reload_conf();") != "t":
        raise HbaContractError("PostgreSQL configuration reload failed")


def _hba_rows() -> list[dict[str, Any]]:
    raw = _postgres_query(
        "SELECT COALESCE(json_agg(json_build_object("
        "'line_number',line_number,'type',type,'database',database,"
        "'user_name',user_name,'address',address,'netmask',netmask,"
        "'auth_method',auth_method,'error',error) "
        "ORDER BY line_number)::text,'[]') FROM pg_hba_file_rules;"
    )
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise HbaContractError("PostgreSQL effective HBA rules are invalid") from exc
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise HbaContractError("PostgreSQL effective HBA rules are invalid")
    return value


def _verify_effective_rules(contract: Contract) -> None:
    rows = _hba_rows()
    if any(row.get("error") is not None for row in rows):
        raise HbaContractError("PostgreSQL reports an HBA parse error")
    fallback_lines = [
        row.get("line_number")
        for row in rows
        if row.get("type") == "local"
        and row.get("database") == ["all"]
        and row.get("user_name") == ["all"]
        and row.get("auth_method") == "peer"
    ]
    if len(fallback_lines) != 1 or not isinstance(fallback_lines[0], int):
        raise HbaContractError("PostgreSQL effective peer fallback is invalid")
    fallback = fallback_lines[0]
    for role in contract.local_roles:
        matches = [
            row
            for row in rows
            if row.get("type") == "local"
            and row.get("database") == [contract.database]
            and row.get("user_name") == [role]
            and row.get("auth_method") == "scram-sha-256"
        ]
        if (
            len(matches) != 1
            or not isinstance(matches[0].get("line_number"), int)
            or matches[0]["line_number"] >= fallback
        ):
            raise HbaContractError("PostgreSQL effective SCRAM rule is invalid")
    worker_rows = [
        row
        for row in rows
        if row.get("user_name") == [contract.worker_role]
    ]
    expected_worker_rules = {
        ("local", ("all",), None, None, "reject"),
        (
            "hostssl",
            (contract.database,),
            "127.0.0.1",
            "255.255.255.255",
            "scram-sha-256",
        ),
        ("host", ("all",), "0.0.0.0", "0.0.0.0", "reject"),
        ("host", ("all",), "::", "::", "reject"),
    }
    observed_worker_rules: set[
        tuple[str, tuple[str, ...], str | None, str | None, str]
    ] = set()
    for row in worker_rows:
        line_number = row.get("line_number")
        database = row.get("database")
        if (
            not isinstance(line_number, int)
            or line_number >= fallback
            or not isinstance(database, list)
            or not all(isinstance(item, str) for item in database)
            or not isinstance(row.get("type"), str)
            or not isinstance(row.get("auth_method"), str)
            or (
                row.get("address") is not None
                and not isinstance(row.get("address"), str)
            )
            or (
                row.get("netmask") is not None
                and not isinstance(row.get("netmask"), str)
            )
        ):
            raise HbaContractError("PostgreSQL effective worker HBA fence is invalid")
        observed_worker_rules.add(
            (
                row["type"],
                tuple(database),
                row.get("address"),
                row.get("netmask"),
                row["auth_method"],
            )
        )
    if len(worker_rows) != 4 or observed_worker_rules != expected_worker_rules:
        raise HbaContractError("PostgreSQL effective worker HBA fence is invalid")


def _verify_scram_passwords(contract: Contract) -> None:
    role_literals = ",".join(f"'{role}'" for role in contract.roles)
    rows = _postgres_query(
        "SELECT rolname || E'\\t' || (rolpassword LIKE 'SCRAM-SHA-256$%')::text "
        f"FROM pg_authid WHERE rolname IN ({role_literals}) ORDER BY rolname;"
    ).splitlines()
    if rows != [f"{role}\tt" for role in sorted(contract.roles)]:
        raise HbaContractError("PostgreSQL LOGIN roles do not have SCRAM credentials")


def _password_login(
    database: str,
    role: str,
    password: str,
    *,
    host: str | None = None,
    sslmode: str | None = None,
    channel_binding: str | None = None,
) -> subprocess.CompletedProcess[bytes]:
    environment = {
        "HOME": "/nonexistent",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "PGAPPNAME": "mooncen-container-hba-check",
        "PGCONNECT_TIMEOUT": "5",
        "PGPASSWORD": password,
    }
    if sslmode is not None:
        environment["PGSSLMODE"] = sslmode
    if channel_binding is not None:
        environment["PGCHANNELBINDING"] = channel_binding
    endpoint = str(SOCKET_DIRECTORY) if host is None else host
    try:
        return subprocess.run(
            (
                PSQL,
                "-X",
                "--no-password",
                "--set=ON_ERROR_STOP=1",
                "-h",
                endpoint,
                "-p",
                "5432",
                "-U",
                role,
                "-d",
                database,
                "-At",
                "-c",
                "SELECT current_user;",
            ),
            cwd="/",
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HbaContractError("PostgreSQL container-role login check failed") from exc


def _verify_password_login(database: str, role: str, password: str) -> None:
    result = _password_login(database, role, password)
    expected = f"{role}\n".encode("ascii")
    if result.returncode != 0 or result.stdout != expected:
        raise HbaContractError("PostgreSQL container-role login check failed")


def _require_rejected_login(result: subprocess.CompletedProcess[bytes]) -> None:
    if result.returncode == 0 or result.stdout:
        raise HbaContractError("deployment worker escaped its exact database HBA fence")


def _verify_worker_transport(contract: Contract, password: str) -> None:
    positive = _password_login(
        contract.database,
        contract.worker_role,
        password,
        host="127.0.0.1",
        sslmode="require",
        channel_binding="require",
    )
    if (
        positive.returncode != 0
        or positive.stdout != f"{contract.worker_role}\n".encode("ascii")
    ):
        raise HbaContractError("deployment worker TLS SCRAM login check failed")
    _require_rejected_login(
        _password_login(contract.database, contract.worker_role, password)
    )
    _require_rejected_login(
        _password_login(
            contract.database,
            contract.worker_role,
            password,
            host="127.0.0.1",
            sslmode="disable",
        )
    )
    databases = _postgres_query(
        "SELECT datname FROM pg_database "
        f"WHERE datallowconn AND datname <> '{contract.database}' ORDER BY datname;"
    ).splitlines()
    if len(databases) > 32 or any(
        IDENTIFIER_PATTERN.fullmatch(database) is None for database in databases
    ):
        raise HbaContractError("PostgreSQL database inventory is unsafe")
    for database in databases:
        _require_rejected_login(
            _password_login(
                database,
                contract.worker_role,
                password,
                host="127.0.0.1",
                sslmode="require",
                channel_binding="require",
            )
        )


def _read_passwords() -> tuple[str, str, str, str]:
    payload = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
    if len(payload) > MAX_STDIN_BYTES or not payload.endswith(b"\n"):
        raise HbaContractError("password input is invalid")
    pieces = payload[:-1].split(b"\n")
    if len(pieces) != 4:
        raise HbaContractError("password input is invalid")
    try:
        passwords = tuple(piece.decode("ascii") for piece in pieces)
    except UnicodeDecodeError as exc:
        raise HbaContractError("password input is invalid") from exc
    if len(passwords) != 4 or any(
        PASSWORD_PATTERN.fullmatch(password) is None for password in passwords
    ):
        raise HbaContractError("password input is invalid")
    return cast(tuple[str, str, str, str], passwords)


def _open_lock() -> int:
    try:
        descriptor = os.open(
            LOCK_PATH,
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise HbaContractError("PostgreSQL HBA operation lock is unsafe")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor
    except OSError as exc:
        raise HbaContractError("PostgreSQL HBA operation lock is unavailable") from exc


def install(contract: Contract, passwords: tuple[str, str, str, str]) -> str:
    _require_root()
    postgres_uid, postgres_gid = _postgres_uid_gid()
    lock_descriptor = _open_lock()
    try:
        version, hba_file, socket_directories, socket_permissions = _read_server_settings()
        if (
            not version.isdigit()
            or not (160000 <= int(version) < 170000)
            or hba_file != str(HBA_PATH)
            or socket_directories != str(SOCKET_DIRECTORY)
            or socket_permissions != "0777"
        ):
            raise HbaContractError("PostgreSQL server settings do not match the contract")
        _require_socket_contract(postgres_uid, postgres_gid)
        _require_hba_file(postgres_uid, postgres_gid)
        try:
            previous = HBA_PATH.read_bytes()
        except OSError as exc:
            raise HbaContractError("PostgreSQL HBA file cannot be read") from exc
        replacement = render_hba(previous, contract)
        wrote = False
        committed = False
        try:
            if replacement != previous:
                _atomic_write_hba(replacement, postgres_uid, postgres_gid)
                wrote = True
            _require_hba_file(postgres_uid, postgres_gid)
            if HBA_PATH.read_bytes() != replacement:
                raise HbaContractError("PostgreSQL HBA file changed during installation")
            _reload()
            _verify_effective_rules(contract)
            _verify_scram_passwords(contract)
            for role, password in zip(
                contract.local_roles,
                passwords[:LOCAL_ROLE_COUNT],
                strict=True,
            ):
                _verify_password_login(contract.database, role, password)
            _verify_worker_transport(contract, passwords[LOCAL_ROLE_COUNT])
            committed = True
        finally:
            if wrote and not committed:
                try:
                    _atomic_write_hba(previous, postgres_uid, postgres_gid)
                    _reload()
                except Exception as exc:
                    raise HbaContractError(
                        "PostgreSQL HBA rollback failed"
                    ) from exc
        if not committed:
            raise HbaContractError("PostgreSQL HBA installation did not commit")
        return hashlib.sha256(replacement).hexdigest()
    finally:
        os.close(lock_descriptor)


def _raise_on_signal(signum: int, _frame: object) -> None:
    raise HbaContractError(f"PostgreSQL HBA installation interrupted by signal {signum}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    install_parser = subparsers.add_parser("install")
    install_parser.add_argument("--database", required=True)
    install_parser.add_argument("--migrator-role", required=True)
    install_parser.add_argument("--api-role", required=True)
    install_parser.add_argument("--ai-role", required=True)
    install_parser.add_argument("--worker-role", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    for handled_signal in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(handled_signal, _raise_on_signal)
    try:
        roles = (
            _validated_identifier(arguments.migrator_role, "migrator role"),
            _validated_identifier(arguments.api_role, "API role"),
            _validated_identifier(arguments.ai_role, "AI role"),
            _validated_identifier(arguments.worker_role, "worker role"),
        )
        if len(set(roles)) != len(roles):
            raise HbaContractError("container PostgreSQL roles must be distinct")
        contract = Contract(
            database=_validated_identifier(arguments.database, "database"),
            roles=roles,
        )
        digest = install(contract, _read_passwords())
    except (HbaContractError, OSError) as exc:
        print(f"mooncen container PostgreSQL auth: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {"hba_sha256": digest, "schema_version": 1, "status": "configured"},
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
