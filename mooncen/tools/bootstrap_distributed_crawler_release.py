"""Install one locally supplied, reviewed crawler rollback baseline.

The central release agent intentionally refuses an automatic first install:
without an already healthy immutable release there is no rollback target.  This
command provides the narrow bootstrap path.  It accepts no URL, verifies a
root-owned local archive against operator-confirmed size/digest and the normal
OpenSSH signing policy, then publishes the immutable release and local state.
It never starts or enables the worker.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ops_agent import crawler_release_agent as release_agent
from ops_agent import crawler_release_control
from ops_agent.crawler_release_control import ArtifactMetadata
from tools.preflight_distributed_crawler_control import (
    PreflightError,
    _protected_environment,
)


FIXED_RELEASE_ENV = Path("/etc/mooncen/crawler-release-agent.env")
BOOTSTRAP_JOURNAL = "bootstrap-pending.json"
COPY_BUFFER_BYTES = 1024 * 1024
MAX_SIGNATURE_BYTES = 1024 * 1024


class BootstrapReleaseError(RuntimeError):
    """The reviewed bootstrap release cannot be installed safely."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _protected_root_file(path: Path, *, label: str, maximum: int | None = None) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise BootstrapReleaseError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_size <= 0
        or (maximum is not None and metadata.st_size > maximum)
        or (os.name == "posix" and (metadata.st_uid != 0 or metadata.st_mode & 0o022))
    ):
        raise BootstrapReleaseError(
            f"{label} must be a nonempty root-owned regular file with no group/world write"
        )
    return metadata


def _copy_and_hash(
    source: Path,
    destination: Path,
    *,
    expected_size: int,
) -> str:
    digest = hashlib.sha256()
    copied = 0
    try:
        source_descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(source_descriptor, "rb") as source_handle:
            destination_descriptor = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(destination_descriptor, "wb") as destination_handle:
                while True:
                    chunk = source_handle.read(COPY_BUFFER_BYTES)
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > expected_size:
                        raise BootstrapReleaseError("bootstrap artifact exceeds confirmed size")
                    digest.update(chunk)
                    destination_handle.write(chunk)
                destination_handle.flush()
                os.fsync(destination_handle.fileno())
    except BootstrapReleaseError:
        destination.unlink(missing_ok=True)
        raise
    except OSError as exc:
        destination.unlink(missing_ok=True)
        raise BootstrapReleaseError("bootstrap artifact could not be copied safely") from exc
    if copied != expected_size:
        destination.unlink(missing_ok=True)
        raise BootstrapReleaseError("bootstrap artifact size differs from confirmation")
    return digest.hexdigest()


def _journal_payload(artifact: ArtifactMetadata, target: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "worker_id": "",  # Filled with the local policy identity before publication.
        "target": target,
        "code_version": artifact.code_version,
        "artifact_digest": artifact.sha256,
        "config_revision": artifact.config_revision,
    }


def _expected_local_state(
    config: release_agent.AgentConfig,
    artifact: ArtifactMetadata,
) -> release_agent.LocalState:
    return release_agent.LocalState(
        worker_id=config.worker_id,
        observed_generation=0,
        applied_generation=0,
        rollout_id="bootstrap",
        current_code_version=artifact.code_version,
        current_artifact_digest=artifact.sha256,
        current_config_revision=artifact.config_revision,
        last_attempt_status="ready",
        updated_at=_utc_now(),
    )


def _state_matches(
    state: release_agent.LocalState,
    expected: release_agent.LocalState,
) -> bool:
    return (
        state.worker_id == expected.worker_id
        and state.observed_generation == 0
        and state.applied_generation == 0
        and state.rollout_id == "bootstrap"
        and state.current_code_version == expected.current_code_version
        and state.current_artifact_digest == expected.current_artifact_digest
        and state.current_config_revision == expected.current_config_revision
        and state.last_attempt_status == "ready"
    )


def _read_bootstrap_journal(path: Path) -> dict[str, Any]:
    try:
        return release_agent._read_private_json(
            path,
            max_bytes=release_agent.MAX_STATUS_DOCUMENT_BYTES,
            label="bootstrap release journal",
        )
    except release_agent.ReleaseAgentError as exc:
        raise BootstrapReleaseError(str(exc)) from exc


