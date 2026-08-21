#!/usr/bin/python3 -I
"""Export the fixed an2p control bootstrap envelope over a non-TTY pipe.

The native production installer owns both inputs.  This helper has no command
line surface and never writes a secret to a terminal, log, or local output
file.  A root operator may invoke it only as the remote side of the reviewed
encrypted SSH pipe into an2p's root-only bootstrap staging file.
"""

from __future__ import annotations

import base64
import binascii
import os
import re
import stat
import sys
from pathlib import Path
from typing import Mapping, Sequence


DEPLOY_SECRETS_PATH = Path("/etc/mooncen/deploy-secrets.env")
TARGET_IDENTITY_PATH = Path("/etc/mooncen/an2p-dev-target-identity")
MAX_INPUT_BYTES = 64 * 1024
MAX_VALUE_BYTES = 4 * 1024
NAME_PATTERN = re.compile(r"\A[A-Z][A-Z0-9_]{1,63}\Z")
LOGIN_PATTERN = re.compile(r"\A[a-z_][a-z0-9_]{0,62}\Z")
SHA256_PATTERN = re.compile(r"\A[0-9a-f]{64}\Z")
ENVIRONMENT_VALUE_PATTERN = re.compile(r"\A[A-Za-z0-9._!@%+=,:/$-]+\Z")
OPS_PASSWORD_HASH_PATTERN = re.compile(
    r"\Apbkdf2_sha256\$([0-9]{6,7})\$[A-Za-z0-9_-]{16,128}\$[0-9a-f]{64}\Z"
)

DIRECT_SOURCE_NAMES = frozenset(
    {
        "BACKUP_AGE_RECIPIENT",
        "BACKUP_PORT",
        "DB_AI_USER",
        "DB_API_USER",
        "DB_APPLIER_USER",
        "DB_CHECK_USER",
        "DB_DEPLOYMENT_WORKER_USER",
        "DB_MIGRATOR_USER",
        "DB_NAME",
        "DB_USER",
        "GOOGLE_OAUTH_CLIENT_ID",
        "NAVER_OAUTH_CLIENT_ID",
    }
)
PAIRED_SOURCE_NAMES = frozenset(
    {
        "AUTH_SECRET",
        "DB_AI_PASSWORD",
        "DB_API_PASSWORD",
        "DB_BACKUP_PASSWORD",
        "DB_CHECK_PASSWORD",
        "DB_CRAWLER_PASSWORD",
        "DB_DEPLOYMENT_WORKER_PASSWORD",
        "DB_PASSWORD",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "KAKAO_MAPS_JAVASCRIPT_KEY",
        "KAKAO_MAPS_REST_API_KEY",
        "MOONCEN_BUG_REPORT_FROM",
        "MOONCEN_BUG_REPORT_TO",
        "MOONCEN_OPS_LOGIN_ID",
        "MOONCEN_OPS_PASSWORD_HASH",
        "MOONCEN_SERVER_MONITOR_TOKEN",
        "MOONCEN_SMTP_HOST",
        "MOONCEN_SMTP_PASSWORD",
        "MOONCEN_SMTP_PORT",
        "MOONCEN_SMTP_SECURITY",
        "MOONCEN_SMTP_USERNAME",
        "NAVER_OAUTH_CLIENT_SECRET",
        "OPS_CLOUDFLARE_ANALYTICS_TOKEN",
        "OPS_CLOUDFLARE_ANALYTICS_ZONE_ID",
        "PRIMARY_DB_PASSWORD",
    }
)
EXPECTED_SOURCE_NAMES = DIRECT_SOURCE_NAMES | PAIRED_SOURCE_NAMES | frozenset(
    f"{name}_B64" for name in PAIRED_SOURCE_NAMES
)
CONTROL_OUTPUT_NAMES = (
    "DB_API_PASSWORD",
    "DB_API_USER",
    "DB_DEPLOYMENT_WORKER_PASSWORD",
    "DB_DEPLOYMENT_WORKER_USER",
    "DB_NAME",
    "MOONCEN_OPS_LOGIN_ID",
    "MOONCEN_OPS_PASSWORD_HASH",
    "OPS_CONTAINER_DEV_TARGET_IDENTITY",
)


class ExportError(RuntimeError):
    """The protected production-to-an2p handoff contract was not satisfied."""


def _assert_root_private_file(path: Path, *, label: str) -> Path:
    try:
        metadata = path.lstat()
        parent_metadata = path.parent.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ExportError(f"{label} is unavailable") from exc
    if (
        path.is_symlink()
        or resolved != path
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_size <= 0
        or metadata.st_size > MAX_INPUT_BYTES
        or path.parent.is_symlink()
        or not stat.S_ISDIR(parent_metadata.st_mode)
        or parent_metadata.st_uid != 0
        or parent_metadata.st_gid != 0
        or stat.S_IMODE(parent_metadata.st_mode) & 0o022
    ):
        raise ExportError(f"{label} is not a protected root-owned file")
    return resolved


def _read_protected_text(path: Path, *, label: str) -> str:
    trusted = _assert_root_private_file(path, label=label)
    try:
        payload = trusted.read_bytes()
        text = payload.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ExportError(f"{label} is unreadable") from exc
    if (
        not payload
        or len(payload) > MAX_INPUT_BYTES
        or b"\x00" in payload
        or b"\r" in payload
        or not text.endswith("\n")
    ):
        raise ExportError(f"{label} encoding is invalid")
    return text


