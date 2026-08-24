#!/usr/bin/env python3
"""Split a human-staged root-only secret envelope for isolated an2p services.

This tool intentionally has no SSH client and never reads a service key.  A
trusted operator obtains/rotates the database and target values through the
interactive Tailscale maintenance path, installs ``control-secrets.env`` as
root:root 0600, and runs this command from a root console.  The Ops signing
secret is an independent, locally generated root-only input; it must never be
exported by the production application.  Outputs remain in the root-only
bootstrap directory until the isolated service installer copies them as
root-owned, service-group-readable EnvironmentFiles.
"""

from __future__ import annotations

import argparse
import os
import re
import socket
import stat
import sys
import tempfile
from pathlib import Path
from typing import Mapping, Sequence


DEFAULT_BOOTSTRAP_ROOT = Path("/root/mooncen-an2p-bootstrap")
DEFAULT_SOURCE = DEFAULT_BOOTSTRAP_ROOT / "control-secrets.env"
DEFAULT_OPS_AUTH_SECRET = DEFAULT_BOOTSTRAP_ROOT / "ops-auth-secret"
DEFAULT_API_ENV = DEFAULT_BOOTSTRAP_ROOT / "ops-api.env"
DEFAULT_WORKER_ENV = DEFAULT_BOOTSTRAP_ROOT / "deployment-worker.env"
DEFAULT_RELEASE_ROOT = Path("/var/lib/mooncen-deployment-worker/releases")
NAME_PATTERN = re.compile(r"\A[A-Z][A-Z0-9_]{1,63}\Z")
LOGIN_PATTERN = re.compile(r"\A[a-z_][a-z0-9_]{0,62}\Z")
SHA256_PATTERN = re.compile(r"\A[0-9a-f]{64}\Z")
OPS_AUTH_SECRET_PATTERN = re.compile(r"\A[A-Za-z0-9_-]{64}\Z")
REQUIRED_NAMES = frozenset(
    {
        "DB_API_PASSWORD",
        "DB_DEPLOYMENT_WORKER_PASSWORD",
        "DB_NAME",
        "MOONCEN_OPS_PASSWORD_HASH",
        "OPS_CONTAINER_DEV_TARGET_IDENTITY",
    }
)
OPTIONAL_NAMES = frozenset(
    {
        "DB_API_USER",
        "DB_DEPLOYMENT_WORKER_USER",
        "MOONCEN_OPS_LOGIN_ID",
    }
)


class PreparationError(RuntimeError):
    """Raised when the protected local bootstrap envelope is unsafe."""


def _assert_root_private_file(path: Path) -> Path:
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise PreparationError(f"protected bootstrap file is unavailable: {path}") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_size <= 0
        or metadata.st_size > 64 * 1024
    ):
        raise PreparationError("bootstrap secret source must be root:root mode 0600")
    return resolved


def load_protected_values(path: Path) -> dict[str, str]:
    trusted = _assert_root_private_file(path)
    try:
        raw = trusted.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise PreparationError("bootstrap secret source is unreadable") from exc
    if "\x00" in text or "\r" in text or not text.endswith("\n"):
        raise PreparationError("bootstrap secret source encoding is invalid")
    allowed = REQUIRED_NAMES | OPTIONAL_NAMES
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if (
            not separator
            or name not in allowed
            or name in values
            or NAME_PATTERN.fullmatch(name) is None
            or not value
            or len(value) > 4_096
            or any(character in value for character in "\x00\r\n")
        ):
            raise PreparationError("bootstrap secret envelope is invalid")
        values[name] = value
    if REQUIRED_NAMES.difference(values):
        raise PreparationError("bootstrap secret envelope is incomplete")
    values.setdefault("DB_API_USER", "mooncen_api_login")
    values.setdefault("DB_DEPLOYMENT_WORKER_USER", "mooncen_deployment_worker_login")
    values.setdefault("MOONCEN_OPS_LOGIN_ID", "opsadmin")
    if (
        LOGIN_PATTERN.fullmatch(values["DB_API_USER"]) is None
        or values["DB_DEPLOYMENT_WORKER_USER"]
        != "mooncen_deployment_worker_login"
        or values["DB_API_USER"] == values["DB_DEPLOYMENT_WORKER_USER"]
        or values["MOONCEN_OPS_LOGIN_ID"] != "opsadmin"
        or SHA256_PATTERN.fullmatch(values["OPS_CONTAINER_DEV_TARGET_IDENTITY"])
        is None
        or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,62}", values["DB_NAME"])
        is None
    ):
        raise PreparationError("bootstrap identities do not match the fixed contract")
    return values


def _validate_ops_auth_secret(
    secret: str,
    protected_values: Mapping[str, str],
) -> str:
    if OPS_AUTH_SECRET_PATTERN.fullmatch(secret) is None:
        raise PreparationError(
            "local Ops signing secret must be exactly 64 URL-safe characters"
        )
    if any(secret == value for value in protected_values.values() if value):
        raise PreparationError(
            "local Ops signing secret must be independent of production values"
        )
    return secret


def load_ops_auth_secret(
    path: Path,
    protected_values: Mapping[str, str],
) -> str:
    """Load the independent an2p-only signing secret from a root-private file."""

    trusted = _assert_root_private_file(path)
    try:
        raw = trusted.read_bytes()
        text = raw.decode("ascii")
    except (OSError, UnicodeError) as exc:
        raise PreparationError("local Ops signing secret is unreadable") from exc
    if text.count("\n") != 1 or not text.endswith("\n") or "\r" in text:
        raise PreparationError("local Ops signing secret encoding is invalid")
    return _validate_ops_auth_secret(text[:-1], protected_values)


