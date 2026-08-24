#!/usr/bin/env python3
"""Stage a new Ops Console password in the root-only an2p bootstrap envelope.

The tool never opens SSH, never reads a deployment key, and does not restart a
service. A root operator reviews the staged credential, regenerates the split
service environments, and runs the isolated control-plane installer.
"""

from __future__ import annotations

import argparse
import os
import secrets
import socket
import sys
from typing import Mapping, Sequence

try:
    from tools.generate_ops_password import encode_password
    from tools.prepare_an2p_ops_control import (
        DEFAULT_BOOTSTRAP_ROOT,
        DEFAULT_OPS_AUTH_SECRET,
        DEFAULT_SOURCE,
        PreparationError,
        _atomic_root_write,
        load_ops_auth_secret,
        load_protected_values,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script compatibility.
    from generate_ops_password import encode_password
    from prepare_an2p_ops_control import (
        DEFAULT_BOOTSTRAP_ROOT,
        DEFAULT_OPS_AUTH_SECRET,
        DEFAULT_SOURCE,
        PreparationError,
        _atomic_root_write,
        load_ops_auth_secret,
        load_protected_values,
    )


CREDENTIAL_PATH = DEFAULT_BOOTSTRAP_ROOT / "ops-credentials.txt"
ENVELOPE_ORDER = (
    "DB_API_PASSWORD",
    "DB_API_USER",
    "DB_DEPLOYMENT_WORKER_PASSWORD",
    "DB_DEPLOYMENT_WORKER_USER",
    "DB_NAME",
    "MOONCEN_OPS_LOGIN_ID",
    "MOONCEN_OPS_PASSWORD_HASH",
    "OPS_CONTAINER_DEV_TARGET_IDENTITY",
)


def _render_envelope(values: Mapping[str, str]) -> str:
    if frozenset(values) != frozenset(ENVELOPE_ORDER):
        raise PreparationError("bootstrap envelope field set changed during rotation")
    lines = []
    for name in ENVELOPE_ORDER:
        value = values[name]
        if not value or any(character in value for character in "\x00\r\n"):
            raise PreparationError("bootstrap envelope value is unsafe")
        lines.append(f"{name}={value}")
    return "\n".join((*lines, ""))


def stage_rotation() -> str:
    values = load_protected_values(DEFAULT_SOURCE)
    # The Ops JWT signing key is generated locally on an2p and is deliberately
    # outside the production control envelope.  Validate it before and after
    # rotation, but never copy, rewrite, or include it in either output.
    ops_auth_secret = load_ops_auth_secret(DEFAULT_OPS_AUTH_SECRET, values)
    password = secrets.token_urlsafe(48)
    values["MOONCEN_OPS_PASSWORD_HASH"] = encode_password(password)
    credential = "\n".join(
        (
            "MoonCen isolated an2p Ops Console",
            "URL: http://127.0.0.1:5175/",
            "Login ID: opsadmin",
            f"Password: {password}",
            "",
        )
    )
    # Publish the root-only human credential first.  If the later envelope
    # write fails, the running/staged password remains unchanged rather than
    # committing a hash whose plaintext was never durably recorded.
    _atomic_root_write(
        CREDENTIAL_PATH,
        credential,
    )
    if load_ops_auth_secret(DEFAULT_OPS_AUTH_SECRET, values) != ops_auth_secret:
        raise PreparationError("independent Ops signing secret changed during rotation")
    _atomic_root_write(DEFAULT_SOURCE, _render_envelope(values))
    return password


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    os.umask(0o077)
    if os.geteuid() != 0:
        raise PreparationError("run from a root console")
    if socket.gethostname().split(".", 1)[0] != "an2p":
        raise PreparationError("Ops password rotation is restricted to an2p")
    stage_rotation()
    print(f"Staged new Ops credentials at {CREDENTIAL_PATH}.")
    print(
        "Next: prepare_an2p_ops_control.py. For a pending first install, run "
        "the trusted finalize-control --pair <pending-pair> command; for an "
        "already finalized pair, run apply-ops-rotation --pair "
        "<active-finalized-pair>."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, PreparationError) as exc:
        print(f"an2p Ops password rotation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
