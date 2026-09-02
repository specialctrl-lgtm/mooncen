#!/usr/bin/env bash
set -euo pipefail
umask 077
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

die() {
  printf '%s\n' "an2p development runtime install: $*" >&2
  exit "${2:-78}"
}

# Root must never resolve or mutate an sgm-controlled pathname.  Keep every
# user-tree directory open, reject symlink/cross-mount substitutions, and use
# only fd-relative leaf operations.  Quarantine copies bytes into a new
# root-owned inode; it never chowns/chmods a user inode that could be a hardlink.
safe_legacy_user_paths() {
  /usr/bin/python3.12 -I - "$@" <<'PY'
import json
import os
import secrets
import stat
import sys


DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
MAX_PAYLOAD = 4 * 1024 * 1024
LEGACY_UNITS = (
    "mooncen-ops-control-env.service",
    "mooncen-ops-db-tunnel.service",
    "mooncen-ops-api.service",
    "mooncen-deployment-worker.service",
    "mooncen-docker-dev.service",
    "mooncen-ops-console.service",
    "mooncen-status-agent.service",
)
CREDENTIALS = (
    "cloud-deploy.ssh_config",
    "keys/cloud-deploy-ed25519",
    "ops-api.env",
    "deployment-worker.env",
)


def fail(message: str) -> None:
    raise SystemExit(f"safe legacy user path operation failed: {message}")


def parse_id(value: str, label: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        fail(f"{label} is not numeric")
    if parsed < 0 or str(parsed) != value:
        fail(f"{label} is not canonical")
    return parsed


def split_absolute(path: str) -> tuple[str, ...]:
    if not path.startswith("/") or path == "/" or "\x00" in path:
        fail("absolute directory is invalid")
    components = tuple(path.split("/")[1:])
    if not components or any(not item or item in {".", ".."} for item in components):
        fail("absolute directory is not canonical")
    return components


def mount_id(descriptor: int) -> int:
    try:
        with open(f"/proc/self/fdinfo/{descriptor}", encoding="ascii") as stream:
            values = [line.split(":", 1)[1].strip() for line in stream if line.startswith("mnt_id:")]
    except (OSError, UnicodeError) as exc:
        fail(f"mount identity is unavailable: {exc}")
    if len(values) != 1 or not values[0].isdigit():
        fail("mount identity is not canonical")
    return int(values[0])


descriptors: list[int] = []
bindings: list[tuple[int, str, int, str]] = []


def track(descriptor: int) -> int:
    descriptors.append(descriptor)
    return descriptor


def same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def bind_directory(parent: int, name: str, label: str) -> int:
    try:
        path_metadata = os.stat(name, dir_fd=parent, follow_symlinks=False)
        descriptor = track(os.open(name, DIRECTORY_FLAGS, dir_fd=parent))
    except OSError as exc:
        fail(f"{label} directory is unsafe: {exc}")
    descriptor_metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(path_metadata.st_mode)
        or not stat.S_ISDIR(descriptor_metadata.st_mode)
        or not same_inode(path_metadata, descriptor_metadata)
    ):
        fail(f"{label} directory identity changed")
    bindings.append((parent, name, descriptor, label))
    return descriptor


def open_absolute_directory(path: str, label: str) -> tuple[int, int]:
    descriptor = track(os.open("/", DIRECTORY_FLAGS))
    parent = descriptor
    for index, component in enumerate(split_absolute(path)):
        parent = descriptor
        descriptor = bind_directory(parent, component, f"{label} component {index}")
    return descriptor, parent


def open_absolute_parent(path: str, label: str) -> tuple[int, str]:
    components = split_absolute(path)
    parent_path = "/" + "/".join(components[:-1]) if len(components) > 1 else "/"
    if parent_path == "/":
        parent = track(os.open("/", DIRECTORY_FLAGS))
    else:
        parent, _unused = open_absolute_directory(parent_path, f"{label} parent")
    return parent, components[-1]


def validate_owned_directory(
    descriptor: int,
    *,
    uid: int,
    gid: int,
    label: str,
    exact_mode: int | None = None,
) -> None:
    metadata = os.fstat(descriptor)
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != uid
        or metadata.st_gid != gid
        or not mode & stat.S_IXUSR
        or mode & 0o022
        or (exact_mode is not None and mode != exact_mode)
    ):
        fail(f"{label} directory metadata is unsafe")


def assert_bound_directories() -> None:
    for parent, name, held, label in bindings:
        try:
            path_metadata = os.stat(name, dir_fd=parent, follow_symlinks=False)
            reopened = os.open(name, DIRECTORY_FLAGS, dir_fd=parent)
        except OSError as exc:
            fail(f"{label} directory disappeared: {exc}")
        try:
            held_metadata = os.fstat(held)
            reopened_metadata = os.fstat(reopened)
            if (
                not stat.S_ISDIR(path_metadata.st_mode)
                or not same_inode(path_metadata, held_metadata)
                or not same_inode(reopened_metadata, held_metadata)
                or mount_id(reopened) != mount_id(held)
            ):
                fail(f"{label} canonical directory changed")
        finally:
            os.close(reopened)


