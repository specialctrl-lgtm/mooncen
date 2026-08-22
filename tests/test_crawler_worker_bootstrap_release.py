from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.build_crawler_worker_bootstrap_release import (
    GENERATED_DROPIN_PATH,
    WORKER_BOOTSTRAP_RELEASE_PATHS,
    WorkerReleaseBuildError,
    _worker_binding,
)


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "worker_key,hostname,memory_high,memory_max,cpu_quota",
    [
        ("wtr-linux", "sgm-standard-pc-i440fx-piix-1996", "4G", "6G", "300%"),
        ("gen1crawler", "gen1crawler", "2G", "4G", "200%"),
    ],
)
def test_signed_worker_binding_is_derived_from_exact_topology(
    worker_key: str,
    hostname: str,
    memory_high: str,
    memory_max: str,
    cpu_quota: str,
) -> None:
    topology = (ROOT / "config/production_topology.json").read_bytes()
    binding = _worker_binding(topology, worker_key)

    assert binding.kernel_hostname == hostname
    assert binding.topology_sha256 == hashlib.sha256(topology).hexdigest()
    assert f"MOONCEN_REVIEWED_WORKER_KEY={worker_key}".encode() in binding.resource_dropin
    assert f"MemoryHigh={memory_high}".encode() in binding.resource_dropin
    assert f"MemoryMax={memory_max}".encode() in binding.resource_dropin
    assert f"CPUQuota={cpu_quota}".encode() in binding.resource_dropin
    assert len(binding.resource_dropin_sha256) == 64


def test_worker_binding_rejects_unreviewed_inventory_fields() -> None:
    document = json.loads(_read("config/production_topology.json"))
    document["crawlerWorkers"][0]["unreviewed"] = True
    with pytest.raises(WorkerReleaseBuildError, match="authoritative contract|unreviewed fields"):
        _worker_binding(json.dumps(document).encode(), "wtr-linux")


def test_worker_binding_rejects_skipping_disabled_canary() -> None:
    document = json.loads(_read("config/production_topology.json"))
    document["crawlerMode"] = "distributed"
    document["crawlerWorkers"][0]["enabled"] = False
    document["crawlerWorkers"][1]["enabled"] = True
    with pytest.raises(WorkerReleaseBuildError, match="authoritative contract"):
        _worker_binding(json.dumps(document).encode(), "gen1crawler")


def test_worker_bootstrap_allowlist_is_exact_and_excludes_mutable_inputs() -> None:
    assert len(WORKER_BOOTSTRAP_RELEASE_PATHS) == len(set(WORKER_BOOTSTRAP_RELEASE_PATHS))
    assert GENERATED_DROPIN_PATH not in WORKER_BOOTSTRAP_RELEASE_PATHS
    assert "config/production_topology.json" in WORKER_BOOTSTRAP_RELEASE_PATHS
    assert "deploy/ubuntu/setup_distributed_crawler_worker.sh" in WORKER_BOOTSTRAP_RELEASE_PATHS
    assert "deploy/ubuntu/activate_crawler_worker_bootstrap_release.sh" in WORKER_BOOTSTRAP_RELEASE_PATHS
    assert not any(path.startswith(("frontend/", "frontend2/", "backend/")) for path in WORKER_BOOTSTRAP_RELEASE_PATHS)
    assert not any(".env" in Path(path).name for path in WORKER_BOOTSTRAP_RELEASE_PATHS)


def test_fixed_root_helper_is_sanitized_and_topology_gated_before_mutation() -> None:
    source = _read("deploy/ubuntu/activate_crawler_worker_bootstrap_release.sh")
    gate = source.index("NOT READY: signed topology keeps this worker disabled")

    assert source.startswith("#!/bin/bash\n")
    assert "PATH=/usr/sbin:/usr/bin:/sbin:/bin" in source
    assert "unset BASH_ENV" in source
    assert "/etc/mooncen-worker-key" in source
    assert "mooncen-crawler-worker-bootstrap-release" in source
    assert "ssh-keygen -Y verify" in source
    assert "RESOURCE_DROPIN_SHA256" in source
    assert "base=/opt/mooncen-worker" not in source[:gate]
    assert "mv -T" not in source[:gate]
    assert "rm -rf" not in source[:gate]
    assert source.index('crawler_mode="$(read_exact_value CRAWLER_MODE') < gate
    assert source.index('worker_enabled="$(read_exact_value WORKER_ENABLED') < gate


def test_transport_stops_before_ssh_when_topology_is_not_enabled() -> None:
    source = _read("deploy/ubuntu/deploy_crawler_worker_bootstrap_from_windows.ps1")
    gate = source.index("NOT READY: committed topology keeps crawlerMode legacy")

    assert "build_crawler_worker_bootstrap_release.py" in source
    assert "target_dns_host" in source
    assert "activator_sha256" in source
    assert "& scp" not in source[:gate].lower()
    assert "& ssh" not in source[:gate].lower()
    assert "/usr/bin/sudo" not in source[:gate].lower()


def test_runtime_lock_and_atomic_state_machine_are_complete_and_offline() -> None:
    lock = _read("deploy/ubuntu/requirements-crawler-worker.lock")
    engine = _read("tools/activate_crawler_worker_bootstrap_state.py")
    helper = _read("deploy/ubuntu/activate_crawler_worker_bootstrap_release.sh")

    assert lock.count("--hash=sha256:") == 35
    assert "--require-hashes" in engine
    assert '"--no-index"' in engine
    assert '"--only-binary=:all:"' in engine
    assert '"--no-deps"' in engine
    assert "actual != expected" in engine
    assert "generated runtime directory link escapes the release" in engine
    assert "for name in directories:" in engine
    assert "os.replace(temporary, base / \"current\")" in engine
    assert '"phase": "prepared"' in engine
    assert 'transaction["phase"] = "published"' in engine
    assert 'transaction["phase"] = "activated"' in engine
    assert "recover_transactions(base" in engine
    assert "MAX_RELEASES: Final = 3" in engine
    assert "prepared transaction has both candidate and published trees" in engine
    assert "prepared transaction lost both candidate and published trees" in engine
    assert "_validate_published_release(published, transaction)" in engine
    assert "_remove_tree(published, releases)" in engine
    assert "if _current_target(base):\n        _prune_releases(base)" in engine
    assert 'original.add_note("; ".join(cleanup_errors))' in engine
    assert "[ -x /usr/bin/chattr ]" in helper
    assert "/opt/mooncen-worker" in helper
    assert "/var/cache/mooncen-worker/wheelhouse" in helper
    installer = _read("deploy/ubuntu/setup_distributed_crawler_worker.sh")
    assert "release_lock=/run/lock/mooncen-worker-bootstrap-release.lock" in installer
    assert "assert_active_release_pinned" in installer
    assert "stat -c '%U:%G' -h" not in installer
    assert "stat -c '%U:%G' -- \"$APP_DIR\"" in installer
    assert "initial-bootstrap-only" in helper
    assert installer.index("systemctl enable mooncen-crawler-worker.target") > installer.index(
        "--require-current-health"
    )


def test_legacy_mutable_worker_setup_remains_unconditionally_blocked() -> None:
    source = _read("deploy/ubuntu/setup_distributed_crawler_worker.sh")
    gate = source.index("NOT READY: distributed crawler worker installation")
    first_lock = source.index("installer_lock_dir=")
    assert gate < first_lock
    assert "No files or units were changed" in source[gate : gate + 400]
