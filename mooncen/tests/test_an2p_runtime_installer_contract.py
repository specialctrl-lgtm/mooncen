from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import threading

from tools import prepare_an2p_ops_control as prepare


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "deploy/an2p/install_runtime_snapshot.sh"


def _source() -> str:
    return INSTALLER.read_text(encoding="utf-8")


def test_installer_builds_only_from_the_exact_reviewed_snapshot() -> None:
    source = _source()

    assert "execute only the reviewed root-installed entrypoint" in source
    assert "runtime-installer.trust" in source
    assert "refs/mooncen/docker-release-snapshots/" in source
    assert 'bundle create "$source_bundle" "$reference"' in source
    assert 'init --quiet "$control_stage"' in source
    assert 'fetch --quiet \\' in source
    assert '--no-tags "$source_bundle" "+${reference}:${reference}"' in source
    assert 'rm -- "$source_bundle"' in source
    assert "clone --quiet --no-local" not in source
    bundle = source.split('bundle create "$source_bundle"', 1)[0].rsplit(
        "env -i HOME=/root", 1
    )[1]
    assert '-c safe.directory="$source_repository"' in bundle
    assert '-c safe.directory="$source_repository/.git"' in bundle
    assert "GIT_CONFIG_GLOBAL=/dev/null" in bundle
    assert 'rev-parse --verify "$reference^{commit}"' in source
    assert 'rev-parse --verify "$commit^{tree}"' in source
    assert 'rev-parse --verify "$commit^"' in source
    assert "status --porcelain --untracked-files=all" in source
    assert "policy-digest --source-root" in source
    assert "verify_clean_source.py" in source
    assert "--allow-dirty-source" not in source


