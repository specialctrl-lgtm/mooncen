#!/usr/bin/env python3
"""Provision the one least-privilege LOGIN role used by the Docker API."""

from __future__ import annotations

import os
import re
import sys
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import TRANSACTION_STATUS_IDLE

from DB.connection_settings import bounded_env_int, database_connect_options


ROOT = Path(__file__).resolve().parents[2]
ROLES_SQL_PATH = ROOT / "DB" / "roles.sql"
ROLE_MARKER = "mooncen:docker-development:api-login:v1"
ROLE_NAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
NOLOGIN_ROLE_PATTERN = re.compile(
    r"\bCREATE\s+ROLE\s+([a-z_][a-z0-9_]*)\s+NOLOGIN\b",
    re.IGNORECASE,
)
SAFE_PASSWORD_PATTERN = re.compile(r"^[A-Za-z0-9._!@%+=,:/\-]+$")
APPLICATION_SCHEMAS = ("public", "crawl_staging")


class ProvisioningError(RuntimeError):
    """Raised when the Docker API login cannot be converged safely."""


@dataclass(frozen=True)
class DatabaseSettings:
    host: str
    port: int
    name: str
    owner_user: str
    owner_password: str
    api_user: str
    api_password: str


def _required_environment(name: str) -> str:
    value = os.getenv(name, "")
    if not value:
        raise ProvisioningError(f"{name} is required")
    return value


def _load_settings(roles_sql: str) -> DatabaseSettings:
    environment = os.getenv("ENVIRONMENT", "").strip().lower()
    host = os.getenv("DB_HOST", "").strip().lower()
    if environment != "development" or host != "postgres":
        raise ProvisioningError(
            "The Docker API role provisioner may run only against the development "
            "Compose postgres service"
        )

    settings = DatabaseSettings(
        host=host,
        port=bounded_env_int("DB_PORT", 5432, 1, 65535),
        name=_required_environment("DB_NAME"),
        owner_user=_required_environment("DB_USER"),
        owner_password=_required_environment("DB_PASSWORD"),
        api_user=_required_environment("DB_API_USER"),
        api_password=_required_environment("DB_API_PASSWORD"),
    )
    if not ROLE_NAME_PATTERN.fullmatch(settings.api_user):
        raise ProvisioningError(
            "DB_API_USER must be a lowercase PostgreSQL identifier of at most 63 characters"
        )
    permission_groups = frozenset(NOLOGIN_ROLE_PATTERN.findall(roles_sql))
    if "mooncen_api" not in permission_groups:
        raise ProvisioningError("DB/roles.sql does not declare the mooncen_api permission group")
    if settings.owner_user in permission_groups:
        raise ProvisioningError("DB_USER must differ from every NOLOGIN permission group")
    if settings.api_user in permission_groups or settings.api_user == settings.owner_user:
        raise ProvisioningError(
            "DB_API_USER must differ from the bootstrap owner and every NOLOGIN group"
        )
    if (
        len(settings.api_password) < 16
        or settings.api_password.startswith(("change-me", "replace-with"))
        or not SAFE_PASSWORD_PATTERN.fullmatch(settings.api_password)
    ):
        raise ProvisioningError(
            "DB_API_PASSWORD must be a random, EnvironmentFile-safe value of at least "
            "16 characters"
        )
    if settings.api_password == settings.owner_password:
        raise ProvisioningError("DB_API_PASSWORD must differ from DB_PASSWORD")
    return settings


def _connect(settings: DatabaseSettings, *, api: bool = False):
    user = settings.api_user if api else settings.owner_user
    password = settings.api_password if api else settings.owner_password
    application_name = "mooncen-docker-api-contract" if api else "mooncen-docker-role-provisioner"
    return psycopg2.connect(
        host=settings.host,
        port=settings.port,
        dbname=settings.name,
        user=user,
        password=password,
        **database_connect_options(settings.host, application_name),
    )


def _assert_bootstrap_superuser(cursor, settings: DatabaseSettings) -> None:
    cursor.execute(
        """
        SELECT role.rolsuper, session_user, current_user
        FROM pg_roles role
        WHERE role.rolname = current_user
        """
    )
    row = cursor.fetchone()
    if row != (True, settings.owner_user, settings.owner_user):
        raise ProvisioningError(
            "DB_USER must authenticate directly as the Compose bootstrap superuser"
        )


def _apply_roles_sql(connection, roles_sql: str) -> None:
    """Let roles.sql own its explicit BEGIN/COMMIT boundary without an implicit BEGIN."""

    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            cursor.execute(roles_sql)
    except BaseException:
        if connection.get_transaction_status() != TRANSACTION_STATUS_IDLE:
            try:
                connection.rollback()
            except psycopg2.Error:
                pass
        raise
    else:
        if connection.get_transaction_status() != TRANSACTION_STATUS_IDLE:
            connection.rollback()
            raise ProvisioningError("DB/roles.sql did not close its explicit transaction")
    finally:
        if not getattr(connection, "closed", False):
            connection.autocommit = False


