"""Build a deterministic, commit-only crawler-worker bootstrap release.

This builder packages only the exact reviewed bootstrap paths below.  It does
not package crawler payload releases, credentials, local configuration, or a
working-tree snapshot.  Per-host inventory and resource bindings are derived
from the committed production topology and covered by the externally signed
metadata document.
"""

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
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, Iterable


FORMAT: Final = "mooncen-crawler-worker-bootstrap-tree-v1"
ROLE: Final = "crawler-worker"
ARCHIVE_NAME: Final = "crawler-worker-bootstrap-release.tar.gz"
MANIFEST_NAME: Final = "crawler-worker-bootstrap-release.tree"
METADATA_NAME: Final = "crawler-worker-bootstrap-release.env"
ACTIVATOR_NAME: Final = "crawler-worker-bootstrap-release-activate.sh"
GENERATED_DROPIN_PATH: Final = (
    "deploy/ubuntu/systemd/mooncen-crawler-pull-worker.service.d/"
    "10-reviewed-worker-resources.conf"
)
MAX_FILE_BYTES: Final = 16 * 1024 * 1024
MAX_ARCHIVE_INPUT_BYTES: Final = 64 * 1024 * 1024
COMMIT_PATTERN: Final = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
SAFE_PATH_PATTERN: Final = re.compile(r"[A-Za-z0-9_./-]+")
WORKER_KEY_PATTERN: Final = re.compile(r"[a-z][a-z0-9_-]{0,63}")
HOSTNAME_PATTERN: Final = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,62})(?:\.[a-z0-9](?:[a-z0-9-]{0,62}))*"
)
MEMORY_PATTERN: Final = re.compile(r"[1-9][0-9]{0,4}[MG]")
CPU_PATTERN: Final = re.compile(r"[1-9][0-9]{0,3}%")

# Exact paths only.  New source or unit files are not deployable until they
# are deliberately reviewed and added here.
WORKER_BOOTSTRAP_RELEASE_PATHS: Final = (
    "config/production_topology.json",
    "Crawler/site_adapters.py",
    "DB/connection_settings.py",
    "ops_agent/__init__.py",
    "ops_agent/crawler_control_db.py",
    "ops_agent/crawler_outcome.py",
    "ops_agent/crawler_registry.py",
    "ops_agent/crawler_release_agent.py",
    "ops_agent/crawler_release_control.py",
    "ops_agent/crawler_release_reporter.py",
    "ops_agent/crawler_worker.py",
    "ops_agent/production_topology.py",
    "tools/bootstrap_distributed_crawler_release.py",
    "tools/activate_crawler_worker_bootstrap_state.py",
    "tools/ops_redaction.py",
    "tools/postgres_scram_verifier.py",
    "tools/preflight_distributed_crawler_control.py",
    "tools/preflight_distributed_crawler_worker_host.py",
    "tools/run_crawler_release_reporter.py",
    "tools/run_distributed_crawler_preflight.py",
    "deploy/ubuntu/activate_crawler_worker_bootstrap_release.sh",
    "deploy/ubuntu/requirements-crawler-worker.lock",
    "deploy/ubuntu/setup_distributed_crawler_worker.sh",
    "deploy/ubuntu/systemd/mooncen-crawler-pull-worker.service",
    "deploy/ubuntu/systemd/mooncen-crawler-release-agent.service",
    "deploy/ubuntu/systemd/mooncen-crawler-release-agent.timer",
    "deploy/ubuntu/systemd/mooncen-crawler-release-reporter.service",
    "deploy/ubuntu/systemd/mooncen-crawler-release-reporter.timer",
    "deploy/ubuntu/systemd/mooncen-crawler-worker.target",
    "deploy/ubuntu/templates/crawler-release-agent.tmpfiles.conf",
)


class WorkerReleaseBuildError(RuntimeError):
    """The reviewed worker bootstrap release cannot be built safely."""


@dataclass(frozen=True)
class GitEntry:
    mode: str
    kind: str
    object_id: str
    path: str


@dataclass(frozen=True)
class ReleaseFile:
    path: str
    mode: int
    content: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