def test_installer_accepts_the_exact_eleven_argument_install_contract() -> None:
    source = _source()
    contract = source.split('\n[ "$#" -eq 11 ]', 1)[1].split(
        ' || \\\n  die "usage:',
        1,
    )[0]
    contract = '[ "$#" -eq 11 ]' + contract
    arguments = (
        "install",
        "--reference",
        f"refs/mooncen/docker-release-snapshots/{'1' * 32}",
        "--commit",
        "2" * 40,
        "--base-commit",
        "3" * 40,
        "--source-tree",
        "4" * 40,
        "--build-policy",
        "5" * 64,
    )

    accepted = subprocess.run(
        ["/bin/bash", "-c", contract, "installer-contract", *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    assert accepted.returncode == 0, accepted.stderr

    rejected = subprocess.run(
        [
            "/bin/bash",
            "-c",
            contract,
            "installer-contract",
            *arguments[:-2],
            "--unexpected-policy",
            arguments[-1],
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode != 0


def test_installer_builds_and_validates_once_before_pair_activation() -> None:
    source = _source()

    build = source.index("build_release_bundle.py")
    smoke = source.index('--release-directory "$release_dir"')
    immutable_move = source.index('mv -- "$pair_stage" "$pair_final"')
    seal_static = source.index("seal_ops_static.py")
    finish = source.split("finish_pair_install() {", 1)[1].split("\n}", 1)[0]
    prepare = finish.index("install_development_runtime.sh")
    activate = finish.index('"$manager" activate-development "$pair_name"')
    finish_call = source.rindex("\nfinish_pair_install\n")
    finalize = source.split("finalize_control() {", 1)[1].split(
        "\n}\n\nops_rotation_transaction() {",
        1,
    )[0]
    running = finalize.rindex('verify_active_development_pair "$pair"')
    tunnel = finalize.rindex("stage_registration_access")
    transport = finalize.rindex("verify_control_transports")
    handoff = finalize.index("container_evidence_handoff.py")
    register = finalize.index("mooncen-register-container-evidence")
    isolated = finalize.rindex("install_isolated_control_plane.sh")

    assert build < smoke < seal_static < immutable_move < finish_call
    assert prepare < activate
    assert running < tunnel < transport < handoff < register < isolated
    assert "mooncen-register-container-evidence" not in finish
    assert "/usr/bin/python3.12 -m venv --copies" in source
    assert '"$control_stage/.venv/bin/python" -I -m pip check' in source
    assert "run_operator /usr/bin/docker build" in source
    assert "--validation-target an2p-dev" in source
    assert "validation.json" in source
    assert source.count('--staging-token "$token"') == 2
    assert source.index('--staging-token "$token"') < seal_static < immutable_move
    assert (
        '/bin/bash "$pair_final/control/deploy/an2p/install_development_runtime.sh"'
        in source
    )
    assert "docker compose down -v" not in source
    assert "docker compose down --volumes" not in source


def test_an2p_runbooks_do_not_execute_non_executable_snapshot_scripts() -> None:
    readme = (ROOT / "deploy/an2p/README.md").read_text(encoding="utf-8")
    development = (ROOT / "docs/docker-development.md").read_text(encoding="utf-8")
    production = (ROOT / "docs/docker-production.md").read_text(encoding="utf-8")

    for document in (readme, development):
        assert "/bin/bash ./deploy/an2p/install_user_services.sh" in document
        assert "\n./deploy/an2p/install_user_services.sh" not in document
    assert "finalize-control --pair" in readme
    assert "--terminate-legacy-session" not in readme
    assert "--terminate-legacy-session" not in (
        ROOT / "deploy/an2p/install_user_services.sh"
    ).read_text(encoding="utf-8")
    assert "/home/sgm/.config/mooncen-docker/runtime-config.js" not in development
    assert "/var/lib/mooncen-docker-operator/runtime-config.js" in development
    for document in (readme, production):
        assert "PostgreSQL HBA" in document
        assert "atomic" in document
        assert "reload" in document
        assert "다른 DB 거부" in document
        assert "HBA, unit은 아직 설치·기동하지" not in document
    assert "Gate 6의 reviewed exporter stream" in production
    assert production.count("Gate 7 finalizer") >= 2
    assert "Gate 5의 reviewed exporter" not in production
    assert "Gate 6 finalizer" not in production
    assert "위 목록의 6단계 target-identity bootstrap" in readme


def test_installer_requires_the_reviewed_external_docker_platform() -> None:
    source = _source()

    assert "for package in docker.io docker-compose-v2 docker-buildx" in source
    assert "operator_build_root=/var/lib/mooncen-docker-operator/build" in source
    assert 'operator_output=$operator_build_root/$token' in source
    assert (
        'install -d -o "$docker_user" -g "$docker_user" -m 0700 '
        '"$operator_build_root"' in source
    )
    assert "Docker CLI metadata is unsafe" in source
    assert "an2p Docker daemon overrides require separate review" in source
    assert "an2p Docker service overrides require separate review" in source
    assert "systemctl is-enabled --quiet docker.service" in source
    assert 'root:docker:660' in source
    assert (
        "docker info --format '{{.OSType}}/{{.Architecture}}')\" = linux/x86_64"
        in source
    )
    assert 'docker compose version --short' in source
    assert "(2, 35, 0)" in source
    assert "run_operator /usr/bin/docker buildx version" in source
    assert source.count("run_operator /usr/bin/env -u DOCKER_HOST") == 2


def test_installer_docker_policy_package_markers_are_present_and_reviewed() -> None:
    installer = _source()
    clean_source = (ROOT / "deploy/docker/verify_clean_source.py").read_text(
        encoding="utf-8"
    )
    integrity = (ROOT / "deploy/docker/production_runtime_integrity.py").read_text(
        encoding="utf-8"
    )

    for relative in (
        "deploy/__init__.py",
        "deploy/an2p/__init__.py",
        "deploy/docker/__init__.py",
    ):
        assert (ROOT / relative).is_file()
        assert relative in installer
        assert relative in clean_source
        assert relative in integrity


def test_installer_preserves_the_protected_docker_stage_metadata() -> None:
    source = _source()

    loop = source.split('for relative in "${docker_policy_paths[@]}"; do', 1)[1].split(
        "\ndone\n",
        1,
    )[0]
    assert 'destination_parent=$(dirname "$destination")' in loop
    assert '[ "$destination_parent" != "$docker_stage" ]' in loop
    assert 'install -d -o root -g root -m 0755 "$destination_parent"' in loop
    assert 'install -d -o root -g root -m 0755 "$(dirname "$destination")"' not in loop


def test_installer_accepts_reviewed_ubuntu_compose_version_suffix() -> None:
    source = _source()
    compose_check = source.split("compose_version=$(", 1)[1]
    parser = compose_check.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]

    for version in ("2.40.3", "v2.40.3", "2.40.3+ds1-0ubuntu1~24.04.1"):
        result = subprocess.run(
            [sys.executable, "-I", "-", version],
            input=parser,
            text=True,
            check=False,
        )
        assert result.returncode == 0, version

    for version in ("2.34.9", "2.40", "2.40.3 attacker"):
        result = subprocess.run(
            [sys.executable, "-I", "-", version],
            input=parser,
            text=True,
            check=False,
        )
        assert result.returncode == 78, version


def test_installer_restarts_the_db_tunnel_after_key_rotation() -> None:
    installer = _source()
    isolated = (
        ROOT / "deploy/an2p/install_isolated_control_plane.sh"
    ).read_text(encoding="utf-8")

    for source in (installer, isolated):
        key_install = source.index("db-id_ed25519")
        restart = source.index("systemctl restart mooncen-ops-db-tunnel.service")
        active = source.index(
            "systemctl is-active --quiet mooncen-ops-db-tunnel.service",
            restart,
        )
        assert key_install < restart < active


def test_isolated_installer_atomically_restarts_and_verifies_the_ops_api_env() -> None:
    isolated = (
        ROOT / "deploy/an2p/install_isolated_control_plane.sh"
    ).read_text(encoding="utf-8")

    stage = isolated.index("api_env_stage=$(mktemp")
    mask = isolated.index("systemctl mask --runtime mooncen-ops-api.service")
    stop = isolated.index("systemctl stop mooncen-ops-api.service", mask)
    publish = isolated.index('mv -fT -- "$api_env_stage" "$api_env_destination"')
    unmask = isolated.index("systemctl unmask --runtime mooncen-ops-api.service", publish)
    restart = isolated.index("systemctl restart mooncen-ops-api.service", unmask)
    health = isolated.index("http://127.0.0.1:5175/health --timeout 90", restart)
    process_env = isolated.index('pathlib.Path(f"/proc/{pid}/environ")', health)
    commit = isolated.index("api_env_committed=true", process_env)

    assert stage < mask < stop < publish < unmask < restart < health < process_env < commit
    assert (
        'install -o root -g "$api_user" -m 0640 \\\n'
        '          "$api_env_backup" "$api_env_destination"'
    ) in isolated
    assert 'loaded != desired' in isolated
    assert 'previous != desired and loaded == previous' in isolated


def test_ops_api_environment_verifier_accepts_only_the_restarted_process_hash(
    tmp_path: Path,
) -> None:
    isolated = (
        ROOT / "deploy/an2p/install_isolated_control_plane.sh"
    ).read_text(encoding="utf-8")
    marker = '/usr/bin/python3 -I - "$api_pid" "$api_env_destination"'
    verifier = isolated.split(marker, 1)[1].split("<<'PY'\n", 1)[1].split(
        "\nPY\napi_env_committed=true", 1
    )[0]
    desired = tmp_path / "new.env"
    previous = tmp_path / "old.env"
    new_hash = f"pbkdf2_sha256$600000$new-salt-1234567${'a' * 64}"
    old_hash = f"pbkdf2_sha256$600000$old-salt-1234567${'b' * 64}"
    desired.write_text(f"MOONCEN_OPS_PASSWORD_HASH={new_hash}\n", encoding="ascii")
    previous.write_text(f"MOONCEN_OPS_PASSWORD_HASH={old_hash}\n", encoding="ascii")
    environment = {**os.environ, "MOONCEN_OPS_PASSWORD_HASH": new_hash}
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        env=environment,
    )
    try:
        accepted = subprocess.run(
            [sys.executable, "-I", "-", str(process.pid), str(desired), str(previous)],
            input=verifier,
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
        assert accepted.returncode == 0, accepted.stderr

        desired.write_text(f"MOONCEN_OPS_PASSWORD_HASH={old_hash}\n", encoding="ascii")
        rejected = subprocess.run(
            [sys.executable, "-I", "-", str(process.pid), str(desired), str(previous)],
            input=verifier,
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
        assert rejected.returncode != 0
        assert "did not load the rotated password hash" in rejected.stderr
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_ops_api_environment_failure_restores_the_previous_bytes_and_service(
    tmp_path: Path,
) -> None:
    isolated = (
        ROOT / "deploy/an2p/install_isolated_control_plane.sh"
    ).read_text(encoding="utf-8")
    cleanup = isolated.split("cleanup_api_environment() {", 1)[1].split(
        "trap cleanup_api_environment EXIT", 1
    )[0]
    cleanup = "cleanup_api_environment() {" + cleanup
    cleanup = cleanup.replace(
        'install -o root -g "$api_user" -m 0640',
        "install -m 0640",
    ).replace("/etc/mooncen-an2p", str(tmp_path))
    destination = tmp_path / "ops-api.env"
    backup = tmp_path / "previous.env"
    service_log = tmp_path / "systemctl.log"
    destination.write_text("MOONCEN_OPS_PASSWORD_HASH=new-hash\n", encoding="ascii")
    backup.write_text("MOONCEN_OPS_PASSWORD_HASH=old-hash\n", encoding="ascii")
    script = f"""\
set -euo pipefail
systemctl() {{ printf '%s\\n' "$*" >> {str(service_log)!r}; }}
api_user=unused
api_env_destination={str(destination)!r}
api_env_backup={str(backup)!r}
api_env_stage=
api_env_had_previous=true
api_env_published=true
api_env_committed=false
api_cutover_started=true
api_service_masked=false
api_was_active=true
{cleanup}
trap cleanup_api_environment EXIT
false
"""
    completed = subprocess.run(
        ["/bin/bash", "-c", script],
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )

    assert completed.returncode != 0
    assert destination.read_text(encoding="ascii") == (
        "MOONCEN_OPS_PASSWORD_HASH=old-hash\n"
    )
    assert not backup.exists()
    calls = service_log.read_text(encoding="utf-8")
    assert "daemon-reload" in calls
    assert "restart mooncen-ops-api.service" in calls


def test_evidence_handoff_executes_the_reviewed_absolute_script() -> None:
    source = _source()

    immutable_python = '"$pair_releases/$pair/control/.venv/bin/python" -I'
    immutable_script = (
        '"$pair_releases/$pair/control/deploy/an2p/container_evidence_handoff.py"'
    )
    handoff = source.index(immutable_python)
    script = source.index(immutable_script, handoff)
    register = source.index(
        '/usr/local/libexec/mooncen-register-container-evidence "$source_tree"'
    )

    assert handoff < script < register
    assert "-m deploy.an2p.container_evidence_handoff" not in source


def test_only_the_pair_manager_creates_or_mutates_the_active_runtime_selection() -> None:
    source = _source()

    assert '"$manager" validate "$pair_name"' in source
    assert '"$manager" activate-development "$pair_name"' in source
    assert '"$manager" activate-retained "$3"' in source
    assert "ln -sfn" not in source
    assert 'ln -s "releases/$pair_name" "$pair_root/current"' not in source
    assert 'ln -s "$pair_final" "$pair_root/current"' not in source
    assert 'rm -- "$pair_root/current"' not in source
    assert "migrate_legacy_alias \"$control_alias_root\" control" in source
    assert "migrate_legacy_alias \"$docker_alias_root\" docker" in source


def test_transport_probe_binds_both_exact_controller_envelopes_to_pending() -> None:
    source = _source()
    marker = '"$status_json" "$deploy_json" <<\'PY\'\n'
    parser = source.split(marker, 1)[1].split("\nPY\n", 1)[0]
    identity = "a" * 64
    identity_envelope = json.dumps(
        {
            "schema_version": 1,
            "target": "an2p-dev",
            "target_identity": identity,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    status_envelope = json.dumps(
        {
            "native_intent": None,
            "schema_version": 1,
            "state": None,
            "transaction": None,
            "worker_lease": None,
        },
        sort_keys=True,
        separators=(",", ":"),
    )

    def run(
        status_identity: str = identity_envelope,
        deploy_identity: str = identity_envelope,
        status: str = status_envelope,
        deploy: str = status_envelope,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-I",
                "-",
                identity,
                status_identity,
                deploy_identity,
                status,
                deploy,
            ],
            input=parser,
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )

    assert run().returncode == 0
    assert run(status_identity=identity).returncode != 0
    wrong_target = json.dumps(
        {
            "schema_version": 1,
            "target": "production",
            "target_identity": identity,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    assert run(deploy_identity=wrong_target).returncode != 0
    polluted_status = status_envelope[:-1] + ',"unexpected":null}'
    assert run(status=polluted_status, deploy=polluted_status).returncode != 0
    assert run(deploy=status_envelope.replace('"state":null', '"state":{}')).returncode != 0


def test_transport_negative_boundaries_are_bounded_exact_and_leave_no_listener(
    tmp_path: Path,
) -> None:
    source = _source()
    helpers = (
        "expect_exact_transport_denial() {"
        + source.split("expect_exact_transport_denial() {", 1)[1].split(
            "\nverify_control_transports() {",
            1,
        )[0]
    )
    binaries = {
        name: tmp_path / name
        for name in ("timeout", "runuser", "ssh", "sftp", "ss")
    }
    log_path = tmp_path / "calls.log"

    binaries["timeout"].write_text(
        "#!/bin/sh\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  case \"$1\" in\n"
        "    --foreground|--signal=*|--kill-after=*) shift ;;\n"
        "    *s) shift; break ;;\n"
        "    *) exit 98 ;;\n"
        "  esac\n"
        "done\n"
        "exec \"$@\"\n",
        encoding="utf-8",
    )
    binaries["runuser"].write_text(
        "#!/bin/sh\n"
        "[ \"$1\" = --user ] || exit 97\n"
        "shift 2\n"
        "[ \"$1\" = -- ] || exit 97\n"
        "shift\n"
        "exec \"$@\"\n",
        encoding="utf-8",
    )
    binaries["sftp"].write_text("#!/bin/sh\nexit 255\n", encoding="utf-8")
    binaries["ss"].write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    for name, path in binaries.items():
        if name == "ssh":
            continue
        path.chmod(0o755)

    def write_ssh(*, pty_status: int = 255, administrative: bool = True) -> None:
        binaries["ssh"].write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$*\" >> {shlex.quote(str(log_path))}\n"
            "case \" $* \" in\n"
            f"  *' -tt '*) exit {pty_status} ;;\n"
            "  *' -W '*)\n"
            + (
                "    printf '%s\\n' 'channel 0: open failed: administratively prohibited: open failed' >&2\n"
                if administrative
                else "    printf '%s\\n' 'channel 0: open failed: connect failed: Connection refused' >&2\n"
            )
            + "    exit 255 ;;\n"
            "esac\n"
            "exit 96\n",
            encoding="utf-8",
        )
        binaries["ssh"].chmod(0o755)

    substitutions = {
        "/usr/bin/timeout": str(binaries["timeout"]),
        "/usr/sbin/runuser": str(binaries["runuser"]),
        "/usr/bin/ssh": str(binaries["ssh"]),
        "/usr/bin/sftp": str(binaries["sftp"]),
        "/usr/bin/ss": str(binaries["ss"]),
        "/run/mooncen-an2p-runtime": str(tmp_path / "probe-runtime"),
    }
    (tmp_path / "probe-runtime").mkdir(mode=0o755)
    for original, replacement in substitutions.items():
        helpers = helpers.replace(original, replacement)
    probe_metadata = subprocess.run(
        ["stat", "-c", "%U:%G:%a", str(tmp_path / "probe-runtime")],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    helpers = helpers.replace("root:root:755", probe_metadata)
    helpers = helpers.replace(
        "root:root:600",
        probe_metadata.rsplit(":", 1)[0] + ":600",
    )

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash"],
            input=(
                "set -euo pipefail\n"
                "die() { printf '%s\\n' \"$*\" >&2; return 1; }\n"
                "worker_user=mooncen_deployment_worker\n"
                "tunnel_user=mooncen_ops_db_tunnel\n"
                f"{helpers}\n"
                "verify_control_transport_negative_boundaries\n"
            ),
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )

    write_ssh()
    accepted = run()
    assert accepted.returncode == 0, accepted.stderr
    calls = log_path.read_text(encoding="utf-8")
    assert calls.count("-tt") == 2
    assert calls.count("-W 127.0.0.1:22") == 1
    assert calls.count("-W 127.0.0.1:5432") == 2

    write_ssh(pty_status=0)
    pty_granted = run()
    assert pty_granted.returncode != 0
    assert "denial returned 0 instead of 255" in pty_granted.stderr

    write_ssh(administrative=False)
    connection_refused = run()
    assert connection_refused.returncode != 0
    assert "not an authoritative administrative" in connection_refused.stderr

    binaries["ss"].write_text(
        "#!/bin/sh\nprintf '%s\\n' 'LISTEN 0 128 127.0.0.1:15433'\n",
        encoding="utf-8",
    )
    binaries["ss"].chmod(0o755)
    listener = run()
    assert listener.returncode != 0
    assert "left a loopback listener" in listener.stderr

    binaries["ss"].write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binaries["ss"].chmod(0o755)
    binaries["timeout"].write_text("#!/bin/sh\nexit 124\n", encoding="utf-8")
    binaries["timeout"].chmod(0o755)
    timed_out = run()
    assert timed_out.returncode != 0
    assert "denial returned 124 instead of 255" in timed_out.stderr


def test_transport_negatives_run_before_handoff_and_database_registration() -> None:
    source = _source()
    finalize = source.split("finalize_control() {", 1)[1].split(
        "\n}\n\nops_rotation_transaction() {",
        1,
    )[0]
    transport = source.split("verify_control_transports() {", 1)[1].split(
        "\n}\n\npublish_control_finalization() {",
        1,
    )[0]

    assert "verify_control_transport_negative_boundaries" in transport
    for required in (
        "/usr/bin/timeout --foreground --signal=TERM --kill-after=2s 15s",
        "/usr/bin/ssh -tt",
        "/usr/bin/sftp -b /dev/null",
        "-W 127.0.0.1:22",
        "-W 127.0.0.1:5432",
        "administratively prohibited",
        "sport = :15433",
    ):
        assert required in source
    assert finalize.index("verify_control_transports") < finalize.index(
        "container_evidence_handoff.py"
    ) < finalize.index("mooncen-register-container-evidence")


def test_host_layer_abi_is_checked_before_global_host_files_change() -> None:
    source = _source()

    compatibility = source.index("existing pair pointer is unsafe")
    mutation = source.index(
        "install -d -o root -g root -m 0755 /usr/local/libexec"
    )
    assert compatibility < mutation
    assert "host runtime ABI changed" in source


def test_phase_one_preserves_native_until_the_manager_journals_the_cutover() -> None:
    installer = _source()
    bootstrap = (ROOT / "deploy/an2p/bootstrap_runtime_installer.sh").read_text(
        encoding="utf-8"
    )
    preparation = (ROOT / "deploy/an2p/install_development_runtime.sh").read_text(
        encoding="utf-8"
    )

    boundary_call = bootstrap.index("\nrevoke_host_root_without_losing_public_development\n")
    assert boundary_call < bootstrap.index('mv -fT -- "$installer_stage" "$target_installer"')
    assert "bootstrap-development.json" in bootstrap
    assert "quarantine_legacy_user_state()" in bootstrap
    for credential in (
        "cloud-deploy.ssh_config",
        "cloud-deploy-ed25519",
        "ops-api.env",
        "deployment-worker.env",
    ):
        assert f'"{credential}"' in bootstrap
    assert "http://127.0.0.1:8001/health" in bootstrap
    assert "http://127.0.0.1:5174" in bootstrap
    assert "/usr/bin/loginctl terminate-user" not in bootstrap
    recovery_boundary = bootstrap.split(
        "revoke_host_root_without_losing_public_development() {",
        1,
    )[1].split("\n}\n\nrevoke_host_root_without_losing_public_development\n", 1)[0]
    assert "mooncen-development-runtime.target" not in recovery_boundary
    assert "for unit in mooncen-api.service mooncen-frontend.service" in recovery_boundary
    legacy_docker_rejection = bootstrap.index(
        "first pair bootstrap requires the reviewed native development runtime"
    )
    recovery_install = bootstrap.index("\n  install_bootstrap_recovery_unit\n")
    assert legacy_docker_rejection < recovery_install < boundary_call
    assert "/opt/mooncen-an2p-runtime/current" in bootstrap
    assert "legacy Docker selection was left untouched" in bootstrap

    assert 'selection_before=$("$selector" runtime-status)' in preparation
    assert "marker_stage=" not in preparation
    assert 'disable --now \\\n  mooncen-api.service mooncen-frontend.service' not in preparation
    assert "development preparation changed the active public runtime" in preparation
    finish = installer.split("finish_pair_install() {", 1)[1].split("\n}", 1)[0]
    assert finish.index("--prepare --pair") < finish.index("activate-development")


def test_runtime_installer_preserves_clean_ssh_sessions_while_quiescing_exact_legacy_units() -> None:
    source = _source()
    preserve = source.split(
        "preserve_public_development_while_revoking_host_root() {",
        1,
    )[1].split("\n}\n\nbootstrap_prerequisites() {", 1)[0]
    quiesce = source.split("quiesce_control_consumers() {", 1)[1].split(
        "\n}\n\nverify_completed_control_plane() {",
        1,
    )[0]
    completed = source.split("verify_completed_control_plane() {", 1)[1].split(
        "\n}\n\nfinalize_control() {",
        1,
    )[0]

    assert "/usr/bin/loginctl terminate-user" not in source
    assert "assert_legacy_host_root_revoked()" in source
    assert source.count("assert_legacy_host_root_revoked") >= 4
    assert 'systemctl --user --machine=sgm@ disable --now \\\n    "${legacy_user_control_units[@]}"' in quiesce
    assert '! systemctl --user --machine=sgm@ is-active --quiet "$unit"' in quiesce
    assert '! systemctl --user --machine=sgm@ is-enabled --quiet "$unit"' in quiesce
    assert quiesce.count("verify_legacy_user_unit_masks") >= 3
    assert "verify_legacy_user_unit_masks" in preserve
    assert completed.count("verify_legacy_user_unit_masks") >= 2
    assert "install -d -o sgm" not in quiesce
    assert 'rm -- "$user_unit"' not in quiesce
    assert 'ln -s /dev/null "$user_unit"' not in quiesce
    assert "chown -h root:root" not in quiesce
    assert "pair_releases/$pair/control/deploy/an2p/mooncen-status-agent.service" not in quiesce
    assert "/proc/" not in quiesce
    assert "pgrep" not in quiesce
    assert "pkill" not in quiesce


def test_legacy_user_mask_verifier_is_fd_relative_nofollow_and_mount_bound() -> None:
    source = _source()
    verifier = source.split("verify_legacy_user_unit_masks() {", 1)[1].split(
        "\n}\n\nensure_account() {",
        1,
    )[0]

    assert "os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC" in verifier
    assert "dir_fd=parent" in verifier
    assert "dir_fd=descriptor, follow_symlinks=False" in verifier
    assert 'os.readlink(unit, dir_fd=descriptor) != "/dev/null"' in verifier
    assert 'line.startswith("mnt_id:")' in verifier
    assert "mount_id(descriptor) != home_mount" in verifier
    assert "metadata.st_uid != expected_mask_uid" in verifier
    assert "metadata.st_gid != expected_mask_gid" in verifier
    assert "first != second" in verifier


def test_trusted_development_bootstrap_is_exact_private_and_idempotent(
    tmp_path: Path,
) -> None:
    source = _source()
    marker = (
        '/usr/bin/python3.12 -I - "$bootstrap" "$destination" 0 0 <<\'PY\'\n'
    )
    program = source.split(marker, 1)[1].split("\nPY\n", 1)[0]
    root = tmp_path / "bootstrap"
    root.mkdir(mode=0o700)
    destination = root / "docker-development.env"

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-I",
                "-",
                str(root),
                str(destination),
                str(os.getuid()),
                str(os.getgid()),
            ],
            input=program,
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )

    first = run()
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout) == {
        "created": True,
        "path": str(destination),
        "production_inputs_read": False,
        "schema_version": 1,
    }
    payload = destination.read_bytes()
    assert destination.stat().st_mode & 0o777 == 0o600
    names = [line.partition(b"=")[0].decode("ascii") for line in payload.splitlines()]
    assert names == [
        "COMPOSE_PROJECT_NAME",
        "MOONCEN_AUTH_SECRET",
        "MOONCEN_CORS_ORIGINS",
        "MOONCEN_DB_API_PASSWORD",
        "MOONCEN_DB_API_USER",
        "MOONCEN_DB_NAME",
        "MOONCEN_DB_PASSWORD",
        "MOONCEN_DB_USER",
        "MOONCEN_OAUTH_REDIRECT_URI",
        "MOONCEN_SITE_URL",
    ]
    values = dict(
        line.decode("ascii").split("=", 1) for line in payload.splitlines()
    )
    secrets = {
        values["MOONCEN_AUTH_SECRET"],
        values["MOONCEN_DB_API_PASSWORD"],
        values["MOONCEN_DB_PASSWORD"],
    }
    assert len(secrets) == 3
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{64}", value) for value in secrets)

    second = run()
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["created"] is False
    assert destination.read_bytes() == payload

    destination.write_bytes(payload + b"UNEXPECTED=value\n")
    destination.chmod(0o600)
    rejected = run()
    assert rejected.returncode != 0
    assert "development bootstrap contract is invalid" in rejected.stderr
    destination.write_bytes(
        payload.replace(b"MOONCEN_DB_NAME=mooncen\n", b"MOONCEN_DB_NAME=production\n")
    )
    destination.chmod(0o600)
    fixed_drift = run()
    assert fixed_drift.returncode != 0
    assert "development bootstrap contract is invalid" in fixed_drift.stderr
    values = dict(
        line.decode("ascii").split("=", 1) for line in payload.splitlines()
    )
    destination.write_bytes(
        payload.replace(
            f"MOONCEN_DB_API_PASSWORD={values['MOONCEN_DB_API_PASSWORD']}\n".encode(),
            f"MOONCEN_DB_API_PASSWORD={values['MOONCEN_DB_PASSWORD']}\n".encode(),
        )
    )
    destination.chmod(0o600)
    reused = run()
    assert reused.returncode != 0
    assert "development bootstrap contract is invalid" in reused.stderr
    assert "prepare-development-bootstrap accepts no arguments" in source
    consumption = source.index('docker_env_source=$bootstrap/docker-development.env')
    validation = source.index("prepare_development_bootstrap >/dev/null", consumption)
    rendering = source.index('/usr/bin/python3.12 -I - "$docker_env_source"', validation)
    assert consumption < validation < rendering


def test_control_preflight_rejects_stale_environments_and_signing_secret(
    tmp_path: Path,
) -> None:
    source = _source().split("preflight_control_bootstrap() {", 1)[1].split(
        "\n}\n\nstage_registration_access() {",
        1,
    )[0]
    program = source.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    bootstrap = tmp_path / "bootstrap"
    control = tmp_path / "control"
    local = control / "deploy/an2p/local"
    bootstrap.mkdir()
    local.mkdir(parents=True)
    templates = {
        "deploy-ssh_config": "cloud-container-deploy.ssh_config",
        "status-ssh_config": "cloud-container-status.ssh_config",
        "db-ssh_config": "cloud-ops-db.ssh_config",
    }
    for index, (destination, template) in enumerate(templates.items(), start=1):
        payload = f"Host reviewed-{index}\n  HostName 127.0.0.{index}\n".encode()
        (local / template).write_bytes(payload)
        (bootstrap / destination).write_bytes(payload)
    known_hosts = b"cloud ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICanary\n"
    (local / "cloud-deploy.known_hosts").write_bytes(known_hosts)
    for destination in (
        "deploy-known_hosts",
        "status-known_hosts",
        "db-known_hosts",
    ):
        (bootstrap / destination).write_bytes(known_hosts)
    key_comments = {
        "deploy": "mooncen-an2p-container-deploy-20260819",
        "status": "mooncen-an2p-container-status-20260819",
        "db": "mooncen-an2p-ops-db-20260819",
    }
    for role, comment in key_comments.items():
        subprocess.run(
            [
                "/usr/bin/ssh-keygen",
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-C",
                comment,
                "-f",
                str(bootstrap / f"{role}-id_ed25519"),
            ],
            check=True,
            timeout=10,
        )

    identity = "8" * 64
    pending = tmp_path / "pending.json"
    pending.write_text(
        json.dumps({"target_identity": identity}, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="ascii",
    )
    control_values = {
        "DB_API_PASSWORD": "api-password-independent-canary",
        "DB_API_USER": "mooncen_api_login",
        "DB_DEPLOYMENT_WORKER_PASSWORD": "worker-password-independent-canary",
        "DB_DEPLOYMENT_WORKER_USER": "mooncen_deployment_worker_login",
        "DB_NAME": "mooncen",
        "MOONCEN_OPS_LOGIN_ID": "opsadmin",
        "MOONCEN_OPS_PASSWORD_HASH": (
            f"pbkdf2_sha256$600000${'s' * 16}${'a' * 64}"
        ),
        "OPS_CONTAINER_DEV_TARGET_IDENTITY": identity,
    }
    control_order = (
        "DB_API_PASSWORD",
        "DB_API_USER",
        "DB_DEPLOYMENT_WORKER_PASSWORD",
        "DB_DEPLOYMENT_WORKER_USER",
        "DB_NAME",
        "MOONCEN_OPS_LOGIN_ID",
        "MOONCEN_OPS_PASSWORD_HASH",
        "OPS_CONTAINER_DEV_TARGET_IDENTITY",
    )
    (bootstrap / "control-secrets.env").write_text(
        "".join(f"{name}={control_values[name]}\n" for name in control_order),
        encoding="utf-8",
    )
    secret = "z" * 64
    (bootstrap / "ops-auth-secret").write_text(f"{secret}\n", encoding="ascii")
    api, worker = prepare.render_environments(
        control_values,
        ops_auth_secret=secret,
    )
    api_path = bootstrap / "ops-api.env"
    worker_path = bootstrap / "deployment-worker.env"
    api_path.write_text(api, encoding="utf-8")
    worker_path.write_text(worker, encoding="utf-8")

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-I", "-", str(bootstrap), str(control), str(pending)],
            input=program,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )

    accepted = run()
    assert accepted.returncode == 0, accepted.stderr
    api_path.write_text(
        api.replace("LOG_LEVEL=INFO\n", "LOG_LEVEL=DEBUG\n"),
        encoding="utf-8",
    )
    stale_api = run()
    assert stale_api.returncode != 0
    assert "generated control environment is stale or incomplete" in stale_api.stderr
    api_path.write_text(api, encoding="utf-8")
    (bootstrap / "ops-auth-secret").write_text(f"{'y' * 64}\n", encoding="ascii")
    secret_drift = run()
    assert secret_drift.returncode != 0
    assert "generated control environment is stale or incomplete" in secret_drift.stderr
    (bootstrap / "ops-auth-secret").write_text(f"{secret}\n", encoding="ascii")
    custom_worker_values = {
        **control_values,
        "DB_DEPLOYMENT_WORKER_USER": "custom_worker_login",
    }
    (bootstrap / "control-secrets.env").write_text(
        "".join(f"{name}={custom_worker_values[name]}\n" for name in control_order),
        encoding="utf-8",
    )
    custom_api, custom_worker = prepare.render_environments(
        custom_worker_values,
        ops_auth_secret=secret,
    )
    api_path.write_text(custom_api, encoding="utf-8")
    worker_path.write_text(custom_worker, encoding="utf-8")
    wrong_worker_login = run()
    assert wrong_worker_login.returncode != 0
    assert "protected control envelope is not exact or pair-bound" in (
        wrong_worker_login.stderr
    )


def test_finalize_commit_and_rotation_actions_are_durable_exact_pair_contracts() -> None:
    source = _source()
    finalize = source.split("finalize_control() {", 1)[1].split(
        "\n}\n\nops_rotation_transaction() {",
        1,
    )[0]
    publish = finalize.rindex("publish_control_finalization")
    authorized = finalize.rindex("control_finalize_transaction update", publish)
    commit = finalize.rindex('rm -- "$pending_path"')
    isolated = finalize.rindex("install_isolated_control_plane.sh")
    resume = finalize.rindex("control_finalize_transaction resume-update")
    readiness = finalize.rindex("verify_completed_control_plane")
    remove = finalize.rindex("control_finalize_transaction remove")

    assert publish < authorized < commit < isolated < resume < readiness < remove
    assert "control-finalization-receipt" in finalize
    assert "authorization_committed=true" in finalize
    assert "apply-ops-rotation --pair <runtime-pair>" in source
    rotation = source.split("apply_ops_rotation() {", 1)[1].split(
        "\n}\n\nif [ \"${1:-}\" = rollback ]",
        1,
    )[0]
    assert rotation.index("preflight_control_bootstrap") < rotation.index(
        "ops_rotation_transaction ensure"
    )
    assert "attempted to change the deployment worker environment" in rotation
    assert "attempted to change a service transport" in rotation
    assert "rotated Ops login was not accepted" in rotation
    assert "predates the current reviewed authentication policy" in rotation
    published = rotation.index("ops_rotation_transaction published")
    assert published < rotation.index(
        "verify_completed_control_plane", published
    ) < rotation.index("ops_rotation_transaction remove", published)
    assert "urllib.request.ProxyHandler({})" in rotation
    completed = source.split("verify_completed_control_plane() {", 1)[1].split(
        "\n}\n\nfinalize_control() {",
        1,
    )[0]
    assert "use the trusted apply-ops-rotation action for a finalized pair" in completed
    assert completed.count("completed control transport differs") == 1
    final_emitter = source.split("emit_finalization_success() {", 1)[1].split(
        "\n}", 1
    )[0]
    rotation_emitter = source.split("emit_rotation_success() {", 1)[1].split(
        "\n}", 1
    )[0]
    for emitter in (final_emitter, rotation_emitter):
        assert emitter.index("begin_development_selection_fence") < emitter.index(
            "verify_development_selection_under_fence"
        ) < emitter.index("printf '%s\\n'") < emitter.index(
            "end_development_selection_fence"
        )
    assert finalize.count('emit_finalization_success "$pair"') == 2
    assert rotation.count('emit_rotation_success "$pair"') == 2


def test_isolated_control_convergence_holds_the_shared_selection_fence() -> None:
    isolated = (
        ROOT / "deploy/an2p/install_isolated_control_plane.sh"
    ).read_text(encoding="utf-8")
    acquire = isolated.index('/usr/bin/flock -x 6')
    authorize = isolated.index('export MOONCEN_AN2P_MANAGER_LOCK_FD=6', acquire)
    select = isolated.index(
        "/usr/local/libexec/mooncen-an2p-service-control docker-select",
        authorize,
    )
    status = isolated.index(
        "/usr/local/libexec/mooncen-an2p-service-control runtime-status",
        select,
    )
    close = isolated.index("exec 6>&-", status)
    success = isolated.index("isolated an2p API, worker", close)

    assert acquire < authorize < select < status < close < success
    assert "runtime pair transaction blocks isolated control convergence" in isolated
    assert "isolated control convergence lost Docker selection" in isolated


def test_rotated_login_verifier_does_not_use_inherited_http_proxy(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    del monkeypatch
    source = _source().split("verify_rotated_api() {", 1)[1]
    program = source.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    calls = {"target": 0, "proxy": 0}

    class TargetHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib handler ABI.
            calls["target"] += 1
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(
                b'{"user":{"email":"opsadmin@ops.internal",'
                b'"id":"00000000-0000-0000-0000-000000000000",'
                b'"name":"opsadmin","provider":"ops"}}'
            )

        def log_message(self, *_args: object) -> None:
            return

    class ProxyCanaryHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib handler ABI.
            calls["proxy"] += 1
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            return

    target = ThreadingHTTPServer(("127.0.0.1", 0), TargetHandler)
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), ProxyCanaryHandler)
    target_thread = threading.Thread(target=target.serve_forever, daemon=True)
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    target_thread.start()
    proxy_thread.start()
    password_hash = f"pbkdf2_sha256$600000${'s' * 16}${'a' * 64}"
    environment_path = tmp_path / "ops-api.env"
    credential_path = tmp_path / "ops-credentials.txt"
    environment_path.write_text(
        "# Generated locally by prepare_an2p_ops_control.py; never commit.\n"
        f"MOONCEN_OPS_PASSWORD_HASH={password_hash}\n",
        encoding="ascii",
    )
    credential_path.write_text(
        "MoonCen isolated an2p Ops Console\n"
        "URL: http://127.0.0.1:5175/\n"
        "Login ID: opsadmin\n"
        "Password: canary-password\n",
        encoding="ascii",
    )
    sleeper = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        env={**os.environ, "MOONCEN_OPS_PASSWORD_HASH": password_hash},
    )
    try:
        direct_url = f"http://127.0.0.1:{target.server_port}/api/auth/ops/login"
        program = program.replace(
            "http://127.0.0.1:5175/api/auth/ops/login",
            direct_url,
        )
        proxy_url = f"http://127.0.0.1:{proxy.server_port}"
        environment = {
            **os.environ,
            "ALL_PROXY": proxy_url,
            "HTTP_PROXY": proxy_url,
            "NO_PROXY": "",
            "all_proxy": proxy_url,
            "http_proxy": proxy_url,
            "no_proxy": "",
        }
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-",
                str(sleeper.pid),
                str(environment_path),
                str(credential_path),
            ],
            input=program,
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
            env=environment,
        )
    finally:
        sleeper.terminate()
        sleeper.wait(timeout=5)
        target.shutdown()
        proxy.shutdown()
        target.server_close()
        proxy.server_close()

    assert completed.returncode == 0, completed.stderr
    assert calls == {"target": 1, "proxy": 0}