def _role_exists(cursor, role_name: str) -> bool:
    cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role_name,))
    return cursor.fetchone() is not None


def _role_comment(cursor, role_name: str) -> str | None:
    cursor.execute(
        "SELECT shobj_description(oid, 'pg_authid') FROM pg_roles WHERE rolname = %s",
        (role_name,),
    )
    row = cursor.fetchone()
    return None if row is None else row[0]


def _assert_role_owns_nothing(cursor, role_name: str) -> None:
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM pg_shdepend dependency
            JOIN pg_roles role ON role.oid = dependency.refobjid
            WHERE dependency.refclassid = 'pg_authid'::regclass
              AND dependency.deptype = 'o'
              AND role.rolname = %s
        )
        """,
        (role_name,),
    )
    if cursor.fetchone()[0]:
        raise ProvisioningError(
            f"Refusing to repurpose database role {role_name!r} because it owns objects"
        )


def _revoke_memberships(cursor, role_name: str) -> None:
    cursor.execute(
        """
        SELECT parent.rolname, member.rolname
        FROM pg_auth_members membership
        JOIN pg_roles parent ON parent.oid = membership.roleid
        JOIN pg_roles member ON member.oid = membership.member
        WHERE parent.rolname = %s OR member.rolname = %s
        """,
        (role_name, role_name),
    )
    for parent, member in cursor.fetchall():
        cursor.execute(
            sql.SQL("REVOKE {} FROM {}").format(
                sql.Identifier(parent), sql.Identifier(member)
            )
        )


def _revoke_direct_database_privileges(cursor, settings: DatabaseSettings, role_name: str) -> None:
    for schema_name in APPLICATION_SCHEMAS:
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = %s)",
            (schema_name,),
        )
        if not cursor.fetchone()[0]:
            continue

        identifiers = (sql.Identifier(schema_name), sql.Identifier(role_name))
        for object_kind in ("TABLES", "SEQUENCES", "ROUTINES"):
            cursor.execute(
                sql.SQL("REVOKE ALL PRIVILEGES ON ALL {} IN SCHEMA {} FROM {}").format(
                    sql.SQL(object_kind), *identifiers
                )
            )
        cursor.execute(
            sql.SQL("REVOKE ALL PRIVILEGES ON SCHEMA {} FROM {}").format(*identifiers)
        )

        cursor.execute(
            """
            SELECT table_schema, table_name, column_name
            FROM information_schema.column_privileges
            WHERE grantee = %s AND table_schema = %s
            """,
            (role_name, schema_name),
        )
        for table_schema, table_name, column_name in cursor.fetchall():
            cursor.execute(
                sql.SQL(
                    "REVOKE ALL PRIVILEGES ({}) ON TABLE {}.{} FROM {}"
                ).format(
                    sql.Identifier(column_name),
                    sql.Identifier(table_schema),
                    sql.Identifier(table_name),
                    sql.Identifier(role_name),
                )
            )

    cursor.execute(
        sql.SQL("REVOKE ALL PRIVILEGES ON DATABASE {} FROM {}").format(
            sql.Identifier(settings.name), sql.Identifier(role_name)
        )
    )


def _disable_stale_login(cursor, settings: DatabaseSettings, role_name: str) -> None:
    _assert_role_owns_nothing(cursor, role_name)
    _revoke_memberships(cursor, role_name)
    _revoke_direct_database_privileges(cursor, settings, role_name)
    cursor.execute(
        sql.SQL(
            "ALTER ROLE {} WITH NOLOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOREPLICATION NOBYPASSRLS"
        ).format(sql.Identifier(role_name))
    )
    cursor.execute(sql.SQL("ALTER ROLE {} RESET ALL").format(sql.Identifier(role_name)))


def _converge_api_login(connection, settings: DatabaseSettings) -> None:
    with connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (ROLE_MARKER,))
            cursor.execute(
                """
                SELECT rolname
                FROM pg_roles
                WHERE shobj_description(oid, 'pg_authid') = %s
                  AND rolname <> %s
                """,
                (ROLE_MARKER, settings.api_user),
            )
            for stale_role in (row[0] for row in cursor.fetchall()):
                _disable_stale_login(cursor, settings, stale_role)

            if _role_exists(cursor, settings.api_user):
                if _role_comment(cursor, settings.api_user) != ROLE_MARKER:
                    raise ProvisioningError(
                        f"DB_API_USER {settings.api_user!r} already exists and was not "
                        "created by this Docker provisioner"
                    )
            else:
                cursor.execute(
                    sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(settings.api_user))
                )
                cursor.execute(
                    sql.SQL("COMMENT ON ROLE {} IS {}").format(
                        sql.Identifier(settings.api_user), sql.Literal(ROLE_MARKER)
                    )
                )

            _assert_role_owns_nothing(cursor, settings.api_user)
            _revoke_memberships(cursor, settings.api_user)
            _revoke_direct_database_privileges(cursor, settings, settings.api_user)
            cursor.execute(
                sql.SQL(
                    "ALTER ROLE {} WITH LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOREPLICATION NOBYPASSRLS CONNECTION LIMIT -1 PASSWORD {} "
                    "VALID UNTIL 'infinity'"
                ).format(
                    sql.Identifier(settings.api_user), sql.Literal(settings.api_password)
                )
            )
            cursor.execute(
                sql.SQL("ALTER ROLE {} RESET ALL").format(sql.Identifier(settings.api_user))
            )
            cursor.execute(
                sql.SQL("GRANT mooncen_api TO {}").format(sql.Identifier(settings.api_user))
            )


def _assert_catalog_contract(connection, settings: DatabaseSettings) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT rolcanlogin, rolinherit, rolsuper, rolcreatedb, rolcreaterole,
                   rolreplication, rolbypassrls, rolconnlimit, rolconfig IS NULL
            FROM pg_roles
            WHERE rolname = %s
            """,
            (settings.api_user,),
        )
        if cursor.fetchone() != (
            True,
            True,
            False,
            False,
            False,
            False,
            False,
            -1,
            True,
        ):
            raise ProvisioningError("The Docker API login has unsafe role attributes")
        cursor.execute(
            """
            SELECT parent.rolname
            FROM pg_auth_members membership
            JOIN pg_roles parent ON parent.oid = membership.roleid
            JOIN pg_roles member ON member.oid = membership.member
            WHERE member.rolname = %s
            ORDER BY parent.rolname
            """,
            (settings.api_user,),
        )
        if cursor.fetchall() != [("mooncen_api",)]:
            raise ProvisioningError("The Docker API login must inherit only mooncen_api")
        cursor.execute(
            """
            SELECT 1
            FROM pg_auth_members membership
            JOIN pg_roles parent ON parent.oid = membership.roleid
            WHERE parent.rolname = %s
            LIMIT 1
            """,
            (settings.api_user,),
        )
        if cursor.fetchone() is not None:
            raise ProvisioningError("No other database role may inherit the Docker API login")
        _assert_role_owns_nothing(cursor, settings.api_user)
    connection.commit()


