#!/usr/bin/env python3
"""Create the local-only development credentials used on an2p.

The generated environment is deliberately stored outside the repository.
systemd reads it directly; one-off commands should load it explicitly so test
processes do not inherit development database settings by accident.
"""

from __future__ import annotations

import argparse
import os
import secrets
import stat
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = Path.home() / ".config" / "mooncen-an2p"
ENV_PATH = CONFIG_DIR / "mooncen.env"
API_ENV_PATH = CONFIG_DIR / "api.env"
STATUS_ENV_PATH = CONFIG_DIR / "status-agent.env"
CREDENTIALS_PATH = CONFIG_DIR / "ops-credentials.txt"

sys.path.insert(0, str(PROJECT_ROOT))
from tools.generate_ops_password import encode_password  # noqa: E402


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        path.chmod(0o600)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create an2p local development secrets")
    parser.add_argument(
        "--show-paths",
        action="store_true",
        help="show the secret file paths; secret values are never printed",
    )
    parser.add_argument(
        "--converge-role-separation",
        action="store_true",
        help="replace a legacy shared API/owner credential with a distinct API credential",
    )
    return parser.parse_args()


def _assert_secure_existing_path(path: Path, *, directory: bool = False) -> None:
    metadata = path.lstat()
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if path.is_symlink() or not expected_type(metadata.st_mode):
        raise ValueError(f"Refusing unsafe credential path: {path}")
    if metadata.st_uid != os.getuid():
        raise ValueError(f"Credential path is not owned by the current user: {path}")


