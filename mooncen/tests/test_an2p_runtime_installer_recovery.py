from __future__ import annotations

import ast
import grp
import hashlib
import json
import os
import pwd
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "deploy/an2p/install_runtime_snapshot.sh"
BOOTSTRAP = ROOT / "deploy/an2p/bootstrap_runtime_installer.sh"
PAIR_NAME = f"runtime-pair.{'1' * 40}.{'2' * 40}.{'3' * 64}"
LEGACY_UID = 1000
PRIVILEGED_GIDS = frozenset({998, 999})
LEGACY_USER_CONTROL_UNITS = (
    "mooncen-ops-control-env.service",
    "mooncen-ops-db-tunnel.service",
    "mooncen-ops-api.service",
    "mooncen-deployment-worker.service",
    "mooncen-docker-dev.service",
    "mooncen-ops-console.service",
    "mooncen-status-agent.service",
)


def _fragment() -> str:
    source = INSTALLER.read_text(encoding="utf-8")
    return source.split('if [ -e "$publish_journal" ]', 1)[1].split(
        "\nenv -i HOME=/root", 1
    )[0]


def _legacy_alias_fragment() -> str:
    source = INSTALLER.read_text(encoding="utf-8")
    body = source.split("migrate_legacy_alias() {", 1)[1].split(
        '\nmigrate_legacy_alias "$control_alias_root" control', 1
    )[0]
    return "migrate_legacy_alias() {" + body


def _cleanup_fragment() -> str:
    source = INSTALLER.read_text(encoding="utf-8")
    body = source.split("cleanup() {", 1)[1].split("\n}\ntrap cleanup EXIT", 1)[0]
    return "cleanup() {" + body + "\n}"


def _finish_pair_fragment() -> str:
    source = INSTALLER.read_text(encoding="utf-8")
    body = source.split("finish_pair_install() {", 1)[1].split(
        "\n}\n\nverify_docker_prerequisites",
        1,
    )[0]
    return "finish_pair_install() {" + body + "\n}"


def _legacy_user_mask_verifier() -> str:
    source = INSTALLER.read_text(encoding="utf-8")
    function = source.split("verify_legacy_user_unit_masks() {", 1)[1].split(
        "\n}\n\nensure_account() {",
        1,
    )[0]
    return function.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]


def _identity() -> tuple[str, str]:
    return (
        pwd.getpwuid(os.getuid()).pw_name,
        grp.getgrgid(os.getgid()).gr_name,
    )


def _journal(path: Path) -> None:
    value = {
        "build_policy_sha256": "3" * 64,
        "commit": "1" * 40,
        "host_transition": False,
        "pair_name": PAIR_NAME,
        "schema_version": 1,
        "source_tree": "2" * 40,
        "transition_from_host_layer": None,
        "transition_from_pair": None,
    }
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="ascii",
    )
    path.chmod(0o600)