def test_shared_loopback_wait_helper_does_not_use_inherited_http_proxy() -> None:
    calls = {"target": 0, "proxy": 0}

    class TargetHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler ABI.
            calls["target"] += 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ready"}')

        def log_message(self, *_args: object) -> None:
            return

    class ProxyCanaryHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler ABI.
            calls["proxy"] += 1
            self.send_response(503)
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            return

    target = ThreadingHTTPServer(("127.0.0.1", 0), TargetHandler)
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), ProxyCanaryHandler)
    target_thread = threading.Thread(target=target.serve_forever, daemon=True)
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    target_thread.start()
    proxy_thread.start()
    try:
        environment = {
            **os.environ,
            "HTTP_PROXY": f"http://127.0.0.1:{proxy.server_port}",
            "HTTPS_PROXY": f"http://127.0.0.1:{proxy.server_port}",
            "ALL_PROXY": f"http://127.0.0.1:{proxy.server_port}",
            "NO_PROXY": "",
            "no_proxy": "",
        }
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools/wait_for_an2p_http.py"),
                f"http://127.0.0.1:{target.server_port}/health",
                "--timeout",
                "2",
            ],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
        assert completed.returncode == 0, completed.stderr
        assert calls == {"target": 1, "proxy": 0}
    finally:
        target.shutdown()
        proxy.shutdown()
        target.server_close()
        proxy.server_close()
        target_thread.join(timeout=2)
        proxy_thread.join(timeout=2)


