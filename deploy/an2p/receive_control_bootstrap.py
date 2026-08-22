#!/usr/bin/python3 -I
"""Receive one exact an2p control-bootstrap artifact from a protected pipe.

The reviewed human operator runs this file as root on an2p after phase 1.  It
accepts no secret-bearing argument, rejects terminals and regular-file stdin,
binds every receive to the exact active/pending runtime pair, and publishes a
root-only bootstrap file atomically.  Existing identical bytes are idempotent;
different residue is never overwritten implicitly.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import socket
import stat
import subprocess
import sys
from pathlib import Path
from typing import BinaryIO, Callable, Mapping, Sequence


PAIR_ROOT = Path("/opt/mooncen-an2p-runtime")
STATE_ROOT = Path("/var/lib/mooncen-an2p-runtime")
BOOTSTRAP_ROOT = Path("/root/mooncen-an2p-bootstrap")
PENDING_PATH = STATE_ROOT / "pending-control-finalization.json"
OPS_AUTH_SECRET_PATH = BOOTSTRAP_ROOT / "ops-auth-secret"
MAX_INPUT_BYTES = 64 * 1024
PAIR_PATTERN = re.compile(
    r"\Aruntime-pair\.([0-9a-f]{40})\.([0-9a-f]{40})\.([0-9a-f]{64})\Z"
)
SHA256_PATTERN = re.compile(r"\A[0-9a-f]{64}\Z")
LOGIN_PATTERN = re.compile(r"\A[a-z_][a-z0-9_]{0,62}\Z")
OPS_PASSWORD_HASH_PATTERN = re.compile(
    r"\Apbkdf2_sha256\$([0-9]{6,7})\$[A-Za-z0-9_-]{16,128}\$[0-9a-f]{64}\Z"
)
SAFE_ENV_VALUE = re.compile(r"\A[A-Za-z0-9._!@%+=,:/$-]+\Z")
OPS_AUTH_SECRET_PATTERN = re.compile(rb"\A[A-Za-z0-9_-]{64}\n\Z")
CONTROL_NAMES = (
    "DB_API_PASSWORD",
    "DB_API_USER",
    "DB_DEPLOYMENT_WORKER_PASSWORD",
    "DB_DEPLOYMENT_WORKER_USER",
    "DB_NAME",
    "MOONCEN_OPS_LOGIN_ID",
    "MOONCEN_OPS_PASSWORD_HASH",
    "OPS_CONTAINER_DEV_TARGET_IDENTITY",
)
TRANSPORT_TEMPLATE_NAMES = {
    "deploy-ssh_config": "cloud-container-deploy.ssh_config",
    "status-ssh_config": "cloud-container-status.ssh_config",
    "db-ssh_config": "cloud-ops-db.ssh_config",
}
KNOWN_HOST_NAMES = frozenset(
    {"deploy-known_hosts", "status-known_hosts", "db-known_hosts"}
)
PRIVATE_KEY_NAMES = frozenset(
    {"deploy-id_ed25519", "status-id_ed25519", "db-id_ed25519"}
)
PRIVATE_KEY_COMMENTS = {
    "deploy-id_ed25519": "mooncen-an2p-container-deploy-20260819",
    "status-id_ed25519": "mooncen-an2p-container-status-20260819",
    "db-id_ed25519": "mooncen-an2p-ops-db-20260819",
}
IMPORT_NAMES = (
    "control-secrets.env",
    *TRANSPORT_TEMPLATE_NAMES,
    *sorted(KNOWN_HOST_NAMES),
    *sorted(PRIVATE_KEY_NAMES),
)


class ReceiveError(RuntimeError):
    """The protected bootstrap receive contract was not satisfied."""


def _canonical(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        + b"\n"
    )


def _assert_private_file(path: Path, *, uid: int = 0, gid: int = 0) -> bytes:
    try:
        metadata = path.lstat()
        payload = path.read_bytes()
    except OSError as exc:
        raise ReceiveError(f"protected file is unavailable: {path.name}") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != uid
        or metadata.st_gid != gid
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or not payload
        or len(payload) > MAX_INPUT_BYTES
    ):
        raise ReceiveError(f"protected file metadata is unsafe: {path.name}")
    return payload


def _pending(pair: str, *, path: Path = PENDING_PATH) -> dict[str, object]:
    payload = _assert_private_file(path)
    try:
        value = json.loads(payload.decode("ascii"))
    except (UnicodeError, ValueError) as exc:
        raise ReceiveError("pending finalization is invalid") from exc
    required = {
        "environment",
        "environment_sha256",
        "pair",
        "receipt_digest",
        "release_digest",
        "schema_version",
        "source_tree",
        "target",
        "target_identity",
    }
    if (
        not isinstance(value, dict)
        or set(value) != required
        or payload != _canonical(value)
        or value.get("schema_version") != 1
        or value.get("pair") != pair
        or value.get("environment") != "development"
        or value.get("target") != "an2p-dev"
        or any(
            SHA256_PATTERN.fullmatch(str(value.get(name, ""))) is None
            for name in (
                "environment_sha256",
                "receipt_digest",
                "release_digest",
                "target_identity",
            )
        )
        or re.fullmatch(r"[0-9a-f]{40}", str(value.get("source_tree", ""))) is None
    ):
        raise ReceiveError("pending finalization does not match the exact pair")
    return value


def _parse_control_envelope(payload: bytes, pending: Mapping[str, object]) -> None:
    if not payload or len(payload) > MAX_INPUT_BYTES or b"\x00" in payload or b"\r" in payload:
        raise ReceiveError("control envelope encoding is invalid")
    try:
        text = payload.decode("utf-8")
    except UnicodeError as exc:
        raise ReceiveError("control envelope encoding is invalid") from exc
    if not text.endswith("\n") or "\n\n" in text:
        raise ReceiveError("control envelope is not canonical")
    values: dict[str, str] = {}
    order: list[str] = []
    for line in text.splitlines():
        name, separator, value = line.partition("=")
        if (
            not separator
            or name in values
            or not value
            or len(value.encode("utf-8")) > 4096
            or SAFE_ENV_VALUE.fullmatch(value) is None
        ):
            raise ReceiveError("control envelope is invalid")
        order.append(name)
        values[name] = value
    if tuple(order) != CONTROL_NAMES:
        raise ReceiveError("control envelope schema or ordering is not exact")
    password_match = OPS_PASSWORD_HASH_PATTERN.fullmatch(
        values["MOONCEN_OPS_PASSWORD_HASH"]
    )
    if (
        LOGIN_PATTERN.fullmatch(values["DB_API_USER"]) is None
        or values["DB_DEPLOYMENT_WORKER_USER"]
        != "mooncen_deployment_worker_login"
        or values["DB_API_USER"] == values["DB_DEPLOYMENT_WORKER_USER"]
        or LOGIN_PATTERN.fullmatch(values["DB_NAME"]) is None
        or len(values["DB_API_PASSWORD"]) < 16
        or len(values["DB_DEPLOYMENT_WORKER_PASSWORD"]) < 16
        or values["DB_API_PASSWORD"] == values["DB_DEPLOYMENT_WORKER_PASSWORD"]
        or values["MOONCEN_OPS_LOGIN_ID"] != "opsadmin"
        or password_match is None
        or not 310_000 <= int(password_match.group(1)) <= 2_000_000
        or values["OPS_CONTAINER_DEV_TARGET_IDENTITY"]
        != pending["target_identity"]
    ):
        raise ReceiveError("control envelope identities do not match phase 1")


def _template_payload(pair: str, name: str, *, pair_root: Path = PAIR_ROOT) -> bytes:
    control = pair_root / "releases" / pair / "control"
    if name in TRANSPORT_TEMPLATE_NAMES:
        template = control / "deploy/an2p/local" / TRANSPORT_TEMPLATE_NAMES[name]
    elif name in KNOWN_HOST_NAMES:
        template = control / "deploy/an2p/local/cloud-deploy.known_hosts"
    else:
        raise ReceiveError("bootstrap artifact has no reviewed template")
    try:
        metadata = template.lstat()
        payload = template.read_bytes()
    except OSError as exc:
        raise ReceiveError("reviewed transport template is unavailable") from exc
    if (
        template.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o644
        or not payload
        or len(payload) > MAX_INPUT_BYTES
    ):
        raise ReceiveError("reviewed transport template is unsafe")
    return payload


def _public_key_blob(path: Path, *, expected_comment: str) -> bytes:
    try:
        result = subprocess.run(
            ("/usr/bin/ssh-keygen", "-y", "-f", str(path)),
            cwd="/",
            env={"HOME": "/root", "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReceiveError("private key validation failed") from exc
    try:
        public = result.stdout.decode("ascii").strip().split()
    except UnicodeError as exc:
        raise ReceiveError("private key validation failed") from exc
    if (
        result.returncode != 0
        or result.stderr
        or len(public) != 3
        or public[0] != "ssh-ed25519"
        or re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", public[1]) is None
        or public[2] != expected_comment
    ):
        raise ReceiveError("bootstrap private key is not an Ed25519 key")
    return public[1].encode("ascii")


def _validate_private_key(path: Path) -> None:
    try:
        expected_comment = PRIVATE_KEY_COMMENTS[path.name]
    except KeyError as exc:
        raise ReceiveError("bootstrap private key name is invalid") from exc
    _public_key_blob(path, expected_comment=expected_comment)


def _ensure_bootstrap_root(root: Path = BOOTSTRAP_ROOT) -> None:
    if not root.exists() and not root.is_symlink():
        try:
            root.mkdir(mode=0o700)
            os.chown(root, 0, 0)
            parent = os.open(root.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        except OSError as exc:
            raise ReceiveError("bootstrap directory could not be created") from exc
    try:
        metadata = root.lstat()
    except OSError as exc:
        raise ReceiveError("bootstrap directory is unavailable") from exc
    if (
        root.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ReceiveError("bootstrap directory is unsafe")


def _atomic_private_install(
    destination: Path,
    payload: bytes,
    *,
    validator: Callable[[Path], None] | None = None,
) -> bool:
    if destination.exists() or destination.is_symlink():
        existing = _assert_private_file(destination)
        if existing != payload:
            raise ReceiveError(f"different bootstrap residue exists: {destination.name}")
        if validator is not None:
            validator(destination)
        return False
    stage = destination.parent / f".{destination.name}.{os.getpid()}.tmp"
    descriptor = -1
    try:
        descriptor = os.open(
            stage,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        os.fchown(descriptor, 0, 0)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if validator is not None:
            validator(stage)
        if destination.exists() or destination.is_symlink():
            raise ReceiveError(f"bootstrap destination raced: {destination.name}")
        os.replace(stage, destination)
        directory = os.open(
            destination.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            stage.unlink()
        except FileNotFoundError:
            pass
    return True


def _ensure_ops_auth_secret(root: Path = BOOTSTRAP_ROOT) -> None:
    path = root / OPS_AUTH_SECRET_PATH.name
    if path.exists() or path.is_symlink():
        payload = _assert_private_file(path)
        if OPS_AUTH_SECRET_PATTERN.fullmatch(payload) is None:
            raise ReceiveError("independent Ops authentication secret is invalid")
        return
    value = secrets.token_urlsafe(48)
    payload = f"{value}\n".encode("ascii")
    if OPS_AUTH_SECRET_PATTERN.fullmatch(payload) is None:
        raise ReceiveError("independent Ops authentication secret generation failed")
    _atomic_private_install(path, payload)


def _validate_key_distinctness(
    name: str,
    path: Path,
    *,
    root: Path = BOOTSTRAP_ROOT,
) -> None:
    observed = _public_key_blob(
        path,
        expected_comment=PRIVATE_KEY_COMMENTS[name],
    )
    for other in PRIVATE_KEY_NAMES - {name}:
        other_path = root / other
        if not other_path.exists() and not other_path.is_symlink():
            continue
        _assert_private_file(other_path)
        if _public_key_blob(
            other_path,
            expected_comment=PRIVATE_KEY_COMMENTS[other],
        ) == observed:
            raise ReceiveError("control-plane private keys must be distinct")


def receive(
    pair: str,
    name: str,
    payload: bytes,
    *,
    pair_root: Path = PAIR_ROOT,
    pending_path: Path = PENDING_PATH,
    bootstrap_root: Path = BOOTSTRAP_ROOT,
) -> bool:
    match = PAIR_PATTERN.fullmatch(pair)
    if match is None:
        raise ReceiveError("runtime pair name is invalid")
    if name not in IMPORT_NAMES:
        raise ReceiveError("bootstrap artifact name is invalid")
    if not payload or len(payload) > MAX_INPUT_BYTES:
        raise ReceiveError("bootstrap input size is invalid")
    pending = _pending(pair, path=pending_path)
    if pending["source_tree"] != match.group(2):
        raise ReceiveError("pending source tree does not match the pair")
    _ensure_bootstrap_root(bootstrap_root)
    validator: Callable[[Path], None] | None = None
    if name == "control-secrets.env":
        _parse_control_envelope(payload, pending)
        _ensure_ops_auth_secret(bootstrap_root)
    elif name in PRIVATE_KEY_NAMES:
        def validate_distinct_key(path: Path) -> None:
            _validate_key_distinctness(name, path, root=bootstrap_root)

        validator = validate_distinct_key
    elif payload != _template_payload(pair, name, pair_root=pair_root):
        raise ReceiveError("bootstrap transport bytes differ from the reviewed pair")
    return _atomic_private_install(
        bootstrap_root / name,
        payload,
        validator=validator,
    )


def _read_protected_stdin(stream: BinaryIO) -> bytes:
    if stream.isatty():
        raise ReceiveError("refusing bootstrap input from a terminal")
    try:
        mode = os.fstat(stream.fileno()).st_mode
    except (AttributeError, OSError) as exc:
        raise ReceiveError("bootstrap stdin pipe is unavailable") from exc
    if not (stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)):
        raise ReceiveError("bootstrap input requires a pipe or socket")
    payload = stream.read(MAX_INPUT_BYTES + 1)
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_INPUT_BYTES:
        raise ReceiveError("bootstrap input size is invalid")
    return payload


def _assert_exact_runtime(pair: str) -> None:
    current = PAIR_ROOT / "current"
    expected_script = (
        PAIR_ROOT
        / "releases"
        / pair
        / "control/deploy/an2p/receive_control_bootstrap.py"
    )
    try:
        current_metadata = current.lstat()
        script = Path(__file__)
        script_metadata = script.lstat()
    except OSError as exc:
        raise ReceiveError("active immutable runtime is unavailable") from exc
    if (
        not current.is_symlink()
        or current_metadata.st_uid != 0
        or current_metadata.st_gid != 0
        or os.readlink(current) != f"releases/{pair}"
        or script.is_symlink()
        or not stat.S_ISREG(script_metadata.st_mode)
        or script.resolve(strict=True) != expected_script
    ):
        raise ReceiveError("execute only the exact active immutable pair receiver")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 4 or arguments[0] != "--pair" or arguments[2] != "--name":
        raise ReceiveError("usage: receive_control_bootstrap.py --pair <pair> --name <artifact>")
    pair, name = arguments[1], arguments[3]
    if os.geteuid() != 0:
        raise ReceiveError("bootstrap receiver must run as root")
    if socket.gethostname().split(".", 1)[0] != "an2p":
        raise ReceiveError("bootstrap receiver may run only on an2p")
    _assert_exact_runtime(pair)
    created = receive(pair, name, _read_protected_stdin(sys.stdin.buffer))
    result = {
        "created": created,
        "name": name,
        "pair": pair,
        "schema_version": 1,
    }
    sys.stdout.buffer.write(_canonical(result))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ReceiveError, subprocess.SubprocessError) as exc:
        print(f"an2p control bootstrap receive failed: {exc}", file=sys.stderr)
        raise SystemExit(78) from None
