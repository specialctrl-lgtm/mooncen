#!/usr/bin/env python3
"""Build one reviewed Docker release bundle from an isolated clean checkout.

The command intentionally has no deployment or registry push capability.  It
builds the cloud target platform once, saves the exact local images, and emits
a canonical manifest that a separate development validator must bind to a PASS
receipt before production promotion.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.docker.release_manifest import (  # noqa: E402
    GIT_OBJECT_PATTERN,
    IMAGE_ID_PATTERN,
    PLATFORM_PATTERN,
    create_release_manifest,
    write_json_evidence,
)
from deploy.docker.production_runtime_integrity import (  # noqa: E402
    BUILD_POLICY_PATHS as BUILD_POLICY_PATHS,
    RuntimeIntegrityError,
    build_policy_digest,
)
from deploy.docker.verify_clean_source import (  # noqa: E402
    SourceVerificationError,
    require_clean_source,
)


COMMAND_TIMEOUT_SECONDS = 3_600
MAX_BUNDLE_BYTES = 8 * 1024 * 1024 * 1024
DOCKERFILE_BY_SERVICE = {
    "api": "deploy/docker/api.Dockerfile",
    "frontend": "deploy/docker/frontend.Dockerfile",
}
class BuildError(RuntimeError):
    """Raised when a bundle cannot be built with trustworthy provenance."""


def _run(
    arguments: Sequence[str],
    *,
    root: Path,
    timeout: int = COMMAND_TIMEOUT_SECONDS,
) -> str:
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith("GIT_CONFIG_"):
            environment.pop(name, None)
    for name in (
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_TLS",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    ):
        environment.pop(name, None)
    try:
        result = subprocess.run(
            list(arguments),
            cwd=root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BuildError(f"{arguments[0]} could not run") from exc
    if result.returncode != 0:
        operation = " ".join(arguments[:2])
        raise BuildError(f"release build failed during {operation}")
    return result.stdout.strip()


def _sha256_file(path: Path, *, maximum: int | None = None) -> str:
    digest = hashlib.sha256()
    total = 0
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                total += len(chunk)
                if maximum is not None and total > maximum:
                    raise BuildError(f"{path.name} exceeds the release size limit")
                digest.update(chunk)
    except OSError as exc:
        raise BuildError(f"{path.name} could not be hashed") from exc
    return digest.hexdigest()


def _git(root: Path, *arguments: str) -> str:
    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise BuildError("reviewed Git checkout does not exist") from exc
    return _run(
        (
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            f"safe.directory={resolved}",
            *arguments,
        ),
        root=resolved,
        timeout=60,
    )


def attest_checkout(
    root: Path,
    *,
    base_commit: str,
    source_tree: str,
    snapshot_commit: str,
) -> None:
    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise BuildError("reviewed source checkout does not exist") from exc
    if not resolved.is_dir() or resolved.is_symlink():
        raise BuildError("reviewed source checkout is unsafe")
    if any(
        pattern.fullmatch(value) is None
        for pattern, value in (
            (GIT_OBJECT_PATTERN, base_commit),
            (GIT_OBJECT_PATTERN, source_tree),
            (GIT_OBJECT_PATTERN, snapshot_commit),
        )
    ):
        raise BuildError("reviewed Git identifiers are invalid")
    if _git(resolved, "rev-parse", "--show-toplevel") != str(resolved):
        raise BuildError("release build must run at the checkout root")
    if _git(resolved, "rev-parse", "--verify", "HEAD^{commit}").lower() != snapshot_commit:
        raise BuildError("checkout HEAD does not match the reviewed snapshot commit")
    if _git(resolved, "rev-parse", "--verify", "HEAD^{tree}").lower() != source_tree:
        raise BuildError("checkout tree does not match the reviewed source tree")
    if _git(resolved, "rev-parse", "--verify", "HEAD^1").lower() != base_commit:
        raise BuildError("snapshot parent does not match the reviewed base commit")
    try:
        require_clean_source(resolved)
    except SourceVerificationError as exc:
        raise BuildError("Docker build inputs do not match the reviewed snapshot") from exc


def require_local_docker(root: Path) -> None:
    context = _run(("docker", "context", "show"), root=root, timeout=30)
    if context != "default":
        raise BuildError("release builds require the default local Docker context")
    endpoint = _run(
        (
            "docker",
            "context",
            "inspect",
            "default",
            "--format",
            "{{.Endpoints.docker.Host}}",
        ),
        root=root,
        timeout=30,
    )
    if endpoint not in {"unix:///var/run/docker.sock", "unix:///run/docker.sock"}:
        raise BuildError("release builds require the local root-owned Docker socket")
    info = _run(
        (
            "docker",
            "info",
            "--format",
            "{{.OSType}}/{{.Architecture}}",
        ),
        root=root,
        timeout=30,
    )
    if info != "linux/x86_64":
        raise BuildError("the reviewed builder must be a Linux amd64 Docker daemon")


def _policy_digest(root: Path) -> str:
    try:
        return build_policy_digest(root)
    except RuntimeIntegrityError as exc:
        raise BuildError(str(exc)) from exc


def migration_ledger_digest(root: Path) -> str:
    migration_root = root / "DB" / "migrations"
    records: list[dict[str, str]] = []
    for path in sorted(migration_root.glob("*.sql")):
        if path.is_symlink() or not path.is_file():
            raise BuildError("migration ledger contains an unsafe path")
        records.append({"version": path.stem, "checksum": _sha256_file(path)})
    if not records:
        raise BuildError("migration ledger is empty")
    encoded = json.dumps(
        records,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _private_release_directory(output_root: Path, source_tree: str) -> Path:
    try:
        root = output_root.resolve(strict=True)
        metadata = root.lstat()
    except OSError as exc:
        raise BuildError("release output root does not exist") from exc
    if root.is_symlink() or not root.is_dir() or metadata.st_uid != os.getuid():
        raise BuildError("release output root is unsafe")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise BuildError("release output root must not be accessible by group or others")
    release = root / source_tree
    try:
        release.mkdir(mode=0o700, parents=False, exist_ok=False)
    except OSError as exc:
        raise BuildError("release output directory already exists or cannot be created") from exc
    return release


def _build_image(
    root: Path,
    *,
    service: str,
    source_tree: str,
    snapshot_commit: str,
    platform: str,
    release_dir: Path,
) -> tuple[str, str]:
    tag = f"mooncen/{service}:release-{source_tree}"
    iidfile = release_dir / f".{service}.iid"
    try:
        _run(
            (
                "docker",
                "build",
                "--pull",
                "--no-cache",
                "--platform",
                platform,
                "--label",
                f"kr.mooncen.source_tree={source_tree}",
                "--label",
                f"org.opencontainers.image.revision={snapshot_commit}",
                "--iidfile",
                str(iidfile),
                "--tag",
                tag,
                "--file",
                DOCKERFILE_BY_SERVICE[service],
                ".",
            ),
            root=root,
        )
    except BuildError as exc:
        raise BuildError(
            f"release build failed during {service} Docker image build"
        ) from exc
    try:
        image_id = iidfile.read_text(encoding="ascii").strip().lower()
        iidfile.unlink()
    except OSError as exc:
        raise BuildError(f"Docker did not write the {service} image ID") from exc
    if IMAGE_ID_PATTERN.fullmatch(image_id) is None:
        raise BuildError(f"Docker returned an invalid {service} image ID")
    inspected = _run(
        ("docker", "image", "inspect", "--format", "{{.Id}}", tag),
        root=root,
        timeout=60,
    ).lower()
    if inspected != image_id:
        raise BuildError(f"the {service} image tag changed during the build")
    return tag, image_id


def build_release(
    *,
    root: Path,
    output_root: Path,
    base_commit: str,
    source_tree: str,
    snapshot_commit: str,
    platform: str,
    created_at: str,
) -> tuple[Path, dict[str, object]]:
    normalized_root = root.resolve(strict=True)
    if PLATFORM_PATTERN.fullmatch(platform) is None or platform != "linux/amd64":
        raise BuildError("the production release platform must be linux/amd64")
    attest_checkout(
        normalized_root,
        base_commit=base_commit,
        source_tree=source_tree,
        snapshot_commit=snapshot_commit,
    )
    require_local_docker(normalized_root)
    release_dir = _private_release_directory(output_root, source_tree)
    images: dict[str, dict[str, str]] = {}
    for service in ("api", "frontend"):
        tag, image_id = _build_image(
            normalized_root,
            service=service,
            source_tree=source_tree,
            snapshot_commit=snapshot_commit,
            platform=platform,
            release_dir=release_dir,
        )
        images[service] = {"tag": tag, "image_id": image_id}

    bundle = release_dir / "images.tar"
    _run(
        (
            "docker",
            "image",
            "save",
            "--output",
            str(bundle),
            images["api"]["tag"],
            images["frontend"]["tag"],
        ),
        root=normalized_root,
    )
    try:
        bundle.chmod(0o600)
    except OSError as exc:
        raise BuildError("release image bundle permissions could not be restricted") from exc
    bundle_sha256 = _sha256_file(bundle, maximum=MAX_BUNDLE_BYTES)
    for service, record in images.items():
        inspected = _run(
            (
                "docker",
                "image",
                "inspect",
                "--format",
                "{{.Id}}",
                record["tag"],
            ),
            root=normalized_root,
            timeout=60,
        ).lower()
        if inspected != record["image_id"]:
            raise BuildError(f"the {service} image tag changed before manifest creation")

    compose_source = normalized_root / "deploy" / "docker" / "compose.production.yaml"
    compose_sha256 = _sha256_file(compose_source)
    compose_copy = release_dir / "compose.production.yaml"
    try:
        shutil.copyfile(compose_source, compose_copy)
        compose_copy.chmod(0o644)
    except OSError as exc:
        raise BuildError("production Compose contract could not be copied") from exc
    if _sha256_file(compose_copy) != compose_sha256:
        raise BuildError("production Compose copy changed during release creation")
    manifest = create_release_manifest(
        base_commit=base_commit,
        source_tree=source_tree,
        snapshot_commit=snapshot_commit,
        platform=platform,
        bundle_sha256=bundle_sha256,
        compose_sha256=compose_sha256,
        build_policy_sha256=_policy_digest(normalized_root),
        migration_ledger_sha256=migration_ledger_digest(normalized_root),
        images=images,
        created_at=created_at,
    )
    manifest_path = release_dir / "release.json"
    write_json_evidence(manifest_path, manifest)
    return manifest_path, manifest


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--base-commit", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--snapshot-commit", required=True)
    parser.add_argument("--platform", default="linux/amd64")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    created_at = datetime.now(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    try:
        manifest_path, manifest = build_release(
            root=args.source_root,
            output_root=args.output_root,
            base_commit=args.base_commit.strip().lower(),
            source_tree=args.source_tree.strip().lower(),
            snapshot_commit=args.snapshot_commit.strip().lower(),
            platform=args.platform.strip().lower(),
            created_at=created_at,
        )
    except (BuildError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "manifest_path": str(manifest_path),
                "release_digest": manifest["release_digest"],
                "source_tree": manifest["source_tree"],
                "bundle_sha256": manifest["bundle_sha256"],
                "image_ids": {
                    name: record["image_id"]
                    for name, record in manifest["images"].items()
                },
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