def test_shared_loopback_wait_helper_rejects_redirect_without_sink_request() -> None:
    calls = {"redirect": 0, "sink": 0}

    class SinkHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler ABI.
            calls["sink"] += 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ready"}')

        def log_message(self, *_args: object) -> None:
            return

    sink = ThreadingHTTPServer(("127.0.0.1", 0), SinkHandler)

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler ABI.
            calls["redirect"] += 1
            self.send_response(302)
            self.send_header(
                "Location",
                f"http://127.0.0.1:{sink.server_port}/health",
            )
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            return

    redirect = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    threads = [
        threading.Thread(target=server.serve_forever, daemon=True)
        for server in (redirect, sink)
    ]
    for thread in threads:
        thread.start()
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools/wait_for_an2p_http.py"),
                f"http://127.0.0.1:{redirect.server_port}/health",
                "--timeout",
                "1",
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    finally:
        for server in (redirect, sink):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=2)

    assert completed.returncode != 0
    assert calls["redirect"] >= 1
    assert calls["sink"] == 0


def test_rotated_login_verifier_rejects_redirect_without_sink_request(
    tmp_path: Path,
) -> None:
    source = _source().split("verify_rotated_api() {", 1)[1]
    program = source.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    calls = {"redirect": 0, "sink": 0}

    class SinkHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler ABI.
            calls["sink"] += 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(
                b'{"user":{"email":"opsadmin@ops.internal",'
                b'"id":"00000000-0000-0000-0000-000000000000",'
                b'"name":"opsadmin","provider":"ops"}}'
            )

        def log_message(self, *_args: object) -> None:
            return

    sink = ThreadingHTTPServer(("127.0.0.1", 0), SinkHandler)

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib handler ABI.
            calls["redirect"] += 1
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(302)
            self.send_header(
                "Location",
                f"http://127.0.0.1:{sink.server_port}/accepted",
            )
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            return

    redirect = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    threads = [
        threading.Thread(target=server.serve_forever, daemon=True)
        for server in (redirect, sink)
    ]
    for thread in threads:
        thread.start()
    password_hash = f"pbkdf2_sha256$600000${'s' * 16}${'a' * 64}"
    environment_path = tmp_path / "ops-api.env"
    credential_path = tmp_path / "ops-credentials.txt"
    environment_path.write_text(
        "# Generated locally by prepare_an2p_ops_control.py; never commit.\n"
        f"MOONCEN_OPS_PASSWORD_HASH={password_hash}\n",
        encoding="ascii",
    )
    credential_path.write_text(
        "MoonCen isolated an2p Ops Console\n"
        "URL: http://127.0.0.1:5175/\n"
        "Login ID: opsadmin\n"
        "Password: canary-password\n",
        encoding="ascii",
    )
    sleeper = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        env={**os.environ, "MOONCEN_OPS_PASSWORD_HASH": password_hash},
    )
    try:
        program = program.replace(
            "http://127.0.0.1:5175/api/auth/ops/login",
            f"http://127.0.0.1:{redirect.server_port}/api/auth/ops/login",
        )
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-",
                str(sleeper.pid),
                str(environment_path),
                str(credential_path),
            ],
            input=program,
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    finally:
        sleeper.terminate()
        sleeper.wait(timeout=5)
        for server in (redirect, sink):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=2)

    assert completed.returncode != 0
    assert calls == {"redirect": 1, "sink": 0}


