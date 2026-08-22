"""Audit or explicitly bootstrap the protected crawler password registry.

Bootstrap is a migration/recovery operation for installations that predate the
registry.  It requires the protected environment for every marked managed
LOGIN and authenticates every supplied password before recording only its
keyed-HMAC fingerprint.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
from pathlib import Path
from typing import Any

import psycopg2

from tools.ensure_crawler_control_schema import _connection_config, _pin_installer_search_path
from tools.preflight_distributed_crawler_control import (
    PreflightError,
    _connection_config as _runtime_connection_config,
    _protected_environment,
)
from tools.provision_crawler_service_login import (
    COMPONENT_CONTRACT,
    REGISTRY_VERSION,
    ServiceLoginError,
    _fingerprint_key,
    _installer_lock,
    _managed_login_inventory,
    _target_contract,
    _verify_managed_login_registry,
    _write_credential_registry,
)


PREFLIGHT_COMPONENT = {
    "control": "scheduler",
    "publisher": "publisher",
    "finalizer": "finalizer",
    "approver": "approver",
    "release_approver": "release_approver",
    "release_admin": "release_admin",
    "worker": "worker",
    "reporter": "reporter",
    "observer": "observer",
}


def _parse_credential_spec(raw: str) -> tuple[str, Path]:
    component, separator, path = raw.partition("=")
    if not separator or component not in COMPONENT_CONTRACT or not path:
        raise ServiceLoginError("--credential must use COMPONENT=/root/protected.env")
    return component, Path(path)


def _connect_identity(config: dict[str, Any], login: str) -> None:
    try:
        connection = psycopg2.connect(**config)
    except Exception as exc:
        raise ServiceLoginError(f"protected password does not authenticate managed login: {login}") from exc
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_user = %s AND session_user = %s", (login, login))
            if cursor.fetchone()[0] is not True:
                raise ServiceLoginError(f"managed login authenticated as another role: {login}")
    finally:
        connection.close()


def audit_or_bootstrap(
    schema_environment_file: Path,
    confirmed_database: str,
    credential_specs: list[str],
    *,
    audit_only: bool,
) -> dict[str, Any]:
    schema_environment = _protected_environment(schema_environment_file, owner_only=True)
    admin_config = _connection_config(schema_environment)
    if admin_config["database"] != confirmed_database:
        raise ServiceLoginError("confirmed database differs from the schema administrator target")

    with _installer_lock():
        try:
            admin_connection = psycopg2.connect(**admin_config)
        except Exception as exc:
            raise ServiceLoginError("cannot connect with the schema administrator") from exc
        try:
            _pin_installer_search_path(admin_connection)
            with admin_connection.cursor() as cursor:
                inventory = _managed_login_inventory(cursor)
                if audit_only:
                    if credential_specs:
                        raise ServiceLoginError("--audit-only does not accept --credential")
                    _verify_managed_login_registry(cursor, allow_pending=False)
                    admin_connection.rollback()
                    return {"status": "ok", "mode": "audit", "managed_logins": len(inventory)}
            admin_connection.rollback()
        finally:
            admin_connection.close()

        supplied: dict[str, tuple[str, str, dict[str, Any]]] = {}
        schema_password = str(admin_config["password"])
        for raw_spec in credential_specs:
            component, environment_file = _parse_credential_spec(raw_spec)
            environment = _protected_environment(environment_file, owner_only=True)
            login, password, _ = _target_contract(component, environment)
            if login in supplied:
                raise ServiceLoginError(f"duplicate protected environment for managed login: {login}")
            runtime_config = _runtime_connection_config(PREFLIGHT_COMPONENT[component], environment)
            if (
                runtime_config["host"] != admin_config["host"]
                or runtime_config["port"] != admin_config["port"]
                or runtime_config["database"] != confirmed_database
            ):
                raise ServiceLoginError(f"managed login selects another database endpoint: {login}")
            if password == schema_password:
                raise ServiceLoginError("managed login reuses the schema administrator password")
            supplied[login] = (component, password, runtime_config)

        if set(supplied) != set(inventory):
            missing = sorted(set(inventory) - set(supplied))
            extra = sorted(set(supplied) - set(inventory))
            detail = (missing or extra or ["unknown"])[0]
            raise ServiceLoginError(
                f"bootstrap requires the exact environment set for every marked login: {detail}"
            )
        if any(supplied[login][0] != component for login, component in inventory.items()):
            raise ServiceLoginError("protected environment component differs from its database marker")

        for login, (_, _, runtime_config) in supplied.items():
            _connect_identity(runtime_config, login)

        key = _fingerprint_key()
        entries: dict[str, dict[str, str]] = {}
        fingerprints: dict[str, str] = {}
        for login, (component, password, _) in sorted(supplied.items()):
            fingerprint = hmac.new(
                key,
                b"mooncen-crawler-login-password:v1\0" + password.encode("ascii"),
                hashlib.sha256,
            ).hexdigest()
            collision = fingerprints.get(fingerprint)
            if collision is not None:
                raise ServiceLoginError(
                    f"managed crawler passwords are reused by {collision} and {login}"
                )
            fingerprints[fingerprint] = login
            entries[login] = {
                "component": component,
                "fingerprint": fingerprint,
                "state": "active",
            }
        _write_credential_registry({"version": REGISTRY_VERSION, "entries": entries})
        return {"status": "ok", "mode": "bootstrap", "managed_logins": len(entries)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit/bootstrap crawler credential fingerprints")
    parser.add_argument("--schema-env", required=True, type=Path)
    parser.add_argument("--confirm-staging-database", required=True)
    parser.add_argument("--credential", action="append", default=[])
    parser.add_argument("--audit-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = audit_or_bootstrap(
            args.schema_env,
            args.confirm_staging_database,
            args.credential,
            audit_only=args.audit_only,
        )
    except (PreflightError, ServiceLoginError) as exc:
        parser.exit(78, f"crawler credential registry failed: {exc}\n")
    except psycopg2.Error:
        parser.exit(70, "crawler credential registry failed: PostgreSQL operation failed\n")
    import json

    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
