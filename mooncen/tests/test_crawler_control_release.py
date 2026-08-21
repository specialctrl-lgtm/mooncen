from __future__ import annotations

import json
from pathlib import Path

import pytest

from ops_agent.crawler_control_provider_scope import reviewed_provider_output_scopes
from ops_agent.crawler_control_scheduler import _load_provider_manifest_details
from tools.build_crawler_control_release import (
    CONTROL_RELEASE_PATHS,
    GitEntry,
    ReleaseBuildError,
    ReleaseFile,
    _selected_entries,
    _validate_provider_ownership_contract,
    _validate_static_python_dependencies,
)


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_reviewed_control_snapshot_is_exactly_42_owners_and_434_non_overlapping_outputs() -> None:
    scopes = reviewed_provider_output_scopes()
    concrete = [provider for values in scopes.values() for provider in values]

    assert len(scopes) == 42
    assert len(concrete) == 434
    assert len(concrete) == len(set(concrete))
    providers, _revision, execution_owners = _load_provider_manifest_details(
        ROOT / "config/production_crawler_providers.yaml"
    )
    assert len(providers) == 434
    assert len(execution_owners) == 434
    assert {owner for _concrete, owner in execution_owners} == set(scopes)


def test_builder_recomputes_committed_runtime_ownership_before_packaging() -> None:
    _validate_provider_ownership_contract(ROOT)
    source = _read("tools/build_crawler_control_release.py")

    assert "from run_crawlers import build_course_provider_owners" in source
    assert "EXPECTED_SCHEDULED_PROVIDER_COUNT" in source
    assert "EXPECTED_CONCRETE_PROVIDER_COUNT" in source
    assert source.index("_validate_provider_ownership_contract(root)") < source.index(
        '_parse_tree(_run_git(root, "ls-tree"'
    )


def test_control_allowlist_is_exact_lightweight_and_has_complete_static_local_imports() -> None:
    assert len(CONTROL_RELEASE_PATHS) == len(set(CONTROL_RELEASE_PATHS))
    assert "run_crawlers.py" not in CONTROL_RELEASE_PATHS
    assert not any(path.startswith("Crawler/") for path in CONTROL_RELEASE_PATHS)
    assert not any(path.startswith(("frontend/", "frontend2/", "backend/")) for path in CONTROL_RELEASE_PATHS)
    assert "requirements.txt" not in CONTROL_RELEASE_PATHS
    assert "deploy/ubuntu/requirements-crawler-control.lock" in CONTROL_RELEASE_PATHS
    assert "config/production_crawler_provider_ownership.json" in CONTROL_RELEASE_PATHS

    release_files = [
        ReleaseFile(path, 0o644, (ROOT / path).read_bytes()) for path in CONTROL_RELEASE_PATHS
    ]
    repository_paths = {
        path.relative_to(ROOT).as_posix()
        for directory in ("DB", "ops_agent", "tools", "utils", "Crawler", "backend")
        for path in (ROOT / directory).rglob("*.py")
    }
    repository_paths.update(
        path.name for path in ROOT.glob("*.py") if path.is_file()
    )
    _validate_static_python_dependencies(release_files, repository_paths)


@pytest.mark.parametrize("mode,kind", [("120000", "blob"), ("160000", "commit")])
def test_control_allowlist_rejects_symlink_and_submodule_modes(mode: str, kind: str) -> None:
    entries = {
        path: GitEntry("100644", "blob", "a" * 40, path)
        for path in CONTROL_RELEASE_PATHS
    }
    first = CONTROL_RELEASE_PATHS[0]
    entries[first] = GitEntry(mode, kind, "b" * 40, first)

    with pytest.raises(ReleaseBuildError, match="symbolic links, submodules"):
        _selected_entries(entries)


def test_control_runtime_is_hash_locked_and_smoked_before_atomic_activation() -> None:
    lock = _read("deploy/ubuntu/requirements-crawler-control.lock")
    activate = _read("deploy/ubuntu/activate_crawler_control_release.sh")

    assert lock.count("--hash=sha256:") == 3
    for dependency in ("psycopg2-binary==2.9.12", "python-dotenv==1.2.2", "PyYAML==6.0.3"):
        assert dependency in lock
    assert "--require-hashes" in activate
    assert "--only-binary=:all:" in activate
    assert "python3.11 -m venv --copies" in activate
    for module in (
        "ops_agent.crawler_control_scheduler",
        "ops_agent.crawler_control_finalizer",
        "ops_agent.crawler_control_metrics",
        "ops_agent.crawler_release_publisher",
        "tools.crawler_control_backup_attestation",
        "tools.approve_crawler_control_batch",
    ):
        assert module in activate
    assert "RUNTIME_LOCK_SHA256" in activate
    assert "RUNTIME_TREE_SHA256" in activate
    assert ".mooncen-control-runtime.manifest" in activate
    assert activate.index("--require-hashes") < activate.index('mv -T -- "$candidate" "$remote_dir"')

    attestation = _read("tools/crawler_control_backup_attestation.py")
    identity_reader = attestation.split("def _read_live_database_identity", 1)[1].split(
        "def _collect_live_contract", 1
    )[0]
    assert "import psycopg2" in identity_reader
    assert "psycopg2.connect(" in identity_reader
    assert "import psycopg\n" not in identity_reader


