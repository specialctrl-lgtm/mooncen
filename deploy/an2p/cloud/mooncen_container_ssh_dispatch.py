#!/usr/bin/env python3
"""Fail-closed forced-command dispatcher for the container control endpoint.

The SSH daemon runs this file through ``ForceCommand``.  It never starts a
shell: a bounded ``SSH_ORIGINAL_COMMAND`` is parsed into an exact argv and is
then replaced with one of the fixed executables below.  The mutation account
may upload the four canonical release files and call reviewed controller
verbs.  The status account may only prove controller presence or read the two
public controller envelopes needed by the Ops API.
"""

from __future__ import annotations

import os
import pwd
import re
import shlex
import sys
from dataclasses import dataclass


DEPLOY_USER = "mooncen_container_deploy"
STATUS_USER = "mooncen_container_status"
CONTROLLER = "/usr/local/libexec/mooncen-container-release"
INGRESS_ROOT = "/var/lib/mooncen-container-ingress"
INGRESS_HELPER = "/usr/local/libexec/mooncen-container-ingress"
SOURCE_TREE = re.compile(r"\A[0-9a-f]{40}\Z")
GENERATION = re.compile(r"\A[0-9]{10}\Z")
DIGEST = re.compile(r"\A[0-9a-f]{64}\Z")
CLAIM_JOB = re.compile(r"\A[0-9a-f]{32}\Z")
CLAIM_EPOCH = re.compile(r"\A[0-9]{20}\Z")
CLAIM_TOKEN = re.compile(r"\A[0-9a-f]{32}\Z")
INGRESS_NAMES = (
    "compose.production.yaml",
    "images.tar",
    "release.json",
    "validation.json",
)
MAX_ORIGINAL_COMMAND_BYTES = 2_048


class DispatchError(ValueError):
    """Raised when the forced SSH request is outside the exact allowlist."""


@dataclass(frozen=True)
class Dispatch:
    executable: str
    argv: tuple[str, ...]


def _parse(command: str) -> tuple[str, ...]:
    if (
        not command
        or "\x00" in command
        or "\r" in command
        or "\n" in command
        or len(command.encode("utf-8")) > MAX_ORIGINAL_COMMAND_BYTES
    ):
        raise DispatchError("remote command is missing or not bounded")
    try:
        argv = tuple(shlex.split(command, posix=True))
    except ValueError as exc:
        raise DispatchError("remote command quoting is invalid") from exc
    if not argv or any(not value for value in argv):
        raise DispatchError("remote command argv is invalid")
    return argv


def _controller_dispatch(argv: tuple[str, ...], *, mutation: bool) -> Dispatch | None:
    prefix = ("/usr/bin/sudo", "-n", "--", CONTROLLER)
    if argv[: len(prefix)] != prefix:
        return None
    arguments = argv[len(prefix) :]
    if arguments in (("status",), ("target-identity",)):
        return Dispatch("/usr/bin/sudo", argv)
    if not mutation or not arguments:
        raise DispatchError("controller mutation is not allowed for this account")
    action, *values = arguments
    claim_valid = (
        len(values) >= 3
        and CLAIM_JOB.fullmatch(values[-3]) is not None
        and CLAIM_EPOCH.fullmatch(values[-2]) is not None
        and CLAIM_TOKEN.fullmatch(values[-1]) is not None
    )
    if action in {"lease-bind", "lease-release"}:
        valid = len(values) == 3 and claim_valid
    elif action in {"stage", "load-images", "preflight"}:
        valid = (
            len(values) == 4
            and SOURCE_TREE.fullmatch(values[0]) is not None
            and claim_valid
        )
    elif action == "promote":
        valid = (
            len(values) == 8
            and SOURCE_TREE.fullmatch(values[0]) is not None
            and GENERATION.fullmatch(values[1]) is not None
            and all(DIGEST.fullmatch(value) is not None for value in values[2:5])
            and claim_valid
        )
    elif action in {"rollback", "rollback-native"}:
        valid = (
            len(values) == 7
            and GENERATION.fullmatch(values[0]) is not None
            and all(DIGEST.fullmatch(value) is not None for value in values[1:4])
            and claim_valid
        )
    else:
        valid = False
    if not valid:
        raise DispatchError("controller argv is outside the exact allowlist")
    return Dispatch("/usr/bin/sudo", argv)


def _ingress_dispatch(argv: tuple[str, ...]) -> Dispatch | None:
    if argv[:1] != (INGRESS_HELPER,):
        return None
    arguments = argv[1:]
    if (
        len(arguments) == 2
        and arguments[0] in {"prepare", "abort"}
        and SOURCE_TREE.fullmatch(arguments[1]) is not None
    ):
        return Dispatch(INGRESS_HELPER, argv)
    if (
        len(arguments) == 5
        and arguments[0] == "upload"
        and SOURCE_TREE.fullmatch(arguments[1]) is not None
        and arguments[2] in INGRESS_NAMES
        and re.fullmatch(r"(?:0|[1-9][0-9]{0,9})", arguments[3]) is not None
        and DIGEST.fullmatch(arguments[4]) is not None
    ):
        return Dispatch(INGRESS_HELPER, argv)
    return None


def select_dispatch(command: str, *, account: str) -> Dispatch:
    if account not in {DEPLOY_USER, STATUS_USER}:
        raise DispatchError("unexpected SSH account")
    argv = _parse(command)
    if argv == ("/usr/bin/test", "-e", CONTROLLER):
        return Dispatch("/usr/bin/test", argv)
    controller = _controller_dispatch(argv, mutation=account == DEPLOY_USER)
    if controller is not None:
        return controller
    if account == DEPLOY_USER:
        ingress = _ingress_dispatch(argv)
        if ingress is not None:
            return ingress
    raise DispatchError("remote command is outside the exact allowlist")


def main() -> int:
    os.umask(0o077)
    if len(sys.argv) != 1:
        raise DispatchError("dispatcher accepts no arguments")
    account = pwd.getpwuid(os.getuid()).pw_name
    dispatch = select_dispatch(os.environ.get("SSH_ORIGINAL_COMMAND", ""), account=account)
    clean_environment = {
        "HOME": pwd.getpwuid(os.getuid()).pw_dir,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    }
    os.execve(dispatch.executable, list(dispatch.argv), clean_environment)
    raise AssertionError("execve returned")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DispatchError, KeyError, OSError) as exc:
        print(f"container SSH request rejected: {exc}", file=sys.stderr)
        raise SystemExit(64) from None