def _assert_runtime_contract(settings: DatabaseSettings) -> None:
    with closing(_connect(settings, api=True)) as connection:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT current_user, session_user, role.rolcanlogin, role.rolinherit,
                           role.rolsuper, role.rolcreatedb, role.rolcreaterole,
                           role.rolreplication, role.rolbypassrls,
                           has_database_privilege(
                               current_user, current_database(), 'CONNECT'
                           ),
                           has_database_privilege(
                               current_user, current_database(), 'CREATE'
                           ),
                           has_schema_privilege(current_user, 'public', 'CREATE'),
                           has_table_privilege(current_user, 'public.courses', 'SELECT'),
                           has_table_privilege(current_user, 'public.courses', 'DELETE'),
                           has_column_privilege(
                               current_user, 'public.courses', 'view_count', 'UPDATE'
                           ),
                           has_column_privilege(
                               current_user, 'public.courses', 'title', 'UPDATE'
                           )
                    FROM pg_roles role
                    WHERE role.rolname = current_user
                    """
                )
                expected = (
                    settings.api_user,
                    settings.api_user,
                    True,
                    True,
                    False,
                    False,
                    False,
                    False,
                    False,
                    True,
                    False,
                    False,
                    True,
                    False,
                    True,
                    False,
                )
                if cursor.fetchone() != expected:
                    raise ProvisioningError(
                        "The Docker API session failed its privilege contract"
                    )


def main() -> int:
    roles_sql = ROLES_SQL_PATH.read_text(encoding="utf-8")
    settings = _load_settings(roles_sql)
    with closing(_connect(settings)) as connection:
        with connection.cursor() as cursor:
            _assert_bootstrap_superuser(cursor, settings)
        connection.commit()
        _apply_roles_sql(connection, roles_sql)
        _converge_api_login(connection, settings)
        _assert_catalog_contract(connection, settings)
    _assert_runtime_contract(settings)
    print(f"Provisioned least-privilege Docker API login: {settings.api_user}")
    return 0


def _cli() -> int:
    try:
        return main()
    except ProvisioningError as exc:
        print(f"Docker API role provisioning failed: {exc}", file=sys.stderr)
    except psycopg2.Error as exc:
        sqlstate = exc.pgcode or "unknown"
        print(
            f"Docker API role provisioning failed: database error (SQLSTATE {sqlstate})",
            file=sys.stderr,
        )
    except OSError as exc:
        print(
            f"Docker API role provisioning failed: {type(exc).__name__}",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
