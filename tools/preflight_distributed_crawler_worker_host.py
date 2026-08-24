"""Validate the local trust policy and reviewed crawler rollback baseline.

This preflight is deliberately local-only.  It never fetches desired state,
changes a release, or starts a service.  Database/RLS validation remains in
``preflight_distributed_crawler_control`` and is run with each service login by
the worker-host installer.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path, PurePosixPath
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ops_agent import crawler_release_agent as release_agent
from ops_agent.crawler_release_control import ArtifactMetadata
from ops_agent.production_topology import CrawlerWorker, load_production_topology
from tools.preflight_distributed_crawler_control import (
    PreflightError,
    _protected_environment,
)


FIXED_RELEASE_ENV = Path("/etc/mooncen/crawler-release-agent.env")
RELEASE_ENV_KEYS = frozenset(
    {
        release_agent.WORKER_CODE_VERSION_ENV,
        release_agent.WORKER_ARTIFACT_DIGEST_ENV,
        release_agent.WORKER_CONFIG_REVISION_ENV,
    }
)
MAX_RELEASE_ENV_BYTES = 4 * 1024


class WorkerHostPreflightError(RuntimeError):
    """A local worker-host trust or baseline contract is unsafe."""


def validate_reviewed_worker_assignment(
    worker_key: str,
    kernel_hostname: str,
    *,
    root: Path = PROJECT_ROOT,
    require_enabled: bool = False,
) -> CrawlerWorker:
    """Bind an environment identity to exactly one reviewed fleet entry."""

    if not worker_key or worker_key != worker_key.strip():
        raise WorkerHostPreflightError("worker key is not canonical")
    if (
        not kernel_hostname
        or kernel_hostname != kernel_hostname.strip()
        or kernel_hostname != kernel_hostname.lower()
        or kernel_hostname.endswith(".")
    ):
        raise WorkerHostPreflightError("kernel hostname is not canonical lowercase text")
    try:
        topology = load_production_topology(root)
        worker = topology.crawler_worker_for(worker_key)
    except (KeyError, ValueError) as exc:
        raise WorkerHostPreflightError(str(exc)) from exc
    if worker.kernel_hostname != kernel_hostname:
        raise WorkerHostPreflightError(
            "kernel hostname does not match the reviewed worker assignment"
        )
    topology_node = topology.nodes.get(worker.topology_node)
    if topology_node is None or topology_node.dns_host != worker.dns_host:
        raise WorkerHostPreflightError(
            "reviewed worker DNS does not match its topology node"
        )
    if require_enabled and not worker.enabled:
        raise WorkerHostPreflightError(
            "reviewed worker is disabled and pending rollout approval"
        )
    return worker


def render_reviewed_worker_systemd_drop_in(worker: CrawlerWorker) -> str:
    """Render the only accepted pull-worker runtime resource override."""

    return (
        "# Generated from config/production_topology.json; do not edit.\n"
        "[Service]\n"
        f"Environment=OPS_CRAWLER_MAX_CONCURRENCY={worker.concurrency}\n"
        f"MemoryHigh={worker.memory_high}\n"
        f"MemoryMax={worker.memory_max}\n"
        f"CPUQuota={worker.cpu_quota}\n"
    )


def _release_identity(path: Path) -> dict[str, str]:
    """Read the generated, immutable release.env without shell evaluation."""

    try:
        metadata = path.lstat()
        if path.is_symlink() or not path.is_file():
            raise WorkerHostPreflightError("current release.env is not a regular file")
        if metadata.st_size <= 0 or metadata.st_size > MAX_RELEASE_ENV_BYTES:
            raise WorkerHostPreflightError("current release.env size is invalid")
        encoded = path.read_bytes()
    except OSError as exc:
        raise WorkerHostPreflightError("current release.env is unavailable") from exc

    try:
        lines = encoded.decode("ascii").splitlines()
    except UnicodeError as exc:
        raise WorkerHostPreflightError("current release.env must be ASCII") from exc
    values: dict[str, str] = {}
    for line in lines:
        if not line or "=" not in line:
            raise WorkerHostPreflightError("current release.env has an invalid entry")
        key, value = line.split("=", 1)
        if key not in RELEASE_ENV_KEYS or key in values or not value or value != value.strip():
            raise WorkerHostPreflightError("current release.env identity is invalid")
        values[key] = value
    if set(values) != RELEASE_ENV_KEYS:
        raise WorkerHostPreflightError("current release.env identity is incomplete")
    return values


def validate_bootstrap_baseline(
    config: release_agent.AgentConfig,
) -> dict[str, Any]:
    """Require one immutable current release and matching durable local state."""

    for unsafe_journal in (
        config.pending_switch_path,
        config.terminal_failure_path,
        config.state_directory / "bootstrap-pending.json",
    ):
        if unsafe_journal.exists() or unsafe_journal.is_symlink():
            raise WorkerHostPreflightError(
                f"unfinished release journal blocks canary: {unsafe_journal.name}"
            )
    try:
        local = release_agent.load_local_state(config, required=True)
        target = release_agent._current_release_target(config, local)
    except release_agent.ReleaseAgentError as exc:
        raise WorkerHostPreflightError(str(exc)) from exc
    if not (
        local.current_code_version
        and local.current_artifact_digest
        and local.current_config_revision
    ):
        raise WorkerHostPreflightError("reviewed rollback baseline identity is empty")

    relative = PurePosixPath(target)
    release_directory = config.release_root.joinpath(*relative.parts)
    identity = _release_identity(release_directory / release_agent.RELEASE_ENV_NAME)
    expected = {
        release_agent.WORKER_CODE_VERSION_ENV: local.current_code_version,
        release_agent.WORKER_ARTIFACT_DIGEST_ENV: local.current_artifact_digest,
        release_agent.WORKER_CONFIG_REVISION_ENV: local.current_config_revision,
    }
    if identity != expected:
        raise WorkerHostPreflightError(
            "current release.env differs from immutable release metadata"
        )
    for required_entry in (
        release_directory / "ops_agent" / "crawler_worker.py",
        release_directory / "run_crawlers.py",
    ):
        if required_entry.is_symlink() or not required_entry.is_file():
            raise WorkerHostPreflightError(
                f"reviewed rollback baseline lacks {required_entry.name}"
            )
    return {
        "baseline_ready": True,
        "current_target": target,
        "code_version": local.current_code_version,
        "artifact_digest": local.current_artifact_digest,
        "config_revision": local.current_config_revision,
    }


def validate_current_health(
    config: release_agent.AgentConfig,
    baseline: dict[str, Any],
) -> dict[str, Any]:
    """Require a fresh worker health document bound to the current baseline."""

    artifact = ArtifactMetadata(
        code_version=str(baseline["code_version"]),
        relative_path="local/current.tar.gz",
        sha256=str(baseline["artifact_digest"]),
        size_bytes=1,
        config_revision=str(baseline["config_revision"]),
    )
    try:
        healthy = release_agent._recent_health_matches(config, artifact)
    except release_agent.ReleaseAgentError as exc:
        raise WorkerHostPreflightError(str(exc)) from exc
    if not healthy:
        raise WorkerHostPreflightError(
            "current worker has not published fresh release-bound health"
        )
    return {"current_health_ready": True}


def preflight_release_policy(
    environment_path: Path,
    *,
    require_baseline: bool,
    require_current_health: bool = False,
) -> dict[str, Any]:
    """Validate an installed root policy and optionally its rollback baseline."""

    try:
        environment = _protected_environment(environment_path, owner_only=True)
        release_agent._secure_existing_file(
            environment_path,
            label="release agent policy file",
        )
        config = release_agent.load_agent_config(environment)
        result: dict[str, Any] = release_agent.check_configuration(config)
    except (PreflightError, release_agent.ReleaseAgentError) as exc:
        raise WorkerHostPreflightError(str(exc)) from exc
    if require_current_health and not require_baseline:
        raise WorkerHostPreflightError("current health requires baseline validation")
    if require_baseline:
        baseline = validate_bootstrap_baseline(config)
        result.update(baseline)
        if require_current_health:
            result.update(validate_current_health(config, baseline))
    else:
        result["baseline_ready"] = False
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Preflight a distributed crawler worker host without network access"
    )
    parser.add_argument("--release-env", type=Path, default=FIXED_RELEASE_ENV)
    parser.add_argument("--require-baseline", action="store_true")
    parser.add_argument("--require-current-health", action="store_true")
    parser.add_argument("--worker-key", default="")
    parser.add_argument("--kernel-hostname", default="")
    parser.add_argument("--inventory-only", action="store_true")
    parser.add_argument("--require-enabled", action="store_true")
    parser.add_argument("--render-systemd-drop-in", action="store_true")
    args = parser.parse_args(argv)
    if args.release_env != FIXED_RELEASE_ENV:
        parser.error(f"--release-env must be {FIXED_RELEASE_ENV}")
    if bool(args.worker_key) != bool(args.kernel_hostname):
        parser.error("--worker-key and --kernel-hostname are required together")
    if args.inventory_only and not args.worker_key:
        parser.error("--inventory-only requires a worker identity")
    if args.render_systemd_drop_in and not args.inventory_only:
        parser.error("--render-systemd-drop-in requires --inventory-only")
    if args.inventory_only and (args.require_baseline or args.require_current_health):
        parser.error("inventory-only validation cannot inspect a release baseline")
    try:
        worker = (
            validate_reviewed_worker_assignment(
                args.worker_key,
                args.kernel_hostname,
                require_enabled=args.require_enabled,
            )
            if args.worker_key
            else None
        )
        if args.inventory_only:
            assert worker is not None
            if args.render_systemd_drop_in:
                print(render_reviewed_worker_systemd_drop_in(worker), end="")
                return 0
            result: dict[str, Any] = {
                "worker_assignment": worker.public_dict(),
                "desired_state": "enabled" if worker.enabled else "pending_disabled",
                "observed_state": "not_checked",
            }
        else:
            result = preflight_release_policy(
                args.release_env,
                require_baseline=args.require_baseline,
                require_current_health=args.require_current_health,
            )
            if worker is not None:
                result["worker_assignment"] = worker.public_dict()
    except WorkerHostPreflightError as exc:
        parser.exit(78, f"crawler worker host preflight failed: {exc}\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
