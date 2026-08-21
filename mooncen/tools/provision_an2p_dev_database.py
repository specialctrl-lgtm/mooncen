#!/usr/bin/env python3
"""Converge and verify the least-privilege API login for the an2p dev DB."""

from __future__ import annotations

import os
import sys
from contextlib import closing
from pathlib import Path

import psycopg2
from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = Path.home() / ".config" / "mooncen-an2p" / "mooncen.env"
sys.path.insert(0, str(PROJECT_ROOT))

from deploy.docker.provision_api_login import (  # noqa: E402
    DatabaseSettings,
    ProvisioningError,
    ROLES_SQL_PATH,
    _apply_roles_sql,
    _assert_bootstrap_superuser,
    _assert_catalog_contract,
    _assert_runtime_contract,
    _connect,
    _converge_api_login,
)


def _required(values: dict[str, str | None], name: str) -> str:
    value = str(values.get(name) or "")
    if not value:
        raise ProvisioningError(f"{name} is required in {ENV_PATH}")
    return value


def main() -> int:
    values = dict(dotenv_values(ENV_PATH))
    if values.get("ENVIRONMENT") != "development" or values.get("DB_HOST") not in {
        "127.0.0.1",
        "localhost",
    }:
        raise ProvisioningError("an2p provisioner accepts only the loopback development database")
    for name, value in values.items():
        if value is not None:
            os.environ[name] = value
    settings = DatabaseSettings(
        host=_required(values, "DB_HOST"),
        port=int(_required(values, "DB_PORT")),
        name=_required(values, "DB_NAME"),
        owner_user=_required(values, "DB_USER"),
        owner_password=_required(values, "DB_PASSWORD"),
        api_user=_required(values, "DB_API_USER"),
        api_password=_required(values, "DB_API_PASSWORD"),
    )
    if settings.owner_user == settings.api_user or settings.owner_password == settings.api_password:
        raise ProvisioningError("API and migration-owner credentials must be distinct")

    roles_sql = ROLES_SQL_PATH.read_text(encoding="utf-8")
    with closing(_connect(settings)) as connection:
        with connection.cursor() as cursor:
            _assert_bootstrap_superuser(cursor, settings)
        connection.commit()
        _apply_roles_sql(connection, roles_sql)
        _converge_api_login(connection, settings)
        _assert_catalog_contract(connection, settings)
    _assert_runtime_contract(settings)
    print(f"Provisioned and verified least-privilege an2p API login: {settings.api_user}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, ProvisioningError, psycopg2.Error) as exc:
        print(f"an2p API role provisioning failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
