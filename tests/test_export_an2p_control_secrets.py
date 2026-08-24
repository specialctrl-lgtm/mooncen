from __future__ import annotations

import base64
import io
from pathlib import Path
import re
import sys

import pytest

from deploy.ubuntu import export_an2p_control_secrets as exporter


ROOT = Path(__file__).resolve().parents[1]
TARGET_IDENTITY = "8" * 64
OPS_PASSWORD_HASH = f"pbkdf2_sha256$310000${'s' * 16}${'a' * 64}"


def _source_values() -> dict[str, str]:
    direct = {name: "" for name in exporter.DIRECT_SOURCE_NAMES}
    direct.update(
        {
            "BACKUP_AGE_RECIPIENT": "age1example",
            "BACKUP_PORT": "22",
            "DB_AI_USER": "mooncen_ai_login",
            "DB_API_USER": "mooncen_api_login",
            "DB_APPLIER_USER": "mooncen_applier_login",
            "DB_CHECK_USER": "mooncen_check_login",
            "DB_DEPLOYMENT_WORKER_USER": "mooncen_deployment_worker_login",
            "DB_MIGRATOR_USER": "mooncen_admin",
            "DB_NAME": "mooncen",
            "DB_USER": "mooncen_admin",
            "GOOGLE_OAUTH_CLIENT_ID": "google-client",
            "NAVER_OAUTH_CLIENT_ID": "naver-client",
        }
    )
    pair_values = {
        name: f"optional-{name.lower().replace('_', '-')}"
        for name in exporter.PAIRED_SOURCE_NAMES
    }
    pair_values.update(
        {
            "AUTH_SECRET": "auth-secret-independent-canary-value",
            "DB_AI_PASSWORD": "ai-password-independent-canary",
            "DB_API_PASSWORD": "api-password-independent-canary",
            "DB_BACKUP_PASSWORD": "backup-password-independent-canary",
            "DB_CHECK_PASSWORD": "check-password-independent-canary",
            "DB_CRAWLER_PASSWORD": "crawler-password-independent-canary",
            "DB_DEPLOYMENT_WORKER_PASSWORD": (
                "deployment-worker-password-independent-canary"
            ),
            "DB_PASSWORD": "master-password-independent-canary",
            "MOONCEN_OPS_LOGIN_ID": "opsadmin",
            "MOONCEN_OPS_PASSWORD_HASH": OPS_PASSWORD_HASH,
            "PRIMARY_DB_PASSWORD": "applier-password-independent-canary",
        }
    )
    values = dict(direct)
    for name, value in pair_values.items():
        values[name] = value
        values[f"{name}_B64"] = base64.b64encode(value.encode()).decode()
    assert set(values) == exporter.EXPECTED_SOURCE_NAMES
    return values


def _write_inputs(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    values = _source_values()
    secret_path = tmp_path / "deploy-secrets.env"
    identity_path = tmp_path / "an2p-dev-target-identity"
    secret_path.write_text(
        "".join(f"{name}={values[name]}\n" for name in sorted(values)),
        encoding="utf-8",
    )
    identity_path.write_text(f"{TARGET_IDENTITY}\n", encoding="ascii")
    return secret_path, identity_path, values


@pytest.fixture
def protected_inputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Path, Path, dict[str, str]]:
    inputs = _write_inputs(tmp_path)
    monkeypatch.setattr(
        exporter,
        "_assert_root_private_file",
        lambda path, *, label: path,
    )
    return inputs


def test_export_is_exact_eight_key_envelope_and_prefers_verified_b64(
    protected_inputs: tuple[Path, Path, dict[str, str]],
) -> None:
    secret_path, identity_path, values = protected_inputs

    payload = exporter.render_control_envelope(secret_path, identity_path)
    lines = payload.decode("utf-8").splitlines()
    assert tuple(line.partition("=")[0] for line in lines) == (
        exporter.CONTROL_OUTPUT_NAMES
    )
    assert len(lines) == 8
    rendered = dict(line.split("=", 1) for line in lines)
    assert set(rendered) == set(exporter.CONTROL_OUTPUT_NAMES)
    assert rendered["DB_API_PASSWORD"] == values["DB_API_PASSWORD"]
    assert (
        rendered["DB_DEPLOYMENT_WORKER_PASSWORD"]
        == values["DB_DEPLOYMENT_WORKER_PASSWORD"]
    )
    assert rendered["OPS_CONTAINER_DEV_TARGET_IDENTITY"] == TARGET_IDENTITY
    assert "AUTH_SECRET" not in rendered
    assert not any(name.endswith("_B64") for name in rendered)