def _remove_journal(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
        release_agent._fsync_directory(path.parent)
    except OSError as exc:
        raise BootstrapReleaseError("bootstrap journal could not be cleared") from exc


def _publish_bootstrap(
    config: release_agent.AgentConfig,
    artifact: ArtifactMetadata,
    release_directory: Path,
) -> str:
    target = f"releases/{release_directory.name}"
    expected_state = _expected_local_state(config, artifact)
    journal_path = config.state_directory / BOOTSTRAP_JOURNAL
    expected_journal = {
        **_journal_payload(artifact, target),
        "worker_id": config.worker_id,
    }

    current_exists = config.current_link.exists() or config.current_link.is_symlink()
    state_exists = config.local_state_path.exists() or config.local_state_path.is_symlink()
    journal_exists = journal_path.exists() or journal_path.is_symlink()
    if journal_exists and _read_bootstrap_journal(journal_path) != expected_journal:
        raise BootstrapReleaseError("bootstrap journal conflicts with the reviewed artifact")
    if (current_exists != state_exists) and not journal_exists:
        raise BootstrapReleaseError("partial bootstrap state has no recovery journal")

    if current_exists:
        try:
            if release_agent._current_link_target(config) != target:
                raise BootstrapReleaseError("current crawler release conflicts with bootstrap artifact")
        except release_agent.ReleaseAgentError as exc:
            raise BootstrapReleaseError(str(exc)) from exc
    if state_exists:
        try:
            local = release_agent.load_local_state(config, required=True)
        except release_agent.ReleaseAgentError as exc:
            raise BootstrapReleaseError(str(exc)) from exc
        if not _state_matches(local, expected_state):
            raise BootstrapReleaseError("local release state conflicts with bootstrap artifact")

    if current_exists and state_exists:
        try:
            release_agent._current_release_target(config, local)
        except release_agent.ReleaseAgentError as exc:
            raise BootstrapReleaseError(str(exc)) from exc
        if journal_exists:
            _remove_journal(journal_path)
        return "already-installed"

    if not journal_exists:
        release_agent._atomic_json(journal_path, expected_journal)
    try:
        if not current_exists:
            release_agent._switch_current(config, target)
        if not state_exists:
            release_agent.save_local_state(config, expected_state)
        local = release_agent.load_local_state(config, required=True)
        release_agent._current_release_target(config, local)
    except release_agent.ReleaseAgentError as exc:
        raise BootstrapReleaseError(str(exc)) from exc
    _remove_journal(journal_path)
    return "installed"


def bootstrap_release(
    *,
    environment_path: Path,
    archive_path: Path,
    signature_path: Path,
    key_id: str,
    code_version: str,
    config_revision: str,
    expected_digest: str,
    expected_size: int,
) -> dict[str, Any]:
    """Verify, materialize and atomically initialize one rollback baseline."""

    archive_metadata = _protected_root_file(archive_path, label="bootstrap artifact")
    _protected_root_file(
        signature_path,
        label="bootstrap artifact signature",
        maximum=MAX_SIGNATURE_BYTES,
    )
    if archive_metadata.st_size != expected_size:
        raise BootstrapReleaseError("bootstrap artifact size differs from confirmation")
    try:
        environment = _protected_environment(environment_path, owner_only=True)
        release_agent._secure_existing_file(
            environment_path,
            label="release agent policy file",
        )
        config = release_agent.load_agent_config(environment)
        release_agent.check_configuration(config)
    except (PreflightError, release_agent.ReleaseAgentError) as exc:
        raise BootstrapReleaseError(str(exc)) from exc
    if not config.require_signature:
        raise BootstrapReleaseError("bootstrap requires signature enforcement")

    try:
        signature = signature_path.read_bytes()
    except OSError as exc:
        raise BootstrapReleaseError("bootstrap artifact signature could not be read") from exc
    # Reuse the desired-state parser's strict metadata validation instead of
    # maintaining a second set of version/digest/key-id regular expressions.
    try:
        artifact = crawler_release_control._parse_artifact(
            {
                "code_version": code_version,
                "relative_path": f"bootstrap/{code_version}.tar.gz",
                "sha256": expected_digest,
                "size_bytes": expected_size,
                "config_revision": config_revision,
                "signature": base64.b64encode(signature).decode("ascii"),
                "key_id": key_id,
            }
        )
    except ValueError as exc:
        raise BootstrapReleaseError("bootstrap artifact identity is invalid") from exc
    if key_id not in config.allowed_key_ids:
        raise BootstrapReleaseError("bootstrap key id is outside the local allowlist")

    staged_archive = config.staging_directory / f"bootstrap-{uuid.uuid4().hex}.tar.gz"
    try:
        observed_digest = _copy_and_hash(
            archive_path,
            staged_archive,
            expected_size=expected_size,
        )
        if observed_digest != expected_digest:
            raise BootstrapReleaseError("bootstrap artifact digest differs from confirmation")
        release_agent.verify_artifact_signature(config, artifact, staged_archive)
        release_directory = release_agent.materialize_release(
            config,
            artifact,
            staged_archive,
        )
        for required_entry in (
            release_directory / "ops_agent" / "crawler_worker.py",
            release_directory / "run_crawlers.py",
        ):
            if required_entry.is_symlink() or not required_entry.is_file():
                raise BootstrapReleaseError(
                    f"bootstrap artifact lacks required entrypoint: {required_entry.name}"
                )
        status = _publish_bootstrap(config, artifact, release_directory)
    except release_agent.ReleaseAgentError as exc:
        raise BootstrapReleaseError(str(exc)) from exc
    finally:
        staged_archive.unlink(missing_ok=True)
    return {
        "status": status,
        "worker_id": config.worker_id,
        "code_version": artifact.code_version,
        "artifact_digest": artifact.sha256,
        "config_revision": artifact.config_revision,
        "services_started": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install a signed reviewed crawler rollback baseline"
    )
    parser.add_argument("--release-env", type=Path, default=FIXED_RELEASE_ENV)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--signature", type=Path, required=True)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--code-version", required=True)
    parser.add_argument("--config-revision", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--size-bytes", type=int, required=True)
    args = parser.parse_args(argv)
    if args.release_env != FIXED_RELEASE_ENV:
        parser.error(f"--release-env must be {FIXED_RELEASE_ENV}")
    if args.size_bytes <= 0:
        parser.error("--size-bytes must be positive")
    try:
        result = bootstrap_release(
            environment_path=args.release_env,
            archive_path=args.artifact,
            signature_path=args.signature,
            key_id=args.key_id,
            code_version=args.code_version,
            config_revision=args.config_revision,
            expected_digest=args.sha256,
            expected_size=args.size_bytes,
        )
    except BootstrapReleaseError as exc:
        parser.exit(78, f"crawler baseline bootstrap failed: {exc}\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