def test_bootstrap_native_health_recovery_rejects_redirect_without_sink() -> None:
    bootstrap = (ROOT / "deploy/an2p/bootstrap_runtime_installer.sh").read_text(
        encoding="utf-8"
    )
    marker = "/usr/bin/python3 -I - <<'PY'\nimport time\n"
    program = "import time\n" + bootstrap.split(marker, 1)[1].split("\nPY\n", 1)[0]
    calls = {"redirect": 0, "sink": 0}

    class SinkHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler ABI.
            calls["sink"] += 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ready"}')

        def log_message(self, *_args: object) -> None:
            return

    sink = ThreadingHTTPServer(("127.0.0.1", 0), SinkHandler)

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler ABI.
            calls["redirect"] += 1
            self.send_response(302)
            self.send_header(
                "Location",
                f"http://127.0.0.1:{sink.server_port}/health",
            )
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            return

    redirect = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    threads = [
        threading.Thread(target=server.serve_forever, daemon=True)
        for server in (redirect, sink)
    ]
    for thread in threads:
        thread.start()
    try:
        program = program.replace(
            '("http://127.0.0.1:8001/health", "http://127.0.0.1:5174")',
            f'("http://127.0.0.1:{redirect.server_port}/health",)',
        ).replace("time.monotonic() + 180", "time.monotonic() + 1")
        completed = subprocess.run(
            [sys.executable, "-I", "-"],
            input=program,
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    finally:
        for server in (redirect, sink):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=2)

    assert completed.returncode != 0
    assert calls["redirect"] >= 1
    assert calls["sink"] == 0


