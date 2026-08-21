"""Build the exact manual/out-of-band gen1db root-trust bootstrap bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path


COMMIT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
ARTIFACTS = (
    (
        "deploy/ubuntu/crawler_control_root_trust.py",
        "mooncen-crawler-control-root-trust",
        "/usr/local/libexec/mooncen-crawler-control-root-trust",
        "0755",
    ),
    (
        "tools/crawler_control_backup_attestation.py",
        "mooncen-crawler-control-backup-attestation",
        "/usr/local/libexec/mooncen-crawler-control-backup-attestation",
        "0755",
    ),
    (
        "config/crawler_control_backup_receipt.schema.json",
        "crawler-control-backup-receipt.schema.json",
        "/usr/local/share/mooncen/crawler-control-backup-receipt.schema.json",
        "0444",
    ),
)


class BootstrapBuildError(RuntimeError):
    pass


def _git(root: Path, *arguments: str) -> bytes:
    result = subprocess.run(
        ["git", "-c", "core.autocrlf=false", *arguments],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise BootstrapBuildError("cannot materialize reviewed bootstrap commit")
    return result.stdout


def _write_exclusive(path: Path, data: bytes, mode: int) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if written <= 0:
                raise BootstrapBuildError("short bootstrap artifact write")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")


def build(root: Path, commit: str, output: Path) -> dict[str, object]:
    normalized = commit.strip().lower()
    if not COMMIT.fullmatch(normalized):
        raise BootstrapBuildError("commit must be an exact lowercase Git object id")
    head = _git(root, "rev-parse", "--verify", "HEAD^{commit}").decode("ascii").strip().lower()
    requested = _git(root, "rev-parse", "--verify", f"{normalized}^{{commit}}").decode("ascii").strip().lower()
    if requested != normalized or head != normalized:
        raise BootstrapBuildError("bootstrap must be built from the exact checked-out HEAD commit")
    if _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise BootstrapBuildError("bootstrap requires a completely clean reviewed worktree")
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    records: list[dict[str, object]] = []
    blobs: dict[str, bytes] = {}
    for source, name, target, mode in ARTIFACTS:
        blob = _git(root, "show", f"{normalized}:{source}")
        if not blob or len(blob) > 2 * 1024 * 1024:
            raise BootstrapBuildError(f"bootstrap artifact size is invalid: {source}")
        blobs[name] = blob
        _write_exclusive(output / name, blob, int(mode, 8))
        records.append(
            {
                "group": "root",
                "mode": mode,
                "name": name,
                "owner": "root",
                "sha256": hashlib.sha256(blob).hexdigest(),
                "size_bytes": len(blob),
                "source": source,
                "target": target,
            }
        )
    by_name = {str(record["name"]): record for record in records}
    policy = (
        "FORMAT=mooncen-crawler-control-root-trust-policy-v1\n"
        f"BOOTSTRAP_COMMIT={normalized}\n"
        f"ROOT_TRUST_HELPER_SHA256={by_name['mooncen-crawler-control-root-trust']['sha256']}\n"
        f"EVIDENCE_ENGINE_SHA256={by_name['mooncen-crawler-control-backup-attestation']['sha256']}\n"
        f"RECEIPT_SCHEMA_SHA256={by_name['crawler-control-backup-receipt.schema.json']['sha256']}\n"
    ).encode("ascii")
    _write_exclusive(output / "crawler-control-root-trust.policy", policy, 0o400)
    records.append(
        {
            "group": "root",
            "mode": "0400",
            "name": "crawler-control-root-trust.policy",
            "owner": "root",
            "sha256": hashlib.sha256(policy).hexdigest(),
            "size_bytes": len(policy),
            "source": "generated-from-reviewed-artifact-digests",
            "target": "/etc/mooncen/crawler-control-root-trust.policy",
        }
    )
    manifest = {
        "artifacts": records,
        "commit": normalized,
        "format": "mooncen-crawler-control-root-trust-bootstrap-manifest-v1",
        "install_method": "manual-out-of-band-only",
        "remote_automation_allowed": False,
        "signature_namespace": "mooncen-crawler-control-root-bootstrap-v1",
        "signature_principal": "mooncen-crawler-control-root-bootstrap",
    }
    manifest_bytes = _canonical_json(manifest)
    _write_exclusive(output / "bootstrap-manifest.json", manifest_bytes, 0o400)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output-directory", required=True, type=Path)
    args = parser.parse_args()
    try:
        manifest = build(args.repository_root.resolve(), args.commit, args.output_directory.resolve())
    except BootstrapBuildError as exc:
        parser.exit(78, f"root trust bootstrap build failed: {exc}\n")
    print(_canonical_json(manifest).decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
