#!/usr/bin/env python3
"""Verify or load a manifest-bound MoonCen Docker image bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.docker.release_manifest import (  # noqa: E402
    IMAGE_ID_PATTERN,
    ManifestError,
    load_json_evidence,
)


MAX_BUNDLE_BYTES = 8 * 1024 * 1024 * 1024
COMMAND_TIMEOUT_SECONDS = 1_800


class VerificationError(RuntimeError):
    """Raised when a saved image bundle differs from its reviewed manifest."""


def _regular_file(path: Path, *, maximum: int) -> Path:
    try:
        resolved_parent = path.parent.resolve(strict=True)
        metadata = path.lstat()
    except OSError as exc:
        raise VerificationError(f"{path.name} cannot be read") from exc
    if path.is_symlink() or not path.is_file() or metadata.st_size <= 0 or metadata.st_size > maximum:
        raise VerificationError(f"{path.name} is unsafe")
    resolved = path.resolve(strict=True)
    if resolved.parent != resolved_parent:
        raise VerificationError(f"{path.name} escaped its reviewed directory")
    return resolved


def _sha256_file(path: Path, *, maximum: int) -> str:
    trusted = _regular_file(path, maximum=maximum)
    digest = hashlib.sha256()
    total = 0
    try:
        with trusted.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                total += len(chunk)
                if total > maximum:
                    raise VerificationError(f"{path.name} exceeds the size limit")
                digest.update(chunk)
    except OSError as exc:
        raise VerificationError(f"{path.name} could not be hashed") from exc
    return digest.hexdigest()


def _docker(arguments: Sequence[str], *, root: Path) -> str:
    environment = os.environ.copy()
    for name in (
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_TLS",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
    ):
        environment.pop(name, None)
    try:
        result = subprocess.run(
            ["docker", *arguments],
            cwd=root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise VerificationError("local Docker could not run") from exc
    if result.returncode != 0:
        raise VerificationError(f"Docker failed during {arguments[0]}")
    return result.stdout.strip()


def require_local_daemon(root: Path) -> None:
    context = _docker(("context", "show"), root=root)
    if context != "default":
        raise VerificationError("bundle verification requires the default Docker context")
    endpoint = _docker(
        (
            "context",
            "inspect",
            "default",
            "--format",
            "{{.Endpoints.docker.Host}}",
        ),
        root=root,
    )
    if endpoint not in {"unix:///var/run/docker.sock", "unix:///run/docker.sock"}:
        raise VerificationError("bundle verification requires the local Docker socket")


def verify_release_artifacts(release_directory: Path) -> dict[str, object]:
    """Verify canonical manifest/bundle/Compose bytes without touching Docker."""

    try:
        directory = release_directory.resolve(strict=True)
        metadata = release_directory.lstat()
    except OSError as exc:
        raise VerificationError("release directory cannot be read") from exc
    if release_directory.is_symlink() or not directory.is_dir() or metadata.st_mode & 0o022:
        raise VerificationError("release directory is unsafe")
    manifest_path = directory / "release.json"
    bundle_path = directory / "images.tar"
    compose_path = directory / "compose.production.yaml"
    try:
        manifest = load_json_evidence(manifest_path)
    except ManifestError as exc:
        raise VerificationError(str(exc)) from exc
    if _sha256_file(bundle_path, maximum=MAX_BUNDLE_BYTES) != manifest["bundle_sha256"]:
        raise VerificationError("image bundle SHA-256 does not match the manifest")
    if _sha256_file(compose_path, maximum=1024 * 1024) != manifest["compose_sha256"]:
        raise VerificationError("Compose SHA-256 does not match the manifest")

    return {
        "release_digest": manifest["release_digest"],
        "source_tree": manifest["source_tree"],
        "bundle_sha256": manifest["bundle_sha256"],
        "compose_sha256": manifest["compose_sha256"],
    }


def verify_release_directory(
    release_directory: Path,
    *,
    load_images: bool,
) -> dict[str, object]:
    artifacts = verify_release_artifacts(release_directory)
    directory = release_directory.resolve(strict=True)
    try:
        manifest = load_json_evidence(directory / "release.json")
    except ManifestError as exc:
        raise VerificationError("release manifest changed during verification") from exc
    bundle_path = directory / "images.tar"
    require_local_daemon(directory)
    if load_images:
        _docker(("image", "load", "--input", str(bundle_path)), root=directory)
    verified_ids: dict[str, str] = {}
    for service, record in manifest["images"].items():
        image_id = _docker(
            ("image", "inspect", "--format", "{{.Id}}", record["tag"]),
            root=directory,
        ).lower()
        if IMAGE_ID_PATTERN.fullmatch(image_id) is None or image_id != record["image_id"]:
            raise VerificationError(f"loaded {service} image ID does not match the manifest")
        verified_ids[service] = image_id
    return {
        **artifacts,
        "image_ids": verified_ids,
        "images_loaded": load_images,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release_directory", type=Path)
    parser.add_argument("--load", action="store_true", help="load images before verifying IDs")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = verify_release_directory(args.release_directory, load_images=args.load)
    except (VerificationError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