def test_all_embedded_python_contracts_compile() -> None:
    for path in (
        INSTALLER,
        ROOT / "deploy/an2p/install_isolated_control_plane.sh",
        ROOT / "deploy/an2p/bootstrap_runtime_installer.sh",
    ):
        source = path.read_text(encoding="utf-8")
        blocks = []
        collecting = False
        lines: list[str] = []
        for line in source.splitlines():
            if not collecting and "<<'PY'" in line:
                collecting = True
                lines = []
            elif collecting and line == "PY":
                blocks.append("\n".join(lines) + "\n")
                collecting = False
            elif collecting:
                lines.append(line)
        assert blocks and not collecting
        for index, block in enumerate(blocks):
            compile(block, f"{path.name}:heredoc:{index}", "exec")


def test_operator_docs_use_only_the_root_installed_runtime_entrypoint() -> None:
    docs = {
        name: (ROOT / name).read_text(encoding="utf-8")
        for name in (
            "docs/docker-ops-console.md",
            "docs/docker-development.md",
        )
    }
    required = (
        "/usr/local/sbin/mooncen-an2p-runtime-install",
        "bootstrap-prerequisites",
        "--reference",
        "--commit",
        "--base-commit",
        "--source-tree",
        "--build-policy",
        "rollback",
        "--pair",
    )
    forbidden = (
        "/home/sgm/src/project/mooncen/deploy/an2p/install_runtime_snapshot.sh",
        "$HOME/.local/share/mooncen-docker/releases",
        "~/.local/share/mooncen-an2p/docker-release-runtime",
        "systemctl --user status mooncen-development-runtime.target mooncen-docker-dev",
        "systemctl --user reload mooncen-docker-dev",
    )

    for document in docs.values():
        assert all(value in document for value in required)
        assert all(value not in document for value in forbidden)