@dataclass(frozen=True)
class WorkerBinding:
    crawler_mode: str
    worker_key: str
    dns_host: str
    kernel_hostname: str
    rollout_order: int
    canary: bool
    enabled: bool
    concurrency: int
    memory_high: str
    memory_max: str
    cpu_quota: str
    topology_sha256: str
    resource_dropin: bytes

    @property
    def resource_dropin_sha256(self) -> str:
        return hashlib.sha256(self.resource_dropin).hexdigest()


def _run_git(root: Path, *arguments: str) -> bytes:
    process = subprocess.run(
        ["git", "-c", "core.quotepath=false", *arguments],
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode:
        raise WorkerReleaseBuildError(f"git {arguments[0]} failed during release build")
    return process.stdout


def _validate_path(path: str) -> None:
    pure = PurePosixPath(path)
    if (
        not path
        or not SAFE_PATH_PATTERN.fullmatch(path)
        or "\\" in path
        or pure.is_absolute()
        or path != pure.as_posix()
        or any(part in {"", ".", ".."} for part in pure.parts)
        or any(part.lower() in {".git", ".venv", "secrets", "state", "uploads"} for part in pure.parts)
        or pure.name.lower() in {".env", ".deploy-info", "deploy.local.ps1", "deploy_servers.json"}
        or pure.name.lower().endswith((".key", ".pem", ".p12", ".pfx", ".secret"))
    ):
        raise WorkerReleaseBuildError(f"unsafe worker release path: {path!r}")


def _parse_tree(raw: bytes) -> dict[str, GitEntry]:
    entries: dict[str, GitEntry] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            header, encoded_path = record.split(b"\t", 1)
            mode, kind, object_id = header.decode("ascii").split(" ", 2)
            path = encoded_path.decode("utf-8", "strict")
        except (ValueError, UnicodeDecodeError) as exc:
            raise WorkerReleaseBuildError("Git tree contains a non-canonical entry") from exc
        _validate_path(path)
        if path in entries:
            raise WorkerReleaseBuildError(f"duplicate Git tree path: {path}")
        entries[path] = GitEntry(mode, kind, object_id, path)
    return entries


def _selected_entries(entries: dict[str, GitEntry]) -> tuple[GitEntry, ...]:
    if len(WORKER_BOOTSTRAP_RELEASE_PATHS) != len(set(WORKER_BOOTSTRAP_RELEASE_PATHS)):
        raise WorkerReleaseBuildError("compiled worker release allowlist contains duplicates")
    selected: list[GitEntry] = []
    for path in WORKER_BOOTSTRAP_RELEASE_PATHS:
        _validate_path(path)
        entry = entries.get(path)
        if entry is None:
            raise WorkerReleaseBuildError(f"reviewed worker release path is missing: {path}")
        if entry.kind != "blob" or entry.mode not in {"100644", "100755"}:
            raise WorkerReleaseBuildError(
                f"symlinks, submodules, and special Git modes are forbidden: {path}"
            )
        selected.append(entry)
    return tuple(sorted(selected, key=lambda item: item.path.encode("utf-8")))


def _canonical_bool(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise WorkerReleaseBuildError(f"worker inventory {field} must be boolean")
    return value


def _worker_binding(topology_bytes: bytes, worker_key: str) -> WorkerBinding:
    # Reuse the authoritative topology contract instead of maintaining a
    # release-specific approximation.  The builder has already required a
    # clean HEAD, so this parser is the exact reviewed parser in that commit.
    try:
        from ops_agent.production_topology import load_production_topology

        with tempfile.TemporaryDirectory(prefix="mooncen-worker-topology-") as temporary:
            temporary_root = Path(temporary)
            config_directory = temporary_root / "config"
            config_directory.mkdir(mode=0o700)
            (config_directory / "production_topology.json").write_bytes(topology_bytes)
            authoritative = load_production_topology(temporary_root)
    except (OSError, ValueError) as exc:
        raise WorkerReleaseBuildError("committed production topology violates the authoritative contract") from exc
    try:
        topology = json.loads(topology_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkerReleaseBuildError("committed production topology is invalid JSON") from exc
    if type(topology) is not dict or topology.get("schemaVersion") != 1:
        raise WorkerReleaseBuildError("committed production topology schema is invalid")
    crawler_mode = topology.get("crawlerMode")
    if crawler_mode not in {"legacy", "distributed"}:
        raise WorkerReleaseBuildError("committed crawler mode is invalid")
    workers = topology.get("crawlerWorkers")
    if type(workers) is not list or not workers:
        raise WorkerReleaseBuildError("committed crawler worker inventory is empty")
    matches = [row for row in workers if type(row) is dict and row.get("workerKey") == worker_key]
    if len(matches) != 1:
        raise WorkerReleaseBuildError("worker key is not uniquely reviewed in production topology")
    row = matches[0]
    try:
        authoritative_worker = authoritative.crawler_worker_for(worker_key)
    except KeyError as exc:
        raise WorkerReleaseBuildError("worker key is absent from the authoritative topology") from exc
    if authoritative.crawler_mode != crawler_mode:
        raise WorkerReleaseBuildError("crawler mode differs from the authoritative topology")
    expected_fields = {
        "workerKey", "topologyNode", "dnsHost", "kernelHostname", "canary",
        "rolloutOrder", "enabled", "resourceLimits",
    }
    if set(row) != expected_fields:
        raise WorkerReleaseBuildError("worker inventory contains unreviewed fields")
    nodes = topology.get("nodes")
    node = nodes.get(row["topologyNode"]) if type(nodes) is dict else None
    resources = row.get("resourceLimits")
    if type(node) is not dict or set(node) != {"dnsHost"} or type(resources) is not dict:
        raise WorkerReleaseBuildError("worker topology-node or resource binding is invalid")
    if set(resources) != {"concurrency", "memoryHigh", "memoryMax", "cpuQuota"}:
        raise WorkerReleaseBuildError("worker resource profile contains unreviewed fields")
    dns_host = row.get("dnsHost")
    kernel_hostname = row.get("kernelHostname")
    rollout_order = row.get("rolloutOrder")
    concurrency = resources.get("concurrency")
    memory_high = resources.get("memoryHigh")
    memory_max = resources.get("memoryMax")
    cpu_quota = resources.get("cpuQuota")
    if (
        not isinstance(dns_host, str)
        or not HOSTNAME_PATTERN.fullmatch(dns_host)
        or node.get("dnsHost") != dns_host
        or not isinstance(kernel_hostname, str)
        or not HOSTNAME_PATTERN.fullmatch(kernel_hostname)
        or isinstance(rollout_order, bool)
        or not isinstance(rollout_order, int)
        or rollout_order < 1
        or isinstance(concurrency, bool)
        or not isinstance(concurrency, int)
        or not 1 <= concurrency <= 32
        or not isinstance(memory_high, str)
        or not MEMORY_PATTERN.fullmatch(memory_high)
        or not isinstance(memory_max, str)
        or not MEMORY_PATTERN.fullmatch(memory_max)
        or not isinstance(cpu_quota, str)
        or not CPU_PATTERN.fullmatch(cpu_quota)
        or authoritative_worker.dns_host != dns_host
        or authoritative_worker.kernel_hostname != kernel_hostname
        or authoritative_worker.rollout_order != rollout_order
        or authoritative_worker.canary is not row.get("canary")
        or authoritative_worker.enabled is not row.get("enabled")
        or authoritative_worker.concurrency != concurrency
        or authoritative_worker.memory_high != memory_high
        or authoritative_worker.memory_max != memory_max
        or authoritative_worker.cpu_quota != cpu_quota
    ):
        raise WorkerReleaseBuildError("worker hostname, rollout, or resource profile is invalid")
    dropin = (
        "# Generated from the signed production topology; do not edit.\n"
        "[Service]\n"
        f"Environment=MOONCEN_REVIEWED_WORKER_KEY={worker_key}\n"
        f"Environment=MOONCEN_REVIEWED_KERNEL_HOSTNAME={kernel_hostname}\n"
        f"Environment=OPS_CRAWLER_MAX_CONCURRENCY={concurrency}\n"
        f"MemoryHigh={memory_high}\n"
        f"MemoryMax={memory_max}\n"
        f"CPUQuota={cpu_quota}\n"
    ).encode("ascii")
    return WorkerBinding(
        crawler_mode=crawler_mode,
        worker_key=worker_key,
        dns_host=dns_host,
        kernel_hostname=kernel_hostname,
        rollout_order=rollout_order,
        canary=_canonical_bool(row.get("canary"), "canary"),
        enabled=_canonical_bool(row.get("enabled"), "enabled"),
        concurrency=concurrency,
        memory_high=memory_high,
        memory_max=memory_max,
        cpu_quota=cpu_quota,
        topology_sha256=hashlib.sha256(topology_bytes).hexdigest(),
        resource_dropin=dropin,
    )


def _manifest(commit: str, binding: WorkerBinding, files: Iterable[ReleaseFile]) -> bytes:
    values = tuple(files)
    lines = [
        f"format={FORMAT}",
        f"commit={commit}",
        f"node_role={ROLE}",
        f"worker_key={binding.worker_key}",
        f"kernel_hostname={binding.kernel_hostname}",
        f"topology_sha256={binding.topology_sha256}",
        f"resource_dropin_sha256={binding.resource_dropin_sha256}",
        f"crawler_mode={binding.crawler_mode}",
        f"file_count={len(values)}",
        "--files--",
    ]
    lines.extend(
        f"{item.mode:04o}\t{len(item.content)}\t{item.sha256}\t{item.path}" for item in values
    )
    return ("\n".join(lines) + "\n").encode("utf-8")


def _add_tar_file(archive: tarfile.TarFile, item: ReleaseFile) -> None:
    info = tarfile.TarInfo(item.path)
    info.size = len(item.content)
    info.mode = item.mode
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.mtime = 0
    info.type = tarfile.REGTYPE
    archive.addfile(info, io.BytesIO(item.content))


def _write_exclusive(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def build_release(root: Path, commit: str, worker_key: str, output_directory: Path) -> dict[str, object]:
    root = root.resolve(strict=True)
    output_directory = output_directory.resolve(strict=True)
    normalized_commit = commit.strip().lower()
    if not root.is_dir() or not output_directory.is_dir() or output_directory.is_symlink():
        raise WorkerReleaseBuildError("repository and output roots must be regular directories")
    if not COMMIT_PATTERN.fullmatch(normalized_commit):
        raise WorkerReleaseBuildError("commit must be an exact lowercase Git object identifier")
    if not WORKER_KEY_PATTERN.fullmatch(worker_key):
        raise WorkerReleaseBuildError("worker key is invalid")
    head = _run_git(root, "rev-parse", "--verify", "HEAD^{commit}").decode("ascii").strip().lower()
    if head != normalized_commit:
        raise WorkerReleaseBuildError("Git HEAD differs from the reviewed worker release commit")
    if _run_git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise WorkerReleaseBuildError("worker release requires a clean Git working tree")
    if _run_git(root, "cat-file", "-t", normalized_commit).decode("ascii").strip() != "commit":
        raise WorkerReleaseBuildError("reviewed release object is not a Git commit")
    tree = _parse_tree(_run_git(root, "ls-tree", "-r", "-z", "--full-tree", normalized_commit))
    selected = _selected_entries(tree)
    topology_entry = tree["config/production_topology.json"]
    topology_bytes = _run_git(root, "cat-file", "blob", topology_entry.object_id)
    binding = _worker_binding(topology_bytes, worker_key)
    if _run_git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise WorkerReleaseBuildError("working tree changed during topology validation")
    files: list[ReleaseFile] = []
    total = 0
    for entry in selected:
        content = _run_git(root, "cat-file", "blob", entry.object_id)
        if len(content) > MAX_FILE_BYTES:
            raise WorkerReleaseBuildError(f"worker release file is too large: {entry.path}")
        total += len(content)
        if total > MAX_ARCHIVE_INPUT_BYTES:
            raise WorkerReleaseBuildError("worker release exceeds its uncompressed input bound")
        files.append(ReleaseFile(entry.path, 0o755 if entry.mode == "100755" else 0o644, content))
    files.append(ReleaseFile(GENERATED_DROPIN_PATH, 0o644, binding.resource_dropin))
    files.sort(key=lambda item: item.path.encode("utf-8"))
    manifest = _manifest(normalized_commit, binding, files)
    tree_sha256 = hashlib.sha256(manifest).hexdigest()
    archive_buffer = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=archive_buffer, mtime=0, compresslevel=9) as zipped:
        with tarfile.open(fileobj=zipped, mode="w", format=tarfile.GNU_FORMAT) as archive:
            for item in files:
                _add_tar_file(archive, item)
            _add_tar_file(archive, ReleaseFile(".mooncen-worker-bootstrap-tree.manifest", 0o444, manifest))
    archive_bytes = archive_buffer.getvalue()
    archive_sha256 = hashlib.sha256(archive_bytes).hexdigest()
    metadata = (
        "FORMAT=mooncen-crawler-worker-bootstrap-release-v1\n"
        f"DEPLOY_COMMIT={normalized_commit}\n"
        f"DEPLOY_ARCHIVE_SHA256={archive_sha256}\n"
        f"DEPLOY_TREE_SHA256={tree_sha256}\n"
        f"NODE_ROLE={ROLE}\n"
        f"CRAWLER_MODE={binding.crawler_mode}\n"
        f"WORKER_KEY={binding.worker_key}\n"
        f"TARGET_DNS_HOST={binding.dns_host}\n"
        f"TARGET_KERNEL_HOSTNAME={binding.kernel_hostname}\n"
        f"TOPOLOGY_SHA256={binding.topology_sha256}\n"
        f"ROLLOUT_ORDER={binding.rollout_order}\n"
        f"CANARY={str(binding.canary).lower()}\n"
        f"WORKER_ENABLED={str(binding.enabled).lower()}\n"
        f"RESOURCE_CONCURRENCY={binding.concurrency}\n"
        f"RESOURCE_MEMORY_HIGH={binding.memory_high}\n"
        f"RESOURCE_MEMORY_MAX={binding.memory_max}\n"
        f"RESOURCE_CPU_QUOTA={binding.cpu_quota}\n"
        f"RESOURCE_DROPIN_SHA256={binding.resource_dropin_sha256}\n"
    ).encode("ascii")
    targets = {
        ARCHIVE_NAME: archive_bytes,
        MANIFEST_NAME: manifest,
        METADATA_NAME: metadata,
        ACTIVATOR_NAME: next(
            item.content
            for item in files
            if item.path == "deploy/ubuntu/activate_crawler_worker_bootstrap_release.sh"
        ),
    }
    for name, content in targets.items():
        target = output_directory / name
        if target.exists() or target.is_symlink():
            raise WorkerReleaseBuildError(f"release output already exists: {name}")
        _write_exclusive(target, content)
    return {
        "format": "mooncen-crawler-worker-bootstrap-build-v1",
        "commit": normalized_commit,
        "node_role": ROLE,
        "worker_key": binding.worker_key,
        "crawler_mode": binding.crawler_mode,
        "worker_enabled": binding.enabled,
        "target_dns_host": binding.dns_host,
        "target_kernel_hostname": binding.kernel_hostname,
        "topology_sha256": binding.topology_sha256,
        "resource_dropin_sha256": binding.resource_dropin_sha256,
        "archive": str(output_directory / ARCHIVE_NAME),
        "archive_sha256": archive_sha256,
        "tree_manifest": str(output_directory / MANIFEST_NAME),
        "tree_sha256": tree_sha256,
        "metadata": str(output_directory / METADATA_NAME),
        "activator": str(output_directory / ACTIVATOR_NAME),
        "activator_sha256": hashlib.sha256(targets[ACTIVATOR_NAME]).hexdigest(),
        "file_count": len(files),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--worker-key", required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        result = build_release(
            arguments.repository_root,
            arguments.commit,
            arguments.worker_key,
            arguments.output_directory,
        )
    except (OSError, WorkerReleaseBuildError) as exc:
        raise SystemExit(f"worker bootstrap release build rejected: {exc}") from exc
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
