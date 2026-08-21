"""Create PostgreSQL SCRAM verifiers without sending plaintext to the server.

PostgreSQL accepts its stored ``SCRAM-SHA-256`` representation in ``ALTER
ROLE ... PASSWORD``.  Generating that representation on the trusted install
host prevents ``log_statement`` and error logs from recording the original
service password.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import re
import secrets
from pathlib import Path

from tools.preflight_distributed_crawler_control import (
    PreflightError,
    _protected_environment,
    _required,
)


SCRAM_ITERATIONS = 4096
SCRAM_SALT_BYTES = 16
FORBIDDEN_PASSWORD_SENTINELS = frozenset(
    {
        "replace_with_schema_admin_password",
        "replace_with_control_login_password",
        "replace_with_finalizer_login_password",
        "replace_with_worker_login_password",
        "change_me",
        "changeme",
    }
)
SYSTEMD_SAFE_PASSWORD = re.compile(r"^[A-Za-z0-9_-]+$")


class PasswordVerifierError(RuntimeError):
    pass


def validate_service_password(password: str, *, label: str = "service") -> None:
    """Reject ambiguous, public, or SASLprep-sensitive install passwords."""

    if not 32 <= len(password) <= 512:
        raise PasswordVerifierError(f"{label} password must contain 32-512 characters")
    if not SYSTEMD_SAFE_PASSWORD.fullmatch(password):
        raise PasswordVerifierError(f"{label} password must use unquoted URL-safe ASCII")
    lowered = password.lower()
    if lowered in FORBIDDEN_PASSWORD_SENTINELS or lowered.startswith("replace_with_"):
        raise PasswordVerifierError(f"{label} password is a public template placeholder")


def build_scram_sha_256_verifier(password: str, *, salt: bytes | None = None) -> str:
    validate_service_password(password)
    verifier_salt = secrets.token_bytes(SCRAM_SALT_BYTES) if salt is None else salt
    if len(verifier_salt) < SCRAM_SALT_BYTES:
        raise PasswordVerifierError("SCRAM salt must contain at least 16 bytes")
    salted_password = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("ascii"),
        verifier_salt,
        SCRAM_ITERATIONS,
    )
    client_key = hmac.new(salted_password, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted_password, b"Server Key", hashlib.sha256).digest()
    encoded_salt = base64.b64encode(verifier_salt).decode("ascii")
    encoded_stored_key = base64.b64encode(stored_key).decode("ascii")
    encoded_server_key = base64.b64encode(server_key).decode("ascii")
    return (
        f"SCRAM-SHA-256${SCRAM_ITERATIONS}:{encoded_salt}"
        f"${encoded_stored_key}:{encoded_server_key}"
    )


def _password_from_environment(
    environment_file: Path,
    password_key: str,
    matching_password_key: str | None,
) -> str:
    try:
        environment = _protected_environment(environment_file, owner_only=True)
        password = _required(environment, password_key)
        if matching_password_key is not None and password != _required(environment, matching_password_key):
            raise PasswordVerifierError(f"{password_key} and {matching_password_key} must match")
    except PreflightError as exc:
        raise PasswordVerifierError(str(exc)) from exc
    validate_service_password(password, label=password_key)
    return password


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a password or emit a client-side SCRAM verifier")
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--password-key", required=True)
    parser.add_argument("--matching-password-key")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--base64", action="store_true", help="Base64-wrap the verifier for psql transport")
    args = parser.parse_args(argv)
    try:
        password = _password_from_environment(
            args.env_file,
            args.password_key,
            args.matching_password_key,
        )
        if args.validate_only:
            print(json.dumps({"status": "ok", "password_key": args.password_key}, sort_keys=True))
            return 0
        verifier = build_scram_sha_256_verifier(password)
        if args.base64:
            verifier = base64.b64encode(verifier.encode("ascii")).decode("ascii")
        print(verifier)
        return 0
    except PasswordVerifierError as exc:
        parser.exit(78, f"password validation failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