def test_operator_docs_bootstrap_the_root_of_trust_outside_the_checkout() -> None:
    documents = (
        (ROOT / "docs/docker-ops-console.md").read_text(encoding="utf-8"),
        (ROOT / "docs/docker-development.md").read_text(encoding="utf-8"),
    )
    required = (
        "deploy/an2p/bootstrap_runtime_installer.sh",
        "sha256sum",
        "systemd-run",
        "--installer-sha256",
        "--integrity-sha256",
        "--clean-source-sha256",
        "--pair-manager-sha256",
        "--handoff-sha256",
        "--registrar-sha256",
        "--build-policy-sha256",
    )

    for document in documents:
        assert all(value in document for value in required)
        assert "sudo /usr/local/sbin/mooncen-an2p-runtime-install" not in document


def test_architecture_keeps_release_evidence_outside_the_runtime_pair() -> None:
    document = (ROOT / "docs/an2p-control-plane-architecture.md").read_text(
        encoding="utf-8"
    )

    assert "/opt/mooncen-an2p-docker/evidence/<source-tree>/" in document
    pair = document.split("/opt/mooncen-an2p-runtime/current", 1)[1].split(
        "/opt/mooncen-an2p-control/current", 1
    )[0]
    assert "evidence/<source-tree>" not in pair