def open_home(path: str, uid: int, gid: int) -> tuple[int, int]:
    descriptor, parent = open_absolute_directory(path, "legacy home")
    validate_owned_directory(descriptor, uid=uid, gid=gid, label="legacy home")
    home_mount = mount_id(descriptor)
    if home_mount != mount_id(parent):
        fail("legacy home is a separate bind mount")
    return descriptor, home_mount


def ensure_quarantine(path: str, root_uid: int, root_gid: int) -> tuple[int, int]:
    parent, name = open_absolute_parent(path, "quarantine")
    validate_owned_directory(parent, uid=root_uid, gid=root_gid, label="quarantine parent")
    try:
        os.stat(name, dir_fd=parent, follow_symlinks=False)
        created = False
    except FileNotFoundError:
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
        except OSError as exc:
            fail(f"cannot create quarantine: {exc}")
        created = True
    descriptor = bind_directory(parent, name, "quarantine")
    if created:
        metadata = os.fstat(descriptor)
        if metadata.st_uid != root_uid or metadata.st_gid != root_gid:
            fail("new quarantine owner is unsafe")
        os.fchmod(descriptor, 0o700)
        os.fchown(descriptor, root_uid, root_gid)
        os.fsync(parent)
    validate_owned_directory(
        descriptor,
        uid=root_uid,
        gid=root_gid,
        label="quarantine",
        exact_mode=0o700,
    )
    quarantine_mount = mount_id(descriptor)
    if quarantine_mount != mount_id(parent):
        fail("quarantine is a separate bind mount")
    return descriptor, quarantine_mount


def ensure_user_directory(
    parent: int,
    name: str,
    *,
    uid: int,
    gid: int,
    root_uid: int,
    root_gid: int,
    mode: int,
    home_mount: int,
    label: str,
    exact_existing_mode: bool,
) -> int:
    try:
        os.stat(name, dir_fd=parent, follow_symlinks=False)
        created = False
    except FileNotFoundError:
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
        except OSError as exc:
            fail(f"cannot create {label}: {exc}")
        created = True
    descriptor = bind_directory(parent, name, label)
    if mount_id(descriptor) != home_mount:
        fail(f"{label} crosses the legacy home mount")
    if created:
        metadata = os.fstat(descriptor)
        if metadata.st_uid != root_uid or metadata.st_gid != root_gid:
            fail(f"new {label} directory owner raced")
        os.fchmod(descriptor, mode)
        os.fchown(descriptor, uid, gid)
        os.fsync(parent)
    validate_owned_directory(
        descriptor,
        uid=uid,
        gid=gid,
        label=label,
        exact_mode=mode if exact_existing_mode else None,
    )
    return descriptor


def optional_user_directory(
    parent: int,
    name: str,
    *,
    uid: int,
    gid: int,
    home_mount: int,
    label: str,
) -> int | None:
    try:
        os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return None
    descriptor = bind_directory(parent, name, label)
    if mount_id(descriptor) != home_mount:
        fail(f"{label} crosses the legacy home mount")
    validate_owned_directory(descriptor, uid=uid, gid=gid, label=label)
    return descriptor


