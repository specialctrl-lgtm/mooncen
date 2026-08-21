import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKUP_DIR = ROOT / "deploy" / "backup"
SYSTEMD_DIR = ROOT / "deploy" / "ubuntu" / "systemd"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_openssh_manifest_signature_rejects_tampering(tmp_path: Path) -> None:
    ssh_keygen = shutil.which("ssh-keygen")
    if ssh_keygen is None:
        pytest.skip("ssh-keygen is not installed")
    key = tmp_path / "manifest-key"
    manifest = tmp_path / "manifest.txt"
    allowed = tmp_path / "allowed_signers"
    subprocess.run(
        [ssh_keygen, "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
        check=True,
        capture_output=True,
    )
    public_fields = (tmp_path / "manifest-key.pub").read_text(encoding="utf-8").split()
    allowed.write_text(
        f"mooncen-backup {public_fields[0]} {public_fields[1]}\n",
        encoding="utf-8",
    )
    manifest.write_text(
        "format=mooncen-backup-manifest-v1\ntimestamp=20260710_120000\n",
        encoding="utf-8",
    )
    subprocess.run(
        [
            ssh_keygen,
            "-Y",
            "sign",
            "-f",
            str(key),
            "-n",
            "mooncen-backup-manifest-v1",
            str(manifest),
        ],
        check=True,
        capture_output=True,
    )
    verify_command = [
        ssh_keygen,
        "-Y",
        "verify",
        "-f",
        str(allowed),
        "-I",
        "mooncen-backup",
        "-n",
        "mooncen-backup-manifest-v1",
        "-s",
        f"{manifest}.sig",
    ]
    valid = subprocess.run(
        verify_command,
        input=manifest.read_bytes(),
        capture_output=True,
        check=False,
    )
    assert valid.returncode == 0, valid.stderr.decode(errors="replace")

    tampered = subprocess.run(
        verify_command,
        input=manifest.read_bytes() + b"server=attacker\n",
        capture_output=True,
        check=False,
    )
    assert tampered.returncode != 0


def test_backup_ssh_requires_pinned_preprovisioned_host_key() -> None:
    common = _text(BACKUP_DIR / "backup_ssh_common.sh")

    assert "StrictHostKeyChecking=yes" in common
    assert 'UserKnownHostsFile=$BACKUP_KNOWN_HOSTS_FILE' in common
    assert "GlobalKnownHostsFile=/dev/null" in common
    assert "UpdateHostKeys=no" in common
    assert "StrictHostKeyChecking=accept-new" not in common
    assert '"$BACKUP_KNOWN_HOSTS_FILE" root mooncen-backup 640' in common
    assert "[ -L \"$file_path\" ]" in common
    assert "backup_validate_exact_local_file" in common
    assert "/run/credentials/mooncen-backup-restore-test.service" in common
    assert "/run/credentials/mooncen-backup-restore-manual.service" in common
    assert '"$credential_path" root root 400 "Backup restore SSH credential"' in common
    assert (
        '"$BACKUP_DEFAULT_IDENTITY_FILE" root mooncen-backup 640 '
        '"Canonical backup SSH identity"' in common
    )
    assert 'cmp -s -- "$BACKUP_DEFAULT_IDENTITY_FILE" "$credential_path"' in common
    assert 'readonly BACKUP_SYSTEMD_CREDENTIALS_DIRECTORY=' in common
    assert "must receive the NAS identity through systemd LoadCredential" in common
    assert 'SSH_CMD="ssh $(backup_ssh_options)"' in common
    assert 'if [ -z "${SSH_CMD:-}" ]' not in common


def test_manifest_is_signed_and_verified_against_local_public_trust() -> None:
    common = _text(BACKUP_DIR / "backup_ssh_common.sh")
    backup = _text(BACKUP_DIR / "mooncen_backup_to_synology.sh")

    assert 'BACKUP_MANIFEST_NAMESPACE="mooncen-backup-manifest-v1"' in common
    assert "ssh-keygen -Y sign" in common
    assert "ssh-keygen -Y verify" in common
    assert '-I "$BACKUP_MANIFEST_PRINCIPAL"' in common
    assert '-f "$BACKUP_MANIFEST_ALLOWED_SIGNERS"' in common
    assert 'backup_sign_manifest "$WORK_DIR/manifest.txt"' in backup
    assert "format=mooncen-backup-manifest-v1" in backup
    manifest_upload = backup.index(
        'backup_scp_file "$WORK_DIR/manifest.txt" "$REMOTE_DIR/manifests/manifest_$STAMP.txt"'
    )
    signature_upload = backup.index(
        'backup_scp_file "$WORK_DIR/manifest.txt.sig" "$REMOTE_DIR/manifests/manifest_$STAMP.txt.sig"'
    )
    assert manifest_upload < signature_upload


def test_both_restore_paths_share_fail_closed_encrypted_verification() -> None:
    common = _text(BACKUP_DIR / "backup_ssh_common.sh")
    restore_test = _text(BACKUP_DIR / "mooncen_restore_test_from_synology.sh")
    restore_live = _text(BACKUP_DIR / "mooncen_restore_latest_from_synology.sh")

    assert "Only timestamped encrypted .dump.age backups can be restored." in common
    assert "backup_verify_manifest" in common
    assert "Encrypted dump hash does not match the signed manifest." in common
    for restore in (restore_test, restore_live):
        assert "backup_restore_fetch_verified_dump" in restore
        assert "backup_select_latest_committed_dump" in restore
        assert "*.dump'" not in restore
        assert "ALLOW_LEGACY_UNENCRYPTED_BACKUP" not in restore


def test_restore_bounds_and_preflights_happen_before_pg_restore() -> None:
    common = _text(BACKUP_DIR / "backup_ssh_common.sh")
    restore_test = _text(BACKUP_DIR / "mooncen_restore_test_from_synology.sh")
    restore_live = _text(BACKUP_DIR / "mooncen_restore_latest_from_synology.sh")

    for contract in (
        "backup_remote_regular_file_size",
        "BACKUP_MAX_ENCRYPTED_DUMP_BYTES",
        "BACKUP_MAX_DECRYPTED_DUMP_BYTES",
        "backup_require_free_bytes",
        'ulimit -f "$max_blocks"',
        "pg_restore --list",
        "SHOW data_directory",
        "BACKUP_RESTORE_EXPANSION_FACTOR",
        "BACKUP_MAX_AGE_SECONDS",
        "(BACKUP_MAX_DECRYPTED_DUMP_BYTES + 1023) / 1024",
    ):
        assert contract in common
    assert common.index("backup_preflight_postgres_restore()") < common.index(
        "backup_restore_database_candidate()"
    )
    for restore in (restore_test, restore_live):
        assert restore.index("backup_preflight_postgres_restore") < restore.index(
            "backup_restore_database_candidate"
        )


def test_live_restore_validates_candidate_before_stopped_service_name_swap() -> None:
    restore = _text(BACKUP_DIR / "mooncen_restore_latest_from_synology.sh")

    restore_candidate = restore.index("backup_restore_database_candidate")
    validate_candidate = restore.index("$BACKUP_RESTORED_COURSE_COUNT", restore_candidate)
    stop_services = restore.index('echo "stopping_application_services"')
    preserve_old = restore.index(
        'ALTER DATABASE \\\"$DB_NAME\\\" RENAME TO \\\"$RESTORE_OLD_DB\\\"'
    )
    promote_candidate = restore.index(
        'ALTER DATABASE \\\"$RESTORE_STAGE_DB\\\" RENAME TO \\\"$DB_NAME\\\"'
    )
    post_swap_probe = restore.index('echo "verifying_promoted_database"')
    api_probe = restore.index('echo "probing_internal_api_without_ingress"')
    drop_old = restore.index('dropdb --force "$RESTORE_OLD_DB"')
    resume = restore.index('echo "resuming_original_application_units"')

    assert restore_candidate < validate_candidate < stop_services
    assert stop_services < preserve_old < promote_candidate < post_swap_probe < api_probe < drop_old < resume
    assert 'dropdb --if-exists "$DB_NAME"' not in restore
    assert "rollback_database_swap" in restore
    assert "database_swap_rolled_back=1" in restore
    assert "RESTORE_SUCCEEDED=1" in restore
    common = _text(BACKUP_DIR / "backup_ssh_common.sh")
    assert 'CREATE DATABASE \\\"$candidate_db\\\" WITH OWNER postgres ALLOW_CONNECTIONS false' in common
    assert 'createdb -O postgres "$candidate_db"' not in common
    assert "--no-owner" in common
    assert "--no-privileges" in common
    assert "--no-tablespaces" in common


def test_live_restore_commits_before_resuming_writers_and_ingress() -> None:
    restore = _text(BACKUP_DIR / "mooncen_restore_latest_from_synology.sh")

    for contract in (
        "nginx.service",
        "cloudflared.service",
        "ACTIVE_DAEMONS",
        "ACTIVE_INGRESS",
        "ACTIVE_TIMERS",
        "DEFERRED_LOCK_TIMERS",
        "INTERRUPTED_ONESHOTS",
        "verify_promoted_database_contract",
        "run_internal_api_probe",
        "DATABASE_COMMITTED=1",
        "restore_committed_service_resume_failed=1",
        "systemctl start --no-block",
    ):
        assert contract in restore

    swap = restore.index('echo "swapping_database_candidate=')
    direct_probe = restore.index('echo "verifying_promoted_database"', swap)
    conditional_api_probe = restore.index('if [ "$API_WAS_ACTIVE" = "1" ]', direct_probe)
    refence = restore.index("fence_promoted_database_for_probe", conditional_api_probe)
    final_quiescence = restore.index("assert_units_quiesced", refence)
    drop_old = restore.index('dropdb --force "$RESTORE_OLD_DB"', final_quiescence)
    restore_connect = restore.index("restore_runtime_database_connect", drop_old)
    resume = restore.index('echo "resuming_original_application_units"', restore_connect)
    success = restore.index("RESTORE_SUCCEEDED=1", resume)

    assert swap < direct_probe < conditional_api_probe < refence < final_quiescence
    assert final_quiescence < drop_old < restore_connect < resume < success
    assert 'systemctl start "${ACTIVE_UNITS[@]}"' not in restore
    assert '[ "$HEALTH_VERIFIED" = "1" ] && [ "$SWAP_ACTIVE" = "1" ]' not in restore
    assert '"status"[[:space:]]*:[[:space:]]*"ready"' in restore
    deferred_resume = restore.index('echo "releasing_restore_lock_before_backup_timers"', resume)
    release_lock = restore.index("exec 9>&-", deferred_resume)
    start_deferred = restore.index('systemctl start "$unit"', release_lock)
    assert resume < deferred_resume < release_lock < start_deferred < success
    cleanup = restore.split("cleanup() {", 1)[1].split("trap cleanup EXIT", 1)[0]
    assert 'if [ "$SWAP_ACTIVE" = "1" ]; then' in cleanup
    assert 'database_exists "$RESTORE_OLD_DB"; then\n      rollback_database_swap' not in cleanup


def test_setup_requires_external_backup_trust_material() -> None:
    setup = _text(ROOT / "deploy" / "ubuntu" / "setup_project.sh")

    assert "Backup pinned known_hosts" in setup
    assert "Backup manifest signing key" in setup
    assert "Backup manifest allowed_signers" in setup
    assert '"$trust_label must already exist as a regular non-symlink file: $trust_path"' in setup
    assert '"$BACKUP_KNOWN_HOSTS_FILE|root:${BACKUP_OS_GROUP}:640|' in setup
    assert '"$BACKUP_MANIFEST_SIGNING_KEY|root:${BACKUP_OS_GROUP}:640|' in setup
    assert '"$BACKUP_MANIFEST_ALLOWED_SIGNERS|root:root:644|' in setup
    assert (
        'sudo -u "$BACKUP_OS_USER" ssh-keygen -y '
        '-f "$BACKUP_MANIFEST_SIGNING_KEY"' in setup
    )
    assert "match the single mooncen-backup allowed_signers entry" in setup


def test_backup_systemd_jobs_have_resource_and_runtime_limits() -> None:
    for name in ("mooncen-backup.service", "mooncen-backup-restore-test.service"):
        unit = _text(SYSTEMD_DIR / name)
        assert "RuntimeMaxSec=" in unit
        assert "MemoryMax=" in unit
        assert "TasksMax=" in unit


def test_backup_and_restore_are_serialized_with_root_owned_flock() -> None:
    common = _text(BACKUP_DIR / "backup_ssh_common.sh")
    setup = _text(ROOT / "deploy" / "ubuntu" / "setup_project.sh")

    assert "BACKUP_OPERATION_LOCK_FILE=/run/lock/mooncen-backup-restore.lock" in common
    assert 'exec 9<>"$BACKUP_OPERATION_LOCK_FILE"' in common
    assert "flock -n 9" in common
    assert '"$BACKUP_OPERATION_LOCK_FILE" root mooncen-backup 660' in common
    for name in (
        "mooncen_backup_to_synology.sh",
        "mooncen_restore_test_from_synology.sh",
        "mooncen_restore_latest_from_synology.sh",
    ):
        assert "backup_acquire_operation_lock" in _text(BACKUP_DIR / name)
    assert "f /run/lock/mooncen-backup-restore.lock 0660 root ${BACKUP_OS_GROUP}" in setup


def test_restore_workdirs_are_atomic_and_override_contract_is_strict() -> None:
    restore_test = _text(BACKUP_DIR / "mooncen_restore_test_from_synology.sh")
    restore_live = _text(BACKUP_DIR / "mooncen_restore_latest_from_synology.sh")

    assert "mktemp -d /tmp/mooncen-restore-test.XXXXXX" in restore_test
    assert "mktemp -d /tmp/mooncen-restore.XXXXXX" in restore_live
    for restore in (restore_test, restore_live):
        assert "mkdir -p \"$WORK_DIR\"" not in restore
        assert "[ -L \"$WORK_DIR\" ]" in restore
        assert '!= "0:700"' in restore
        assert '-mindepth 1 -maxdepth 1 -print -quit' in restore


def test_manifest_context_binds_server_database_size_and_freshness() -> None:
    common = _text(BACKUP_DIR / "backup_ssh_common.sh")
    backup = _text(BACKUP_DIR / "mooncen_backup_to_synology.sh")

    for field in ("server", "db_name", "db_size_bytes", "timestamp"):
        assert f"{field}=" in backup
    for parsed_field in (
        "manifest_servers",
        "manifest_databases",
        "manifest_database_sizes",
        "manifest_stamps",
    ):
        assert parsed_field in common
    assert '"${expected_db}_${stamp}.dump.age"' in common
    assert "BACKUP_VERIFIED_DB_SIZE_BYTES" in common
    assert "BACKUP_MAX_SOURCE_DB_BYTES" in common
    assert "BACKUP_MAX_AGE_SECONDS" in common
    assert "DB_SIZE_AFTER_BYTES" in backup


def test_trust_files_reject_extra_wildcard_and_hashed_entries() -> None:
    common = _text(BACKUP_DIR / "backup_ssh_common.sh")
    setup = _text(ROOT / "deploy" / "ubuntu" / "setup_project.sh")

    assert 'NF != 3 || $1 != "mooncen-backup" || $2 != "ssh-ed25519"' in common
    assert "count == 1 && invalid != 1" in common
    assert '$1 == expected_host && $2 == "ssh-ed25519"' in setup
    assert "Backup age identity does not match BACKUP_AGE_RECIPIENT" in setup
    assert "age-keygen -y" in setup


def test_live_and_scheduled_restore_use_one_acl_and_owner_pipeline() -> None:
    common = _text(BACKUP_DIR / "backup_ssh_common.sh")
    restore_test = _text(BACKUP_DIR / "mooncen_restore_test_from_synology.sh")
    restore_live = _text(BACKUP_DIR / "mooncen_restore_latest_from_synology.sh")

    for restore in (restore_test, restore_live):
        assert "backup_restore_database_candidate" in restore
        assert "sudo -n -u postgres pg_restore" not in restore
    for option in (
        "--no-owner",
        "--no-privileges",
        "--no-tablespaces",
        "--single-transaction",
        "--exit-on-error",
    ):
        assert option in common
    assert "backup_converge_database_ownership" in common
    assert "ALTER ROUTINE" in common
    assert "ALTER DOMAIN" in common
    assert "ALTER LARGE OBJECT" in common
    assert "Restored database runtime role contract probe failed" in common


def test_backup_generation_and_downloads_have_hard_file_caps() -> None:
    common = _text(BACKUP_DIR / "backup_ssh_common.sh")
    backup = _text(BACKUP_DIR / "mooncen_backup_to_synology.sh")

    assert 'ulimit -f "$max_blocks" || exit 65' in common
    assert "exec age -d" in common
    assert "Bounded backup download failed" in common
    assert "DB_SIZE_AFTER_BYTES" in backup
    assert "BACKUP_MAX_APP_ARCHIVE_BYTES" in backup
    assert "BACKUP_MAX_CONFIG_ARCHIVE_BYTES" in backup
    assert "ulimit -f" in backup
    assert "|| exit 65" in backup


@pytest.mark.skipif(os.name != "posix", reason="Linux ulimit semantics are deployment-specific")
def test_bounded_fetch_removes_partial_file_when_writer_exceeds_stat(tmp_path: Path) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not installed")
    common = BACKUP_DIR / "backup_ssh_common.sh"
    output = tmp_path / "bounded.bin"
    script = f'''
set -euo pipefail
source "{common}"
backup_remote_regular_file_size() {{ printf '1024\\n'; }}
backup_scp_from_remote() {{ dd if=/dev/zero of="$2" bs=1024 count=3 status=none; }}
if (backup_fetch_remote_bounded /remote/file "{output}" 4096); then
  exit 90
fi
test ! -e "{output}"
'''
    subprocess.run([bash, "-c", script], check=True, capture_output=True)


def test_backup_runs_as_dedicated_nologin_account() -> None:
    setup = _text(ROOT / "deploy" / "ubuntu" / "setup_project.sh")
    unit = _text(SYSTEMD_DIR / "mooncen-backup.service")

    assert 'BACKUP_OS_USER="${BACKUP_OS_USER:-mooncen-backup}"' in setup
    assert "BACKUP_OS_USER must be the dedicated mooncen-backup service account" in setup
    assert "User=mooncen-backup" in unit
    assert "Group=mooncen-backup" in unit
    assert "BACKUP_IDENTITY_FILE=/etc/mooncen/backup-ssh-key" in unit
    helper = _text(ROOT / "deploy" / "ubuntu" / "ops_service_helper.sh")
    control = _text(ROOT / "deploy" / "ubuntu" / "mooncenctl.sh")
    sudoers = _text(ROOT / "deploy" / "ubuntu" / "install_sudoers.sh")
    assert "runuser -u mooncen-backup" in helper
    assert "run_role_ops backup-list" in control
    assert "${OPS_HELPER} backup-list" in sudoers


def test_live_swap_quiesces_every_installed_mooncen_unit_and_checks_pid() -> None:
    restore = _text(BACKUP_DIR / "mooncen_restore_latest_from_synology.sh")
    unit_names = {
        path.name
        for systemd_root in (
            ROOT / "deploy" / "ubuntu" / "systemd",
            ROOT / "deploy" / "ha" / "systemd",
        )
        for path in systemd_root.glob("mooncen-*")
        if path.suffix in {".service", ".timer"}
    }
    assert unit_names
    assert unit_names <= {line.strip() for line in restore.splitlines()}
    assert "systemctl show --property=ActiveState --value" in restore
    assert "systemctl show --property=MainPID --value" in restore
    assert 'unit_active_state" != "inactive"' in restore
    assert 'unit_main_pid" != "0"' in restore
    assert "mooncen-crawler-browser-smoke.service" in restore
    assert "mooncen-deploy-guard@.service" in restore


def test_live_restore_serializes_against_durable_deployment_recovery() -> None:
    restore = _text(BACKUP_DIR / "mooncen_restore_latest_from_synology.sh")

    assert "assert_no_active_deployment_guard" in restore
    assert "'mooncen-deploy-guard@*.service'" in restore
    assert "^mooncen-deploy-guard@[0-9a-f]{32}\\.service$" in restore
    assert 'guard_active_state" != "inactive"' in restore
    assert 'guard_main_pid" != "0"' in restore
    assert "DEPLOYMENT_LOCK_DIR=/opt/.mooncen-deploy.lock" in restore
    assert 'mkdir -- "$DEPLOYMENT_LOCK_DIR"' in restore
    assert 'RESTORE_DEPLOYMENT_OWNER="live-restore:$$"' in restore
    assert "release_restore_deployment_exclusion" in restore
    assert 'if [ "$unit" = "mooncen-deploy-guard@.service" ]' in restore
    assert restore.index("if ! acquire_restore_deployment_exclusion") < restore.index(
        'echo "stopping_application_services"'
    )


def test_fixed_backup_environment_is_validated_before_scripts_read_configuration() -> None:
    common = _text(BACKUP_DIR / "backup_ssh_common.sh")
    assert "readonly BACKUP_ENV_FILE=/etc/mooncen/backup.env" in common
    assert common.index("BACKUP_RUNTIME_ENV_LOADED=0") < common.index(
        "backup_load_runtime_environment()"
    )
    assert '"$BACKUP_ENV_FILE" root mooncen-backup 640' in common
    assert "backup_load_runtime_environment()" in common

    for name in (
        "mooncen_backup_to_synology.sh",
        "mooncen_backup_list.sh",
        "mooncen_restore_test_from_synology.sh",
        "mooncen_restore_latest_from_synology.sh",
    ):
        script = _text(BACKUP_DIR / name)
        source_at = script.index('. "$SCRIPT_DIR/backup_ssh_common.sh"')
        load_at = script.index("backup_load_runtime_environment")
        prepare_at = script.index("backup_prepare_ssh")
        app_config_at = script.index('APP_DIR="${APP_DIR:-/opt/mooncen}"')
        assert source_at < load_at < app_config_at < prepare_at
        assert "ENV_FILE=" not in script
        assert 'BACKUP_ENV_FILE="${BACKUP_ENV_FILE' not in script

    for name in ("mooncen-backup.service", "mooncen-backup-restore-test.service"):
        assert "EnvironmentFile=" not in _text(SYSTEMD_DIR / name)


def test_runtime_secret_and_trust_file_contracts_are_exact() -> None:
    common = _text(BACKUP_DIR / "backup_ssh_common.sh")
    for contract in (
        '"$BACKUP_ENV_FILE" root mooncen-backup 640',
        '"$age_identity_file" root root 600',
        '"$BACKUP_IDENTITY_FILE" root mooncen-backup 640',
        '"$BACKUP_MANIFEST_SIGNING_KEY" root mooncen-backup 640',
        '"$BACKUP_KNOWN_HOSTS_FILE" root mooncen-backup 640',
        '"$BACKUP_MANIFEST_ALLOWED_SIGNERS" root root 644',
        '"$BACKUP_OPERATION_LOCK_FILE" root mooncen-backup 660',
    ):
        assert contract in common
    assert "backup_validate_secure_local_file" not in common


def test_setup_converges_dedicated_backup_gid_and_port_bound_host_token() -> None:
    setup = _text(ROOT / "deploy" / "ubuntu" / "setup_project.sh")

    assert 'sudo usermod --gid "$service_user" --groups "$APP_GROUP" "$service_user"' in setup
    assert 'BACKUP_OS_GROUP="${BACKUP_OS_GROUP:-mooncen-backup}"' in setup
    assert 'BACKUP_OS_GROUP" != "mooncen-backup"' in setup
    assert '$(id -gn "$BACKUP_OS_USER")" != "mooncen-backup"' in setup
    assert 'backup_known_host_token="[wtr-nas]:${BACKUP_PORT}"' in setup
    assert "BACKUP_PORT must be empty or an integer from 1 to 65535" in setup
    assert "BACKUP_PORT=${BACKUP_PORT}" in setup
    assert "printf 'BACKUP_PORT=%s\\n' \"$BACKUP_PORT\"" in setup


def test_latest_restore_selects_only_signature_committed_database_dump() -> None:
    common = _text(BACKUP_DIR / "backup_ssh_common.sh")
    restore_test = _text(BACKUP_DIR / "mooncen_restore_test_from_synology.sh")
    restore_live = _text(BACKUP_DIR / "mooncen_restore_latest_from_synology.sh")

    assert "backup_select_latest_committed_dump()" in common
    assert "manifest_*.txt.sig" in common
    assert "committed_signatures" in common
    assert r'[ -f \"\$manifest\" ] && [ ! -L \"\$manifest\" ]' in common
    assert r'[ -f \"\$dump\" ] && [ ! -L \"\$dump\" ]' in common
    assert 'manifest_path="${signature_path%.sig}"' in common
    assert 'dump_path="$remote_dir/db/${expected_db}_${stamp}.dump.age"' in common
    assert 'backup_remote_regular_file_size "$signature_path"' in common
    assert 'backup_remote_regular_file_size "$manifest_path"' in common
    assert 'backup_remote_regular_file_size "$dump_path"' in common
    for restore in (restore_test, restore_live):
        assert 'backup_select_latest_committed_dump "$REMOTE_DIR" "$DB_NAME"' in restore


def test_latest_selector_skips_newer_commit_for_another_or_incomplete_database() -> None:
    bash = shutil.which("bash")
    if bash is None and os.name == "nt":
        git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        if git_bash.is_file():
            bash = str(git_bash)
    if bash is None:
        pytest.skip("bash is not installed")
    common = (BACKUP_DIR / "backup_ssh_common.sh").as_posix()
    script = f'''
set -euo pipefail
source "{common}"
root="$(mktemp -d /tmp/mooncen-latest-test.XXXXXX)"
trap 'rm -rf "$root"' EXIT
remote_dir="$root/server"
mkdir -p "$remote_dir/manifests" "$remote_dir/db"
touch "$remote_dir/manifests/manifest_20260710_120000.txt"
touch "$remote_dir/manifests/manifest_20260710_120000.txt.sig"
touch "$remote_dir/db/mooncen_20260710_120000.dump.age"
touch "$remote_dir/manifests/manifest_20260710_130000.txt"
touch "$remote_dir/manifests/manifest_20260710_130000.txt.sig"
touch "$remote_dir/db/other_20260710_130000.dump.age"
mock_ssh() {{ local target="$1"; shift; bash -c "$1"; }}
SSH_CMD=mock_ssh
BACKUP_USER=test
BACKUP_HOST=test
selected="$(backup_select_latest_committed_dump "$remote_dir" mooncen)"
test "$selected" = "$remote_dir/db/mooncen_20260710_120000.dump.age"
'''
    subprocess.run([bash, "-c", script], check=True, capture_output=True)


def test_encryption_uses_immutable_non_age_input_snapshot() -> None:
    backup = _text(BACKUP_DIR / "mooncen_backup_to_synology.sh")

    snapshot = backup.index("mapfile -d '' -t encryption_inputs")
    loop = backup.index('for file in "${encryption_inputs[@]}"')
    assert snapshot < loop
    assert "-type f ! -name '*.age' -print0" in backup
    assert "Backup encryption input snapshot is empty" in backup
    assert "done < <(find" not in backup


def test_owner_convergence_includes_only_standalone_composite_rowtypes() -> None:
    common = _text(BACKUP_DIR / "backup_ssh_common.sh")

    assert "composite_class.oid = t.typrelid" in common
    assert "composite_class.relkind = 'c'" in common
    assert "c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')" in common
    assert "c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f', 'c')" not in common
    assert "sequence_dependency.objid = c.oid" in common
    assert "sequence_dependency.refclassid = 'pg_class'::regclass" in common
    assert "sequence_dependency.deptype IN ('a', 'i')" in common


def test_restore_streams_protected_role_contract_through_a_privileged_reader() -> None:
    common = _text(BACKUP_DIR / "backup_ssh_common.sh")

    assert 'sudo -n cat "$app_dir/DB/roles.sql" |' in common
    assert 'sudo -n -u postgres psql -v ON_ERROR_STOP=1 -d "$candidate_db"' in common
    assert '-f "$app_dir/DB/roles.sql"' not in common


def test_wtr_backup_wrapper_does_not_require_nested_script_execute_bit() -> None:
    wrapper = _text(BACKUP_DIR / "mooncen_backup_to_wtr_nas.sh")

    assert 'exec /bin/bash "$SCRIPT_DIR/mooncen_backup_to_synology.sh" "$@"' in wrapper


def test_application_archive_excludes_runtime_only_root_artifacts() -> None:
    backup = _text(BACKUP_DIR / "mooncen_backup_to_synology.sh")

    assert '--exclude="$APP_BASENAME/.mooncen-prebuilt-release"' in backup
    assert '--exclude="$APP_BASENAME/__pycache__"' in backup
    assert '--exclude="$APP_BASENAME/**/__pycache__"' in backup


def test_application_archive_exclusions_avoid_unreadable_runtime_artifacts(
    tmp_path: Path,
) -> None:
    tar = shutil.which("tar")
    if tar is None:
        pytest.skip("tar is not installed")

    backup = _text(BACKUP_DIR / "mooncen_backup_to_synology.sh")
    archive_block = backup.split("  exec tar \\\n", 1)[1].split(
        '  -czf "$WORK_DIR/app/mooncen_app_${STAMP}.tar.gz"', 1
    )[0]
    app_dir = tmp_path / "mooncen"
    backend_dir = app_dir / "backend"
    root_cache = app_dir / "__pycache__"
    nested_cache = backend_dir / "__pycache__"
    backend_dir.mkdir(parents=True)
    root_cache.mkdir()
    nested_cache.mkdir()
    (backend_dir / "main.py").write_text("ready = True\n", encoding="utf-8")
    marker = app_dir / ".mooncen-prebuilt-release"
    marker.write_text("PREBUILD_VERSION=1\n", encoding="utf-8")
    deploy_info = app_dir / ".deploy-info"
    deploy_info.write_text("DEPLOY_COMMIT=" + "a" * 40 + "\n", encoding="utf-8")
    (root_cache / "root.cpython-312.pyc").write_bytes(b"root-cache")
    (nested_cache / "main.cpython-312.pyc").write_bytes(b"nested-cache")

    exclude_patterns: list[str] = []
    for line in archive_block.splitlines():
        token = line.strip().removesuffix("\\").strip()
        if not token.startswith('--exclude="'):
            continue
        assert token.endswith('"')
        pattern = token.removeprefix('--exclude="').removesuffix('"')
        exclude_patterns.append(pattern.replace("$APP_BASENAME", app_dir.name))

    archive = tmp_path / "app.tar.gz"
    marker.chmod(0)
    root_cache.chmod(0)
    nested_cache.chmod(0)
    try:
        packed = subprocess.run(
            [
                tar,
                *(f"--exclude={pattern}" for pattern in exclude_patterns),
                "-czf",
                str(archive),
                "-C",
                str(tmp_path),
                app_dir.name,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        marker.chmod(0o600)
        root_cache.chmod(0o700)
        nested_cache.chmod(0o700)

    assert packed.returncode == 0, packed.stderr
    members = set(
        subprocess.run(
            [tar, "-tzf", str(archive)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    )
    assert f"{app_dir.name}/backend/main.py" in members
    assert f"{app_dir.name}/.deploy-info" in members
    for excluded in (
        f"{app_dir.name}/.mooncen-prebuilt-release",
        f"{app_dir.name}/__pycache__",
        f"{app_dir.name}/backend/__pycache__",
    ):
        assert all(member != excluded and not member.startswith(f"{excluded}/") for member in members)
