from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "deploy" / "ubuntu" / "mooncen_release_guard.sh"
DEPLOY = ROOT / "deploy" / "ubuntu" / "deploy_from_windows.ps1"
TOKEN = "a" * 32
COMMIT = "b" * 40


def _bash() -> str | None:
    candidates = (
        shutil.which("bash"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    )
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    return None


def _function(source: str, name: str, next_name: str) -> str:
    return source.split(f"{name}() {{", 1)[1].split(f"{next_name}() {{", 1)[0]


def _powershell_heredoc(source: str, variable: str) -> str:
    return source.split(f"${variable} = @'", 1)[1].split("\n'@", 1)[0]


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")
    path.chmod(0o755)


def _build_harness(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    if os.name != "posix":
        pytest.skip("release guard state-machine harness requires POSIX ownership semantics")

    import grp
    import pwd

    fake_root = tmp_path / "fake-root"
    fake_root.mkdir(mode=0o700)
    fake_root_text = fake_root.as_posix()
    current_user = pwd.getpwuid(os.getuid()).pw_name
    current_group = grp.getgrgid(os.getgid()).gr_name

    source = GUARD.read_text(encoding="utf-8")
    source = source.split('command="${1:-}"', 1)[0]
    for root_uid_check in (
        '[ "$(stat -c \'%u\' /etc/systemd/system)" = 0 ]',
        '[ "$(stat -c \'%u\' "$unit_target")" = 0 ]',
        '[ "$(stat -c \'%u\' "$unit_stage")" = 0 ]',
        '[ "$(stat -c \'%u\' "$stage")" = 0 ]',
    ):
        assert root_uid_check in source
        source = source.replace(root_uid_check, root_uid_check.replace("= 0 ]", f"= {os.getuid()} ]"))
    for original in ("/usr/local", "/var/log", "/opt", "/etc"):
        source = source.replace(original, f"{fake_root_text}{original}")
    source = source.replace("root:root", f"{current_user}:{current_group}")
    source = source.replace("postgres:postgres", f"{current_user}:{current_group}")
    source = source.replace("-o root -g root", "")
    source = source.replace(
        'install -o "$deploy_user" -g "$(id -gn "$deploy_user")" -m 0600',
        "install -m 0600",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    systemctl_state = tmp_path / "systemctl-state"
    systemctl_state.mkdir()
    _write_executable(fake_bin / "chown", "#!/bin/sh\nexit 0\n")
    _write_executable(
        fake_bin / "systemctl",
        """#!/bin/sh
set -eu
state=${MOONCEN_TEST_SYSTEMCTL_STATE:?}
unit=
for argument in "$@"; do
  unit=$argument
done
case "${1:-}" in
  cat) exit 1 ;;
  enable)
    : > "$state/enabled-$unit"
    ;;
  disable)
    rm -f -- "$state/enabled-$unit"
    ;;
  is-enabled)
    test -f "$state/enabled-$unit"
    ;;
  start)
    : > "$state/active-$unit"
    ;;
  stop)
    rm -f -- "$state/active-$unit"
    ;;
  is-active)
    case "$unit" in
      mooncen-deploy-guard@*)
        if [ -n "${MOONCEN_TEST_OPERATION_LOCK:-}" ]; then
          flock -n "$MOONCEN_TEST_OPERATION_LOCK" true || exit 1
        fi
        ;;
    esac
    test -f "$state/active-$unit"
    ;;
  show)
    case "$*" in
      *MainPID*) printf '0\n' ;;
      *mooncen-deploy-guard@*ActiveState*) printf 'active\n' ;;
      *ActiveState*) printf 'inactive\n' ;;
      *) printf '\n' ;;
    esac
    ;;
  *) exit 0 ;;
esac
""",
    )

    harness = tmp_path / "guard-harness.sh"
    harness.write_text(
        source
        + f"""
TEST_ROOT={fake_root_text!r}
TEST_TOKEN={TOKEN!r}
TEST_COMMIT={COMMIT!r}
TEST_USER={current_user!r}
NATIVE_START_AUTH_DIR="$TEST_ROOT/run/mooncen-native-deploy-start"
NATIVE_START_AUTH_PATH="$NATIVE_START_AUTH_DIR/authorization.json"

build_mutable_artifact_inventory() {{
  MUTABLE_ARTIFACT_IDS=(fixture-env)
  MUTABLE_ARTIFACT_PATHS=("$TEST_ROOT/etc/mooncen/api.env")
  MUTABLE_ARTIFACT_POLICIES=(file)
}}

validate_mutable_source() {{
  local path="$1"
  local policy="$2"
  if [ ! -e "$path" ] && [ ! -L "$path" ]; then
    return 0
  fi
  [ "$policy" = file ] && [ -f "$path" ] && [ ! -L "$path" ] ||
    die "fixture mutable path is unsafe"
}}

load_journal() {{
  local lock_dir="$1"
  local expected_token="$2"
  JOURNAL="$lock_dir/journal.env"
  [ -f "$JOURNAL" ] && [ ! -L "$JOURNAL" ] || die "fixture journal is unsafe"
  TOKEN="$(journal_value "$JOURNAL" TOKEN)"
  PHASE="$(journal_value "$JOURNAL" PHASE)"
  REMOTE_DIR="$(journal_value "$JOURNAL" REMOTE_DIR)"
  RELEASE_DIR="$(journal_value "$JOURNAL" RELEASE_DIR)"
  PREVIOUS_DIR="$(journal_value "$JOURNAL" PREVIOUS_DIR)"
  FAILED_DIR="$(journal_value "$JOURNAL" FAILED_DIR)"
  HEARTBEAT="$(journal_value "$JOURNAL" HEARTBEAT)"
  EXPECTED_COMMIT="$(journal_value "$JOURNAL" EXPECTED_COMMIT)"
  HAD_ACTIVE="$(journal_value "$JOURNAL" HAD_ACTIVE)"
  ARM_BOOT_ID="$(journal_value "$JOURNAL" ARM_BOOT_ID)"
  DEADLINE_EPOCH="$(journal_value "$JOURNAL" DEADLINE_EPOCH)"
  NATIVE_INTENT_TOKEN="$(journal_value "$JOURNAL" NATIVE_INTENT_TOKEN)"
  [ "$TOKEN" = "$expected_token" ] || die "fixture journal token mismatch"
}}

current_boot_id() {{
  printf '11111111-1111-4111-8111-111111111111\n'
}}

restore_systemd_configuration() {{
  local lock_dir="$1"
  [ -d "$lock_dir/systemd-units" ] &&
    [ -f "$lock_dir/systemd-unit-names" ] &&
    [ -f "$lock_dir/systemd-enabled-units" ] ||
    die "fixture systemd backup was consumed"
  printf 'restore\n' >> "$TEST_ROOT/systemd-restore-count"
}}

stop_managed_units() {{ :; }}
restore_active_units() {{
  [ -f "$NATIVE_START_AUTH_PATH" ] || die "fixture native start authorization is missing"
  cp -- "$NATIVE_START_AUTH_PATH" "$TEST_ROOT/observed-native-start-authorization.json"
  printf 'restore\n' >> "$TEST_ROOT/active-unit-restore-count"
}}

restore_mutable_artifacts() {{
  local lock_dir="$1"
  validate_mutable_artifact_backup "$lock_dir"
  if [ -f "$TEST_ROOT/fail-restore-once" ]; then
    rm -f -- "$TEST_ROOT/fail-restore-once"
    die "injected host restore failure"
  fi
}}

eval "$(declare -f cleanup_mutable_artifact_backup |
  sed '1s/^cleanup_mutable_artifact_backup/original_cleanup_mutable_artifact_backup/')"
cleanup_mutable_artifact_backup() {{
  if [ -f "$TEST_ROOT/fail-finish-once" ]; then
    rm -f -- "$TEST_ROOT/fail-finish-once"
    die "injected finalization failure"
  fi
  original_cleanup_mutable_artifact_backup "$@"
}}

make_candidate_marker() {{
  local directory="$1"
  install -d -m 0700 "$directory"
  printf 'PREBUILD_VERSION=1\nDEPLOY_COMMIT=%s\n' "$TEST_COMMIT" \
    > "$directory/.mooncen-prebuilt-release"
  chmod 0600 "$directory/.mooncen-prebuilt-release"
}}

make_active_units_journal() {{
  install -m 0600 /dev/null "$LOCK_DIR_EXPECTED/active-units"
}}

initialize_fixture() {{
  local scenario="$1"
  local remote_dir="$TEST_ROOT/opt/mooncen"
  local release_dir="$TEST_ROOT/opt/.mooncen-release-$TEST_TOKEN"
  local previous_dir="$TEST_ROOT/opt/.mooncen-previous-$TEST_TOKEN"
  local failed_dir="$TEST_ROOT/opt/.mooncen-failed-$TEST_TOKEN"

  install -d -m 0700 "$TEST_ROOT/opt"
  install -d -m 0700 "$TEST_ROOT/usr/local" "$TEST_ROOT/var/log"
  install -d -m 0755 "$TEST_ROOT/etc/systemd/system"
  install -d -m 0751 "$TEST_ROOT/etc/mooncen"
  printf 'old-host-value\n' > "$TEST_ROOT/etc/mooncen/api.env"
  chmod 0600 "$TEST_ROOT/etc/mooncen/api.env"
  install -d -m 0700 "$release_dir"
  install -d -m 0700 "$release_dir/deploy" "$release_dir/deploy/ubuntu"
  install -d -m 0700 "$release_dir/deploy/ubuntu/systemd"
  printf '[Unit]\nDescription=fixture deployment guard\n' \
    > "$release_dir/deploy/ubuntu/systemd/mooncen-deploy-guard@.service"
  chmod 0644 "$release_dir/deploy/ubuntu/systemd/mooncen-deploy-guard@.service"
  if [ "$scenario" != first-post-swap ]; then
    install -d -m 0700 "$remote_dir"
    printf 'old-release\n' > "$remote_dir/old.txt"
  fi

  install -d -m 0700 "$LOCK_DIR_EXPECTED"
  printf '%s\n' "$TEST_TOKEN" > "$LOCK_DIR_EXPECTED/token"
  chmod 0600 "$LOCK_DIR_EXPECTED/token"
  bootstrap_guard "$LOCK_DIR_EXPECTED" "$TEST_TOKEN" "$release_dir" "$TEST_USER" 900 "$TEST_TOKEN"
  arm_guard "$LOCK_DIR_EXPECTED" "$TEST_TOKEN" "$remote_dir" "$release_dir" \
    "$previous_dir" "$failed_dir" "$TEST_COMMIT" "$TEST_USER" 300
  if [ "$scenario" != prepared-partial ]; then
    make_candidate_marker "$release_dir"
    make_active_units_journal
  fi

  case "$scenario" in
    prepared-partial)
      printf 'partially extracted candidate\n' > "$release_dir/PARTIAL"
      ;;
    first-post-swap)
      set_phase "$LOCK_DIR_EXPECTED" "$TEST_TOKEN" activating
      mv -T -- "$release_dir" "$remote_dir"
      set_phase "$LOCK_DIR_EXPECTED" "$TEST_TOKEN" activated
      ;;
    existing-mid-swap)
      set_phase "$LOCK_DIR_EXPECTED" "$TEST_TOKEN" activating
      mv -T -- "$remote_dir" "$previous_dir"
      ;;
    failed-already-present)
      set_phase "$LOCK_DIR_EXPECTED" "$TEST_TOKEN" activating
      mv -T -- "$remote_dir" "$previous_dir"
      mv -T -- "$release_dir" "$remote_dir"
      set_phase "$LOCK_DIR_EXPECTED" "$TEST_TOKEN" activated
      set_phase "$LOCK_DIR_EXPECTED" "$TEST_TOKEN" recovering
      mv -T -- "$remote_dir" "$failed_dir"
      ;;
    commit-retry)
      set_phase "$LOCK_DIR_EXPECTED" "$TEST_TOKEN" activating
      mv -T -- "$remote_dir" "$previous_dir"
      mv -T -- "$release_dir" "$remote_dir"
      set_phase "$LOCK_DIR_EXPECTED" "$TEST_TOKEN" activated
      printf 'DEPLOY_COMMIT=%s\n' "$TEST_COMMIT" > "$remote_dir/.deploy-info"
      chmod 0600 "$remote_dir/.deploy-info"
      set_phase "$LOCK_DIR_EXPECTED" "$TEST_TOKEN" verified
      ;;
    *) die "unknown fixture scenario" ;;
  esac
}}

action="${{1:-}}"
case "$action" in
  init) initialize_fixture "$2" ;;
  recover) recover_release "$LOCK_DIR_EXPECTED" "$TEST_TOKEN" ;;
  boot-recover) boot_recover_guard "$LOCK_DIR_EXPECTED" "$TEST_TOKEN" ;;
  commit) commit_release "$LOCK_DIR_EXPECTED" "$TEST_TOKEN" ;;
  authorize-candidate)
    publish_native_start_authorization "$LOCK_DIR_EXPECTED" "$TEST_TOKEN" candidate
    ;;
  revoke-start)
    clear_native_start_authorization "$TEST_TOKEN"
    ;;
  cleanup-twice)
    manifest="$LOCK_DIR_EXPECTED/$MUTABLE_ARTIFACT_MANIFEST_NAME"
    backup="$LOCK_DIR_EXPECTED/$MUTABLE_ARTIFACT_BACKUP_NAME"
    mv -- "$manifest" "$backup/$MUTABLE_ARTIFACT_MANIFEST_NAME"
    cleanup_mutable_artifact_backup "$LOCK_DIR_EXPECTED"
    cleanup_mutable_artifact_backup "$LOCK_DIR_EXPECTED"
    [ ! -e "$manifest" ] && [ ! -e "$backup" ] || die "fixture secret cleanup did not converge"
    ;;
  *) die "unknown fixture action" ;;
esac
""",
        encoding="utf-8",
        newline="\n",
    )
    harness.chmod(0o700)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["MOONCEN_TEST_SYSTEMCTL_STATE"] = str(systemctl_state)
    env["MOONCEN_TEST_OPERATION_LOCK"] = str(
        fake_root / "opt" / ".mooncen-deploy.lock" / "operation.lock"
    )
    return harness, fake_root, env


def _build_absent_parent_harness(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    if os.name != "posix":
        pytest.skip("mutable restore harness requires POSIX ownership semantics")

    import grp
    import pwd

    fake_root = tmp_path / "absent-parent-root"
    fake_root.mkdir(mode=0o700)
    fake_root_text = fake_root.as_posix()
    current_user = pwd.getpwuid(os.getuid()).pw_name
    current_group = grp.getgrgid(os.getgid()).gr_name

    source = GUARD.read_text(encoding="utf-8").split('command="${1:-}"', 1)[0]
    for original in ("/usr/local", "/var/log", "/opt", "/etc"):
        source = source.replace(original, f"{fake_root_text}{original}")
    source = source.replace("root:root", f"{current_user}:{current_group}")
    source = source.replace("postgres:postgres", f"{current_user}:{current_group}")
    source = source.replace("-o root -g root", "")

    fake_bin = tmp_path / "absent-parent-bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "chown", "#!/bin/sh\nexit 0\n")
    _write_executable(fake_bin / "systemctl", "#!/bin/sh\nexit 1\n")

    harness = tmp_path / "absent-parent-harness.sh"
    harness.write_text(
        source
        + f"""
TEST_ROOT={fake_root_text!r}
TEST_TOKEN={TOKEN!r}

build_mutable_artifact_inventory() {{
  MUTABLE_ARTIFACT_IDS=(
    fixture-container-api fixture-container-ai fixture-container-migrator
    fixture-container-runtime fixture-hba fixture-child fixture-parent
  )
  MUTABLE_ARTIFACT_PATHS=(
    "$TEST_ROOT/etc/mooncen/container-api.env"
    "$TEST_ROOT/etc/mooncen/container-ai.env"
    "$TEST_ROOT/etc/mooncen/container-migrator.env"
    "$TEST_ROOT/etc/mooncen/container-frontend-runtime-config.js"
    "$TEST_ROOT/etc/postgresql/16/main/pg_hba.conf"
    "$TEST_ROOT/etc/mooncen-absent/api.env"
    "$TEST_ROOT/etc/mooncen-absent"
  )
  MUTABLE_ARTIFACT_POLICIES=(file file file file file-postgres file metadata-any)
}}

validate_mutable_source() {{
  local path="$1"
  local policy="$2"
  if [ ! -e "$path" ] && [ ! -L "$path" ]; then
    return 0
  fi
  case "$policy" in
    file|file-postgres) [ -f "$path" ] && [ ! -L "$path" ] ;;
    metadata-any) [ -d "$path" ] && [ ! -L "$path" ] ;;
    *) return 1 ;;
  esac || die "fixture mutable source is unsafe"
}}

reload_restored_postgresql_hba() {{
  printf 'reload\n' >> "$TEST_ROOT/hba-reload-count"
}}

install -d -m 0700 \
  "$TEST_ROOT/opt" "$TEST_ROOT/etc" "$TEST_ROOT/usr/local" "$TEST_ROOT/var/log"
install -d -m 0755 "$TEST_ROOT/etc/mooncen"
install -d -m 0755 "$TEST_ROOT/etc/postgresql/16/main"
printf 'native-before-container-bootstrap:hba\n' \
  > "$TEST_ROOT/etc/postgresql/16/main/pg_hba.conf"
chmod 0640 "$TEST_ROOT/etc/postgresql/16/main/pg_hba.conf"
for fixture in \
  container-api.env container-ai.env container-migrator.env \
  container-frontend-runtime-config.js; do
  printf 'native-before-container-bootstrap:%s\n' "$fixture" \
    > "$TEST_ROOT/etc/mooncen/$fixture"
  chmod 0600 "$TEST_ROOT/etc/mooncen/$fixture"
done
chmod 0644 "$TEST_ROOT/etc/mooncen/container-frontend-runtime-config.js"
install -d -m 0700 "$LOCK_DIR_EXPECTED"
printf '%s\n' "$TEST_TOKEN" > "$LOCK_DIR_EXPECTED/token"
chmod 0600 "$LOCK_DIR_EXPECTED/token"
TOKEN="$TEST_TOKEN"

backup_mutable_artifacts "$LOCK_DIR_EXPECTED"
validate_mutable_artifact_backup "$LOCK_DIR_EXPECTED"
install -d -m 0700 "$TEST_ROOT/etc/mooncen-absent"
printf 'candidate value\n' > "$TEST_ROOT/etc/mooncen-absent/api.env"
chmod 0600 "$TEST_ROOT/etc/mooncen-absent/api.env"
printf 'candidate-mutated:hba\n' > "$TEST_ROOT/etc/postgresql/16/main/pg_hba.conf"
for fixture in \
  container-api.env container-ai.env container-migrator.env \
  container-frontend-runtime-config.js; do
  printf 'candidate-mutated:%s\n' "$fixture" > "$TEST_ROOT/etc/mooncen/$fixture"
done

restore_mutable_artifacts "$LOCK_DIR_EXPECTED"
[ ! -e "$TEST_ROOT/etc/mooncen-absent" ] || die "first restore did not remove absent parent"
grep -Fxq 'native-before-container-bootstrap:hba' \
  "$TEST_ROOT/etc/postgresql/16/main/pg_hba.conf" || die "HBA was not restored"
[ "$(stat -c '%a' "$TEST_ROOT/etc/postgresql/16/main/pg_hba.conf")" = 640 ] ||
  die "HBA mode was not restored"
for fixture in \
  container-api.env container-ai.env container-migrator.env \
  container-frontend-runtime-config.js; do
  grep -Fxq "native-before-container-bootstrap:$fixture" \
    "$TEST_ROOT/etc/mooncen/$fixture" || die "container input was not restored: $fixture"
  expected_mode=600
  [ "$fixture" = container-frontend-runtime-config.js ] && expected_mode=644
  [ "$(stat -c '%a' "$TEST_ROOT/etc/mooncen/$fixture")" = "$expected_mode" ] ||
    die "container input mode was not restored: $fixture"
done
restore_mutable_artifacts "$LOCK_DIR_EXPECTED"
[ ! -e "$TEST_ROOT/etc/mooncen-absent" ] || die "retry recreated absent parent"
[ "$(wc -l < "$TEST_ROOT/hba-reload-count")" -eq 2 ] ||
  die "restored HBA was not reloaded on each retry"
""",
        encoding="utf-8",
        newline="\n",
    )
    harness.chmod(0o700)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    return harness, fake_root, env


def _build_preflight_lock_harness(tmp_path: Path) -> tuple[Path, Path, dict[str, str], str]:
    if os.name != "posix":
        pytest.skip("preflight lock harness requires POSIX ownership and flock semantics")

    import grp
    import pwd

    boot_id_path = Path("/proc/sys/kernel/random/boot_id")
    if not boot_id_path.is_file():
        pytest.skip("kernel boot identifier is unavailable")
    current_boot_id = boot_id_path.read_text(encoding="ascii").strip()
    if not re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        current_boot_id,
    ):
        pytest.skip("kernel boot identifier is invalid")

    current_user = pwd.getpwuid(os.getuid()).pw_name
    current_group = grp.getgrgid(os.getgid()).gr_name
    fake_root = tmp_path / "preflight-root"
    (fake_root / "opt").mkdir(parents=True, mode=0o700)
    fake_root_text = fake_root.as_posix()

    deploy = DEPLOY.read_text(encoding="utf-8")
    source = _powershell_heredoc(deploy, "lockAndCleanupScript")
    controller = fake_root / "mooncen-container-release"
    source = source.replace(
        "/usr/local/libexec/mooncen-container-release", controller.as_posix()
    )
    source = source.replace("/opt", f"{fake_root_text}/opt")
    source = source.replace("__LOCK_TOKEN__", TOKEN)
    source = source.replace("__NATIVE_INTENT_TOKEN__", TOKEN)
    source = source.replace("__DEPLOY_USER__", current_user)
    source = source.replace("root:root", f"{current_user}:{current_group}")
    source = source.replace(
        '[ "$(sudo stat -c \'%u\' "$entry")" = 0 ]',
        f'[ "$(sudo stat -c \'%u\' "$entry")" = {os.getuid()} ]',
    )
    source = source.replace('-o "$deploy_user" -g "$(id -gn "$deploy_user")" ', "")
    source = source.replace("-o root -g root ", "")

    fake_bin = tmp_path / "preflight-bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "sudo",
        """#!/bin/sh
set -eu
while [ "${1:-}" = -n ] || [ "${1:-}" = -- ]; do shift; done
exec "$@"
""",
    )
    _write_executable(fake_bin / "chown", "#!/bin/sh\nexit 0\n")
    _write_executable(
        controller,
        """#!/bin/sh
set -eu
[ "$#" -eq 2 ] && [ "$1" = native-end ]
printf '%s %s\n' "$1" "$2" >> "${MOONCEN_TEST_CONTROLLER_LOG:?}"
printf '{"ended":true,"schema_version":1,"token":"%s"}\n' "$2"
""",
    )

    harness = tmp_path / "preflight-lock-harness.sh"
    _write_executable(harness, source)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["MOONCEN_TEST_CONTROLLER_LOG"] = str(tmp_path / "controller.log")
    return harness, fake_root, env, current_boot_id


def _run(harness: Path, env: dict[str, str], *arguments: str) -> subprocess.CompletedProcess[str]:
    bash = _bash()
    assert bash is not None
    return subprocess.run(
        [bash, str(harness), *arguments],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )


def _journal_phase(journal: Path) -> str:
    match = re.search(r"^PHASE=(\w+)$", journal.read_text(encoding="utf-8"), re.MULTILINE)
    assert match is not None
    return match.group(1)


def test_guard_declares_resumable_release_and_finalization_states() -> None:
    source = GUARD.read_text(encoding="utf-8")
    recovery = _function(source, "recover_release", "arm_guard")
    convergence = _function(source, "converge_failed_release_paths", "recover_release")
    commit = _function(source, "commit_release", "boot_recover_guard")
    finish = _function(source, "finish_lock", "backup_systemd_units")

    for phase in (
        "recovering",
        "committing",
        "finalizing_abort",
        "finalizing_recovery",
        "finalizing_commit",
    ):
        assert phase in source
    assert recovery.index('set_phase "$lock_dir" "$token" recovering') < recovery.index(
        "converge_failed_release_paths"
    )
    assert 'elif [ "$HAD_ACTIVE" = 1 ]' in convergence
    assert "first-deploy recovery" in convergence
    assert "failed-already-present" not in convergence
    assert 'finalizing_abort|finalizing_recovery|finalizing_commit|committed|aborted|recovered)' in recovery
    assert commit.index('set_phase "$lock_dir" "$token" committing') < commit.index("finalize_commit_release")
    assert finish.index('mv -T -- "$lock_dir" "$finalized_lock"') < finish.index('disable_guard_unit "$token"')


def test_prepared_recovery_accepts_a_partial_candidate_without_prebuild_marker() -> None:
    source = GUARD.read_text(encoding="utf-8")
    recovery = _function(source, "recover_release", "arm_guard")
    prepared = recovery.split(
        '  if [ "$PHASE" = recovering_prepared ]; then', 1
    )[1].split("\n  fi\n", 1)[0]

    assert 'set_phase "$lock_dir" "$token" recovering_prepared' in recovery
    assert 'validate_release_state_path "$RELEASE_DIR" unactivated' in prepared
    assert 'preserve_directory "$RELEASE_DIR" "$prepared_entry/unactivated"' in prepared
    assert "validate_candidate_release" not in prepared
    assert prepared.index('restore_mutable_artifacts "$lock_dir"') < prepared.index(
        'preserve_directory "$RELEASE_DIR" "$prepared_entry/unactivated"'
    )


def test_commit_preflight_finishes_before_the_durable_committing_decision() -> None:
    source = GUARD.read_text(encoding="utf-8")
    commit = _function(source, "commit_release", "boot_recover_guard")
    decision = 'set_phase "$lock_dir" "$token" committing'

    assert commit.index('[ -f "$REMOTE_DIR/.deploy-info" ]') < commit.index(decision)
    assert commit.index("preflight_commit=", commit.index('[ -f "$REMOTE_DIR/.deploy-info" ]')) < commit.index(decision)
    assert commit.index('[ "$preflight_commit" = "$EXPECTED_COMMIT" ]') < commit.index(decision)
    assert commit.index('validate_candidate_release "$REMOTE_DIR"') < commit.index(decision)
    assert commit.index('validate_candidate_release "$REMOTE_DIR"') < commit.index(
        "sync_recovery_filesystems"
    )
    assert commit.index("sync_recovery_filesystems") < commit.index(decision)


def test_mutable_restore_cleans_validated_stage_and_old_paths_before_reuse() -> None:
    source = GUARD.read_text(encoding="utf-8")
    restore = _function(source, "restore_mutable_artifacts", "cleanup_mutable_artifact_backup")
    cleanup_call = 'cleanup_mutable_restore_transients "$stage" "$old" "$policy" "$id"'

    assert "cleanup_mutable_restore_transients()" in source
    assert restore.count("cleanup_mutable_restore_transients") >= 2
    leaf_cleanup = restore.index(cleanup_call)
    assert leaf_cleanup < restore.index('[ ! -e "$stage" ]', leaf_cleanup)
    assert leaf_cleanup < restore.index('cp -a -- "$stored" "$stage"', leaf_cleanup)


def test_absent_mutable_leaf_retry_does_not_require_its_absent_parent() -> None:
    source = GUARD.read_text(encoding="utf-8")
    restore = _function(source, "restore_mutable_artifacts", "cleanup_mutable_artifact_backup")
    leaf_pass = restore.split("# Restore only fixed leaf artifacts", 1)[1].split(
        "# Metadata-only directories are deliberately last",
        1,
    )[0]

    absent_parent_retry = (
        'if [ "$state" = absent ] && [ ! -e "$parent" ] && [ ! -L "$parent" ]; then'
    )
    required_parent = '[ -d "$parent" ] && [ ! -L "$parent" ]'
    assert absent_parent_retry in leaf_pass
    assert '[ ! -e "$path" ] && [ ! -L "$path" ]' in leaf_pass
    assert leaf_pass.index(absent_parent_retry) < leaf_pass.index(required_parent)


def test_bootstrap_snapshot_precedes_watcher_enable_and_arm_reuses_the_baseline() -> None:
    source = GUARD.read_text(encoding="utf-8")
    deploy = (ROOT / "deploy" / "ubuntu" / "deploy_from_windows.ps1").read_text(encoding="utf-8")
    bootstrap = _function(source, "bootstrap_guard", "abort_bootstrap_guard")
    arm_guard = _function(source, "arm_guard", "finalize_commit_release")
    boot_recovery = _function(source, "boot_recover_guard", "watch_guard")
    arm_script = deploy.split("$armReleaseGuardScript = @'", 1)[1].split("'@", 1)[0]

    assert bootstrap.index('backup_systemd_units "$lock_dir"') < bootstrap.index(
        'mv -fT -- "$temporary" "$lock_dir/$BOOTSTRAP_MANIFEST_NAME"'
    )
    assert 'backup_systemd_units "$lock_dir"' not in arm_guard
    assert arm_guard.index('validate_systemd_configuration_backup "$lock_dir"') < arm_guard.index(
        'backup_mutable_artifacts "$lock_dir"'
    )
    assert arm_guard.index('flock -x 9') < arm_guard.index('load_bootstrap "$lock_dir" "$token"')
    assert arm_guard.index('load_bootstrap "$lock_dir" "$token"') < arm_guard.index(
        'mv -fT -- "$temporary" "$lock_dir/journal.env"'
    )

    bootstrap_call = 'sudo "$lock_dir/guard.sh" bootstrap'
    arm_call = 'sudo "$lock_dir/guard.sh" arm'
    assert arm_script.index(bootstrap_call) < arm_script.index(arm_call)
    for wrapper_mutation in (
        'sudo mv -fT -- "$unit_stage" "$unit_target"',
        'sudo systemctl enable "$guard_unit"',
        'sudo systemctl start --no-block "$guard_unit"',
    ):
        assert wrapper_mutation not in arm_script

    publish_unit = 'mv -fT -- "$unit_stage" "$unit_target"'
    enable_unit = 'systemctl enable "$guard_unit"'
    start_unit = 'systemctl start --no-block "$guard_unit"'
    publish_journal = 'mv -fT -- "$temporary" "$lock_dir/journal.env"'
    assert arm_guard.index('flock -x 9') < arm_guard.index(publish_unit)
    assert arm_guard.index(publish_unit) < arm_guard.index(enable_unit)
    assert arm_guard.index(enable_unit) < arm_guard.index(start_unit)
    assert arm_guard.index(start_unit) < arm_guard.index('backup_mutable_artifacts "$lock_dir"')
    assert arm_guard.index('backup_mutable_artifacts "$lock_dir"') < arm_guard.index(publish_journal)
    assert 'enable --now "$guard_unit"' not in arm_guard
    assert arm_script.index('"$lock_dir/journal.env"') < arm_script.index('"$lock_dir/bootstrap.env"')

    journal_preference = boot_recovery.index(
        'if [ -e "$lock_dir/journal.env" ] || [ -L "$lock_dir/journal.env" ]; then'
    )
    assert journal_preference < boot_recovery.index('load_bootstrap "$lock_dir" "$token"')


def test_boot_recovery_is_ordered_and_avoids_systemd_start_deadlock() -> None:
    source = GUARD.read_text(encoding="utf-8")
    unit = (ROOT / "deploy" / "ubuntu" / "systemd" / "mooncen-deploy-guard@.service").read_text(
        encoding="utf-8"
    )
    boot_recovery = _function(source, "boot_recover_guard", "watch_guard")
    active_restore = _function(source, "restore_active_units", "validate_release_state_path")

    assert 'ARM_BOOT_ID="$(journal_value "$JOURNAL" ARM_BOOT_ID)"' in source
    assert '[ "$boot_id" != "$ARM_BOOT_ID" ]' in boot_recovery
    assert 'MOONCEN_BOOT_RECOVERY=1 recover_release "$lock_dir" "$token"' in boot_recovery
    assert "ExecStartPre=" in unit and " boot-recover " in unit
    assert "Before=nginx.service cloudflared.service" in unit
    for unit_name in (
        "mooncen-api.service",
        "mooncen-frontend.service",
        "mooncen-crawler.timer",
        "mooncen-staging-apply.timer",
        "mooncen-backup.timer",
        "mooncen-functional-test.timer",
    ):
        assert unit_name in unit
    assert 'if [ "${MOONCEN_BOOT_RECOVERY:-0}" = 1 ]' in active_restore
    assert 'systemctl start --no-block "${units[@]}"' in active_restore
    assert 'systemctl is-active --quiet "$unit_name"' in active_restore


def test_cloudflared_logical_enablement_is_independent_of_etc_unit_presence() -> None:
    source = GUARD.read_text(encoding="utf-8")
    backup = _function(source, "backup_systemd_units", "restore_systemd_configuration")
    restore = _function(source, "restore_systemd_configuration", "prune_history")

    exact_file_loop_end = backup.index("# The vendor cloudflared unit may live")
    logical_snapshot = backup.index("systemctl cat cloudflared.service", exact_file_loop_end)
    assert logical_snapshot > exact_file_loop_end
    assert 'systemctl is-enabled --quiet cloudflared.service' in backup[logical_snapshot:]
    assert "printf '%s\\n' cloudflared.service" in backup[logical_snapshot:]

    assert 'if systemctl cat cloudflared.service' in restore
    assert 'grep -Fxq -- cloudflared.service "$enabled_manifest"' in restore
    assert 'systemctl enable cloudflared.service' in restore
    assert 'systemctl disable cloudflared.service' in restore


@pytest.mark.skipif(_bash() is None, reason="bash unavailable")
def test_guard_stop_dynamically_preserves_external_deployment_transport(
    tmp_path: Path,
) -> None:
    source = GUARD.read_text(encoding="utf-8")
    crawler_helper = "is_crawler_runtime_unit_name() {" + _function(
        source,
        "is_crawler_runtime_unit_name",
        "is_external_control_plane_unit_name",
    )
    transport_helper = "is_external_control_plane_unit_name() {" + _function(
        source,
        "is_external_control_plane_unit_name",
        "build_mutable_artifact_inventory",
    )
    stop_function = "stop_managed_units() {" + _function(
        source,
        "stop_managed_units",
        "restore_active_units",
    )
    stop_function = stop_function.replace("/etc/systemd/system", '"$UNIT_ROOT"')

    unit_root = tmp_path / "systemd"
    unit_root.mkdir()
    for unit_name in (
        "mooncen-api.service",
        "mooncen-crawler.service",
        "mooncen-node-metrics.service",
        "mooncen-an2p-deploy-sshd.service",
    ):
        (unit_root / unit_name).write_text("[Unit]\n", encoding="utf-8")
    stop_log = tmp_path / "stopped-units"
    env = os.environ.copy()
    env["UNIT_ROOT"] = str(unit_root)
    env["STOP_LOG"] = str(stop_log)
    completed = subprocess.run(
        [_bash() or "bash", "-s"],
        input=(
            "set -euo pipefail\n"
            "die() { echo \"$*\" >&2; exit 1; }\n"
            "systemctl() {\n"
            "  case \"$1\" in\n"
            "    cat) return 1 ;;\n"
            "    stop) shift; printf '%s\\n' \"$@\" > \"$STOP_LOG\" ;;\n"
            "    is-active) return 1 ;;\n"
            "    show) printf '0\\n' ;;\n"
            "    *) return 0 ;;\n"
            "  esac\n"
            "}\n"
            f"{crawler_helper}{transport_helper}{stop_function}"
            "stop_managed_units\n"
        ),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
    assert stop_log.read_text(encoding="utf-8").splitlines() == [
        "mooncen-api.service"
    ]


def test_preflight_lock_publication_and_reclamation_are_fenced_and_durable() -> None:
    deploy = DEPLOY.read_text(encoding="utf-8")
    lock_script = _powershell_heredoc(deploy, "lockAndCleanupScript")
    reclaim = lock_script.split("reclaim_raw_preguard_lock() {", 1)[1].split(
        "converge_existing_lock() {",
        1,
    )[0]
    converge = lock_script.split("converge_existing_lock() {", 1)[1].split(
        'if [ "${1:-}" = __reclaim_raw ]',
        1,
    )[0]

    preflight_temp_sync = 'sudo sync -f -- "$lock_stage"'
    preflight_publish = 'sudo mv -fT -- "$preflight_stage" "$lock_stage/preflight.env"'
    atomic_publish = 'sudo mv -nT -- "$lock_stage" "$lock_dir"'
    stage_lost_race_check = 'if sudo test -e "$lock_stage" || sudo test -L "$lock_stage"; then'
    opt_sync = "sudo sync -f -- /opt"
    first_sync = lock_script.index(preflight_temp_sync, lock_script.index("preflight_stage="))
    publish = lock_script.index(preflight_publish, first_sync)
    second_sync = lock_script.index(preflight_temp_sync, publish)
    atomic = lock_script.index(atomic_publish, second_sync)
    lost_race = lock_script.index(stage_lost_race_check, atomic)
    durable_parent = lock_script.index(opt_sync, lost_race)
    assert first_sync < publish < second_sync < atomic < lost_race < durable_parent

    assert '[ "$state_boot" != "$current_boot_id" ] || return 73' in reclaim
    assert "Deadline-only takeover" in reclaim
    assert reclaim.index("journal.env") < reclaim.index("validate_raw_preguard_shape")
    assert reclaim.index("bootstrap.env") < reclaim.index("validate_raw_preguard_shape")
    assert reclaim.index("validate_raw_preguard_shape") < reclaim.index(
        'sudo rm -rf -- "$lock_dir"'
    )

    mtime_capture = 'raw_lock_mtime="$(sudo stat -c \'%Y\' "$lock_dir")"'
    tokenless_flock = (
        'sudo flock -x "$lock_dir/operation.lock" /bin/bash "$0" '
        '__reclaim_raw - "$raw_lock_mtime"'
    )
    token_flock = (
        'sudo flock -x "$lock_dir/operation.lock" /bin/bash "$0" '
        '__reclaim_raw "$actual_token" "$raw_lock_mtime"'
    )
    assert converge.count(mtime_capture) == 2
    assert converge.index(mtime_capture) < converge.index(tokenless_flock)
    second_capture = converge.index(mtime_capture, converge.index(tokenless_flock) + 1)
    assert second_capture < converge.index(token_flock, second_capture)
    assert 'reclaim_raw_preguard_lock "$2" "$3"' in lock_script


def test_bootstrap_arm_and_unit_publication_share_token_and_operation_fences() -> None:
    source = GUARD.read_text(encoding="utf-8")
    deploy = DEPLOY.read_text(encoding="utf-8")
    bootstrap = _function(source, "bootstrap_guard", "abort_bootstrap_guard")
    arm = _function(source, "arm_guard", "finalize_commit_release")
    arm_script = _powershell_heredoc(deploy, "armReleaseGuardScript")

    operation_fd = 'exec 9>"$lock_dir/operation.lock"'
    operation_lock = "flock -x 9"
    for block, protected_operation in (
        (bootstrap, 'backup_systemd_units "$lock_dir"'),
        (arm, 'load_bootstrap "$lock_dir" "$token"'),
    ):
        assert block.index(operation_fd) < block.index(operation_lock)
        post_lock_validation = block.index(
            'validate_lock "$lock_dir" "$token"',
            block.index(operation_lock),
        )
        assert block.index(operation_lock) < post_lock_validation < block.index(
            protected_operation,
            post_lock_validation,
        )

    fence_positions = [match.start() for match in re.finditer(r"^assert_lock_ownership$", arm_script, re.MULTILINE)]
    assert len(fence_positions) == 3
    install_guard = arm_script.index(
        'sudo install -o root -g root -m 0700 "$guard_source" "$lock_dir/guard.sh"'
    )
    bootstrap_call = arm_script.index('sudo "$lock_dir/guard.sh" bootstrap')
    arm_call = arm_script.index('sudo "$lock_dir/guard.sh" arm')
    assert fence_positions[0] < install_guard < bootstrap_call < fence_positions[1]
    assert fence_positions[1] < fence_positions[2] < arm_call

    unit_stage = 'install -o root -g root -m 0644 "$unit_source" "$unit_stage"'
    unit_stage_sync = 'durability_barrier "$unit_stage"'
    unit_publish = 'mv -fT -- "$unit_stage" "$unit_target"'
    unit_parent_sync = "durability_barrier /etc/systemd/system"
    unit_enable = 'systemctl enable "$guard_unit"'
    watcher_start = 'systemctl start --no-block "$guard_unit"'
    mutable_snapshot = 'backup_mutable_artifacts "$lock_dir"'
    journal_publish = 'mv -fT -- "$temporary" "$lock_dir/journal.env"'
    journal_parent_sync = 'durability_barrier "$lock_dir"'
    operation_unlock = "flock -u 9"
    operation_close = "exec 9>&-"
    active_check = 'systemctl is-active --quiet "$guard_unit"'
    assert arm.index(operation_lock) < arm.index(unit_stage)
    assert arm.index(unit_stage) < arm.index(unit_stage_sync) < arm.index(unit_publish)
    assert arm.index(unit_publish) < arm.index(unit_parent_sync) < arm.index(unit_enable)
    assert arm.index(unit_enable) < arm.index(watcher_start) < arm.index(mutable_snapshot)
    assert arm.index(mutable_snapshot) < arm.index(journal_publish)
    assert arm.index(journal_publish) < arm.index(journal_parent_sync, arm.index(journal_publish))
    journal_sync = arm.index(journal_parent_sync, arm.index(journal_publish))
    assert journal_sync < arm.index(operation_unlock) < arm.index(operation_close)
    assert arm.index(operation_close) < arm.index(active_check)


def test_finalizing_decisions_and_systemd_parent_validation_are_durable() -> None:
    source = GUARD.read_text(encoding="utf-8")
    recovery = _function(source, "recover_release", "arm_guard")
    bootstrap_abort = _function(source, "abort_bootstrap_guard", "arm_guard")
    systemd_restore = _function(source, "restore_systemd_configuration", "prune_history")

    prepared = recovery.split(
        '  if [ "$PHASE" = recovering_prepared ]; then', 1
    )[1].split("\n  fi\n", 1)[0]
    assert prepared.index("sync_recovery_filesystems") < prepared.index(
        'set_phase "$lock_dir" "$token" finalizing_abort'
    )
    assert recovery.index("sync_recovery_filesystems", recovery.index("recovering)")) < recovery.index(
        'set_phase "$lock_dir" "$token" finalizing_recovery'
    )
    assert bootstrap_abort.index("sync_recovery_filesystems") < bootstrap_abort.index(
        'set_bootstrap_phase "$lock_dir" "$token" finalizing'
    )

    assert "parent_mode=\"$(stat -c '%a' /etc/systemd/system)\"" in systemd_restore
    assert "(( (8#$parent_mode & 8#022) == 0 ))" in systemd_restore
    assert "systemd configuration parent mode is unsafe" in systemd_restore


@pytest.mark.skipif(
    os.name != "posix" or _bash() is None,
    reason="preflight lock harness requires POSIX ownership and flock semantics",
)
def test_fresh_same_boot_preflight_owner_is_not_stolen(tmp_path: Path) -> None:
    harness, fake_root, env, current_boot_id = _build_preflight_lock_harness(tmp_path)
    old_token = "c" * 32
    opt = fake_root / "opt"
    lock = opt / ".mooncen-deploy.lock"
    lock.mkdir(mode=0o700)
    token_file = lock / "token"
    token_file.write_text(f"{old_token}\n", encoding="ascii")
    token_file.chmod(0o600)
    preflight = lock / "preflight.env"
    preflight.write_text(
        "VERSION=1\n"
        f"TOKEN={old_token}\n"
        f"BOOT_ID={current_boot_id}\n"
        "DEADLINE_EPOCH=999999999999\n"
        f"NATIVE_INTENT_TOKEN={old_token}\n"
        f"RELEASE_DIR={opt.as_posix()}/.mooncen-release-{old_token}\n",
        encoding="ascii",
    )
    preflight.chmod(0o600)

    result = _run(harness, env)
    assert result.returncode == 73, result.stderr
    assert token_file.read_text(encoding="ascii") == f"{old_token}\n"
    assert preflight.is_file()
    assert not (opt / f".mooncen-deploy-heartbeat-{TOKEN}").exists()


@pytest.mark.skipif(
    os.name != "posix" or _bash() is None,
    reason="preflight lock harness requires POSIX ownership and flock semantics",
)
@pytest.mark.parametrize("legacy_shape", ("empty", "token-only", "token-preflight"))
def test_old_boot_raw_locks_converge_before_a_new_atomic_lock(
    tmp_path: Path,
    legacy_shape: str,
) -> None:
    harness, fake_root, env, current_boot_id = _build_preflight_lock_harness(tmp_path)
    old_token = "c" * 32
    opt = fake_root / "opt"
    lock = opt / ".mooncen-deploy.lock"
    lock.mkdir(mode=0o700)

    if legacy_shape in {"empty", "token-only"}:
        if legacy_shape == "token-only":
            token_file = lock / "token"
            token_file.write_text(f"{old_token}\n", encoding="ascii")
            token_file.chmod(0o600)
        boot_epoch = next(
            int(line.split()[1])
            for line in Path("/proc/stat").read_text(encoding="ascii").splitlines()
            if line.startswith("btime ")
        )
        os.utime(lock, (boot_epoch - 60, boot_epoch - 60))
    else:
        old_boot_id = "00000000-0000-4000-8000-000000000001"
        if old_boot_id == current_boot_id:
            old_boot_id = "00000000-0000-4000-8000-000000000002"
        token_file = lock / "token"
        token_file.write_text(f"{old_token}\n", encoding="ascii")
        token_file.chmod(0o600)
        release = opt / f".mooncen-release-{old_token}"
        release.mkdir(mode=0o700)
        (release / "candidate.txt").write_text("unactivated\n", encoding="ascii")
        preflight = lock / "preflight.env"
        preflight.write_text(
            "VERSION=1\n"
            f"TOKEN={old_token}\n"
            f"BOOT_ID={old_boot_id}\n"
            "DEADLINE_EPOCH=999999999999\n"
            f"NATIVE_INTENT_TOKEN={old_token}\n"
            f"RELEASE_DIR={release.as_posix()}\n",
            encoding="ascii",
        )
        preflight.chmod(0o600)

    result = _run(harness, env)
    assert result.returncode == 0, result.stderr
    assert (lock / "token").read_text(encoding="ascii") == f"{TOKEN}\n"
    assert f"BOOT_ID={current_boot_id}\n" in (lock / "preflight.env").read_text(
        encoding="ascii"
    )
    if legacy_shape == "token-preflight":
        preserved = (
            opt
            / ".mooncen-release-history"
            / old_token
            / "preflight-unactivated"
            / "candidate.txt"
        )
        assert preserved.read_text(encoding="ascii") == "unactivated\n"


@pytest.mark.skipif(
    os.name != "posix" or _bash() is None,
    reason="preflight lock harness requires POSIX ownership and flock semantics",
)
def test_existing_release_history_does_not_collide_with_candidate_scan(tmp_path: Path) -> None:
    harness, fake_root, env, current_boot_id = _build_preflight_lock_harness(tmp_path)
    opt = fake_root / "opt"
    history = opt / ".mooncen-release-history"
    history.mkdir(mode=0o700)

    result = _run(harness, env)
    assert result.returncode == 0, result.stderr
    lock = opt / ".mooncen-deploy.lock"
    assert (lock / "token").read_text(encoding="ascii") == f"{TOKEN}\n"
    assert f"BOOT_ID={current_boot_id}\n" in (lock / "preflight.env").read_text(
        encoding="ascii"
    )
    assert history.is_dir()


@pytest.mark.skipif(
    os.name != "posix" or _bash() is None,
    reason="preflight lock harness requires POSIX ownership and flock semantics",
)
def test_pre_guard_failure_releases_only_its_exact_native_intent(
    tmp_path: Path,
) -> None:
    harness, fake_root, env, _current_boot_id = _build_preflight_lock_harness(
        tmp_path
    )
    opt = fake_root / "opt"
    stale_recovery = opt / f".mooncen-previous-{'d' * 32}"
    stale_recovery.mkdir(mode=0o700)

    result = _run(harness, env)

    assert result.returncode == 75, result.stderr
    assert "preserving recovery release" in result.stderr
    assert not (opt / ".mooncen-deploy.lock").exists()
    assert not (opt / f".mooncen-deploy-lock-{TOKEN}.staged").exists()
    assert Path(env["MOONCEN_TEST_CONTROLLER_LOG"]).read_text(
        encoding="ascii"
    ) == f"native-end {TOKEN}\n"


@pytest.mark.skipif(_bash() is None, reason="bash unavailable")
def test_prepared_partial_candidate_finalization_retries_without_a_marker(tmp_path: Path) -> None:
    harness, fake_root, env = _build_harness(tmp_path)
    initialized = _run(harness, env, "init", "prepared-partial")
    assert initialized.returncode == 0, initialized.stderr

    (fake_root / "fail-finish-once").touch()
    first = _run(harness, env, "recover")
    assert first.returncode != 0
    assert "injected finalization failure" in first.stderr

    lock = fake_root / "opt" / ".mooncen-deploy.lock"
    history = fake_root / "opt" / ".mooncen-release-history" / TOKEN
    assert lock.is_dir()
    assert _journal_phase(lock / "journal.env") == "finalizing_abort"
    assert (history / "unactivated" / "PARTIAL").is_file()
    assert not (history / "unactivated" / ".mooncen-prebuilt-release").exists()
    assert (lock / "mutable-artifacts").is_dir()
    assert (lock / "systemd-unit-metadata").is_file()
    assert (lock / "systemd-dropin-metadata").is_file()

    second = _run(harness, env, "recover")
    assert second.returncode == 0, second.stderr
    assert not lock.exists()
    assert (fake_root / "opt" / "mooncen" / "old.txt").is_file()
    assert (history / "unactivated" / "PARTIAL").read_text(
        encoding="utf-8"
    ) == "partially extracted candidate\n"
    assert _journal_phase(history / "journal.env") == "aborted"
    assert (history / "systemd-unit-metadata-before-deploy").is_file()
    assert (history / "systemd-dropin-metadata-before-deploy").is_file()
    assert not list(history.rglob("mutable-artifacts*"))


@pytest.mark.skipif(
    os.name != "posix" or _bash() is None,
    reason="native authorization harness requires POSIX ownership semantics",
)
@pytest.mark.skip(reason="container-to-native candidate authorization was retired")
def test_candidate_start_authorization_is_exact_and_short_lived(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from deploy.ubuntu import mooncen_native_runtime_condition as condition

    harness, fake_root, env = _build_harness(tmp_path)
    initialized = _run(harness, env, "init", "first-post-swap")
    assert initialized.returncode == 0, initialized.stderr

    lock = fake_root / "opt" / ".mooncen-deploy.lock"
    authorization_directory = fake_root / "run" / "mooncen-native-deploy-start"
    authorization = authorization_directory / "authorization.json"
    boot_id = tmp_path / "boot_id"
    boot_id.write_text(
        "11111111-1111-4111-8111-111111111111\n",
        encoding="ascii",
    )
    status = {
        "native_intent": {"schema_version": 1, "token": TOKEN},
        "schema_version": 1,
        "state": None,
        "transaction": None,
        "worker_lease": None,
    }
    monkeypatch.setattr(condition.os, "geteuid", lambda: 0)
    monkeypatch.setattr(condition, "_controller_status", lambda: status)
    monkeypatch.setattr(condition, "NATIVE_DEPLOY_LOCK", lock)
    monkeypatch.setattr(
        condition,
        "NATIVE_DEPLOY_AUTHORIZATION_DIRECTORY",
        authorization_directory,
    )
    monkeypatch.setattr(condition, "NATIVE_DEPLOY_AUTHORIZATION", authorization)
    monkeypatch.setattr(condition, "BOOT_ID_PATH", boot_id)
    monkeypatch.setattr(condition, "_safe_root_file", lambda *_args: None)
    monkeypatch.setattr(
        condition,
        "_safe_root_directory",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(condition, "_guard_is_running", lambda *_args, **_kwargs: None)

    with pytest.raises(condition.NativeRuntimeConditionError):
        condition.assert_native_runtime_allowed()


    published = _run(harness, env, "authorize-candidate")
    assert published.returncode == 0, published.stderr
    authorization_payload = authorization.read_bytes()
    authorization_value = json.loads(authorization_payload.decode("ascii"))
    assert authorization_payload == (
        json.dumps(
            authorization_value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")
    assert authorization_value["guard_token"] == TOKEN
    assert authorization_value["intent_token"] == TOKEN
    assert authorization_value["mode"] == "candidate"
    assert authorization_value["phase"] == "activated"
    assert authorization_value["arm_boot_id"] == authorization_value["authorization_boot_id"]
    assert (
        authorization_value["authorization_deadline_epoch"]
        <= authorization_value["arm_deadline_epoch"]
    )
    journal_path = lock / "journal.env"
    journal_path.write_bytes(
        journal_path.read_bytes().replace(fake_root.as_posix().encode("ascii"), b"")
    )
    condition.assert_native_runtime_allowed()

    revoked = _run(harness, env, "revoke-start")
    assert revoked.returncode == 0, revoked.stderr
    assert not authorization.exists()
    with pytest.raises(condition.NativeRuntimeConditionError):
        condition.assert_native_runtime_allowed()


@pytest.mark.skipif(_bash() is None, reason="bash unavailable")
@pytest.mark.parametrize("action", ("recover", "boot-recover"))
def test_recovery_uses_fresh_authorization_after_reboot_and_arm_expiry(
    tmp_path: Path,
    action: str,
) -> None:
    harness, fake_root, env = _build_harness(tmp_path)
    initialized = _run(harness, env, "init", "existing-mid-swap")
    assert initialized.returncode == 0, initialized.stderr

    lock = fake_root / "opt" / ".mooncen-deploy.lock"
    journal_path = lock / "journal.env"
    journal = journal_path.read_text(encoding="ascii")
    journal = re.sub(
        r"^ARM_BOOT_ID=.*$",
        "ARM_BOOT_ID=22222222-2222-4222-8222-222222222222",
        journal,
        flags=re.MULTILINE,
    )
    journal = re.sub(
        r"^DEADLINE_EPOCH=.*$",
        "DEADLINE_EPOCH=1000000000",
        journal,
        flags=re.MULTILINE,
    )
    journal_path.write_text(journal, encoding="ascii")
    journal_path.chmod(0o600)

    recovered = _run(harness, env, action)
    assert recovered.returncode == 0, recovered.stderr
    observed = json.loads(
        (fake_root / "observed-native-start-authorization.json").read_text(
            encoding="ascii"
        )
    )
    assert observed == {
        "arm_boot_id": "22222222-2222-4222-8222-222222222222",
        "arm_deadline_epoch": 1000000000,
        "authorization_boot_id": "11111111-1111-4111-8111-111111111111",
        "authorization_deadline_epoch": observed["authorization_deadline_epoch"],
        "guard_token": TOKEN,
        "intent_token": TOKEN,
        "mode": "recovery",
        "phase": "recovering",
        "schema_version": 1,
    }
    assert observed["authorization_deadline_epoch"] > 1000000000
    assert (fake_root / "active-unit-restore-count").read_text(
        encoding="ascii"
    ) == "restore\n"
    assert not (
        fake_root / "run" / "mooncen-native-deploy-start" / "authorization.json"
    ).exists()
    assert not lock.exists()
    assert (fake_root / "opt" / "mooncen" / "old.txt").is_file()


@pytest.mark.skipif(_bash() is None, reason="bash unavailable")
@pytest.mark.parametrize(
    ("scenario", "expect_active_release"),
    (
        ("first-post-swap", False),
        ("existing-mid-swap", True),
        ("failed-already-present", True),
    ),
)
def test_recovery_retries_converge_without_consuming_backups(
    tmp_path: Path,
    scenario: str,
    expect_active_release: bool,
) -> None:
    harness, fake_root, env = _build_harness(tmp_path)
    initialized = _run(harness, env, "init", scenario)
    assert initialized.returncode == 0, initialized.stderr

    (fake_root / "fail-restore-once").touch()
    first = _run(harness, env, "recover")
    assert first.returncode != 0
    assert "injected host restore failure" in first.stderr

    lock = fake_root / "opt" / ".mooncen-deploy.lock"
    assert lock.is_dir()
    assert (lock / "systemd-units").is_dir()
    assert (lock / "systemd-unit-names").is_file()
    assert (lock / "systemd-unit-metadata").is_file()
    assert (lock / "systemd-dropin-metadata").is_file()
    assert (lock / "mutable-artifacts").is_dir()
    assert (lock / "mutable-artifacts.manifest").is_file()
    assert _journal_phase(lock / "journal.env") == "recovering"

    remote = fake_root / "opt" / "mooncen"
    failed = fake_root / "opt" / f".mooncen-failed-{TOKEN}"
    assert remote.is_dir() is expect_active_release
    assert failed.is_dir()
    assert not (fake_root / "opt" / f".mooncen-release-{TOKEN}").exists()
    assert not (fake_root / "opt" / f".mooncen-previous-{TOKEN}").exists()

    second = _run(harness, env, "recover")
    assert second.returncode == 0, second.stderr
    assert not lock.exists()
    assert remote.is_dir() is expect_active_release
    if expect_active_release:
        assert (remote / "old.txt").read_text(encoding="utf-8") == "old-release\n"

    history = fake_root / "opt" / ".mooncen-release-history" / TOKEN
    assert (history / "failed" / ".mooncen-prebuilt-release").is_file()
    assert (history / "systemd-units-before-deploy").is_dir()
    assert (history / "systemd-unit-metadata-before-deploy").is_file()
    assert (history / "systemd-dropin-metadata-before-deploy").is_file()
    assert _journal_phase(history / "journal.env") == "recovered"
    assert not list(history.rglob("mutable-artifacts*"))


@pytest.mark.skipif(_bash() is None, reason="bash unavailable")
def test_commit_retry_resumes_after_previous_release_was_archived(tmp_path: Path) -> None:
    harness, fake_root, env = _build_harness(tmp_path)
    initialized = _run(harness, env, "init", "commit-retry")
    assert initialized.returncode == 0, initialized.stderr

    (fake_root / "fail-finish-once").touch()
    first = _run(harness, env, "commit")
    assert first.returncode != 0
    assert "injected finalization failure" in first.stderr

    lock = fake_root / "opt" / ".mooncen-deploy.lock"
    history = fake_root / "opt" / ".mooncen-release-history" / TOKEN
    assert lock.is_dir()
    assert _journal_phase(lock / "journal.env") == "finalizing_commit"
    assert (history / "previous" / "old.txt").is_file()
    assert (lock / "mutable-artifacts").is_dir()
    assert (lock / "systemd-unit-metadata").is_file()
    assert (lock / "systemd-dropin-metadata").is_file()

    second = _run(harness, env, "commit")
    assert second.returncode == 0, second.stderr
    assert not lock.exists()
    assert (fake_root / "opt" / "mooncen" / ".deploy-info").is_file()
    assert (history / "previous" / "old.txt").read_text(encoding="utf-8") == "old-release\n"
    assert _journal_phase(history / "journal.env") == "committed"
    assert (history / "systemd-unit-metadata-before-deploy").is_file()
    assert (history / "systemd-dropin-metadata-before-deploy").is_file()
    assert not list(history.rglob("mutable-artifacts*"))


@pytest.mark.skipif(_bash() is None, reason="bash unavailable")
def test_secret_snapshot_cleanup_retries_from_manifest_move_boundary(tmp_path: Path) -> None:
    harness, fake_root, env = _build_harness(tmp_path)
    initialized = _run(harness, env, "init", "first-post-swap")
    assert initialized.returncode == 0, initialized.stderr

    cleaned = _run(harness, env, "cleanup-twice")
    assert cleaned.returncode == 0, cleaned.stderr
    lock = fake_root / "opt" / ".mooncen-deploy.lock"
    assert not (lock / "mutable-artifacts").exists()
    assert not (lock / "mutable-artifacts.manifest").exists()
    assert not (lock / ".mutable-artifacts.deleting").exists()


@pytest.mark.skipif(_bash() is None, reason="bash unavailable")
def test_mutable_restore_retry_accepts_an_already_absent_parent_and_child(tmp_path: Path) -> None:
    harness, _fake_root, env = _build_absent_parent_harness(tmp_path)
    restored = _run(harness, env)
    assert restored.returncode == 0, restored.stderr