def lstat_optional(parent: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return None


def read_all(descriptor: int, label: str) -> bytes:
    metadata = os.fstat(descriptor)
    if metadata.st_size < 0 or metadata.st_size > MAX_PAYLOAD:
        fail(f"{label} exceeds the size limit")
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, min(65536, MAX_PAYLOAD + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_PAYLOAD:
            fail(f"{label} exceeds the size limit")
    return b"".join(chunks)


def open_user_regular(
    parent: int,
    name: str,
    *,
    uid: int,
    gid: int,
    root_uid: int,
    root_gid: int,
    home_mount: int,
    private: bool,
    label: str,
) -> tuple[int, bytes, os.stat_result]:
    path_metadata = lstat_optional(parent, name)
    if path_metadata is None:
        fail(f"{label} disappeared")
    mode = stat.S_IMODE(path_metadata.st_mode)
    if (
        not stat.S_ISREG(path_metadata.st_mode)
        or path_metadata.st_nlink != 1
        or (path_metadata.st_uid, path_metadata.st_gid)
        not in {(uid, gid), (root_uid, root_gid)}
        or mode & (0o077 if private else 0o022)
    ):
        fail(f"{label} regular source metadata is unsafe")
    try:
        descriptor = track(os.open(name, READ_FLAGS, dir_fd=parent))
    except OSError as exc:
        fail(f"cannot open {label}: {exc}")
    descriptor_metadata = os.fstat(descriptor)
    if (
        not same_inode(path_metadata, descriptor_metadata)
        or descriptor_metadata.st_nlink != 1
        or mount_id(descriptor) != home_mount
    ):
        fail(f"{label} regular source identity changed")
    payload = read_all(descriptor, label)
    final_metadata = os.fstat(descriptor)
    rebound = lstat_optional(parent, name)
    if (
        rebound is None
        or not same_inode(path_metadata, rebound)
        or not same_inode(path_metadata, final_metadata)
        or final_metadata.st_nlink != 1
    ):
        fail(f"{label} regular source raced")
    return descriptor, payload, path_metadata


def read_quarantine_regular(
    parent: int,
    name: str,
    *,
    root_uid: int,
    root_gid: int,
    quarantine_mount: int,
    label: str,
) -> bytes:
    metadata = lstat_optional(parent, name)
    if (
        metadata is None
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != root_uid
        or metadata.st_gid != root_gid
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        fail(f"{label} quarantine residue is unsafe")
    try:
        descriptor = track(os.open(name, READ_FLAGS, dir_fd=parent))
    except OSError as exc:
        fail(f"cannot open {label} quarantine residue: {exc}")
    if not same_inode(metadata, os.fstat(descriptor)) or mount_id(descriptor) != quarantine_mount:
        fail(f"{label} quarantine identity changed")
    return read_all(descriptor, f"{label} quarantine")


def random_stage(prefix: str) -> str:
    return f".{prefix}.{os.getpid()}.{secrets.token_hex(12)}.tmp"


def create_quarantine_regular(
    parent: int,
    name: str,
    payload: bytes,
    *,
    root_uid: int,
    root_gid: int,
    label: str,
) -> None:
    stage = random_stage(name)
    descriptor = -1
    try:
        descriptor = os.open(
            stage,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=parent,
        )
        os.fchown(descriptor, root_uid, root_gid)
        os.fchmod(descriptor, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                fail(f"{label} quarantine write did not progress")
            view = view[written:]
        os.fsync(descriptor)
        if lstat_optional(parent, name) is not None:
            fail(f"{label} quarantine destination raced")
        os.link(stage, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
        os.unlink(stage, dir_fd=parent)
        stage = ""
        os.fsync(parent)
    except OSError as exc:
        fail(f"cannot publish {label} quarantine: {exc}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if stage:
            try:
                os.unlink(stage, dir_fd=parent)
            except FileNotFoundError:
                pass


def validate_quarantine_symlink(
    parent: int,
    name: str,
    *,
    target: str,
    root_uid: int,
    root_gid: int,
    label: str,
) -> None:
    metadata = lstat_optional(parent, name)
    if (
        metadata is None
        or not stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != root_uid
        or metadata.st_gid != root_gid
        or os.readlink(name, dir_fd=parent) != target
    ):
        fail(f"{label} symlink quarantine residue is unsafe")


def quarantine_leaf(
    source_parent: int | None,
    source_name: str,
    destination_parent: int,
    destination_name: str,
    *,
    uid: int,
    gid: int,
    root_uid: int,
    root_gid: int,
    home_mount: int,
    quarantine_mount: int,
    allow_symlink: bool,
    private: bool,
    label: str,
) -> None:
    source_metadata = None if source_parent is None else lstat_optional(source_parent, source_name)
    destination_metadata = lstat_optional(destination_parent, destination_name)
    if (
        allow_symlink
        and source_metadata is not None
        and stat.S_ISLNK(source_metadata.st_mode)
        and source_metadata.st_uid == root_uid
        and source_metadata.st_gid == root_gid
        and os.readlink(source_name, dir_fd=source_parent) == "/dev/null"
    ):
        if destination_metadata is None:
            return
        if stat.S_ISREG(destination_metadata.st_mode):
            read_quarantine_regular(
                destination_parent,
                destination_name,
                root_uid=root_uid,
                root_gid=root_gid,
                quarantine_mount=quarantine_mount,
                label=label,
            )
            return
        if stat.S_ISLNK(destination_metadata.st_mode):
            target = os.readlink(destination_name, dir_fd=destination_parent)
            validate_quarantine_symlink(
                destination_parent,
                destination_name,
                target=target,
                root_uid=root_uid,
                root_gid=root_gid,
                label=label,
            )
            return
        fail(f"{label} quarantine residue has an unsafe type")
    if source_metadata is None:
        if destination_metadata is None:
            return
        if stat.S_ISREG(destination_metadata.st_mode):
            read_quarantine_regular(
                destination_parent,
                destination_name,
                root_uid=root_uid,
                root_gid=root_gid,
                quarantine_mount=quarantine_mount,
                label=label,
            )
            return
        if allow_symlink and stat.S_ISLNK(destination_metadata.st_mode):
            target = os.readlink(destination_name, dir_fd=destination_parent)
            validate_quarantine_symlink(
                destination_parent,
                destination_name,
                target=target,
                root_uid=root_uid,
                root_gid=root_gid,
                label=label,
            )
            return
        fail(f"{label} quarantine residue has an unsafe type")

    if stat.S_ISREG(source_metadata.st_mode):
        _descriptor, payload, identity = open_user_regular(
            source_parent,
            source_name,
            uid=uid,
            gid=gid,
            root_uid=root_uid,
            root_gid=root_gid,
            home_mount=home_mount,
            private=private,
            label=label,
        )
        if destination_metadata is None:
            create_quarantine_regular(
                destination_parent,
                destination_name,
                payload,
                root_uid=root_uid,
                root_gid=root_gid,
                label=label,
            )
        elif read_quarantine_regular(
            destination_parent,
            destination_name,
            root_uid=root_uid,
            root_gid=root_gid,
            quarantine_mount=quarantine_mount,
            label=label,
        ) != payload:
            fail(f"{label} quarantine collision differs")
        rebound = lstat_optional(source_parent, source_name)
        if rebound is None or not same_inode(identity, rebound) or rebound.st_nlink != 1:
            fail(f"{label} source changed before unlink")
        os.unlink(source_name, dir_fd=source_parent)
        os.fsync(source_parent)
        return

    if allow_symlink and stat.S_ISLNK(source_metadata.st_mode):
        if (source_metadata.st_uid, source_metadata.st_gid) not in {
            (uid, gid),
            (root_uid, root_gid),
        }:
            fail(f"{label} symlink source owner is unsafe")
        target = os.readlink(source_name, dir_fd=source_parent)
        if destination_metadata is None:
            try:
                os.symlink(target, destination_name, dir_fd=destination_parent)
                os.fsync(destination_parent)
            except OSError as exc:
                fail(f"cannot publish {label} symlink quarantine: {exc}")
        validate_quarantine_symlink(
            destination_parent,
            destination_name,
            target=target,
            root_uid=root_uid,
            root_gid=root_gid,
            label=label,
        )
        rebound = lstat_optional(source_parent, source_name)
        if (
            rebound is None
            or not same_inode(source_metadata, rebound)
            or not stat.S_ISLNK(rebound.st_mode)
            or os.readlink(source_name, dir_fd=source_parent) != target
        ):
            fail(f"{label} symlink source changed before unlink")
        os.unlink(source_name, dir_fd=source_parent)
        os.fsync(source_parent)
        return
    fail(f"{label} source is neither an exact regular file nor an allowed symlink")


def ensure_mask(parent: int, name: str, root_uid: int, root_gid: int) -> None:
    metadata = lstat_optional(parent, name)
    if metadata is not None:
        if (
            stat.S_ISLNK(metadata.st_mode)
            and metadata.st_uid == root_uid
            and metadata.st_gid == root_gid
            and os.readlink(name, dir_fd=parent) == "/dev/null"
        ):
            return
        fail(f"legacy unit mask destination is unsafe: {name}")
    stage = random_stage(name)
    try:
        os.symlink("/dev/null", stage, dir_fd=parent)
        stage_metadata = os.stat(stage, dir_fd=parent, follow_symlinks=False)
        if stage_metadata.st_uid != root_uid or stage_metadata.st_gid != root_gid:
            fail(f"legacy unit mask stage owner is unsafe: {name}")
        os.rename(stage, name, src_dir_fd=parent, dst_dir_fd=parent)
        stage = ""
        os.fsync(parent)
    except OSError as exc:
        fail(f"cannot publish legacy unit mask {name}: {exc}")
    finally:
        if stage:
            try:
                os.unlink(stage, dir_fd=parent)
            except FileNotFoundError:
                pass
    metadata = lstat_optional(parent, name)
    if (
        metadata is None
        or not stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != root_uid
        or metadata.st_gid != root_gid
        or os.readlink(name, dir_fd=parent) != "/dev/null"
    ):
        fail(f"legacy unit mask did not converge: {name}")


def read_reviewed_source(path: str, root_uid: int, root_gid: int, label: str) -> bytes:
    try:
        descriptor = track(os.open(path, READ_FLAGS))
    except OSError as exc:
        fail(f"cannot open reviewed {label}: {exc}")
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != root_uid
        or metadata.st_gid != root_gid
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        fail(f"reviewed {label} metadata is unsafe")
    payload = read_all(descriptor, f"reviewed {label}")
    try:
        reopened = os.open(path, READ_FLAGS)
    except OSError as exc:
        fail(f"reviewed {label} canonical path disappeared: {exc}")
    try:
        if not same_inode(metadata, os.fstat(reopened)) or mount_id(reopened) != mount_id(descriptor):
            fail(f"reviewed {label} canonical identity changed")
    finally:
        os.close(reopened)
    return payload


def validate_published_regular(
    parent: int,
    name: str,
    *,
    uid: int,
    gid: int,
    mode: int,
    home_mount: int,
    payload: bytes,
    label: str,
) -> tuple[int, bool]:
    metadata = lstat_optional(parent, name)
    if metadata is None:
        return -1, False
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != mode
    ):
        fail(f"published {label} leaf is unsafe")
    try:
        descriptor = track(os.open(name, READ_FLAGS, dir_fd=parent))
    except OSError as exc:
        fail(f"cannot open published {label}: {exc}")
    descriptor_metadata = os.fstat(descriptor)
    if not same_inode(metadata, descriptor_metadata) or mount_id(descriptor) != home_mount:
        fail(f"published {label} identity changed")
    owner = (descriptor_metadata.st_uid, descriptor_metadata.st_gid)
    if owner not in {(uid, gid), (root_uid, root_gid)}:
        fail(f"published {label} owner is unsafe")
    existing = read_all(descriptor, f"published {label}")
    if owner == (root_uid, root_gid) and existing != payload:
        fail(f"root-owned partial {label} differs from reviewed bytes")
    return descriptor, existing == payload


def publish_reviewed(
    source: str,
    parent: int,
    name: str,
    *,
    uid: int,
    gid: int,
    root_uid: int,
    root_gid: int,
    mode: int,
    home_mount: int,
    label: str,
) -> None:
    payload = read_reviewed_source(source, root_uid, root_gid, label)
    existing_descriptor, identical = validate_published_regular(
        parent,
        name,
        uid=uid,
        gid=gid,
        mode=mode,
        home_mount=home_mount,
        payload=payload,
        label=label,
    )
    if identical:
        metadata = os.fstat(existing_descriptor)
        if (metadata.st_uid, metadata.st_gid) == (root_uid, root_gid):
            os.fchown(existing_descriptor, uid, gid)
            os.fsync(existing_descriptor)
        return
    stage = random_stage(name)
    descriptor = -1
    try:
        descriptor = os.open(
            stage,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            mode,
            dir_fd=parent,
        )
        os.fchown(descriptor, root_uid, root_gid)
        os.fchmod(descriptor, mode)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                fail(f"reviewed {label} write did not progress")
            view = view[written:]
        os.fsync(descriptor)
        os.rename(stage, name, src_dir_fd=parent, dst_dir_fd=parent)
        stage = ""
        rebound = lstat_optional(parent, name)
        metadata = os.fstat(descriptor)
        if (
            rebound is None
            or not same_inode(rebound, metadata)
            or metadata.st_nlink != 1
            or mount_id(descriptor) != home_mount
        ):
            fail(f"published {label} destination raced")
        os.fchown(descriptor, uid, gid)
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
        os.fsync(parent)
    except OSError as exc:
        fail(f"cannot atomically publish reviewed {label}: {exc}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if stage:
            try:
                os.unlink(stage, dir_fd=parent)
            except FileNotFoundError:
                pass
    final_descriptor, final_identical = validate_published_regular(
        parent,
        name,
        uid=uid,
        gid=gid,
        mode=mode,
        home_mount=home_mount,
        payload=payload,
        label=label,
    )
    if not final_identical or (os.fstat(final_descriptor).st_uid, os.fstat(final_descriptor).st_gid) != (uid, gid):
        fail(f"published {label} did not converge")


if len(sys.argv) < 8:
    fail("arguments are incomplete")
action, home_path, uid_text, gid_text, quarantine_path, root_uid_text, root_gid_text = sys.argv[1:8]
arguments = sys.argv[8:]
uid = parse_id(uid_text, "legacy uid")
gid = parse_id(gid_text, "legacy gid")
root_uid = parse_id(root_uid_text, "root uid")
root_gid = parse_id(root_gid_text, "root gid")

try:
    home, home_mount = open_home(home_path, uid, gid)
    quarantine, quarantine_mount = ensure_quarantine(quarantine_path, root_uid, root_gid)
    if action == "quarantine-credentials":
        if arguments:
            fail("credential quarantine accepts no extra arguments")
        config = optional_user_directory(
            home,
            ".config",
            uid=uid,
            gid=gid,
            home_mount=home_mount,
            label="legacy .config",
        )
        legacy_config = None
        if config is not None:
            legacy_config = optional_user_directory(
                config,
                "mooncen-an2p",
                uid=uid,
                gid=gid,
                home_mount=home_mount,
                label="legacy credential directory",
            )
        for relative in CREDENTIALS:
            source_parent = legacy_config
            source_name = relative
            if "/" in relative:
                directory_name, source_name = relative.split("/", 1)
                source_parent = None if legacy_config is None else optional_user_directory(
                    legacy_config,
                    directory_name,
                    uid=uid,
                    gid=gid,
                    home_mount=home_mount,
                    label=f"legacy credential {directory_name} directory",
                )
            quarantine_leaf(
                source_parent,
                source_name,
                quarantine,
                relative.replace("/", "_"),
                uid=uid,
                gid=gid,
                root_uid=root_uid,
                root_gid=root_gid,
                home_mount=home_mount,
                quarantine_mount=quarantine_mount,
                allow_symlink=False,
                private=True,
                label=f"legacy credential {relative}",
            )
    elif action == "prepare-user-runtime":
        if len(arguments) != 3 or arguments[2] not in {"0", "1"}:
            fail("user runtime preparation arguments are invalid")
        unit_source, helper_source, include_docs_text = arguments
        config = ensure_user_directory(
            home,
            ".config",
            uid=uid,
            gid=gid,
            root_uid=root_uid,
            root_gid=root_gid,
            mode=0o700,
            home_mount=home_mount,
            label="user .config",
            exact_existing_mode=False,
        )
        systemd = ensure_user_directory(
            config,
            "systemd",
            uid=uid,
            gid=gid,
            root_uid=root_uid,
            root_gid=root_gid,
            mode=0o700,
            home_mount=home_mount,
            label="user systemd directory",
            exact_existing_mode=False,
        )
        user_units = ensure_user_directory(
            systemd,
            "user",
            uid=uid,
            gid=gid,
            root_uid=root_uid,
            root_gid=root_gid,
            mode=0o755,
            home_mount=home_mount,
            label="user unit directory",
            exact_existing_mode=True,
        )
        local = ensure_user_directory(
            home,
            ".local",
            uid=uid,
            gid=gid,
            root_uid=root_uid,
            root_gid=root_gid,
            mode=0o700,
            home_mount=home_mount,
            label="user .local directory",
            exact_existing_mode=False,
        )
        share = ensure_user_directory(
            local,
            "share",
            uid=uid,
            gid=gid,
            root_uid=root_uid,
            root_gid=root_gid,
            mode=0o700,
            home_mount=home_mount,
            label="user share directory",
            exact_existing_mode=False,
        )
        user_runtime = ensure_user_directory(
            share,
            "mooncen-an2p",
            uid=uid,
            gid=gid,
            root_uid=root_uid,
            root_gid=root_gid,
            mode=0o700,
            home_mount=home_mount,
            label="user runtime directory",
            exact_existing_mode=True,
        )
        for unit in LEGACY_UNITS:
            quarantine_leaf(
                user_units,
                unit,
                quarantine,
                unit,
                uid=uid,
                gid=gid,
                root_uid=root_uid,
                root_gid=root_gid,
                home_mount=home_mount,
                quarantine_mount=quarantine_mount,
                allow_symlink=True,
                private=False,
                label=f"legacy user unit {unit}",
            )
            ensure_mask(user_units, unit, root_uid, root_gid)
        units = [
            "mooncen-api.service",
            "mooncen-frontend.service",
            "mooncen-development-runtime.target",
        ]
        if include_docs_text == "1":
            units.append("mooncen-docs.service")
        for unit in units:
            publish_reviewed(
                os.path.join(unit_source, unit),
                user_units,
                unit,
                uid=uid,
                gid=gid,
                root_uid=root_uid,
                root_gid=root_gid,
                mode=0o644,
                home_mount=home_mount,
                label=f"native unit {unit}",
            )
        for helper in ("wait_for_an2p_http.py", "wait_for_an2p_database.py"):
            publish_reviewed(
                os.path.join(helper_source, helper),
                user_runtime,
                helper,
                uid=uid,
                gid=gid,
                root_uid=root_uid,
                root_gid=root_gid,
                mode=0o700,
                home_mount=home_mount,
                label=f"native helper {helper}",
            )
    else:
        fail("action is invalid")
    assert_bound_directories()
    print(json.dumps({"action": action, "safe": True, "schema_version": 1}, sort_keys=True, separators=(",", ":")))
finally:
    for descriptor in reversed(descriptors):
        try:
            os.close(descriptor)
        except OSError:
            pass
PY
}

[ "$(id -u)" -eq 0 ] || die "run from a root console"
[ "$(hostname -s)" = an2p ] || die "unexpected host"
[ "$#" -eq 3 ] && [ "$1" = --prepare ] && [ "$2" = --pair ] || \
  die "usage: $0 --prepare --pair <runtime-pair>" 64
pair_name=$3
[[ "$pair_name" =~ ^runtime-pair\.([0-9a-f]{40})\.([0-9a-f]{40})\.([0-9a-f]{64})$ ]] || \
  die "runtime pair name is invalid"
source_tree=${BASH_REMATCH[2]}

pair_root=/opt/mooncen-an2p-runtime
pair_path=$pair_root/releases/$pair_name
pair_pointer=$pair_root/current
control_runtime=/opt/mooncen-an2p-control/current
docker_runtime=/opt/mooncen-an2p-docker/current
manager=/usr/local/libexec/mooncen-an2p-runtime-manager
selector=/usr/local/libexec/mooncen-an2p-service-control
legacy_user=sgm
api_user=mooncen_ops_api
docker_user=mooncen_docker_operator
script_path=$pair_path/control/deploy/an2p/install_development_runtime.sh

[ "$(readlink -f -- "${BASH_SOURCE[0]}")" = "$script_path" ] && \
  [ ! -L "${BASH_SOURCE[0]}" ] || \
  die "execute only the exact immutable pair script"
[ -L "$control_runtime" ] && \
  [ "$(readlink "$control_runtime")" = ../mooncen-an2p-runtime/current/control ] && \
  [ "$(stat -c '%U:%G' "$control_runtime")" = root:root ] || \
  die "control runtime compatibility alias is unsafe"
[ -L "$docker_runtime" ] && \
  [ "$(readlink "$docker_runtime")" = ../mooncen-an2p-runtime/current/docker ] && \
  [ "$(stat -c '%U:%G' "$docker_runtime")" = root:root ] || \
  die "Docker runtime compatibility alias is unsafe"
for executable in "$manager" "$selector"; do
  [ -x "$executable" ] && [ ! -L "$executable" ] && \
    [ "$(stat -c '%U:%G:%a' "$executable")" = root:root:755 ] || \
    die "root-installed runtime helper is unsafe"
done
"$manager" validate "$pair_name" >/dev/null
selection_before=$("$selector" runtime-status)

ensure_system_account() {
  local account=$1 home=$2 record
  if ! record=$(getent passwd "$account"); then
    /usr/sbin/useradd --system --user-group --home-dir "$home" --create-home \
      --shell /usr/sbin/nologin "$account"
    record=$(getent passwd "$account")
  fi
  [ "$(printf '%s' "$record" | cut -d: -f6)" = "$home" ] && \
    [ "$(printf '%s' "$record" | cut -d: -f7)" = /usr/sbin/nologin ] && \
    [ "$(id -gn "$account")" = "$account" ] || \
    die "dedicated account metadata drifted: $account"
  install -d -o "$account" -g "$account" -m 0700 "$home"
  /usr/sbin/usermod --password '*' "$account"
}

ensure_system_account "$api_user" /var/lib/mooncen-ops-api
ensure_system_account "$docker_user" /var/lib/mooncen-docker-operator
getent group docker >/dev/null || die "Docker must already provide the docker group"
/usr/sbin/usermod --append --groups docker "$docker_user"
for account in "$legacy_user" "$api_user"; do
  for privileged_group in docker lxd; do
    if getent group "$privileged_group" >/dev/null && \
      id -nG "$account" | tr ' ' '\n' | grep -Fxq "$privileged_group"; then
      /usr/bin/gpasswd --delete "$account" "$privileged_group" >/dev/null
    fi
  done
done
if getent group lxd >/dev/null && \
  id -nG "$docker_user" | tr ' ' '\n' | grep -Fxq lxd; then
  /usr/bin/gpasswd --delete "$docker_user" lxd >/dev/null
fi

# The long build phase already revoked every retained docker/lxd GID while
# restoring the public development runtime.  Preparation must not terminate
# that clean runtime before the manager owns a durable cutover journal.
[ "${SUDO_USER:-root}" = root ] || \
  die "run the reviewed installer from an independent root console"
legacy_uid=$(id -u "$legacy_user")
legacy_gid=$(id -g "$legacy_user")
legacy_home=$(getent passwd "$legacy_user" | cut -d: -f6)
[ "$legacy_home" = "/home/$legacy_user" ] || \
  die "legacy user home is not canonical"
for privileged_group in docker lxd; do
  getent group "$privileged_group" >/dev/null || continue
  privileged_gid=$(getent group "$privileged_group" | cut -d: -f3)
  for status in /proc/[0-9]*/status; do
    [ -r "$status" ] || continue
    process_uid=$(awk '/^Uid:/ {print $2}' "$status" 2>/dev/null) || continue
    [ "$process_uid" = "$legacy_uid" ] || continue
    process_state=$(awk '{value=$0; sub(/^.*\) /, "", value); split(value, fields, " "); print fields[1]}' \
      "${status%/status}/stat" 2>/dev/null) || continue
    case "$process_state" in
      Z|X|x) continue ;;
    esac
    if awk -v gid="$privileged_gid" \
      '/^Groups:/ {for (i=2;i<=NF;i++) if ($i==gid) found=1} END {exit !found}' \
      "$status" 2>/dev/null; then
      die "legacy session still retains ${privileged_group} host-root"
    fi
  done
done

install -d -o root -g root -m 0755 /etc/mooncen-an2p

# The former user control plane is never allowed to reclaim 5175 or restart a
# worker.  Superseded shared credentials remain recoverable but root-only.
legacy_units=(
  mooncen-ops-control-env.service
  mooncen-ops-db-tunnel.service
  mooncen-ops-api.service
  mooncen-deployment-worker.service
  mooncen-docker-dev.service
  mooncen-ops-console.service
  mooncen-status-agent.service
)
/bin/systemctl --global mask "${legacy_units[@]}" >/dev/null
quarantine=/var/lib/mooncen-an2p-legacy-credentials
safe_legacy_user_paths quarantine-credentials \
  "$legacy_home" "$legacy_uid" "$legacy_gid" "$quarantine" 0 0 >/dev/null

# A per-user unit file has higher lookup precedence than the global mask.  The
# user manager is stopped, so replace every exact legacy name with a local
# /dev/null mask before it can be recreated.  Preserve old bytes for review.
user_unit_dir=$legacy_home/.config/systemd/user
user_runtime_dir=$legacy_home/.local/share/mooncen-an2p
unit_quarantine=/var/lib/mooncen-an2p-legacy-user-units
safe_legacy_user_paths prepare-user-runtime \
  "$legacy_home" "$legacy_uid" "$legacy_gid" "$unit_quarantine" 0 0 \
  "$pair_path/control/deploy/an2p" "$pair_path/control/tools" 0 >/dev/null

sudoers=/etc/sudoers.d/mooncen-an2p-service-control
sudoers_stage=$(mktemp /etc/sudoers.d/.mooncen-an2p-service-control.XXXXXXXX)
printf '%s\n' \
  "${legacy_user} ALL=(root) NOPASSWD: /usr/local/libexec/mooncen-an2p-service-control docker-select, /usr/local/libexec/mooncen-an2p-service-control native-select, /usr/local/libexec/mooncen-an2p-service-control docker-reload, /usr/local/libexec/mooncen-an2p-service-control runtime-status, /usr/local/libexec/mooncen-an2p-service-control lxd-db-start, /usr/local/libexec/mooncen-an2p-service-control lxd-db-stop, /usr/local/libexec/mooncen-an2p-service-control lxd-db-status" \
  >"$sudoers_stage"
chmod 0440 "$sudoers_stage"
/usr/sbin/visudo -cf "$sudoers_stage" >/dev/null
install -o root -g root -m 0440 "$sudoers_stage" "$sudoers"
rm -f -- "$sudoers_stage"

# Claim 5175 before the unprivileged user manager exists.  These sockets do
# not read a control credential or establish a production connection.  Keep
# their service runtime-masked on the first pair until an exact control
# finalization publishes every private input.  An existing pair is left intact
# here: its control cutover occurs only after the pair manager has durably
# journaled the activation transaction.
if [ ! -e "$pair_pointer" ] && [ ! -L "$pair_pointer" ]; then
  /bin/systemctl mask --runtime --now mooncen-ops-api.service
  /bin/systemctl stop mooncen-ops-db-tunnel.service \
    >/dev/null 2>&1 || true
  /bin/systemctl reset-failed mooncen-ops-api.service \
    mooncen-ops-db-tunnel.service >/dev/null 2>&1 || true
fi
/bin/systemctl daemon-reload
/bin/systemctl enable --now mooncen-an2p-runtime-recovery.service \
  mooncen-ops-api.socket mooncen-ops-api-ipv6.socket \
  mooncen-ops-api-ipv6.service
for unit in mooncen-an2p-runtime-recovery.service mooncen-ops-api.socket \
  mooncen-ops-api-ipv6.socket mooncen-ops-api-ipv6.service; do
  /bin/systemctl is-active --quiet "$unit" || \
    die "development host boundary did not become active: $unit"
done

/usr/bin/loginctl enable-linger "$legacy_user"
/bin/systemctl start "user@${legacy_uid}.service"
/bin/systemctl --user --machine="${legacy_user}@" daemon-reload
/bin/systemctl --user --machine="${legacy_user}@" disable --now \
  "${legacy_units[@]}" >/dev/null 2>&1 || true
for unit in "${legacy_units[@]}"; do
  if /bin/systemctl --user --machine="${legacy_user}@" is-active --quiet "$unit"; then
    die "legacy network/control service restarted: $unit"
  fi
done

if /usr/bin/ss -H -ltn 'sport = :8002' | grep -q .; then
  die "retired Ops API port 8002 is still listening"
fi
[ "$("$selector" runtime-status)" = "$selection_before" ] || \
  die "development preparation changed the active public runtime"

printf '%s\n' \
  "{\"control_finalized\":false,\"development_prepared\":true,\"pair\":\"$pair_name\",\"schema_version\":1}"