def test_export_rejects_raw_b64_mismatch_without_echoing_either_value(
    protected_inputs: tuple[Path, Path, dict[str, str]],
) -> None:
    secret_path, identity_path, values = protected_inputs
    original = values["DB_DEPLOYMENT_WORKER_PASSWORD"]
    mismatched = "different-worker-secret-that-must-not-leak"
    text = secret_path.read_text(encoding="utf-8")
    secret_path.write_text(
        text.replace(
            f"DB_DEPLOYMENT_WORKER_PASSWORD={original}\n",
            f"DB_DEPLOYMENT_WORKER_PASSWORD={mismatched}\n",
        ),
        encoding="utf-8",
    )

    with pytest.raises(exporter.ExportError) as failure:
        exporter.render_control_envelope(secret_path, identity_path)
    message = str(failure.value)
    assert original not in message
    assert mismatched not in message
    assert "companions do not match" in message


def test_export_requires_controller_target_identity(
    protected_inputs: tuple[Path, Path, dict[str, str]],
) -> None:
    secret_path, identity_path, _values = protected_inputs
    identity_path.write_text("not-a-target-identity\n", encoding="ascii")

    with pytest.raises(exporter.ExportError, match="target identity is invalid"):
        exporter.render_control_envelope(secret_path, identity_path)

    identity_path.unlink()
    with pytest.raises(exporter.ExportError, match="target identity is unreadable"):
        exporter.render_control_envelope(secret_path, identity_path)


def test_export_rejects_noncanonical_deployment_worker_login(
    protected_inputs: tuple[Path, Path, dict[str, str]],
) -> None:
    secret_path, identity_path, _values = protected_inputs
    payload = secret_path.read_text(encoding="utf-8").replace(
        "DB_DEPLOYMENT_WORKER_USER=mooncen_deployment_worker_login\n",
        "DB_DEPLOYMENT_WORKER_USER=custom_worker_login\n",
    )
    secret_path.write_text(payload, encoding="utf-8")

    with pytest.raises(exporter.ExportError, match="identities or credentials"):
        exporter.render_control_envelope(secret_path, identity_path)


@pytest.mark.parametrize(
    "duplicate_name",
    ("DB_API_PASSWORD", "DB_DEPLOYMENT_WORKER_PASSWORD"),
)
def test_export_rejects_control_credentials_reused_by_another_database_login(
    protected_inputs: tuple[Path, Path, dict[str, str]],
    duplicate_name: str,
) -> None:
    secret_path, identity_path, values = protected_inputs
    duplicate = values["DB_PASSWORD"]
    original = values[duplicate_name]
    encoded_original = values[f"{duplicate_name}_B64"]
    encoded_duplicate = base64.b64encode(duplicate.encode("utf-8")).decode("ascii")
    payload = secret_path.read_text(encoding="utf-8")
    payload = payload.replace(
        f"{duplicate_name}={original}\n",
        f"{duplicate_name}={duplicate}\n",
    ).replace(
        f"{duplicate_name}_B64={encoded_original}\n",
        f"{duplicate_name}_B64={encoded_duplicate}\n",
    )
    secret_path.write_text(payload, encoding="utf-8")

    with pytest.raises(exporter.ExportError) as failure:
        exporter.render_control_envelope(secret_path, identity_path)
    assert "pairwise distinct" in str(failure.value)
    assert duplicate not in str(failure.value)


class _CapturedStdout:
    def __init__(self, *, tty: bool) -> None:
        self._tty = tty
        self.buffer = io.BytesIO()

    def isatty(self) -> bool:
        return self._tty


def test_export_main_rejects_arguments_and_tty_stdout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(exporter.os, "geteuid", lambda: 0)

    with pytest.raises(exporter.ExportError, match="accepts no arguments"):
        exporter.main(["--output", "/root/forbidden"])

    monkeypatch.setattr(sys, "stdout", _CapturedStdout(tty=True))
    with pytest.raises(exporter.ExportError, match="refusing.*terminal"):
        exporter.main([])

    with (tmp_path / "forbidden-plaintext-output").open(
        "w",
        encoding="utf-8",
    ) as regular_output:
        monkeypatch.setattr(sys, "stdout", regular_output)
        with pytest.raises(exporter.ExportError, match="pipe or socket"):
            exporter.main([])


