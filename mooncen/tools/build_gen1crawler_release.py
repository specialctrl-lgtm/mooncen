"""Build a deterministic, commit-only release for the legacy gen1crawler owner."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
from pathlib import Path, PurePosixPath


FORMAT = "mooncen-gen1crawler-release-v1"
ARCHIVE_NAME = "gen1crawler-release.tar.gz"
TREE_NAME = "gen1crawler-release.tree"
METADATA_NAME = "gen1crawler-release.env"
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
SAFE_PATH_RE = re.compile(r"[A-Za-z0-9_./+@-]+")
MAX_FILES = 30_000
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
FORBIDDEN_PARTS = {".git", ".venv", "node_modules", "logs", "secrets", "uploads"}
FORBIDDEN_NAMES = {".env", "deploy.local.ps1", "deploy_servers.json"}
REQUIRED_PATHS = {
    "requirements.lock",
    "run_crawlers.py",
    "tools/apply_staging_batch.py",
    "tools/promote_latest_staging_batch.py",
    "deploy/ubuntu/activate_gen1crawler_release.sh",
    "deploy/ubuntu/activate_split_crawler.sh",
    "deploy/ubuntu/install_gen1crawler_release_uploader.sh",
    "deploy/ubuntu/systemd/mooncen-crawler-once.service",
    "deploy/ubuntu/systemd/mooncen-crawler.timer",
    "deploy/ubuntu/systemd/mooncen-staging-apply.service",
    "deploy/ubuntu/systemd/mooncen-staging-apply.timer",
}
RUNTIME_PREFIXES = ("Crawler/", "DB/", "backend/", "config/", "deploy/ubuntu/", "ops_agent/", "tools/", "utils/")
RUNTIME_ROOT_FILES = {
    "data_parser.py",
    "description_cleaner.py",
    "requirements.lock",
    "run_crawlers.py",
    "service_group.py",
    "target_category_fallback.py",
    "target_cleaner.py",
    "title_cleaner.py",
    "utils.py",
}


class BuildError(RuntimeError):
    pass


def git(root: Path, *args: str) -> bytes:
    env = {
        "HOME": os.devnull,
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
    }
    proc = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=root,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode:
        raise BuildError(f"git {args[0]} failed")
    return proc.stdout


def safe_path(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(
        name
        and SAFE_PATH_RE.fullmatch(name)
        and not path.is_absolute()
        and name == path.as_posix()
        and all(part not in {"", ".", ".."} and part not in FORBIDDEN_PARTS for part in path.parts)
        and path.name not in FORBIDDEN_NAMES
    )


def canonical_archive(root: Path, commit: str) -> tuple[bytes, bytes, str, int]:
    names = git(root, "ls-tree", "-r", "--name-only", f"{commit}:mooncen").decode("utf-8").splitlines()
    selected = sorted(
        name
        for name in names
        if name in RUNTIME_ROOT_FILES or name.startswith(RUNTIME_PREFIXES)
    )
    if not selected:
        raise BuildError("release path selection is empty")
    # Do not pass every selected path on the command line.  A real crawler
    # release contains enough files to exceed Windows' process command-line
    # limit.  Read the commit subtree once and retain only the reviewed
    # selection while rebuilding the canonical archive below.
    selected_set = set(selected)
    raw_tar = git(root, "archive", "--format=tar", f"{commit}:mooncen")
    records: list[str] = []
    files: set[str] = set()
    release_files: list[tuple[str, bytes, int]] = []
    with tarfile.open(fileobj=io.BytesIO(raw_tar), mode="r:") as archive:
        members = archive.getmembers()
        if len(members) > MAX_FILES:
            raise BuildError("release contains too many archive members")
        for member in members:
            name = member.name.rstrip("/")
            if member.isdir() or name not in selected_set:
                continue
            if not safe_path(name):
                raise BuildError(f"unsafe release path: {name}")
            if not member.isfile() or member.size < 0 or member.size > MAX_FILE_BYTES:
                raise BuildError(f"non-regular or oversized release member: {name}")
            stream = archive.extractfile(member)
            if stream is None:
                raise BuildError(f"release member cannot be read: {name}")
            content = stream.read(MAX_FILE_BYTES + 1)
            if len(content) != member.size:
                raise BuildError(f"release member size changed: {name}")
            mode = member.mode & 0o777
            if mode not in {0o644, 0o755}:
                mode = 0o755 if mode & 0o111 else 0o644
            files.add(name)
            records.append(f"{mode:04o} {hashlib.sha256(content).hexdigest()} {member.size} {name}")
            release_files.append((name, content, mode))
    missing = sorted(REQUIRED_PATHS - files)
    if missing:
        raise BuildError(f"required release paths are missing: {', '.join(missing)}")
    source_tree = git(root, "rev-parse", f"{commit}:mooncen").decode("ascii").strip()
    header = [FORMAT, f"commit={commit}", f"source_tree={source_tree}", f"file_count={len(records)}", "--files--"]
    tree = ("\n".join([*header, *sorted(records)]) + "\n").encode("utf-8")
    canonical_tar = io.BytesIO()
    with tarfile.open(fileobj=canonical_tar, mode="w", format=tarfile.GNU_FORMAT) as output:
        for name, content, mode in sorted(release_files, key=lambda item: item[0].encode("utf-8")):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = mode
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            info.mtime = 0
            output.addfile(info, io.BytesIO(content))
    compressed = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=compressed, mtime=0, compresslevel=9) as output:
        output.write(canonical_tar.getvalue())
    payload = compressed.getvalue()
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise BuildError("release archive is too large")
    return payload, tree, source_tree, len(records)


def build(repository_root: Path, commit: str, output_directory: Path) -> dict[str, object]:
    root = repository_root.resolve(strict=True)
    if not COMMIT_RE.fullmatch(commit):
        raise BuildError("commit must be an exact SHA-1 object identifier")
    resolved = git(root, "rev-parse", f"{commit}^{{commit}}").decode("ascii").strip()
    if resolved != commit:
        raise BuildError("commit did not resolve exactly")
    archive, tree, source_tree, file_count = canonical_archive(root, commit)
    archive_sha = hashlib.sha256(archive).hexdigest()
    tree_sha = hashlib.sha256(tree).hexdigest()
    metadata = (
        f"FORMAT={FORMAT}\nDEPLOY_COMMIT={commit}\nSOURCE_TREE={source_tree}\n"
        f"ARCHIVE_SHA256={archive_sha}\nTREE_SHA256={tree_sha}\n"
        "NODE_ROLE=crawler\nTARGET_HOSTNAME=gen1crawler\n"
    ).encode("ascii")
    output_directory.mkdir(parents=True, exist_ok=True)
    if output_directory.is_symlink():
        raise BuildError("output directory must not be a symlink")
    paths = {
        "archive": output_directory / ARCHIVE_NAME,
        "tree_manifest": output_directory / TREE_NAME,
        "metadata": output_directory / METADATA_NAME,
    }
    for key, path in paths.items():
        path.write_bytes({"archive": archive, "tree_manifest": tree, "metadata": metadata}[key])
        path.chmod(0o600)
    return {
        "format": FORMAT,
        "commit": commit,
        "source_tree": source_tree,
        "archive_sha256": archive_sha,
        "tree_sha256": tree_sha,
        "file_count": file_count,
        **{key: str(path.resolve()) for key, path in paths.items()},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.repository_root, args.commit, args.output_directory), sort_keys=True))
    except (BuildError, OSError, tarfile.TarError) as exc:
        print(f"gen1crawler release build failed: {exc}", file=os.sys.stderr)
        return 78
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