def _script(
    tmp_path: Path,
    *,
    manager: Path,
    host_transition: bool = False,
    host_transition_helper: Path = Path("/nonexistent"),
) -> str:
    user, group = _identity()
    pair_root = tmp_path / "pairs"
    pair_releases = pair_root / "releases"
    evidence_root = tmp_path / "evidence"
    state_root = tmp_path / "state"
    pair_releases.mkdir(parents=True)
    evidence_root.mkdir()
    state_root.mkdir()
    install_lock = state_root / "install.lock"
    install_lock.write_text("", encoding="ascii")
    install_lock.chmod(0o600)
    pair_stage = pair_releases / ".stage.test"
    pair_stage.mkdir()
    operator_output = state_root / "build"
    operator_output.mkdir()
    fragment = 'if [ -e "$publish_journal" ]' + _fragment()
    fragment = (
        fragment.replace("root:root:600", f"{user}:{group}:600")
        .replace("root:root:755", f"{user}:{group}:755")
        .replace('"root:${docker_user}:750"', f'"{user}:{group}:750"')
        .replace('"root:${docker_user}:640"', f'"{user}:{group}:640"')
    )
    manager_sha = hashlib.sha256(manager.read_bytes()).hexdigest()
    transition_sha = (
        hashlib.sha256(host_transition_helper.read_bytes()).hexdigest()
        if host_transition_helper.is_file()
        else "0" * 64
    )
    previous_pair = f"runtime-pair.{'4' * 40}.{'5' * 40}.{'6' * 64}"
    finish_function = (
        _finish_pair_fragment()
        if host_transition
        else f"finish_pair_install() {{ : >{str(tmp_path / 'resumed')!r}; }}"
    )
    return f"""
set -euo pipefail
die() {{ printf '%s\n' "$*" >&2; exit 78; }}
declare -A trust=([PAIR_MANAGER_SHA256]={manager_sha} [HOST_TRANSITION_SHA256]={transition_sha})
pair_name={PAIR_NAME!r}
commit={'1' * 40!r}
source_tree={'2' * 40!r}
build_policy={'3' * 64!r}
docker_user={group!r}
pair_root={str(pair_root)!r}
pair_releases={str(pair_releases)!r}
pair_final={str(pair_releases / PAIR_NAME)!r}
evidence_root={str(evidence_root)!r}
evidence_target={str(evidence_root / ('2' * 40))!r}
state_root={str(state_root)!r}
publish_journal={str(state_root / 'install-transaction.json')!r}
manager={str(manager)!r}
selector=/nonexistent
pair_stage={str(pair_stage)!r}
operator_output={str(operator_output)!r}
pair_published=false
evidence_published=false
resume_required=false
activated=false
activation_attempted=false
activation_previous_kind=
activation_previous_selection=
previous_pair=
host_transition_requested={'true' if host_transition else 'false'}
transition_from_pair={previous_pair if host_transition else ''!r}
transition_from_host_layer={'8' * 64 if host_transition else ''!r}
host_layer_sha=
host_transition_helper={str(host_transition_helper)!r}
runtime_install_lock={str(install_lock)!r}
acquire_runtime_install_lock() {{ exec 8<>"$runtime_install_lock"; /usr/bin/flock -x 8; }}
acquire_runtime_install_lock
{finish_function}
{fragment}
: >{str(tmp_path / 'continued')!r}
"""


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash unavailable")
def test_orphan_evidence_crash_is_purged_only_with_exact_journal(tmp_path: Path) -> None:
    manager = tmp_path / "manager"
    manager.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    manager.chmod(0o755)
    script = _script(tmp_path, manager=manager)
    state = tmp_path / "state"
    journal = state / "install-transaction.json"
    _journal(journal)
    evidence = tmp_path / "evidence" / ("2" * 40)
    evidence.mkdir(mode=0o750)

    completed = subprocess.run(
        [shutil.which("bash") or "bash", "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    assert not evidence.exists()
    assert not journal.exists()
    assert (tmp_path / "continued").is_file()


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash unavailable")
def test_published_pair_crash_resumes_without_rebuilding_or_deleting(tmp_path: Path) -> None:
    manager = tmp_path / "manager"
    manager.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    manager.chmod(0o755)
    script = _script(tmp_path, manager=manager)
    journal = tmp_path / "state" / "install-transaction.json"
    _journal(journal)
    pair = tmp_path / "pairs" / "releases" / PAIR_NAME
    pair.mkdir(mode=0o755)
    (pair / ".pair-receipt.json").write_text(
        json.dumps(
            {
                "build_policy_sha256": "3" * 64,
                "commit": "1" * 40,
                "host_layer_sha256": "7" * 64,
                "pair_name": PAIR_NAME,
                "source_tree": "2" * 40,
            }
        ),
        encoding="ascii",
    )
    evidence = tmp_path / "evidence" / ("2" * 40)
    evidence.mkdir(mode=0o750)
    for name in ("compose.production.yaml", "images.tar", "release.json", "validation.json"):
        path = evidence / name
        path.write_text(name, encoding="ascii")
        path.chmod(0o640)

    completed = subprocess.run(
        [shutil.which("bash") or "bash", "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    assert pair.is_dir() and evidence.is_dir()
    assert not journal.exists()
    assert (tmp_path / "resumed").is_file()
    assert not (tmp_path / "continued").exists()


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash unavailable")
def test_committed_host_transition_resumes_without_publication_journal(
    tmp_path: Path,
) -> None:
    manager = tmp_path / "manager"
    manager.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    manager.chmod(0o755)
    helper = tmp_path / "host-transition"
    helper.write_text(
        f"""#!/bin/bash
set -euo pipefail
[ "$1" = prepare ]
[ "$2" = --previous-pair ]
[ "$4" = --target-pair ]
[ "$5" = {PAIR_NAME!r} ]
[ "$6" = --previous-host-layer ]
[ "$8" = --target-host-layer ]
[ "$9" = {'7' * 64!r} ]
[ "${{10}}" = --publish-journal ]
: >{str(tmp_path / 'committed-receipt-verified')!r}
printf '%s\n' '{{"active_pair":"{PAIR_NAME}","host_transition":"committed","schema_version":1}}'
""",
        encoding="ascii",
    )
    helper.chmod(0o755)
    script = _script(
        tmp_path,
        manager=manager,
        host_transition=True,
        host_transition_helper=helper,
    )
    pair = tmp_path / "pairs" / "releases" / PAIR_NAME
    pair.mkdir(mode=0o755)
    (pair / ".pair-receipt.json").write_text(
        json.dumps(
            {
                "build_policy_sha256": "3" * 64,
                "commit": "1" * 40,
                "host_layer_sha256": "7" * 64,
                "pair_name": PAIR_NAME,
                "source_tree": "2" * 40,
            }
        ),
        encoding="ascii",
    )
    evidence = tmp_path / "evidence" / ("2" * 40)
    evidence.mkdir(mode=0o750)
    for name in (
        "compose.production.yaml",
        "images.tar",
        "release.json",
        "validation.json",
    ):
        path = evidence / name
        path.write_text(name, encoding="ascii")
        path.chmod(0o640)

    completed = subprocess.run(
        [shutil.which("bash") or "bash", "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    assert (tmp_path / "committed-receipt-verified").is_file()
    assert pair.is_dir() and evidence.is_dir()
    assert not (tmp_path / "state" / "install-transaction.json").exists()
    assert not (tmp_path / "continued").exists()


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash unavailable")
@pytest.mark.parametrize("handoff_completed", [False, True])
def test_failure_cleanup_preserves_only_post_handoff_publication_for_resume(
    tmp_path: Path,
    handoff_completed: bool,
) -> None:
    pair_releases = tmp_path / "pairs"
    evidence_root = tmp_path / "evidence"
    state_root = tmp_path / "state"
    pair = pair_releases / PAIR_NAME
    evidence = evidence_root / ("2" * 40)
    journal = state_root / "install-transaction.json"
    for directory in (pair, evidence, state_root):
        directory.mkdir(parents=True)
    journal.write_text("journal\n", encoding="ascii")
    script = f"""
set -u
pair_stage=
operator_output=
evidence_stage=
pair_published=true
evidence_published=true
resume_required={'true' if handoff_completed else 'false'}
activated=false
activation_attempted=false
activation_previous_kind=
activation_previous_selection=
manager=/nonexistent
selector=/nonexistent
previous_pair=
host_transition_requested=false
pair_final={str(pair)!r}
pair_releases={str(pair_releases)!r}
evidence_target={str(evidence)!r}
evidence_root={str(evidence_root)!r}
publish_journal={str(journal)!r}
state_root={str(state_root)!r}
{_cleanup_fragment()}
false
cleanup
"""
    completed = subprocess.run(
        [shutil.which("bash") or "bash", "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert completed.returncode == 1
    assert pair.exists() is handoff_completed
    assert evidence.exists() is handoff_completed
    assert journal.exists() is handoff_completed


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash unavailable")
def test_manager_child_sigkill_immediately_recovers_previous_native_selection(
    tmp_path: Path,
) -> None:
    previous = f"runtime-pair.{'4' * 40}.{'5' * 40}.{'6' * 64}"
    pair_root = tmp_path / "pairs"
    pair_releases = pair_root / "releases"
    pair_final = pair_releases / PAIR_NAME
    previous_pair_path = pair_releases / previous
    state_root = tmp_path / "state"
    evidence_root = tmp_path / "evidence"
    evidence_target = evidence_root / ("2" * 40)
    for directory in (
        pair_final / "control/deploy/an2p",
        previous_pair_path,
        state_root,
        evidence_target,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    (pair_root / "current").symlink_to(f"releases/{previous}")
    prepare = pair_final / "control/deploy/an2p/install_development_runtime.sh"
    prepare.write_text("#!/bin/bash\nexit 0\n", encoding="ascii")
    prepare.chmod(0o755)
    manager = tmp_path / "manager"
    manager.write_text(
        f"""#!/bin/bash
set -eu
case "$1" in
  activate-development)
    rm -- {str(pair_root / 'current')!r}
    ln -s {f'releases/{PAIR_NAME}'!r} {str(pair_root / 'current')!r}
    printf 'switched\n' > {str(state_root / 'transaction.json')!r}
    kill -KILL $$
    ;;
  recover)
    rm -f -- {str(pair_root / 'current')!r}
    ln -s {f'releases/{previous}'!r} {str(pair_root / 'current')!r}
    rm -f -- {str(state_root / 'transaction.json')!r}
    : > {str(tmp_path / 'manager-recovered')!r}
    ;;
  *) exit 64 ;;
esac
""",
        encoding="ascii",
    )
    manager.chmod(0o755)
    selector = tmp_path / "selector"
    native_status = json.dumps(
        {
            "docker_active": False,
            "docker_enabled": False,
            "marker": False,
            "native_active": ["mooncen-api.service", "mooncen-frontend.service"],
            "native_enabled": ["mooncen-api.service", "mooncen-frontend.service"],
            "schema_version": 1,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    selector.write_text(
        f"""#!/bin/bash
set -eu
case "$1" in
  runtime-status) printf '%s\\n' {native_status!r} ;;
  native-select) : > {str(tmp_path / 'native-recovered')!r} ;;
  *) exit 64 ;;
esac
""",
        encoding="ascii",
    )
    selector.chmod(0o755)
    script = f"""
set -euo pipefail
die() {{ printf '%s\n' "$*" >&2; exit 78; }}
pair_name={PAIR_NAME!r}
pair_root={str(pair_root)!r}
pair_releases={str(pair_releases)!r}
pair_final={str(pair_final)!r}
state_root={str(state_root)!r}
evidence_root={str(evidence_root)!r}
evidence_target={str(evidence_target)!r}
publish_journal={str(state_root / 'install-transaction.json')!r}
manager={str(manager)!r}
selector={str(selector)!r}
pair_stage=
operator_output=
evidence_stage=
pair_published=true
evidence_published=true
resume_required=false
activated=false
activation_attempted=false
activation_previous_kind=
activation_previous_selection=
previous_pair={previous!r}
host_transition_requested=false
transition_from_pair=
transition_from_host_layer=
host_layer_sha=
host_transition_helper=/nonexistent
{_cleanup_fragment()}
{_finish_pair_fragment()}
trap cleanup EXIT INT TERM
finish_pair_install
"""
    completed = subprocess.run(
        [shutil.which("bash") or "bash", "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert completed.returncode == 137
    assert (tmp_path / "manager-recovered").is_file()
    assert (tmp_path / "native-recovered").is_file()
    assert not (state_root / "transaction.json").exists()
    assert (pair_root / "current").readlink() == Path(f"releases/{previous}")


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash unavailable")
def test_resume_rejects_any_extra_evidence_entry(tmp_path: Path) -> None:
    manager = tmp_path / "manager"
    manager.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    manager.chmod(0o755)
    script = _script(tmp_path, manager=manager)
    journal = tmp_path / "state" / "install-transaction.json"
    _journal(journal)
    pair = tmp_path / "pairs" / "releases" / PAIR_NAME
    pair.mkdir(mode=0o755)
    (pair / ".pair-receipt.json").write_text(
        json.dumps(
            {
                "build_policy_sha256": "3" * 64,
                "commit": "1" * 40,
                "host_layer_sha256": "7" * 64,
                "pair_name": PAIR_NAME,
                "source_tree": "2" * 40,
            }
        ),
        encoding="ascii",
    )
    evidence = tmp_path / "evidence" / ("2" * 40)
    evidence.mkdir(mode=0o750)
    for name in (
        "compose.production.yaml",
        "images.tar",
        "release.json",
        "validation.json",
    ):
        path = evidence / name
        path.write_text(name, encoding="ascii")
        path.chmod(0o640)
    (evidence / "unexpected").mkdir()

    completed = subprocess.run(
        [shutil.which("bash") or "bash", "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert completed.returncode == 78
    assert "unexpected file set" in completed.stderr
    assert journal.exists() and pair.exists() and evidence.exists()


def test_publication_and_bootstrap_are_reviewed_fail_closed_contracts() -> None:
    installer = INSTALLER.read_text(encoding="utf-8")
    journal = installer.index('descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL')
    evidence = installer.index('mv -- "$evidence_stage" "$evidence_target"')
    pair = installer.index('mv -- "$pair_stage" "$pair_final"')
    handoff = installer.index("container_evidence_handoff.py")
    assert journal < evidence < pair
    assert "runtime publication journal is unsafe" in installer
    assert "unowned runtime publication residue requires manual review" in installer
    assert "finish_pair_install" in installer
    finish = installer.split("finish_pair_install() {", 1)[1].split("\n}", 1)[0]
    assert finish.index("install_development_runtime.sh") < finish.index(
        '"$manager" activate-development "$pair_name"'
    )
    assert finish.index("activation_attempted=true") < finish.index(
        '"$manager" activate-development "$pair_name"'
    )
    cleanup = _cleanup_fragment()
    assert cleanup.index('"$manager" recover') < cleanup.index(
        '"$selector" runtime-status'
    )
    finalize = installer.split("finalize_control() {", 1)[1].split(
        '\nif [ "${1:-}" = rollback ]',
        1,
    )[0]
    assert finalize.index("container_evidence_handoff.py") < finalize.index(
        "mooncen-register-container-evidence"
    )
    assert handoff < pair

    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
    assert "do not run through an untrusted user session" in bootstrap
    assert "/usr/bin/loginctl terminate-user" not in bootstrap
    assert "drain_retained_privileged_processes" in bootstrap
    assert "pidfd_send_signal" in bootstrap
    assert "staged installer digest mismatch" in bootstrap
    assert "mv -fT -- \"$installer_stage\" \"$target_installer\"" in bootstrap
    assert "EXPECTED_BUILD_POLICY_SHA256=%s" in bootstrap
    assert "sync -f -- /usr/local/sbin" in bootstrap
    stage_recovery_installer = bootstrap.index(
        "\n  stage_reviewed_installer_for_recovery\n"
    )
    recovery_install = bootstrap.index("install_bootstrap_recovery_unit\n")
    revoke = bootstrap.index("\nrevoke_host_root_without_losing_public_development\n")
    trust_commit = bootstrap.index('mv -fT -- "$trust_stage" "$trust_target"')
    recovery_disable = bootstrap.rindex('systemctl disable "$recovery_unit_name"')
    assert stage_recovery_installer < recovery_install < revoke < trust_commit < recovery_disable
    assert "source_installer=$recovery_installer" in bootstrap
    assert 'install -o root -g root -m 0700 "$checkout_installer" "$stage"' in bootstrap
    recovery_source_check = bootstrap.index(
        '"$(stat -c \'%U:%G:%a\' "$source_installer")" = root:root:700'
    )
    final_install = bootstrap.index(
        'install -o root -g root -m 0755 "$source_installer" "$installer_stage"'
    )
    converged = bootstrap.index("durable bootstrap recovery did not converge")
    remove_recovery_installer = bootstrap.index(
        'rm -- "$recovery_installer"',
        converged,
    )
    assert revoke < recovery_source_check < final_install
    assert converged < remove_recovery_installer
    assert "MOONCEN_AN2P_BOOTSTRAP_RECOVERY=1" in bootstrap
    assert "Restart=on-abnormal" in bootstrap
    assert "RestartSec=30s" in bootstrap
    assert "RestartPreventExitStatus=78" in bootstrap
    assert "TimeoutStartSec=15min" in bootstrap
    assert "StartLimitIntervalSec=infinity" in bootstrap
    assert "StartLimitBurst=2" in bootstrap
    assert "Restart=on-failure" not in bootstrap
    assert "StartLimitIntervalSec=0" not in bootstrap
    assert "WantedBy=multi-user.target" in bootstrap
    assert 'systemctl enable --now "$recovery_unit_name"' in bootstrap
    assert 'systemctl start "$recovery_unit_name"' not in bootstrap
    assert "bootstrap-development.json" in bootstrap
    launcher_lock = bootstrap.index("verify_outer_launcher_lock")
    outer_preflight = bootstrap.index(
        "preflight_bootstrap_inputs_and_public_runtime",
        launcher_lock,
    )
    outer_stage = bootstrap.index("stage_reviewed_installer_for_recovery", outer_preflight)
    outer_enable = bootstrap.index("install_bootstrap_recovery_unit", outer_stage)
    outer_exact = bootstrap.index("verify_outer_bootstrap_convergence", outer_enable)
    outer_result = bootstrap.index("--property=Result", outer_exact)
    outer_disable = bootstrap.index(
        'systemctl disable "$recovery_unit_name"', outer_result
    )
    outer_cleanup = bootstrap.index('rm -- "$recovery_unit"', outer_disable)
    assert (
        launcher_lock
        < outer_preflight
        < outer_stage
        < outer_enable
        < outer_exact
        < outer_result
        < outer_disable
        < outer_cleanup
    )
    assert 'bootstrap-launcher.lock' in bootstrap
    assert 'MOONCEN_AN2P_BOOTSTRAP_LAUNCHER_LOCK_FD' in bootstrap
    transaction_guard = bootstrap.index(
        "refuse_pending_runtime_transactions",
    )
    guarded_preflight = bootstrap.index(
        "refuse_pending_runtime_transactions",
        bootstrap.index("preflight_bootstrap_inputs_and_public_runtime()"),
    )
    assert transaction_guard < outer_preflight
    assert guarded_preflight < bootstrap.index(
        'if [ "${MOONCEN_AN2P_BOOTSTRAP_RECOVERY:-}" != 1 ]',
        guarded_preflight,
    )
    for pending_name in (
        "host-layer-transition.json",
        "install-transaction.json",
        "transaction.json",
        "control-finalization-transaction.json",
        "ops-rotation-transaction.json",
        "ops-rotation-previous.env",
    ):
        assert pending_name in bootstrap[transaction_guard:outer_preflight]
    assert 'systemctl disable "$recovery_unit_name" >/dev/null 2>&1 || true' not in bootstrap
    recovery_unit = (
        ROOT / "deploy/an2p/mooncen-an2p-runtime-recovery.service"
    ).read_text(encoding="utf-8")
    assert "RestrictAddressFamilies=AF_UNIX AF_INET" in recovery_unit
    assert "AF_INET6" not in recovery_unit


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash unavailable")
def test_legacy_split_aliases_are_removed_only_before_pair_activation(
    tmp_path: Path,
) -> None:
    user, group = _identity()
    pair_root = tmp_path / "runtime"
    pair_root.mkdir()
    fragment = _legacy_alias_fragment().replace("root:root", f"{user}:{group}")
    script = f"""
set -euo pipefail
die() {{ printf '%s\n' "$*" >&2; exit 78; }}
pair_root={str(pair_root)!r}
{fragment}
migrate_legacy_alias "$1" "$2"
"""

    for runtime_kind in ("control", "docker"):
        alias_root = tmp_path / runtime_kind
        legacy_name = (
            f"{runtime_kind}-runtime.{'1' * 40}.{'2' * 40}.{'3' * 64}"
        )
        legacy = alias_root / "releases" / legacy_name
        legacy.mkdir(parents=True)
        (alias_root / "current").symlink_to(f"releases/{legacy_name}")
        completed = subprocess.run(
            [shutil.which("bash") or "bash", "-c", script, "bash", str(alias_root), runtime_kind],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        assert completed.returncode == 0, completed.stderr
        assert not (alias_root / "current").exists()
        assert legacy.is_dir()

    unsafe_root = tmp_path / "unsafe"
    unsafe_root.mkdir()
    (unsafe_root / "current").symlink_to("../../unreviewed")
    rejected = subprocess.run(
        [shutil.which("bash") or "bash", "-c", script, "bash", str(unsafe_root), "control"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert rejected.returncode == 78
    assert (unsafe_root / "current").is_symlink()


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash unavailable")
def test_root_bootstrap_copies_only_the_exact_reviewed_installer_and_envelope(
    tmp_path: Path,
) -> None:
    user, group = _identity()
    repository = tmp_path / "repository"
    source = repository / "deploy/an2p/install_runtime_snapshot.sh"
    source.parent.mkdir(parents=True)
    source.write_bytes(INSTALLER.read_bytes())
    installer_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    transition_source = repository / "deploy/an2p/host_layer_transition.py"
    transition_source.write_bytes(
        (ROOT / "deploy/an2p/host_layer_transition.py").read_bytes()
    )
    transition_digest = hashlib.sha256(transition_source.read_bytes()).hexdigest()
    target = tmp_path / "sbin/mooncen-an2p-runtime-install"
    transition_target = tmp_path / "libexec/mooncen-an2p-host-transition"
    trust_directory = tmp_path / "etc"
    recovery_source = tmp_path / "state/reviewed-install-runtime-snapshot.sh"
    recovery_source.parent.mkdir()
    recovery_source.write_bytes(source.read_bytes())
    recovery_source.chmod(0o700)
    recovery_transition = tmp_path / "state/reviewed-host-layer-transition.py"
    recovery_transition.write_bytes(transition_source.read_bytes())
    recovery_transition.chmod(0o700)
    bootstrap_source = BOOTSTRAP.read_text(encoding="utf-8")
    bootstrap_source = (
        bootstrap_source.replace(
            '[ "$(id -u)" -eq 0 ] || die "run from an independent root console"',
            ': test fixture runs with the current uid',
        )
        .replace('[ "$(hostname -s)" = an2p ] || die "unexpected host"', ': fixed test host')
        .replace(
            "\npreflight_bootstrap_inputs_and_public_runtime\n"
            "revoke_host_root_without_losing_public_development\n\n[ -f",
            "\nbootstrap_phase=trust_committed\n\n[ -f",
        )
        .replace("source_root=/home/sgm/src/project/mooncen", f"source_root={repository}")
        .replace(
            "target_installer=/usr/local/sbin/mooncen-an2p-runtime-install",
            f"target_installer={target}",
        )
        .replace(
            "target_host_transition=/usr/local/libexec/mooncen-an2p-host-transition",
            f"target_host_transition={transition_target}",
        )
        .replace(
            "/var/lib/mooncen-an2p-runtime",
            str(tmp_path / "state"),
        )
        .replace("trust_directory=/etc/mooncen-an2p", f"trust_directory={trust_directory}")
        .replace(
            "install -d -o root -g root -m 0755 /usr/local/sbin /usr/local/libexec",
            (
                f"install -d -o {user} -g {group} -m 0755 "
                f"{target.parent} {transition_target.parent}"
            ),
        )
        .replace(
            "mktemp /usr/local/sbin/.mooncen-an2p-runtime-install.XXXXXXXX",
            f"mktemp {target.parent}/.mooncen-an2p-runtime-install.XXXXXXXX",
        )
        .replace(
            "mktemp /usr/local/libexec/.mooncen-an2p-host-transition.XXXXXXXX",
            f"mktemp {transition_target.parent}/.mooncen-an2p-host-transition.XXXXXXXX",
        )
        .replace("-o root -g root", f"-o {user} -g {group}")
        .replace("chown root:root", f"chown {user}:{group}")
        .replace(
            "os.fchown(descriptor, 0, 0)",
            "os.fchown(descriptor, os.getuid(), os.getgid())",
        )
        .replace("st_uid != 0", f"st_uid != {os.getuid()}")
        .replace("st_gid != 0", f"st_gid != {os.getgid()}")
        .replace("root:root:700", f"{user}:{group}:700")
        .replace("root:root:755", f"{user}:{group}:755")
        .replace("root:root:600", f"{user}:{group}:600")
        .replace("sync -f -- /usr/local/sbin", f"sync -f -- {target.parent}")
        .replace(
            "sync -f -- /usr/local/libexec",
            f"sync -f -- {transition_target.parent}",
        )
        .replace(
            'systemctl disable "$recovery_unit_name" >/dev/null',
            ": fixture recovery unit commit",
        )
        .replace('rm -- "$journal"', ": fixture journal commit")
    )
    executable = tmp_path / "bootstrap.sh"
    executable.write_text(bootstrap_source, encoding="utf-8")
    executable.chmod(0o700)
    arbitrary_digests = [str(index) * 64 for index in range(1, 7)]
    command = [
        str(executable),
        "--installer-sha256",
        installer_digest,
        "--integrity-sha256",
        arbitrary_digests[0],
        "--clean-source-sha256",
        arbitrary_digests[1],
        "--pair-manager-sha256",
        arbitrary_digests[2],
        "--handoff-sha256",
        arbitrary_digests[3],
        "--registrar-sha256",
        arbitrary_digests[4],
        "--host-transition-sha256",
        transition_digest,
        "--build-policy-sha256",
        arbitrary_digests[5],
    ]
    completed = subprocess.run(
        command,
        cwd="/",
        env={"MOONCEN_AN2P_BOOTSTRAP_RECOVERY": "1", "SUDO_USER": "root"},
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    assert target.read_bytes() == source.read_bytes()
    assert target.stat().st_mode & 0o777 == 0o755
    assert transition_target.read_bytes() == transition_source.read_bytes()
    assert transition_target.stat().st_mode & 0o777 == 0o755
    trust = trust_directory / "runtime-installer.trust"
    assert trust.stat().st_mode & 0o777 == 0o600
    assert trust.read_text(encoding="ascii").splitlines() == [
        "VERSION=1",
        f"INSTALLER_SHA256={installer_digest}",
        f"INTEGRITY_SHA256={arbitrary_digests[0]}",
        f"CLEAN_SOURCE_SHA256={arbitrary_digests[1]}",
        f"PAIR_MANAGER_SHA256={arbitrary_digests[2]}",
        f"HANDOFF_SHA256={arbitrary_digests[3]}",
        f"REGISTRAR_SHA256={arbitrary_digests[4]}",
        f"HOST_TRANSITION_SHA256={transition_digest}",
        f"EXPECTED_BUILD_POLICY_SHA256={arbitrary_digests[5]}",
    ]

    target.unlink()
    transition_target.unlink()
    trust.unlink()
    rejected = subprocess.run(
        [*command[:2], "0" * 64, *command[3:]],
        cwd="/",
        env={"SUDO_USER": "root"},
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert rejected.returncode == 78
    assert not target.exists() and not trust.exists()


def _bootstrap_source() -> str:
    return BOOTSTRAP.read_text(encoding="utf-8")


def _inline_bootstrap_python(function_name: str) -> str:
    source = _bootstrap_source()
    function = source.split(f"{function_name}() {{", 1)[1].split("\nPY\n}", 1)[0]
    return function.split("<<'PY'\n", 1)[1]


def _drain_namespace() -> dict[str, Any]:
    namespace: dict[str, Any] = {"__name__": "mooncen_bootstrap_drain_test"}
    program = _inline_bootstrap_python("drain_retained_privileged_processes")
    compiled = compile(
        program,
        "<bootstrap-retained-process-drain>",
        "exec",
        dont_inherit=True,
    )
    exec(compiled, namespace)  # noqa: S102
    return namespace


def _quarantine_namespace() -> dict[str, Any]:
    namespace: dict[str, Any] = {"__name__": "mooncen_bootstrap_quarantine_test"}
    program = _inline_bootstrap_python("quarantine_legacy_user_state")
    program = program.replace("ROOT_UID = 0", f"ROOT_UID = {os.getuid()}")
    program = program.replace("ROOT_GID = 0", f"ROOT_GID = {os.getgid()}")
    compiled = compile(
        program,
        "<bootstrap-legacy-user-quarantine>",
        "exec",
        dont_inherit=True,
    )
    exec(compiled, namespace)  # noqa: S102
    return namespace


@dataclass(frozen=True)
class FakeProcess:
    uid: int
    gids: frozenset[int]
    start_time: int


class FakeOps:
    def __init__(
        self,
        process_identity: type[Any],
        processes: dict[int, FakeProcess],
        *,
        exit_on_kill: set[int] | None = None,
        spawn_after_exit: dict[int, tuple[int, FakeProcess]] | None = None,
        reuse_after_first_snapshot: dict[int, FakeProcess] | None = None,
    ) -> None:
        self.process_identity = process_identity
        self.processes = dict(processes)
        self.exit_on_kill = set(exit_on_kill or ())
        self.spawn_after_exit = dict(spawn_after_exit or {})
        self.reuse_after_first_snapshot = dict(reuse_after_first_snapshot or {})
        self.signals: list[tuple[int, signal.Signals]] = []
        self.closed: list[tuple[int, int]] = []
        self.issued: list[Any] = []
        self.sleeps: list[float] = []
        self.snapshot_calls = 0

    def snapshot(self, uid: int, privileged_gids: frozenset[int]) -> list[Any]:
        self.snapshot_calls += 1
        identities = [
            self.process_identity(pid, process.start_time, pid + 10_000)
            for pid, process in sorted(self.processes.items())
            if process.uid == uid and process.gids.intersection(privileged_gids)
        ]
        self.issued.extend(identities)
        if self.snapshot_calls == 1:
            self.processes.update(self.reuse_after_first_snapshot)
        return identities

    def still_privileged(
        self,
        identity: Any,
        uid: int,
        privileged_gids: frozenset[int],
    ) -> bool:
        process = self.processes.get(identity.pid)
        return bool(
            process is not None
            and process.uid == uid
            and process.start_time == identity.start_time
            and process.gids.intersection(privileged_gids)
        )

    def send(self, identity: Any, requested_signal: signal.Signals) -> None:
        process = self.processes.get(identity.pid)
        # Model pidfd semantics: a reused numeric PID is not the process bound to
        # the original handle, so no signal reaches the replacement process.
        if process is None or process.start_time != identity.start_time:
            return
        self.signals.append((identity.pid, requested_signal))
        exits = requested_signal == signal.SIGKILL and identity.pid in self.exit_on_kill
        if exits:
            self.processes.pop(identity.pid)
            spawned = self.spawn_after_exit.get(identity.pid)
            if spawned is not None:
                spawned_pid, spawned_process = spawned
                self.processes[spawned_pid] = spawned_process

    def close(self, identity: Any) -> None:
        self.closed.append((identity.pid, identity.start_time))

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        assert seconds == 0.0


def _drain(
    namespace: dict[str, Any],
    ops: FakeOps,
    *,
    rounds: int = 3,
) -> None:
    namespace["drain_retained_processes"](
        ops,
        LEGACY_UID,
        PRIVILEGED_GIDS,
        stop_grace=0.0,
        kill_grace=0.0,
        rounds=rounds,
    )


def test_old_privileged_process_is_stopped_then_killed_without_touching_clean_process() -> None:
    namespace = _drain_namespace()
    ops = FakeOps(
        namespace["ProcessIdentity"],
        {
            101: FakeProcess(LEGACY_UID, frozenset({998}), 11),
            202: FakeProcess(LEGACY_UID, frozenset({44}), 22),
            303: FakeProcess(2000, frozenset({998}), 33),
        },
        exit_on_kill={101},
    )

    _drain(namespace, ops)

    assert ops.signals == [(101, signal.SIGSTOP), (101, signal.SIGKILL)]
    assert [(identity.pid, identity.start_time) for identity in ops.issued] == [(101, 11)]
    assert set(ops.processes) == {202, 303}


def test_pid_start_time_reuse_is_not_privileged_and_replacement_is_not_signalled() -> None:
    namespace = _drain_namespace()
    ops = FakeOps(
        namespace["ProcessIdentity"],
        {101: FakeProcess(LEGACY_UID, frozenset({998}), 11)},
        reuse_after_first_snapshot={
            101: FakeProcess(LEGACY_UID, frozenset({44}), 99),
        },
    )

    _drain(namespace, ops)

    assert len(ops.issued) == 1
    assert not ops.still_privileged(ops.issued[0], LEGACY_UID, PRIVILEGED_GIDS)
    assert ops.signals == []
    assert ops.processes[101].start_time == 99


def test_new_old_gid_process_spawned_during_drain_is_found_by_rescan() -> None:
    namespace = _drain_namespace()
    replacement = FakeProcess(LEGACY_UID, frozenset({999}), 22)
    ops = FakeOps(
        namespace["ProcessIdentity"],
        {101: FakeProcess(LEGACY_UID, frozenset({998}), 11)},
        exit_on_kill={101, 202},
        spawn_after_exit={101: (202, replacement)},
    )

    _drain(namespace, ops)

    assert ops.signals == [
        (101, signal.SIGSTOP),
        (101, signal.SIGKILL),
        (202, signal.SIGSTOP),
        (202, signal.SIGKILL),
    ]
    assert ops.snapshot_calls == 3
    assert ops.processes == {}


def test_unkillable_old_gid_process_raises_boundary_error_after_bounded_rounds() -> None:
    namespace = _drain_namespace()
    ops = FakeOps(
        namespace["ProcessIdentity"],
        {101: FakeProcess(LEGACY_UID, frozenset({998}), 11)},
    )

    with pytest.raises(
        namespace["BoundaryError"],
        match="did not exit within the bounded drain: 101",
    ):
        _drain(namespace, ops, rounds=2)

    assert ops.snapshot_calls == 2
    assert ops.signals == [
        (101, signal.SIGSTOP),
        (101, signal.SIGKILL),
        (101, signal.SIGSTOP),
        (101, signal.SIGKILL),
    ]


def _run_legacy_user_mask_verifier(home: Path) -> subprocess.CompletedProcess[str]:
    uid = str(os.getuid())
    gid = str(os.getgid())
    return subprocess.run(
        [
            sys.executable,
            "-I",
            "-",
            str(home),
            uid,
            gid,
            uid,
            gid,
            *LEGACY_USER_CONTROL_UNITS,
        ],
        input=_legacy_user_mask_verifier(),
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )


def test_legacy_user_mask_verifier_accepts_exact_fd_relative_masks(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    user_units = home / ".config/systemd/user"
    user_units.mkdir(parents=True)
    for unit in LEGACY_USER_CONTROL_UNITS:
        (user_units / unit).symlink_to("/dev/null")

    completed = _run_legacy_user_mask_verifier(home)

    assert completed.returncode == 0, completed.stderr


def test_legacy_user_mask_verifier_rejects_parent_symlink_without_touching_target(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    outside_config = tmp_path / "outside-config"
    user_units = outside_config / "systemd/user"
    user_units.mkdir(parents=True)
    for unit in LEGACY_USER_CONTROL_UNITS:
        (user_units / unit).symlink_to("/dev/null")
    sentinel = outside_config / "sentinel"
    sentinel.write_text("unchanged\n", encoding="ascii")
    before = (sentinel.read_bytes(), sentinel.stat().st_mode, sentinel.stat().st_ino)
    (home / ".config").symlink_to(outside_config, target_is_directory=True)

    completed = _run_legacy_user_mask_verifier(home)

    assert completed.returncode != 0
    assert (sentinel.read_bytes(), sentinel.stat().st_mode, sentinel.stat().st_ino) == before


def test_bootstrap_recovery_does_not_terminate_sessions_or_require_missing_target() -> None:
    source = _bootstrap_source()
    recovery = source.split(
        "revoke_host_root_without_losing_public_development() {",
        1,
    )[1].split("\n}\n\nrevoke_host_root_without_losing_public_development\n", 1)[0]

    assert "/usr/bin/loginctl terminate-user" not in source
    assert "mooncen-development-runtime.target" not in recovery
    assert "for unit in mooncen-api.service mooncen-frontend.service" in recovery
    assert 'systemctl --user --machine="${legacy_user}@" start "$unit"' in recovery
    global_mask = recovery.index('systemctl --global mask "${legacy_units[@]}"')
    membership_revoke = recovery.index("/usr/bin/gpasswd --delete")
    process_drain = recovery.index("drain_retained_privileged_processes")
    quarantine = recovery.index("quarantine_legacy_user_state")
    clean_manager = recovery.index('systemctl start "user@${legacy_uid}.service"')
    user_disable = recovery.index(
        'systemctl --user --machine="${legacy_user}@" disable --now'
    )
    native_restore = recovery.index(
        "for unit in mooncen-api.service mooncen-frontend.service"
    )
    assert (
        global_mask
        < membership_revoke
        < process_drain
        < quarantine
        < clean_manager
        < user_disable
        < native_restore
    )
    assert 'install -d -o "$legacy_user"' not in recovery


def test_user_quarantine_uses_nofollow_dirfds_mount_ids_and_single_links() -> None:
    program = _inline_bootstrap_python("quarantine_legacy_user_state")

    assert "os.O_DIRECTORY | os.O_NOFOLLOW" in program
    assert 'path = f"/proc/self/fdinfo/{descriptor}"' in program
    assert "child_mount != self.anchor_mount" in program
    assert "canonical directory changed during quarantine" in program
    assert "metadata.st_nlink != 1" in program
    assert "src_dir_fd=source_directory" in program
    assert "dst_dir_fd=destination_directory" in program
    assert "follow_symlinks=False" in program
    assert "realpath" not in program


def test_bootstrap_journal_has_five_phases_and_only_adjacent_forward_transitions() -> None:
    program = _inline_bootstrap_python("advance_bootstrap_journal")
    tree = ast.parse(program)
    phases = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "phases" for target in node.targets)
    )
    assert phases == (
        "prepared",
        "membership_revoked",
        "privileged_processes_drained",
        "native_restored",
        "trust_committed",
    )
    assert "phases.index(next_phase) != phases.index(expected) + 1" in program

    normalized = " ".join(_bootstrap_source().replace("\\\n", "").split())
    transitions = (
        'advance_bootstrap_journal "$journal" prepared membership_revoked',
        'advance_bootstrap_journal "$journal" membership_revoked privileged_processes_drained',
        'advance_bootstrap_journal "$journal" privileged_processes_drained native_restored',
        'advance_bootstrap_journal "$journal" native_restored trust_committed',
    )
    positions = [normalized.index(transition) for transition in transitions]
    assert positions == sorted(positions)
    assert normalized.count('advance_bootstrap_journal "$journal"') == len(transitions)


def test_bootstrap_recovery_unit_has_bounded_abnormal_restart_contract() -> None:
    source = _bootstrap_source()

    for directive in (
        "StartLimitIntervalSec=infinity",
        "StartLimitBurst=2",
        "Restart=on-abnormal",
        "RestartSec=30s",
        "TimeoutStartSec=15min",
        "RestartPreventExitStatus=78",
    ):
        assert directive in source
    assert "Restart=on-failure" not in source
    assert "StartLimitIntervalSec=0" not in source


def test_outer_launcher_lock_and_exact_postcondition_are_fail_closed() -> None:
    source = _bootstrap_source()
    launcher = _inline_bootstrap_python("acquire_outer_launcher_lock_and_reexec")
    inherited = _inline_bootstrap_python("verify_outer_launcher_lock")
    convergence = _inline_bootstrap_python("verify_outer_bootstrap_convergence")

    assert '"bootstrap-launcher.lock"' in launcher
    assert "os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW" in launcher
    assert "fcntl.flock(lock_descriptor, fcntl.LOCK_EX)" in launcher
    assert "mount_id(state_directory) != mount_id(var_directory)" in launcher
    assert "lock_metadata.st_nlink != 1" in launcher
    assert "os.set_inheritable(lock_descriptor, True)" in launcher
    assert '"MOONCEN_AN2P_BOOTSTRAP_LAUNCHER_LOCK_FD"' in launcher
    assert 'os.execve(' in launcher
    assert (
        'acquire_outer_launcher_lock_and_reexec() {\n'
        '  exec /usr/bin/python3 -I - "$0" "$@"'
    ) in source
    assert "fcntl.LOCK_EX | fcntl.LOCK_NB" in inherited
    assert "(metadata.st_dev, metadata.st_ino)" in inherited
    assert "(path_metadata.st_dev, path_metadata.st_ino)" in inherited

    assert "os.O_NOFOLLOW" in convergence
    assert "metadata.st_nlink != 1" in convergence
    assert "hashlib.sha256()" in convergence
    for trust_key in (
        "INSTALLER_SHA256",
        "INTEGRITY_SHA256",
        "CLEAN_SOURCE_SHA256",
        "PAIR_MANAGER_SHA256",
        "HANDOFF_SHA256",
        "REGISTRAR_SHA256",
        "EXPECTED_BUILD_POLICY_SHA256",
    ):
        assert trust_key in convergence
    assert "journal.lstat()" in convergence
    assert '--property=Result --value' in source
    assert '--property=ActiveState --value' in source
    assert 'recovery_enablement=$(systemctl is-enabled' in source


def _quarantine_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, Path, Path, Path]:
    home = tmp_path / "home"
    user_units = home / ".config/systemd/user"
    credential_config = home / ".config/mooncen-an2p"
    credential_keys = credential_config / "keys"
    user_units.mkdir(parents=True)
    credential_keys.mkdir(parents=True)
    home.chmod(0o750)
    (home / ".config").chmod(0o755)
    (home / ".config/systemd").chmod(0o755)
    user_units.chmod(0o755)
    credential_config.chmod(0o700)
    credential_keys.chmod(0o700)
    quarantine_parent = tmp_path / "var-lib"
    quarantine_parent.mkdir(mode=0o755)
    return (
        home,
        user_units,
        credential_config,
        quarantine_parent / "legacy-units",
        quarantine_parent / "legacy-credentials",
    )


def _run_quarantine(
    namespace: dict[str, Any],
    home: Path,
    unit_quarantine: Path,
    credential_quarantine: Path,
) -> None:
    namespace["quarantine_state"](
        home,
        os.getuid(),
        os.getgid(),
        unit_quarantine,
        credential_quarantine,
    )


def test_user_quarantine_is_normal_and_recovery_idempotent(tmp_path: Path) -> None:
    namespace = _quarantine_namespace()
    home, user_units, credentials, unit_quarantine, credential_quarantine = (
        _quarantine_fixture(tmp_path)
    )
    unit = "mooncen-ops-api.service"
    unit_payload = b"[Unit]\nDescription=legacy control\n"
    (user_units / unit).write_bytes(unit_payload)
    (user_units / unit).chmod(0o644)
    credential_payload = b"Host production\n"
    (credentials / "cloud-deploy.ssh_config").write_bytes(credential_payload)
    (credentials / "cloud-deploy.ssh_config").chmod(0o600)

    _run_quarantine(
        namespace,
        home,
        unit_quarantine,
        credential_quarantine,
    )
    _run_quarantine(
        namespace,
        home,
        unit_quarantine,
        credential_quarantine,
    )

    destination = unit_quarantine / unit
    assert destination.read_bytes() == unit_payload
    assert destination.stat().st_mode & 0o777 == 0o600
    for legacy_unit in namespace["LEGACY_UNITS"]:
        mask = user_units / legacy_unit
        assert mask.is_symlink()
        assert mask.readlink() == Path("/dev/null")
    quarantined_credential = credential_quarantine / "cloud-deploy.ssh_config"
    assert quarantined_credential.read_bytes() == credential_payload
    assert quarantined_credential.stat().st_mode & 0o777 == 0o600
    assert not (credentials / "cloud-deploy.ssh_config").exists()


def test_partial_user_unit_quarantine_move_resumes_to_private_residue(
    tmp_path: Path,
) -> None:
    namespace = _quarantine_namespace()
    home, user_units, _credentials, unit_quarantine, credential_quarantine = (
        _quarantine_fixture(tmp_path)
    )
    unit_quarantine.mkdir(mode=0o700)
    credential_quarantine.mkdir(mode=0o700)
    unit = "mooncen-ops-control-env.service"
    original = b"[Unit]\nDescription=partially moved\n"
    destination = unit_quarantine / unit
    destination.write_bytes(original)
    destination.chmod(0o644)

    _run_quarantine(
        namespace,
        home,
        unit_quarantine,
        credential_quarantine,
    )

    assert destination.read_bytes() == original
    assert destination.stat().st_mode & 0o777 == 0o600
    source_path = user_units / unit
    assert source_path.is_symlink()
    assert source_path.readlink() == Path("/dev/null")


def test_symlinked_user_unit_directory_fails_without_touching_target(
    tmp_path: Path,
) -> None:
    namespace = _quarantine_namespace()
    home = tmp_path / "home"
    systemd = home / ".config/systemd"
    systemd.mkdir(parents=True)
    home.chmod(0o750)
    (home / ".config").chmod(0o755)
    systemd.chmod(0o755)
    protected = tmp_path / "protected"
    protected.mkdir(mode=0o700)
    sentinel = protected / "sentinel"
    sentinel.write_bytes(b"protected bytes\n")
    sentinel.chmod(0o640)
    (systemd / "user").symlink_to(protected, target_is_directory=True)
    quarantine_parent = tmp_path / "var-lib"
    quarantine_parent.mkdir(mode=0o755)
    before_directory = protected.stat()
    before_sentinel = sentinel.stat()
    before_payload = sentinel.read_bytes()

    with pytest.raises(namespace["BoundaryError"], match="symlinked directory boundary"):
        _run_quarantine(
            namespace,
            home,
            quarantine_parent / "legacy-units",
            quarantine_parent / "legacy-credentials",
        )

    after_directory = protected.stat()
    after_sentinel = sentinel.stat()
    assert sentinel.read_bytes() == before_payload
    assert (after_directory.st_uid, after_directory.st_gid, after_directory.st_mode) == (
        before_directory.st_uid,
        before_directory.st_gid,
        before_directory.st_mode,
    )
    assert (after_sentinel.st_uid, after_sentinel.st_gid, after_sentinel.st_mode) == (
        before_sentinel.st_uid,
        before_sentinel.st_gid,
        before_sentinel.st_mode,
    )
    assert (systemd / "user").is_symlink()


def test_hardlinked_user_unit_fails_without_chown_or_chmod_of_sentinel(
    tmp_path: Path,
) -> None:
    namespace = _quarantine_namespace()
    home, user_units, _credentials, unit_quarantine, credential_quarantine = (
        _quarantine_fixture(tmp_path)
    )
    sentinel = tmp_path / "sentinel.service"
    sentinel.write_bytes(b"[Unit]\nDescription=protected hardlink\n")
    sentinel.chmod(0o644)
    os.link(sentinel, user_units / "mooncen-ops-control-env.service")
    before = sentinel.stat()
    payload = sentinel.read_bytes()

    with pytest.raises(namespace["BoundaryError"], match="unsafe regular-file boundary"):
        _run_quarantine(
            namespace,
            home,
            unit_quarantine,
            credential_quarantine,
        )

    after = sentinel.stat()
    assert sentinel.read_bytes() == payload
    assert (after.st_uid, after.st_gid, after.st_mode, after.st_nlink) == (
        before.st_uid,
        before.st_gid,
        before.st_mode,
        before.st_nlink,
    )


@pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("flock") is None,
    reason="bash/flock unavailable",
)
def test_bootstrap_lock_is_create_once_inode_bound_and_serializes_contenders(
    tmp_path: Path,
) -> None:
    user, group = _identity()
    source = _bootstrap_source()
    marker = source.index("# Serialize a manually retried stage")
    start = source.index("install -d -o root -g root -m 0700", marker)
    end = source.index("# Share the installed ABI lock", start)
    lock_fragment = source[start:end]

    assert "install -o root -g root -m 0600 /dev/null" not in lock_fragment
    assert "os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW" in lock_fragment
    assert "descriptor_metadata = os.fstat(descriptor)" in lock_fragment
    assert "path_metadata = path.lstat()" in lock_fragment
    assert "(descriptor_metadata.st_dev, descriptor_metadata.st_ino)" in lock_fragment
    assert "(path_metadata.st_dev, path_metadata.st_ino)" in lock_fragment

    state_root = tmp_path / "state"
    fixture = (
        lock_fragment.replace("/var/lib/mooncen-an2p-runtime", str(state_root))
        .replace("-o root -g root", f"-o {user} -g {group}")
        .replace(
            "os.fchown(descriptor, 0, 0)",
            "os.fchown(descriptor, os.getuid(), os.getgid())",
        )
        .replace("st_uid != 0", f"st_uid != {os.getuid()}")
        .replace("st_gid != 0", f"st_gid != {os.getgid()}")
        .replace("root:root:600", f"{user}:{group}:600")
    )
    inode_log = tmp_path / "inodes"
    event_log = tmp_path / "events"
    script = f"""
set -euo pipefail
die() {{ printf '%s\n' "$*" >&2; exit 78; }}
inode_log={str(inode_log)!r}
event_log={str(event_log)!r}
{fixture}
stat -c '%d:%i' "$bootstrap_lock" >>"$inode_log"
printf 'enter:%s\n' "$1" >>"$event_log"
/usr/bin/sleep 0.4
printf 'exit:%s\n' "$1" >>"$event_log"
"""
    first = subprocess.Popen(
        [shutil.which("bash") or "bash", "-c", script, "bash", "A"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    second: subprocess.Popen[str] | None = None
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if event_log.exists() and "enter:A" in event_log.read_text(encoding="ascii"):
                break
            if first.poll() is not None:
                break
            time.sleep(0.01)
        assert event_log.exists(), first.stderr.read() if first.stderr else ""
        assert event_log.read_text(encoding="ascii").splitlines() == ["enter:A"]
        second = subprocess.Popen(
            [shutil.which("bash") or "bash", "-c", script, "bash", "B"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        first_stdout, first_stderr = first.communicate(timeout=5)
        second_stdout, second_stderr = second.communicate(timeout=5)
        assert first.returncode == 0, first_stderr or first_stdout
        assert second.returncode == 0, second_stderr or second_stdout
    finally:
        for process in (first, second):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=5)

    assert event_log.read_text(encoding="ascii").splitlines() == [
        "enter:A",
        "exit:A",
        "enter:B",
        "exit:B",
    ]
    inodes = inode_log.read_text(encoding="ascii").splitlines()
    assert len(inodes) == 2
    assert len(set(inodes)) == 1


def test_bootstrap_holds_the_shared_install_lock_across_guard_and_abi_replacement() -> None:
    source = _bootstrap_source()
    shared = source.index("# Share the installed ABI lock")
    flock = source.index("/usr/bin/flock -x 8", shared)
    guard = source.index("refuse_pending_runtime_transactions", flock)
    revoke = source.index("revoke_host_root_without_losing_public_development", guard)
    installer = source.index('mv -fT -- "$installer_stage" "$target_installer"', revoke)
    helper = source.index('mv -fT -- "$host_transition_stage" "$target_host_transition"', installer)
    trust = source.index('mv -fT -- "$trust_stage" "$trust_target"', helper)

    assert shared < flock < guard < revoke < installer < helper < trust
    guard_body = source[
        source.index("refuse_pending_runtime_transactions()") :
        source.index("preflight_bootstrap_inputs_and_public_runtime()")
    ]
    for residue in (
        "mooncen-an2p-host-transition-recovery.service",
        "mooncen-an2p-host-transition-continue.service",
        "mooncen-docker-dev.service.requires/mooncen-an2p-host-transition-recovery.service",
    ):
        assert residue in guard_body


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash unavailable")
def test_unsafe_root_recovery_installer_residue_is_rejected_without_replacement(
    tmp_path: Path,
) -> None:
    user, group = _identity()
    source = _bootstrap_source()
    start = source.index("stage_reviewed_installer_for_recovery() {")
    end = source.index("\n}\n\ninstall_bootstrap_recovery_unit() {", start) + 2
    function = (
        source[start:end]
        .replace("/var/lib/mooncen-an2p-runtime", str(tmp_path / "state"))
        .replace("-o root -g root", f"-o {user} -g {group}")
        .replace("root:root:700", f"{user}:{group}:700")
    )
    checkout = tmp_path / "checkout-installer.sh"
    checkout.write_bytes(INSTALLER.read_bytes())
    digest = hashlib.sha256(checkout.read_bytes()).hexdigest()
    recovery = tmp_path / "state/reviewed-install-runtime-snapshot.sh"
    recovery.parent.mkdir()
    recovery.write_bytes(b"unreviewed residue\n")
    recovery.chmod(0o700)
    original = recovery.read_bytes()
    script = f"""
set -euo pipefail
die() {{ printf '%s\n' "$*" >&2; exit 78; }}
checkout_installer={str(checkout)!r}
recovery_installer={str(recovery)!r}
installer_sha={digest!r}
{function}
stage_reviewed_installer_for_recovery
"""

    completed = subprocess.run(
        [shutil.which("bash") or "bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )

    assert completed.returncode == 78
    assert "root recovery installer residue is unsafe" in completed.stderr
    assert recovery.read_bytes() == original
    assert recovery.stat().st_mode & 0o777 == 0o700


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash unavailable")
def test_runtime_publication_cleanup_preserves_term_status_and_runs_rollback_once(
    tmp_path: Path,
) -> None:
    installer = INSTALLER.read_text(encoding="utf-8")
    assert installer.count("trap 'exit 130' INT") == 4
    assert installer.count("trap 'exit 143' TERM") == 4
    assert "trap cleanup EXIT INT TERM" not in installer
    assert "trap cleanup_control_finalization EXIT INT TERM" not in installer
    assert "trap cleanup_ops_rotation EXIT INT TERM" not in installer

    manager_log = tmp_path / "manager.log"
    native_selected = tmp_path / "native-selected"
    transaction = tmp_path / "state/transaction.json"
    transaction.parent.mkdir()
    manager = tmp_path / "manager"
    manager.write_text(
        f"""#!/bin/bash
set -eu
printf '%s\n' "$1" >> {str(manager_log)!r}
[ "$1" = recover ]
rm -f -- {str(transaction)!r}
""",
        encoding="ascii",
    )
    manager.chmod(0o755)
    native_status = json.dumps(
        {
            "docker_active": False,
            "docker_enabled": False,
            "marker": False,
            "native_active": ["mooncen-api.service", "mooncen-frontend.service"],
            "native_enabled": ["mooncen-api.service", "mooncen-frontend.service"],
            "schema_version": 1,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    selector = tmp_path / "selector"
    selector.write_text(
        f"""#!/bin/bash
set -eu
case "$1" in
  runtime-status) printf '%s\n' {native_status!r} ;;
  native-select) : > {str(native_selected)!r} ;;
  *) exit 64 ;;
esac
""",
        encoding="ascii",
    )
    selector.chmod(0o755)
    cleanup = _cleanup_fragment().replace(
        "cleanup() {\n  status=$?",
        "cleanup() {\n  status=$?\n  printf '%s\\n' \"$status\" >>\"$cleanup_log\"",
    )

    def script(cleanup_log: Path, ready: Path) -> str:
        return f"""
set -euo pipefail
manager={str(manager)!r}
selector={str(selector)!r}
state_root={str(transaction.parent)!r}
pair_stage=
operator_output=
evidence_stage=
pair_published=false
evidence_published=false
resume_required=false
activated=false
activation_attempted=true
activation_previous_kind=
activation_previous_selection=
    previous_pair=
    host_transition_requested=false
    pair_final=
pair_releases={str(tmp_path / 'pairs')!r}
evidence_target=
evidence_root={str(tmp_path / 'evidence')!r}
publish_journal={str(tmp_path / 'publication.json')!r}
cleanup_log={str(cleanup_log)!r}
ready_file={str(ready)!r}
{cleanup}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
: >"$ready_file"
if [ "${{1:-}}" = wait ]; then
  while :; do :; done
fi
exit 0
"""

    term_cleanup = tmp_path / "term-cleanup.log"
    ready = tmp_path / "ready"
    process = subprocess.Popen(
        [shutil.which("bash") or "bash", "-c", script(term_cleanup, ready), "bash", "wait"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not ready.exists():
            if process.poll() is not None:
                break
            time.sleep(0.01)
        assert ready.exists()
        process.terminate()
        stdout, stderr = process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    assert process.returncode == 143, stderr or stdout
    assert term_cleanup.read_text(encoding="ascii").splitlines() == ["143"]
    assert manager_log.read_text(encoding="ascii").splitlines() == ["recover"]
    assert native_selected.is_file()

    normal_cleanup = tmp_path / "normal-cleanup.log"
    normal = subprocess.run(
        [
            shutil.which("bash") or "bash",
            "-c",
            script(normal_cleanup, tmp_path / "normal-ready"),
            "bash",
            "normal",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    assert normal.returncode == 0, normal.stderr
    assert normal_cleanup.read_text(encoding="ascii").splitlines() == ["0"]
    assert manager_log.read_text(encoding="ascii").splitlines() == ["recover"]
