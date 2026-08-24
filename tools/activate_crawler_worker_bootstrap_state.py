"""Materialize and atomically publish one verified crawler-worker bootstrap tree.

This module is executed only from a root-owned candidate whose complete source
tree was authenticated by the fixed shell verifier.  It installs from an
offline digest-complete wheelhouse, journals every pointer transition, and
keeps at most three immutable versioned releases.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
from pathlib import Path
from typing import Any, Final


RELEASE_ID: Final = re.compile(r"[0-9a-f]{32}")
DIGEST: Final = re.compile(r"[0-9a-f]{64}")
LOCK_RECORD: Final = re.compile(
    r"(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[A-Za-z0-9_.+-]+) "
    r"--hash=sha256:(?P<digest>[0-9a-f]{64})"
)
TRANSACTION_FIELDS: Final = {
    "format",
    "release_id",
    "phase",
    "previous_target",
    "new_target",
    "commit",
    "archive_sha256",
    "tree_sha256",
}
MAX_RELEASES: Final = 3
SMOKE_IMPORTS: Final = (
    "bs4",
    "defusedxml",
    "dotenv",
    "geopy",
    "lxml",
    "pandas",
    "psycopg2",
    "requests",
    "selenium",
    "sqlalchemy",
    "yaml",
    "ops_agent.crawler_release_agent",
    "ops_agent.crawler_release_reporter",
    "ops_agent.crawler_worker",
    "tools.preflight_distributed_crawler_worker_host",
)


class ActivationError(RuntimeError):
    """The root-owned worker release state cannot advance safely."""


class ActivationInterrupted(ActivationError):
    """A termination signal interrupted a journaled activation."""


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def _read_transaction(path: Path) -> dict[str, str]:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_mode & 0o077:
        raise ActivationError("activation journal is not a private single-link regular file")
    payload = json.loads(path.read_text(encoding="ascii"))
    if type(payload) is not dict or set(payload) != TRANSACTION_FIELDS:
        raise ActivationError("activation journal contract is invalid")
    values = {key: str(value) for key, value in payload.items()}
    if (
        values["format"] != "mooncen-worker-bootstrap-transaction-v1"
        or not RELEASE_ID.fullmatch(values["release_id"])
        or values["phase"] not in {"prepared", "published", "activated"}
        or values["new_target"] != f"releases/{values['release_id']}"
        or values["previous_target"]
        and not re.fullmatch(r"releases/[0-9a-f]{32}", values["previous_target"])
        or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", values["commit"])
        or not DIGEST.fullmatch(values["archive_sha256"])
        or not DIGEST.fullmatch(values["tree_sha256"])
    ):
        raise ActivationError("activation journal identity is invalid")
    return values


def _safe_tree(path: Path, expected_parent: Path) -> None:
    if path.parent != expected_parent or not RELEASE_ID.fullmatch(path.name):
        raise ActivationError("release transaction path escapes its fixed root")
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink() or metadata.st_uid != 0:
        raise ActivationError("release transaction tree is not a root-owned directory")


def _remove_tree(path: Path, expected_parent: Path) -> None:
    _safe_tree(path, expected_parent)
    info = path / ".deploy-info"
    if info.is_file() and not info.is_symlink():
        try:
            subprocess.run(
                ["/usr/bin/chattr", "-i", "--", str(info)],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            # Immutable-bit cleanup is best effort here.  shutil.rmtree will
            # either succeed or preserve the original recovery evidence.
            pass
    shutil.rmtree(path)
    _fsync_directory(expected_parent)


def _current_target(base: Path, *, required: bool = False) -> str:
    current = base / "current"
    if not current.exists() and not current.is_symlink():
        if required:
            raise ActivationError("active worker bootstrap pointer is absent")
        return ""
    metadata = current.lstat()
    if not stat.S_ISLNK(metadata.st_mode):
        raise ActivationError("active worker bootstrap pointer is not a symbolic link")
    target = os.readlink(current)
    if not re.fullmatch(r"releases/[0-9a-f]{32}", target):
        raise ActivationError("active worker bootstrap target is invalid")
    resolved = (base / target).resolve(strict=True)
    releases = (base / "releases").resolve(strict=True)
    if resolved.parent != releases:
        raise ActivationError("active worker bootstrap target escapes releases")
    return target


def _switch_current(base: Path, target: str, release_id: str) -> None:
    if not re.fullmatch(r"releases/[0-9a-f]{32}", target):
        raise ActivationError("new worker bootstrap target is invalid")
    temporary = base / f".current-{release_id}"
    if temporary.exists() or temporary.is_symlink():
        raise ActivationError("temporary worker bootstrap pointer already exists")
    os.symlink(target, temporary)
    _fsync_directory(base)
    os.replace(temporary, base / "current")
    _fsync_directory(base)
    if _current_target(base, required=True) != target:
        raise ActivationError("atomic worker bootstrap pointer verification failed")


def _validate_published_release(path: Path, transaction: dict[str, str]) -> None:
    _safe_tree(path, path.parent)
    info = path / ".deploy-info"
    metadata = info.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or stat.S_IMODE(metadata.st_mode) != 0o400:
        raise ActivationError("published release provenance is unsafe")
    values: dict[str, str] = {}
    for line in info.read_text(encoding="ascii").splitlines():
        if "=" not in line:
            raise ActivationError("published release provenance is invalid")
        key, value = line.split("=", 1)
        if key in values:
            raise ActivationError("published release provenance contains duplicates")
        values[key] = value
    expected = {
        "RELEASE_ID": transaction["release_id"],
        "DEPLOY_COMMIT": transaction["commit"],
        "DEPLOY_ARCHIVE_SHA256": transaction["archive_sha256"],
        "DEPLOY_TREE_SHA256": transaction["tree_sha256"],
    }
    if any(values.get(key) != value for key, value in expected.items()):
        raise ActivationError("published release provenance differs from its journal")


def recover_transactions(base: Path, *, current_candidate: str = "") -> None:
    transactions = base / ".transactions"
    staging = base / ".staging"
    releases = base / "releases"
    journals = sorted(transactions.glob("*.json"))
    if len(journals) > 1:
        raise ActivationError("multiple activation journals require manual review")
    if journals:
        transaction = _read_transaction(journals[0])
        release_id = transaction["release_id"]
        candidate = staging / release_id
        published = releases / release_id
        if transaction["phase"] == "prepared":
            candidate_exists = candidate.exists() or candidate.is_symlink()
            published_exists = published.exists() or published.is_symlink()
            if candidate_exists and published_exists:
                raise ActivationError("prepared transaction has both candidate and published trees")
            if candidate_exists and not candidate.is_symlink():
                _remove_tree(candidate, staging)
            elif candidate.is_symlink():
                raise ActivationError("prepared candidate is an unsafe link")
            elif published_exists and not published.is_symlink():
                # SIGKILL can land after candidate->release rename but before
                # the journal fsync. Provenance was sealed before that rename;
                # validate it and deterministically roll the unpublished tree
                # back instead of leaking an orphan release.
                _validate_published_release(published, transaction)
                _remove_tree(published, releases)
            elif published.is_symlink():
                raise ActivationError("prepared published release is an unsafe link")
            else:
                raise ActivationError("prepared transaction lost both candidate and published trees")
        else:
            _validate_published_release(published, transaction)
            if transaction["phase"] == "published":
                _switch_current(base, transaction["new_target"], release_id)
                transaction["phase"] = "activated"
                _write_json(journals[0], transaction)
            if _current_target(base, required=True) != transaction["new_target"]:
                raise ActivationError("recovered active pointer differs from journal")
        journals[0].unlink()
        _fsync_directory(transactions)
    orphaned = [
        path
        for path in staging.iterdir()
        if path.name not in {".keep", current_candidate}
    ]
    if orphaned:
        raise ActivationError("orphaned worker bootstrap staging tree requires manual review")
    if _current_target(base):
        _prune_releases(base)


def _validate_wheelhouse(lock_path: Path, wheelhouse: Path) -> None:
    expected: set[str] = set()
    for line in lock_path.read_text(encoding="ascii").splitlines():
        if not line or line.startswith("#"):
            continue
        match = LOCK_RECORD.fullmatch(line)
        if not match or match.group("digest") in expected:
            raise ActivationError("worker runtime lock is invalid or has duplicate hashes")
        expected.add(match.group("digest"))
    if len(expected) != 35:
        raise ActivationError("worker runtime lock must contain exactly 35 distributions")
    metadata = wheelhouse.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or wheelhouse.is_symlink() or metadata.st_uid != 0 or metadata.st_mode & 0o022:
        raise ActivationError("offline worker wheelhouse is not root-owned and non-writable")
    actual: set[str] = set()
    for path in sorted(wheelhouse.iterdir()):
        item = path.lstat()
        if (
            not path.name.endswith(".whl")
            or not stat.S_ISREG(item.st_mode)
            or item.st_uid != 0
            or item.st_nlink != 1
            or item.st_mode & 0o022
        ):
            raise ActivationError("offline wheelhouse contains an unsafe entry")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest not in expected or digest in actual:
            raise ActivationError("offline wheelhouse contains an unexpected or duplicate wheel")
        actual.add(digest)
    if actual != expected:
        raise ActivationError("offline wheelhouse is incomplete for the exact worker lock")


def _create_runtime(candidate: Path, python: Path, wheelhouse: Path) -> tuple[str, str]:
    lock_path = candidate / "deploy/ubuntu/requirements-crawler-worker.lock"
    _validate_wheelhouse(lock_path, wheelhouse)
    subprocess.run([str(python), "-I", "-m", "venv", "--copies", str(candidate / ".venv")], check=True)
    runtime_python = candidate / ".venv/bin/python"
    subprocess.run(
        [
            str(runtime_python), "-I", "-m", "pip", "--isolated", "install",
            "--disable-pip-version-check", "--no-input", "--no-cache-dir", "--no-deps",
            "--no-index", "--only-binary=:all:", "--require-hashes",
            "--find-links", str(wheelhouse), "--requirement", str(lock_path),
        ],
        check=True,
        stdin=subprocess.DEVNULL,
    )
    third_party = tuple(module for module in SMOKE_IMPORTS if not module.startswith(("ops_agent.", "tools.")))
    local_modules = tuple(module for module in SMOKE_IMPORTS if module.startswith(("ops_agent.", "tools.")))
    smoke = "\n".join(f"import {module}" for module in third_party)
    subprocess.run(
        [str(runtime_python), "-I", "-c", smoke],
        cwd=candidate,
        check=True,
        stdin=subprocess.DEVNULL,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "PYTHONDONTWRITEBYTECODE": "1"},
    )
    subprocess.run(
        [str(runtime_python), "-X", "utf8", "-c", "\n".join(f"import {module}" for module in local_modules)],
        cwd=candidate,
        check=True,
        stdin=subprocess.DEVNULL,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "PYTHONDONTWRITEBYTECODE": "1"},
    )
    records: list[str] = []
    runtime_root = candidate / ".venv"
    for root, directories, files in os.walk(runtime_root, topdown=True, followlinks=False):
        directories.sort()
        files.sort()
        for name in directories:
            path = Path(root, name)
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                target = os.readlink(path)
                resolved = (path.parent / target).resolve()
                if os.path.isabs(target) or candidate not in (resolved, *resolved.parents):
                    raise ActivationError("generated runtime directory link escapes the release")
                relative = path.relative_to(candidate).as_posix()
                records.append(
                    f"link\t{hashlib.sha256(target.encode()).hexdigest()}\t{relative}\t{target}"
                )
            elif not stat.S_ISDIR(metadata.st_mode):
                raise ActivationError("generated runtime directory entry is special")
        for name in files:
            path = Path(root, name)
            relative = path.relative_to(candidate).as_posix()
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                target = os.readlink(path)
                resolved = (path.parent / target).resolve()
                if os.path.isabs(target) or candidate not in (resolved, *resolved.parents):
                    raise ActivationError("generated runtime link escapes the release")
                records.append(f"link\t{hashlib.sha256(target.encode()).hexdigest()}\t{relative}\t{target}")
            elif stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                records.append(f"file\t{stat.S_IMODE(metadata.st_mode):04o}\t{metadata.st_size}\t{digest}\t{relative}")
            else:
                raise ActivationError("generated runtime contains a special or hard-linked file")
    runtime_manifest = candidate / ".mooncen-worker-runtime.manifest"
    runtime_manifest.write_text(
        "format=mooncen-crawler-worker-runtime-v1\n" + "\n".join(sorted(records)) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    with runtime_manifest.open("rb") as handle:
        os.fsync(handle.fileno())
    return hashlib.sha256(lock_path.read_bytes()).hexdigest(), hashlib.sha256(runtime_manifest.read_bytes()).hexdigest()


def _seal_candidate(candidate: Path, deploy_info: dict[str, str]) -> None:
    group_id = int(subprocess.check_output(["/usr/bin/getent", "group", "mooncen"], text=True).split(":")[2])
    for root, directories, files in os.walk(candidate, topdown=False, followlinks=False):
        for name in files:
            path = Path(root, name)
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                os.lchown(path, 0, group_id)
                continue
            os.chown(path, 0, group_id)
            os.chmod(path, 0o550 if metadata.st_mode & 0o111 else 0o440)
        for name in directories:
            path = Path(root, name)
            if path.is_symlink():
                os.lchown(path, 0, group_id)
            else:
                os.chown(path, 0, group_id)
                os.chmod(path, 0o750)
    os.chown(candidate, 0, group_id)
    os.chmod(candidate, 0o750)
    info = candidate / ".deploy-info"
    descriptor = os.open(info, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii", newline="\n", closefd=False) as handle:
            for key in sorted(deploy_info):
                handle.write(f"{key}={deploy_info[key]}\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    os.chown(info, 0, 0)
    subprocess.run(["/usr/bin/chattr", "+i", "--", str(info)], check=True)
    for root, directories, files in os.walk(candidate, topdown=False):
        for name in files:
            with Path(root, name).open("rb") as handle:
                os.fsync(handle.fileno())
        _fsync_directory(Path(root))


def _prune_releases(base: Path) -> None:
    releases = base / "releases"
    current = _current_target(base, required=True).split("/", 1)[1]
    candidates: list[tuple[int, Path]] = []
    for path in releases.iterdir():
        _safe_tree(path, releases)
        if path.name != current:
            candidates.append((path.stat().st_mtime_ns, path))
    candidates.sort(reverse=True)
    for _mtime, path in candidates[MAX_RELEASES - 1 :]:
        _remove_tree(path, releases)
    if sum(1 for _ in releases.iterdir()) > MAX_RELEASES:
        raise ActivationError("worker release retention remains above its hard bound")


def activate(arguments: argparse.Namespace) -> None:
    base = arguments.base.resolve(strict=True)
    candidate = arguments.candidate.resolve(strict=True)
    staging = (base / ".staging").resolve(strict=True)
    releases = (base / "releases").resolve(strict=True)
    transactions = (base / ".transactions").resolve(strict=True)
    _safe_tree(candidate, staging)
    recover_transactions(base, current_candidate=arguments.release_id)
    if any(transactions.iterdir()):
        raise ActivationError("activation journal recovery did not converge")
    previous_target = _current_target(base)
    new_target = f"releases/{arguments.release_id}"
    release_path = releases / arguments.release_id
    if release_path.exists() or release_path.is_symlink():
        raise ActivationError("versioned worker release already exists")
    journal = transactions / f"{arguments.release_id}.json"
    transaction = {
        "format": "mooncen-worker-bootstrap-transaction-v1",
        "release_id": arguments.release_id,
        "phase": "prepared",
        "previous_target": previous_target,
        "new_target": new_target,
        "commit": arguments.commit,
        "archive_sha256": arguments.archive_sha256,
        "tree_sha256": arguments.tree_sha256,
    }
    _write_json(journal, transaction)
    try:
        runtime_lock_sha256, runtime_tree_sha256 = _create_runtime(
            candidate, arguments.python, arguments.wheelhouse
        )
        deploy_info = {
            "DEPLOY_INFO_FORMAT": "mooncen-crawler-worker-bootstrap-provenance-v1",
            "RELEASE_ID": arguments.release_id,
            "DEPLOY_COMMIT": arguments.commit,
            "DEPLOY_ARCHIVE_SHA256": arguments.archive_sha256,
            "DEPLOY_TREE_SHA256": arguments.tree_sha256,
            "NODE_ROLE": "crawler-worker",
            "WORKER_KEY": arguments.worker_key,
            "TARGET_KERNEL_HOSTNAME": arguments.kernel_hostname,
            "TOPOLOGY_SHA256": arguments.topology_sha256,
            "RESOURCE_DROPIN_SHA256": arguments.resource_dropin_sha256,
            "RUNTIME_LOCK_SHA256": runtime_lock_sha256,
            "RUNTIME_TREE_SHA256": runtime_tree_sha256,
        }
        _seal_candidate(candidate, deploy_info)
        os.replace(candidate, release_path)
        _fsync_directory(staging)
        _fsync_directory(releases)
        transaction["phase"] = "published"
        _write_json(journal, transaction)
        _switch_current(base, new_target, arguments.release_id)
        transaction["phase"] = "activated"
        _write_json(journal, transaction)
        _validate_published_release(release_path, transaction)
        journal.unlink()
        _fsync_directory(transactions)
        _prune_releases(base)
    except BaseException as original:
        cleanup_errors: list[str] = []
        try:
            if _current_target(base) == new_target:
                if previous_target:
                    _switch_current(base, previous_target, arguments.release_id)
                else:
                    (base / "current").unlink(missing_ok=True)
                    _fsync_directory(base)
        except (ActivationError, OSError) as exc:
            cleanup_errors.append(f"pointer rollback failed: {exc}")
        try:
            if release_path.exists() and not release_path.is_symlink() and _current_target(base) != new_target:
                _remove_tree(release_path, releases)
            elif candidate.exists() and not candidate.is_symlink():
                _remove_tree(candidate, staging)
        except (ActivationError, OSError) as exc:
            cleanup_errors.append(f"release cleanup failed: {exc}")
        try:
            if journal.exists() and not journal.is_symlink() and not cleanup_errors:
                journal.unlink()
                _fsync_directory(transactions)
        except OSError as exc:
            cleanup_errors.append(f"journal cleanup failed: {exc}")
        if cleanup_errors and hasattr(original, "add_note"):
            original.add_note("; ".join(cleanup_errors))
        raise original


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--tree-sha256", required=True)
    parser.add_argument("--worker-key", required=True)
    parser.add_argument("--kernel-hostname", required=True)
    parser.add_argument("--topology-sha256", required=True)
    parser.add_argument("--resource-dropin-sha256", required=True)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    if os.name != "posix" or os.geteuid() != 0:
        raise SystemExit("worker bootstrap state engine requires Linux root")
    if (
        arguments.base != Path("/opt/mooncen-worker")
        or arguments.wheelhouse != Path("/var/cache/mooncen-worker/wheelhouse")
        or arguments.python != Path("/usr/bin/python3.12")
        or not RELEASE_ID.fullmatch(arguments.release_id)
        or arguments.candidate != arguments.base / ".staging" / arguments.release_id
        or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", arguments.commit)
        or not all(DIGEST.fullmatch(value) for value in (
            arguments.archive_sha256,
            arguments.tree_sha256,
            arguments.topology_sha256,
            arguments.resource_dropin_sha256,
        ))
        or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", arguments.worker_key)
        or not re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]{0,62})(?:\.[a-z0-9](?:[a-z0-9-]{0,62}))*",
            arguments.kernel_hostname,
        )
    ):
        raise SystemExit("worker bootstrap state engine arguments are not canonical")
    for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda received, _frame: (_ for _ in ()).throw(ActivationInterrupted(str(received))))
    try:
        activate(arguments)
    except (ActivationError, OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"worker bootstrap activation failed closed: {exc}") from exc
    print(
        f"MOONCEN_WORKER_BOOTSTRAP_ACTIVATED={arguments.worker_key}:"
        f"{arguments.commit}:{arguments.archive_sha256}:{arguments.tree_sha256}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