def test_export_main_writes_only_payload_to_non_tty_stdout(
    protected_inputs: tuple[Path, Path, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_path, identity_path, _values = protected_inputs
    captured = _CapturedStdout(tty=False)
    monkeypatch.setattr(exporter.os, "geteuid", lambda: 0)
    monkeypatch.setattr(exporter, "DEPLOY_SECRETS_PATH", secret_path)
    monkeypatch.setattr(exporter, "TARGET_IDENTITY_PATH", identity_path)
    monkeypatch.setattr(exporter, "_assert_protected_stdout", lambda: None)
    monkeypatch.setattr(sys, "stdout", captured)

    assert exporter.main([]) == 0
    payload = captured.buffer.getvalue()
    assert len(payload.decode("utf-8").splitlines()) == 8
    assert b"Prepared" not in payload


def test_native_setup_installs_root_only_export_source_after_db_contract() -> None:
    setup = (ROOT / "deploy/ubuntu/setup_project.sh").read_text(encoding="utf-8")
    deploy = (ROOT / "deploy/ubuntu/deploy_from_windows.ps1").read_text(
        encoding="utf-8"
    )
    production_installer = (
        ROOT / "deploy/docker/install_production_runtime.sh"
    ).read_text(encoding="utf-8")

    assert "ROOT_DEPLOY_SECRET_FILE=/etc/mooncen/deploy-secrets.env" in setup
    assert "printf 'DB_NAME=%s\\n' \"$DB_NAME\"" in setup
    assert "printf 'DB_API_USER=%s\\n' \"$DB_API_USER\"" in setup
    root_commit = setup.index(
        'sudo mv -fT -- "$root_deploy_secret_stage" "$ROOT_DEPLOY_SECRET_FILE"'
    )
    role_contract = setup.index('if [ "$db_role_contract" != "t" ]')
    skipped_database = setup.index(
        'echo "Skipping DB setup/migration because SKIP_DB_SETUP=1."'
    )
    assert role_contract < root_commit < skipped_database
    assert 'sudo sync -f -- "$root_deploy_secret_stage"' in setup
    assert 'sudo sync -f -- "$SERVICE_CONFIG_DIR"' in setup

    helper_install = (
        'install_root_runtime_helper \\\n'
        '  "$AN2P_CONTROL_SECRETS_EXPORT_SOURCE" \\\n'
        '  "$AN2P_CONTROL_SECRETS_EXPORT_HELPER"'
    )
    assert helper_install in setup
    assert (
        "AN2P_CONTROL_SECRETS_EXPORT_HELPER="
        "/usr/local/libexec/mooncen-export-an2p-control-secrets"
        in setup
    )
    assert 'sudo install -o root -g root -m 0755 "$helper_source"' in setup
    exporter_revoke = setup.index(
        'sudo rm -f -- "$AN2P_CONTROL_SECRETS_EXPORT_HELPER"'
    )
    hba_probe = setup.index('sudo "$CONTAINER_PG_HBA_HELPER" install')
    database_boundary = setup.index("--verify-database-boundary")
    exporter_install = setup.index(helper_install)
    assert (
        exporter_revoke
        < hba_probe
        < database_boundary
        < root_commit
        < exporter_install
        < skipped_database
    )
    assert "deploy/ubuntu/export_an2p_control_secrets.py" in deploy
    assert (
        '"$repository_root/deploy/ubuntu/export_an2p_control_secrets.py"'
        in production_installer
    )
    assert "/usr/local/libexec/mooncen-export-an2p-control-secrets" in production_installer
    exporter_gate = production_installer.index(
        "guarded native setup did not publish the exact control-secret exporter"
    )
    controller_install = production_installer.index(
        'install -d -o root -g root -m 0755 /usr/local/libexec'
    )
    assert exporter_gate < controller_install
    assert (
        'cmp -s -- "$repository_root/deploy/ubuntu/export_an2p_control_secrets.py"'
        in production_installer
    )
    assert (
        'install -o root -g root -m 0755 \\\n  "$repository_root/deploy/ubuntu/export_an2p_control_secrets.py"'
        not in production_installer
    )
    assert "guarded native setup did not commit its root-only control source" in (
        production_installer
    )

    sudoers = (ROOT / "deploy/ubuntu/install_sudoers.sh").read_text(encoding="utf-8")
    assert "Defaults!${AN2P_CONTROL_EXPORT} !use_pty" in sudoers
    assert "${DEPLOY_USER} ALL=(root) NOPASSWD: ${AN2P_CONTROL_EXPORT}" in sudoers
    assert "${CONTAINER_DEPLOY_USER} ALL=(root)" not in sudoers


def test_native_deploy_secret_writer_exactly_matches_exporter_source_schema() -> None:
    setup = (ROOT / "deploy/ubuntu/setup_project.sh").read_text(encoding="utf-8")
    writer = setup.split(
        'deploy_secret_tmp="$(mktemp "$DEPLOY_SECRET_DIR/deploy-secrets.env.XXXXXX")"',
        1,
    )[1].split('} > "$deploy_secret_tmp"', 1)[0]
    direct_names = set(re.findall(r"printf '([A-Z][A-Z0-9_]*)=%s\\n'", writer))
    paired_names = set(
        re.findall(r"write_deploy_secret_pair ([A-Z][A-Z0-9_]*)", writer)
    )
    written_names = direct_names | paired_names | {
        f"{name}_B64" for name in paired_names
    }

    assert written_names == exporter.EXPECTED_SOURCE_NAMES


def test_export_helper_has_no_network_or_user_home_output_surface() -> None:
    source = (ROOT / "deploy/ubuntu/export_an2p_control_secrets.py").read_text(
        encoding="utf-8"
    )

    assert "subprocess" not in source
    assert "import socket" not in source
    assert "/home/" not in source
    assert "DEPLOY_SECRETS_PATH = Path(\"/etc/mooncen/deploy-secrets.env\")" in source
    assert "TARGET_IDENTITY_PATH = Path(" in source
    assert "sys.stdout.isatty()" in source
    assert "stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)" in source
    assert "sys.stdout.buffer.write(payload)" in source