def _load_deploy_secrets(path: Path) -> dict[str, str]:
    text = _read_protected_text(path, label="production deploy secret source")
    values: dict[str, str] = {}
    for line in text.splitlines():
        name, separator, value = line.partition("=")
        if (
            not separator
            or NAME_PATTERN.fullmatch(name) is None
            or name in values
            or len(value.encode("utf-8")) > MAX_VALUE_BYTES
            or any(character in value for character in "\x00\r\n")
        ):
            raise ExportError("production deploy secret source is invalid")
        values[name] = value
    if set(values) != EXPECTED_SOURCE_NAMES:
        raise ExportError("production deploy secret source has an unexpected schema")
    return values


def _paired_value(values: Mapping[str, str], name: str) -> str:
    encoded = values[f"{name}_B64"]
    raw = values[name]
    if not encoded:
        raise ExportError(f"protected value is unavailable: {name}")
    try:
        encoded_bytes = encoded.encode("ascii")
        decoded_bytes = base64.b64decode(encoded_bytes, validate=True)
        if base64.b64encode(decoded_bytes) != encoded_bytes:
            raise ValueError
        decoded = decoded_bytes.decode("utf-8")
    except (binascii.Error, UnicodeError, ValueError) as exc:
        raise ExportError(f"protected base64 value is invalid: {name}") from exc
    if not decoded or decoded != raw or any(
        character in decoded for character in "\x00\r\n"
    ):
        raise ExportError(f"protected value companions do not match: {name}")
    return decoded


def _target_identity(path: Path) -> str:
    identity = _read_protected_text(
        path,
        label="container development target identity",
    )
    if SHA256_PATTERN.fullmatch(identity.removesuffix("\n")) is None:
        raise ExportError("container development target identity is invalid")
    return identity.removesuffix("\n")


def _validated_control_values(
    values: Mapping[str, str],
    *,
    target_identity: str,
) -> dict[str, str]:
    api_password = _paired_value(values, "DB_API_PASSWORD")
    worker_password = _paired_value(values, "DB_DEPLOYMENT_WORKER_PASSWORD")
    api_user = values["DB_API_USER"]
    worker_user = values["DB_DEPLOYMENT_WORKER_USER"]
    database_name = values["DB_NAME"]
    ops_login = _paired_value(values, "MOONCEN_OPS_LOGIN_ID")
    ops_password_hash = _paired_value(values, "MOONCEN_OPS_PASSWORD_HASH")

    if (
        LOGIN_PATTERN.fullmatch(api_user) is None
        or worker_user != "mooncen_deployment_worker_login"
        or api_user == worker_user
        or LOGIN_PATTERN.fullmatch(database_name) is None
        or len(api_password) < 16
        or len(worker_password) < 16
        or ENVIRONMENT_VALUE_PATTERN.fullmatch(api_password) is None
        or ENVIRONMENT_VALUE_PATTERN.fullmatch(worker_password) is None
        or ops_login != "opsadmin"
        or OPS_PASSWORD_HASH_PATTERN.fullmatch(ops_password_hash) is None
    ):
        raise ExportError("control bootstrap identities or credentials are invalid")
    rounds = int(OPS_PASSWORD_HASH_PATTERN.fullmatch(ops_password_hash).group(1))
    if not 310_000 <= rounds <= 2_000_000:
        raise ExportError("control bootstrap password hash policy is invalid")

    database_login_passwords = (
        _paired_value(values, "DB_PASSWORD"),
        api_password,
        _paired_value(values, "DB_CRAWLER_PASSWORD"),
        worker_password,
        _paired_value(values, "DB_AI_PASSWORD"),
        _paired_value(values, "PRIMARY_DB_PASSWORD"),
        _paired_value(values, "DB_BACKUP_PASSWORD"),
        _paired_value(values, "DB_CHECK_PASSWORD"),
    )
    if len(set(database_login_passwords)) != len(database_login_passwords):
        raise ExportError("database LOGIN credentials are not pairwise distinct")

    return {
        "DB_API_PASSWORD": api_password,
        "DB_API_USER": api_user,
        "DB_DEPLOYMENT_WORKER_PASSWORD": worker_password,
        "DB_DEPLOYMENT_WORKER_USER": worker_user,
        "DB_NAME": database_name,
        "MOONCEN_OPS_LOGIN_ID": ops_login,
        "MOONCEN_OPS_PASSWORD_HASH": ops_password_hash,
        "OPS_CONTAINER_DEV_TARGET_IDENTITY": target_identity,
    }


def render_control_envelope(secret_path: Path, identity_path: Path) -> bytes:
    values = _validated_control_values(
        _load_deploy_secrets(secret_path),
        target_identity=_target_identity(identity_path),
    )
    if tuple(values) != CONTROL_OUTPUT_NAMES:
        raise ExportError("control bootstrap output schema is invalid")
    rendered = "".join(f"{name}={values[name]}\n" for name in CONTROL_OUTPUT_NAMES)
    return rendered.encode("utf-8")


def _assert_protected_stdout() -> None:
    if sys.stdout.isatty():
        raise ExportError("refusing to write control secrets to a terminal")
    try:
        mode = os.fstat(sys.stdout.fileno()).st_mode
    except (AttributeError, OSError) as exc:
        raise ExportError("control secret stdout pipe is unavailable") from exc
    if not (stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)):
        raise ExportError("control secrets require a pipe or socket stdout")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if arguments:
        raise ExportError("this exporter accepts no arguments")
    if os.geteuid() != 0:
        raise ExportError("this exporter must run as root")
    _assert_protected_stdout()
    payload = render_control_envelope(DEPLOY_SECRETS_PATH, TARGET_IDENTITY_PATH)
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ExportError) as exc:
        print(f"an2p control secret export failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