def test_root_activator_requires_signed_metadata_fsync_and_atomic_rollback() -> None:
    activate = _read("deploy/ubuntu/activate_crawler_control_release.sh")
    transport = _read("deploy/ubuntu/deploy_crawler_control_from_windows.ps1")

    assert "/etc/mooncen/crawler-control-release-allowed-signers" in activate
    assert "ssh-keygen -Y verify" in activate
    assert "mooncen-crawler-control-release" in activate
    assert "chattr +i" in activate
    assert "lsattr -d" in activate
    assert "sync -f" in activate
    assert 'mv -T -- "$remote_dir" "$previous"' in activate
    assert 'mv -T -- "$candidate" "$remote_dir"' in activate
    assert 'mv -T -- "$previous" "$remote_dir"' in activate
    assert "root:mooncen:750" in activate
    assert transport.index("Locally rebuilt release") < transport.index("$remotePreflight")
    assert transport.index("$remotePreflight") < transport.index("mktemp -d /tmp/mooncen-control-upload")
    assert "/usr/local/libexec/mooncen-activate-crawler-control-release" in transport
    assert "sudo -n bash" not in transport
    assert "helper_sha256=" in transport
    assert "crawler-control-release-verified" in transport


def test_root_activator_is_fail_closed_before_mutation_and_sanitizes_privileged_lookup() -> None:
    activate = _read("deploy/ubuntu/activate_crawler_control_release.sh")
    transport = _read("deploy/ubuntu/deploy_crawler_control_from_windows.ps1")

    assert activate.startswith("#!/bin/bash\n")
    assert "PATH=/usr/sbin:/usr/bin:/sbin:/bin" in activate
    assert "unset BASH_ENV" in activate
    direct_gate = activate.index("NOT READY: direct crawler-control activation is disabled")
    assert direct_gate < activate.index("lock_dir=/opt/.mooncen-control-deploy.lock")
    assert direct_gate < activate.index('install -d -o root -g root -m 0700 -- "$ingress" "$candidate"')
    assert "--verify-active" in activate
    assert "MOONCEN_CONTROL_RELEASE_VERIFIED=" in activate

    assert "| /usr/bin/base64 -d | /bin/bash" in transport
    assert "/usr/bin/sudo -n /usr/local/libexec/mooncen-activate-crawler-control-release" in transport
    assert "MOONCEN_CONTROL_RELEASE_ACTIVATED=" in transport
    assert "MOONCEN_CONTROL_RELEASE_VERIFIED=" in transport
    assert "info=/opt/mooncen/.deploy-info" not in transport


def test_root_activator_cleanup_tracks_signal_gaps_and_bounds_retained_trees() -> None:
    activate = _read("deploy/ubuntu/activate_crawler_control_release.sh")

    for signal_status in ("129", "130", "143"):
        assert f"on_signal {signal_status}" in activate
    assert 'candidate_identity="$(stat -c \'%d:%i\' "$candidate")"' in activate
    assert 'previous_moved=1\n  previous_move_started=1\n  mv -T -- "$remote_dir" "$previous"' in activate
    assert 'activation_move_started=1\nmv -T -- "$candidate" "$remote_dir"' in activate
    assert 'remove_current_transaction_tree "$candidate" /opt/.mooncen-control-candidate-' in activate
    assert 'remove_current_transaction_tree "$ingress" /opt/.mooncen-control-ingress-' in activate
    assert ".mooncen-control-previous-*" in activate
    assert ".mooncen-control-failed-*" in activate
    assert "one retained previous release already requires reviewed retirement" in activate
    assert "a failed release tree requires manual forensic review" in activate


def test_ownership_manifest_is_bound_to_provider_bytes() -> None:
    ownership = json.loads(_read("config/production_crawler_provider_ownership.json"))
    assert ownership["format"] == "mooncen-crawler-provider-ownership-v1"
    assert len(ownership["providers_manifest_sha256"]) == 64