def _environment_values(content: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        values[name] = value
    return values


def _replace_environment_values(content: str, replacements: dict[str, str]) -> str:
    remaining = dict(replacements)
    lines: list[str] = []
    for line in content.splitlines():
        name = line.split("=", 1)[0] if "=" in line else ""
        if name in remaining:
            lines.append(f"{name}={remaining.pop(name)}")
        else:
            lines.append(line)
    for name, value in remaining.items():
        lines.append(f"{name}={value}")
    return "\n".join(lines) + "\n"


def _required(values: dict[str, str], name: str) -> str:
    value = values.get(name, "")
    if not value or "\x00" in value or any(character in value for character in "\r\n"):
        raise ValueError(f"Development credential is missing or invalid: {name}")
    return value


def _render_environment(values: dict[str, str], names: tuple[str, ...]) -> str:
    return "\n".join(
        (
            "# Derived by tools/setup_an2p_dev_secrets.py; never commit this file.",
            *(f"{name}={_required(values, name)}" for name in names),
            "",
        )
    )


def _write_service_environments(content: str) -> None:
    values = _environment_values(content)
    api_values = {
        **values,
        "MOONCEN_API_PROFILE": "public",
        "DB_APPLICATION_NAME": "mooncen-an2p-dev-api",
        "MOONCEN_CORS_ORIGINS": (
            "http://localhost:5174,http://127.0.0.1:5174"
        ),
    }
    api_names = (
        "ENVIRONMENT",
        "MOONCEN_API_PROFILE",
        "DB_HOST",
        "DB_PORT",
        "DB_NAME",
        "DB_API_USER",
        "DB_API_PASSWORD",
        "DB_RUNTIME_USER",
        "DB_RUNTIME_PASSWORD",
        "DB_APPLICATION_NAME",
        "DB_SSLMODE",
        "DB_CONNECT_TIMEOUT",
        "DB_STATEMENT_TIMEOUT_MS",
        "DB_LOCK_TIMEOUT_MS",
        "AUTH_SECRET",
        "MOONCEN_CORS_ORIGINS",
        "MOONCEN_TRUSTED_HOSTS",
        "OAUTH_REDIRECT_URIS",
        "VITE_SITE_URL",
        "LOG_LEVEL",
    )
    status_values = {
        **values,
        "OPS_STATUS_DB_HOST": _required(values, "DB_HOST"),
        "OPS_STATUS_DB_PORT": _required(values, "DB_PORT"),
        "OPS_STATUS_DB_NAME": _required(values, "DB_NAME"),
        "OPS_STATUS_DEPLOYMENT_CAPABILITY_ENABLED": "false",
    }
    status_names = (
        "ENVIRONMENT",
        "OPS_STATUS_DB_HOST",
        "OPS_STATUS_DB_PORT",
        "OPS_STATUS_DB_NAME",
        "OPS_STATUS_DB_USER",
        "OPS_STATUS_DB_PASSWORD",
        "OPS_STATUS_DEPLOYMENT_CAPABILITY_ENABLED",
        "DB_SSLMODE",
        "DB_CONNECT_TIMEOUT",
        "DB_STATEMENT_TIMEOUT_MS",
        "DB_LOCK_TIMEOUT_MS",
        "LOG_LEVEL",
    )
    _atomic_write(API_ENV_PATH, _render_environment(api_values, api_names))
    _atomic_write(STATUS_ENV_PATH, _render_environment(status_values, status_names))


def main() -> int:
    args = _parse_args()
    os.umask(0o077)

    existing = [path for path in (ENV_PATH, CREDENTIALS_PATH) if path.exists() or path.is_symlink()]
    if existing:
        if len(existing) != 2:
            print("Refusing a partial credential state; inspect ~/.config/mooncen-an2p.", file=sys.stderr)
            return 2
        try:
            _assert_secure_existing_path(CONFIG_DIR, directory=True)
            for path in existing:
                _assert_secure_existing_path(path)
                path.chmod(0o600)
            CONFIG_DIR.chmod(0o700)
        except (OSError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if args.converge_role_separation:
            content = ENV_PATH.read_text(encoding="utf-8")
            values = _environment_values(content)
            owner_user = values.get("DB_USER", "")
            owner_password = values.get("DB_PASSWORD", "")
            api_user = values.get("DB_API_USER", "")
            api_password = values.get("DB_API_PASSWORD", "")
            replacements = {
                "OPS_STATUS_DB_USER": owner_user,
                "OPS_STATUS_DB_PASSWORD": owner_password,
            }
            if not api_user or api_user == owner_user or not api_password or api_password == owner_password:
                api_password = secrets.token_urlsafe(48)
                replacements.update(
                    {
                        "DB_API_USER": "mooncen_api_login",
                        "DB_API_PASSWORD": api_password,
                        "DB_RUNTIME_USER": "mooncen_api_login",
                        "DB_RUNTIME_PASSWORD": api_password,
                    }
                )
            _atomic_write(ENV_PATH, _replace_environment_values(content, replacements))
            content = ENV_PATH.read_text(encoding="utf-8")
            print("Converged distinct API and migration-owner development credentials.")
        else:
            content = ENV_PATH.read_text(encoding="utf-8")
        try:
            _write_service_environments(content)
        except (OSError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if args.show_paths:
            print(f"Environment: {ENV_PATH}")
            print(f"API environment: {API_ENV_PATH}")
            print(f"Status Agent environment: {STATUS_ENV_PATH}")
            print(f"Ops credentials: {CREDENTIALS_PATH}")
        if not args.converge_role_separation:
            print("Existing an2p development credentials preserved.")
        return 0

    database_password = secrets.token_urlsafe(48)
    api_password = secrets.token_urlsafe(48)
    auth_secret = secrets.token_urlsafe(64)
    ops_password = secrets.token_urlsafe(32)
    ops_password_hash = encode_password(ops_password)

    environment = "\n".join(
        (
            "# Generated locally by tools/setup_an2p_dev_secrets.py; never commit this file.",
            "ENVIRONMENT=development",
            "DB_HOST=127.0.0.1",
            "DB_PORT=5432",
            "DB_NAME=mooncen",
            "DB_USER=mooncen_admin",
            f"DB_PASSWORD={database_password}",
            "DB_API_USER=mooncen_api_login",
            f"DB_API_PASSWORD={api_password}",
            "DB_RUNTIME_USER=mooncen_api_login",
            f"DB_RUNTIME_PASSWORD={api_password}",
            "OPS_STATUS_DB_USER=mooncen_admin",
            f"OPS_STATUS_DB_PASSWORD={database_password}",
            "DB_APPLICATION_NAME=mooncen-an2p-dev",
            "DB_SSLMODE=disable",
            "DB_CONNECT_TIMEOUT=5",
            "DB_STATEMENT_TIMEOUT_MS=15000",
            "DB_LOCK_TIMEOUT_MS=3000",
            f"AUTH_SECRET={auth_secret}",
            "MOONCEN_OPS_SINGLE_ACCOUNT_ONLY=true",
            "MOONCEN_OPS_LOGIN_ID=opsadmin",
            f"MOONCEN_OPS_PASSWORD_HASH={ops_password_hash}",
            "OPS_CRAWLER_API_DB_REQUIRED=false",
            "MOONCEN_CORS_ORIGINS=http://localhost:5174,http://127.0.0.1:5174,http://localhost:5175,http://127.0.0.1:5175",
            "MOONCEN_TRUSTED_HOSTS=localhost,127.0.0.1",
            "OAUTH_REDIRECT_URIS=http://localhost:5174/",
            "VITE_SITE_URL=http://localhost:5174",
            "LOG_LEVEL=INFO",
            "",
        )
    )
    credentials = "\n".join(
        (
            "MoonCen an2p local Ops Console",
            "URL: http://127.0.0.1:5175",
            "Login ID: opsadmin",
            f"Initial password: {ops_password}",
            "",
        )
    )

    _atomic_write(ENV_PATH, environment)
    _write_service_environments(environment)
    _atomic_write(CREDENTIALS_PATH, credentials)
    print("Created local an2p development credentials with mode 0600.")
    if args.show_paths:
        print(f"Environment: {ENV_PATH}")
        print(f"API environment: {API_ENV_PATH}")
        print(f"Status Agent environment: {STATUS_ENV_PATH}")
        print(f"Ops credentials: {CREDENTIALS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