def _safe_user_path_program() -> str:
    source = (ROOT / "deploy/an2p/install_development_runtime.sh").read_text(
        encoding="utf-8"
    )
    marker = '  /usr/bin/python3.12 -I - "$@" <<\'PY\'\n'
    return source.split(marker, 1)[1].split("\nPY\n}", 1)[0]


def _run_safe_user_path(
    program: str,
    home: Path,
    quarantine: Path,
    action: str,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    identity = str(os.getuid())
    group = str(os.getgid())
    return subprocess.run(
        [
            sys.executable,
            "-I",
            "-",
            action,
            str(home),
            identity,
            group,
            str(quarantine),
            identity,
            group,
            *arguments,
        ],
        input=program,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )


def test_safe_user_path_helper_quarantines_and_publishes_idempotently(
    tmp_path: Path,
) -> None:
    program = _safe_user_path_program()
    home = tmp_path / "home"
    legacy_config = home / ".config/mooncen-an2p"
    legacy_config.mkdir(parents=True, mode=0o700)
    home.chmod(0o700)
    (home / ".config").chmod(0o700)
    legacy_config.chmod(0o700)
    credential = legacy_config / "ops-api.env"
    credential.write_bytes(b"SECRET=retired\n")
    credential.chmod(0o600)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    credential_quarantine = state / "credentials"

    first_credential = _run_safe_user_path(
        program, home, credential_quarantine, "quarantine-credentials"
    )
    assert first_credential.returncode == 0, first_credential.stderr
    quarantined_credential = credential_quarantine / "ops-api.env"
    assert quarantined_credential.read_bytes() == b"SECRET=retired\n"
    assert quarantined_credential.stat().st_mode & 0o777 == 0o600
    assert not credential.exists()
    second_credential = _run_safe_user_path(
        program, home, credential_quarantine, "quarantine-credentials"
    )
    assert second_credential.returncode == 0, second_credential.stderr

    unit_source = tmp_path / "reviewed-units"
    helper_source = tmp_path / "reviewed-tools"
    unit_source.mkdir(mode=0o700)
    helper_source.mkdir(mode=0o700)
    for name in (
        "mooncen-api.service",
        "mooncen-frontend.service",
        "mooncen-development-runtime.target",
    ):
        (unit_source / name).write_text(f"reviewed:{name}\n", encoding="ascii")
        (unit_source / name).chmod(0o644)
    for name in ("wait_for_an2p_http.py", "wait_for_an2p_database.py"):
        (helper_source / name).write_text(f"# reviewed {name}\n", encoding="ascii")
        (helper_source / name).chmod(0o755)
    user_units = home / ".config/systemd/user"
    user_units.mkdir(parents=True, mode=0o755)
    (home / ".config/systemd").chmod(0o700)
    user_units.chmod(0o755)
    retired = user_units / "mooncen-ops-control-env.service"
    retired.write_bytes(b"retired unit\n")
    retired.chmod(0o600)
    unit_quarantine = state / "units"

    first_prepare = _run_safe_user_path(
        program,
        home,
        unit_quarantine,
        "prepare-user-runtime",
        str(unit_source),
        str(helper_source),
        "0",
    )
    assert first_prepare.returncode == 0, first_prepare.stderr
    assert (unit_quarantine / retired.name).read_bytes() == b"retired unit\n"
    assert (unit_quarantine / retired.name).stat().st_mode & 0o777 == 0o600
    assert retired.is_symlink() and retired.readlink() == Path("/dev/null")
    published = user_units / "mooncen-api.service"
    assert published.read_bytes() == b"reviewed:mooncen-api.service\n"
    assert published.stat().st_mode & 0o777 == 0o644
    inode = published.stat().st_ino
    second_prepare = _run_safe_user_path(
        program,
        home,
        unit_quarantine,
        "prepare-user-runtime",
        str(unit_source),
        str(helper_source),
        "0",
    )
    assert second_prepare.returncode == 0, second_prepare.stderr
    assert published.stat().st_ino == inode


def test_safe_user_path_helper_rejects_symlink_and_hardlink_sentinels(
    tmp_path: Path,
) -> None:
    program = _safe_user_path_program()
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    sentinel_directory = tmp_path / "sentinel-directory"
    sentinel_directory.mkdir(mode=0o700)
    sentinel = sentinel_directory / "sentinel"
    sentinel.write_bytes(b"do-not-touch\n")
    sentinel.chmod(0o600)
    (home / ".config").symlink_to(sentinel_directory, target_is_directory=True)

    symlink_result = _run_safe_user_path(
        program,
        home,
        state / "symlink-units",
        "prepare-user-runtime",
        str(tmp_path / "unused-units"),
        str(tmp_path / "unused-tools"),
        "0",
    )
    assert symlink_result.returncode != 0
    assert "directory is unsafe" in symlink_result.stderr
    assert sentinel.read_bytes() == b"do-not-touch\n"
    assert sentinel.stat().st_mode & 0o777 == 0o600

    (home / ".config").unlink()
    legacy_config = home / ".config/mooncen-an2p"
    legacy_config.mkdir(parents=True, mode=0o700)
    (home / ".config").chmod(0o700)
    legacy_config.chmod(0o700)
    hardlink = legacy_config / "ops-api.env"
    os.link(sentinel, hardlink)
    hardlink_result = _run_safe_user_path(
        program,
        home,
        state / "hardlink-credentials",
        "quarantine-credentials",
    )
    assert hardlink_result.returncode != 0
    assert "regular source metadata is unsafe" in hardlink_result.stderr
    assert sentinel.read_bytes() == b"do-not-touch\n"
    assert sentinel.stat().st_mode & 0o777 == 0o600
    assert hardlink.exists()


def test_both_phase_installers_use_the_no_follow_user_path_boundary() -> None:
    development = (ROOT / "deploy/an2p/install_development_runtime.sh").read_text(
        encoding="utf-8"
    )
    isolated = (ROOT / "deploy/an2p/install_isolated_control_plane.sh").read_text(
        encoding="utf-8"
    )

    for required in (
        "os.O_NOFOLLOW",
        'line.startswith("mnt_id:")',
        "assert_bound_directories()",
        "metadata.st_nlink != 1",
        "safe_legacy_user_paths quarantine-credentials",
        "safe_legacy_user_paths prepare-user-runtime",
    ):
        assert required in development
    assert "safe_user_path_helper=$script_dir/install_development_runtime.sh" in isolated
    assert "safe_legacy_user_paths quarantine-credentials" in isolated
    assert "safe_legacy_user_paths prepare-user-runtime" in isolated
    assert 'install -d -o "$legacy_user" -g "$legacy_user"' not in development
    assert 'install -d -o "$legacy_user" -g "$legacy_user"' not in isolated
    assert 'install -o "$legacy_user" -g "$legacy_user"' not in development
    assert 'install -o "$legacy_user" -g "$legacy_user"' not in isolated
    assert '/usr/bin/python3 "$user_runtime_dir/' not in isolated