def _environment(lines: Sequence[tuple[str, str]]) -> str:
    rendered = ["# Generated locally by prepare_an2p_ops_control.py; never commit."]
    for name, value in lines:
        if NAME_PATTERN.fullmatch(name) is None or not value or any(
            character in value for character in "\x00\r\n"
        ):
            raise PreparationError(f"invalid generated environment value: {name}")
        rendered.append(f"{name}={value}")
    return "\n".join((*rendered, ""))


def render_environments(
    values: Mapping[str, str],
    *,
    ops_auth_secret: str,
) -> tuple[str, str]:
    ops_auth_secret = _validate_ops_auth_secret(ops_auth_secret, values)
    common = (
        ("ENVIRONMENT", "production"),
        ("DB_HOST", "127.0.0.1"),
        ("DB_PORT", "15432"),
        ("DB_NAME", values["DB_NAME"]),
        ("DB_SSLMODE", "require"),
        ("DB_CONNECT_TIMEOUT", "5"),
        ("DB_STATEMENT_TIMEOUT_MS", "15000"),
        ("DB_LOCK_TIMEOUT_MS", "3000"),
    )
    api = _environment(
        (
            *common,
            ("MOONCEN_API_PROFILE", "ops"),
            ("MOONCEN_AUTH_COOKIE_PREFIX", "mooncen_ops"),
            ("MOONCEN_AUTH_COOKIE_SECURE", "false"),
            ("MOONCEN_LOCAL_LOOPBACK_OPS_HTTP", "true"),
            ("DB_OWNER_USER", "mooncen_admin"),
            ("DB_API_USER", values["DB_API_USER"]),
            ("DB_API_PASSWORD", values["DB_API_PASSWORD"]),
            ("AUTH_SECRET", ops_auth_secret),
            ("MOONCEN_OPS_LOGIN_ID", values["MOONCEN_OPS_LOGIN_ID"]),
            ("MOONCEN_OPS_PASSWORD_HASH", values["MOONCEN_OPS_PASSWORD_HASH"]),
            ("MOONCEN_OPS_SINGLE_ACCOUNT_ONLY", "true"),
            ("OPS_LOCAL_CRAWLER_RUNTIME_ENABLED", "false"),
            ("OPS_CRAWLER_API_DB_REQUIRED", "false"),
            ("OPS_DEPLOY_REQUIRED_AGENT_HOSTNAME", "an2p"),
            (
                "OPS_CONTAINER_DEV_TARGET_IDENTITY",
                values["OPS_CONTAINER_DEV_TARGET_IDENTITY"],
            ),
            ("MOONCEN_TRUSTED_HOSTS", "localhost,127.0.0.1,[::1]"),
            ("LOG_LEVEL", "INFO"),
        )
    )
    worker = _environment(
        (
            *common,
            ("DB_OWNER_USER", "mooncen_admin"),
            ("OPS_DEPLOY_QUEUE_DB_HOST", "127.0.0.1"),
            ("OPS_DEPLOY_QUEUE_DB_PORT", "15432"),
            ("OPS_DEPLOY_QUEUE_DB_NAME", values["DB_NAME"]),
            ("OPS_DEPLOY_QUEUE_DB_USER", values["DB_DEPLOYMENT_WORKER_USER"]),
            (
                "OPS_DEPLOY_QUEUE_DB_PASSWORD",
                values["DB_DEPLOYMENT_WORKER_PASSWORD"],
            ),
            ("OPS_DEPLOY_AGENT_EXCLUSIVE", "true"),
            ("OPS_DEPLOY_REQUIRED_AGENT_HOSTNAME", "an2p"),
            (
                "OPS_CONTAINER_DEV_TARGET_IDENTITY",
                values["OPS_CONTAINER_DEV_TARGET_IDENTITY"],
            ),
            ("OPS_CONTAINER_RELEASE_ROOT", str(DEFAULT_RELEASE_ROOT)),
            ("OPS_LOCAL_CRAWLER_RUNTIME_ENABLED", "false"),
            ("LOG_LEVEL", "INFO"),
        )
    )
    return api, worker


def _atomic_root_write(path: Path, content: str) -> None:
    parent = path.parent
    try:
        metadata = parent.lstat()
    except OSError as exc:
        raise PreparationError("bootstrap output directory is unavailable") from exc
    if (
        parent.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise PreparationError("bootstrap output directory must be root:root mode 0700")
    if path.exists() or path.is_symlink():
        _assert_root_private_file(path)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument(
        "--ops-auth-secret",
        type=Path,
        default=DEFAULT_OPS_AUTH_SECRET,
    )
    parser.add_argument("--api-output", type=Path, default=DEFAULT_API_ENV)
    parser.add_argument("--worker-output", type=Path, default=DEFAULT_WORKER_ENV)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    os.umask(0o077)
    if os.geteuid() != 0:
        raise PreparationError("run from a root console")
    if socket.gethostname().split(".", 1)[0] != "an2p":
        raise PreparationError("this bootstrap may run only on an2p")
    arguments = _parser().parse_args(argv)
    values = load_protected_values(arguments.source)
    ops_auth_secret = load_ops_auth_secret(arguments.ops_auth_secret, values)
    api, worker = render_environments(values, ops_auth_secret=ops_auth_secret)
    _atomic_root_write(arguments.api_output, api)
    _atomic_root_write(arguments.worker_output, worker)
    print("Prepared isolated an2p environments without reading or printing a service key.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, PreparationError) as exc:
        print(f"an2p Ops control preparation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
