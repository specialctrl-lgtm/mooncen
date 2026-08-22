"""Create the PBKDF2-HMAC-SHA256 verifier for the standalone Ops login."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import secrets
import sys


ROUNDS = 600_000
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 256


def encode_password(password: str, *, salt: str | None = None) -> str:
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must be between {MIN_PASSWORD_LENGTH} and {MAX_PASSWORD_LENGTH} characters"
        )
    selected_salt = salt or secrets.token_urlsafe(24)
    if not 16 <= len(selected_salt) <= 128:
        raise ValueError("Salt must be between 16 and 128 characters")
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        selected_salt.encode("utf-8"),
        ROUNDS,
    ).hex()
    return f"pbkdf2_sha256${ROUNDS}${selected_salt}${digest}"


def read_password() -> str:
    password = getpass.getpass("Ops password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if not secrets.compare_digest(password, confirmation):
        raise ValueError("Passwords do not match")
    return password


def generated_password() -> str:
    return hashlib.sha256(secrets.token_bytes(32)).hexdigest()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a salted PBKDF2-HMAC-SHA256 hash for the opsadmin account."
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="generate a random 256-bit password instead of prompting",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        password = generated_password() if args.generate else read_password()
        encoded = encode_password(password)
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.", file=sys.stderr)
        return 130
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    print("MOONCEN_OPS_LOGIN_ID=opsadmin")
    if args.generate:
        print(f"OPS_INITIAL_PASSWORD={password}")
    print(f"MOONCEN_OPS_PASSWORD_HASH={encoded}")
    if args.generate:
        print("Store the password in a password manager; do not store it in .env.")
    else:
        print("Copy only MOONCEN_OPS_PASSWORD_HASH to .env.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
