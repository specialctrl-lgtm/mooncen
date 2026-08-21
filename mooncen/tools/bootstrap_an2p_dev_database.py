#!/usr/bin/env python3
"""Bootstrap the owner, database, and extensions in the an2p LXD PostgreSQL."""

from __future__ import annotations

import os
import re
import socket
import stat
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import psycopg2
from dotenv import dotenv_values
from psycopg2.extensions import adapt


ENV_PATH = Path.home() / ".config" / "mooncen-an2p" / "mooncen.env"
LXC_PATH = Path("/snap/bin/lxc")
CONTAINER_NAME = "mooncen-dev-db"
IDENTIFIER_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
REQUIRED_EXTENSIONS = ("postgis", "pg_trgm", "pgcrypto", "uuid-ossp")


class BootstrapError(RuntimeError):
    """Raised when the guarded an2p bootstrap cannot be completed."""


def _required(values: dict[str, str | None], name: str) -> str:
    value = str(values.get(name) or "")
    if not value:
        raise BootstrapError(f"{name} is required in {ENV_PATH}")
    return value


def _assert_secure_environment_file() -> None:
    metadata = ENV_PATH.lstat()
    if ENV_PATH.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise BootstrapError(f"Refusing unsafe environment file: {ENV_PATH}")
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise BootstrapError(f"Environment file must be owned by the user with mode 0600: {ENV_PATH}")


def _sql_literal(value: str) -> str:
    return adapt(value).getquoted().decode("utf-8")


def _sql_identifier(value: str) -> str:
    if not IDENTIFIER_PATTERN.fullmatch(value):
        raise BootstrapError(f"Unsafe PostgreSQL identifier: {value!r}")
    return f'"{value}"'


def _run_lxc(arguments: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # noqa: S603 - exact, locally guarded executable and arguments.
        [str(LXC_PATH), *arguments],
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise BootstrapError(f"LXD command failed ({arguments[0]}, exit {result.returncode})")
    return result


def _bootstrap_sql(owner_user: str, owner_password: str, database_name: str) -> str:
    owner_identifier = _sql_identifier(owner_user)
    database_identifier = _sql_identifier(database_name)
    owner_literal = _sql_literal(owner_user)
    password_literal = _sql_literal(owner_password)
    database_literal = _sql_literal(database_name)
    return rf"""
DO $mooncen$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {owner_literal}) THEN
        ALTER ROLE {owner_identifier}
            WITH LOGIN SUPERUSER CREATEDB CREATEROLE INHERIT
            PASSWORD {password_literal};
    ELSE
        CREATE ROLE {owner_identifier}
            WITH LOGIN SUPERUSER CREATEDB CREATEROLE INHERIT
            PASSWORD {password_literal};
    END IF;
END
$mooncen$;

SELECT format('CREATE DATABASE %I OWNER %I', {database_literal}, {owner_literal})
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = {database_literal}) \gexec
ALTER DATABASE {database_identifier} OWNER TO {owner_identifier};
\connect {database_identifier}
CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public;
"""


def main() -> int:
    if socket.gethostname().split(".", 1)[0] != "an2p":
        raise BootstrapError("This bootstrap may run only on an2p")
    if not LXC_PATH.is_file():
        raise BootstrapError(f"LXD client is missing: {LXC_PATH}")
    _assert_secure_environment_file()
    values = dict(dotenv_values(ENV_PATH))
    if values.get("ENVIRONMENT") != "development" or values.get("DB_HOST") not in {
        "127.0.0.1",
        "localhost",
    }:
        raise BootstrapError("Bootstrap accepts only the loopback development database")

    owner_user = _required(values, "DB_USER")
    owner_password = _required(values, "DB_PASSWORD")
    database_name = _required(values, "DB_NAME")
    database_port = int(_required(values, "DB_PORT"))
    _sql_identifier(owner_user)
    _sql_identifier(database_name)

    state = _run_lxc(["list", CONTAINER_NAME, "--format", "csv", "-c", "s"]).stdout.strip()
    if state != "RUNNING":
        raise BootstrapError(f"{CONTAINER_NAME} must be RUNNING (found {state or 'missing'})")
    _run_lxc(
        [
            "exec",
            CONTAINER_NAME,
            "--mode=non-interactive",
            "--",
            "sudo",
            "-u",
            "postgres",
            "psql",
            "--no-psqlrc",
            "--set=ON_ERROR_STOP=1",
            "postgres",
        ],
        input_text=_bootstrap_sql(owner_user, owner_password, database_name),
    )

    with closing(
        psycopg2.connect(
            host=str(values["DB_HOST"]),
            port=database_port,
            dbname=database_name,
            user=owner_user,
            password=owner_password,
            sslmode=str(values.get("DB_SSLMODE") or "disable"),
            connect_timeout=5,
            application_name="mooncen-an2p-bootstrap-verification",
        )
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
            )
            if cursor.fetchone() != (True,):
                raise BootstrapError("Migration owner is not the expected PostgreSQL superuser")
            cursor.execute(
                "SELECT extname FROM pg_extension WHERE extname = ANY(%s) ORDER BY extname",
                (list(REQUIRED_EXTENSIONS),),
            )
            installed = {row[0] for row in cursor.fetchall()}
            if installed != set(REQUIRED_EXTENSIONS):
                raise BootstrapError("Required PostgreSQL extensions are incomplete")
    print(f"Bootstrapped and verified {database_name} in LXD container {CONTAINER_NAME}.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, BootstrapError, psycopg2.Error) as exc:
        print(f"an2p database bootstrap failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
