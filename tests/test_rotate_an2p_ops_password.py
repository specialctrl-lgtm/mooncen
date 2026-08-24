from __future__ import annotations

from pathlib import Path

import pytest

from deploy.an2p import receive_control_bootstrap as receiver
from deploy.ubuntu import export_an2p_control_secrets as exporter
from tools import rotate_an2p_ops_password as rotation
from tools.prepare_an2p_ops_control import PreparationError


def test_rotation_envelope_schema_matches_export_and_receive_boundaries() -> None:
    assert rotation.ENVELOPE_ORDER == exporter.CONTROL_OUTPUT_NAMES
    assert rotation.ENVELOPE_ORDER == receiver.CONTROL_NAMES


def test_password_rotation_stages_only_root_bootstrap_outputs(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "control-secrets.env"
    credential = tmp_path / "ops-credentials.txt"
    ops_auth_secret = tmp_path / "ops-auth-secret"
    values = {
        "DB_API_PASSWORD": "api",
        "DB_API_USER": "mooncen_api_login",
        "DB_DEPLOYMENT_WORKER_PASSWORD": "worker",
        "DB_DEPLOYMENT_WORKER_USER": "mooncen_deployment_worker_login",
        "DB_NAME": "mooncen",
        "MOONCEN_OPS_LOGIN_ID": "opsadmin",
        "MOONCEN_OPS_PASSWORD_HASH": "old-hash",
        "OPS_CONTAINER_DEV_TARGET_IDENTITY": "8" * 64,
    }
    writes: list[tuple[Path, str]] = []
    monkeypatch.setattr(rotation, "DEFAULT_SOURCE", source)
    monkeypatch.setattr(rotation, "CREDENTIAL_PATH", credential)
    monkeypatch.setattr(rotation, "DEFAULT_OPS_AUTH_SECRET", ops_auth_secret)
    monkeypatch.setattr(rotation, "load_protected_values", lambda path: dict(values))
    secret_reads: list[tuple[Path, dict[str, str]]] = []

    def load_local_secret(path: Path, observed: dict[str, str]) -> str:
        secret_reads.append((path, dict(observed)))
        return "L" * 64

    monkeypatch.setattr(rotation, "load_ops_auth_secret", load_local_secret)
    monkeypatch.setattr(rotation.secrets, "token_urlsafe", lambda _length: "new-password")
    monkeypatch.setattr(rotation, "encode_password", lambda password: f"encoded:{password}")
    monkeypatch.setattr(
        rotation,
        "_atomic_root_write",
        lambda path, content: writes.append((path, content)),
    )

    assert rotation.stage_rotation() == "new-password"
    assert [path for path, _content in writes] == [credential, source]
    assert "Password: new-password\n" in writes[0][1]
    assert "URL: http://127.0.0.1:5175/\n" in writes[0][1]
    assert "ops.localhost" not in writes[0][1]
    assert writes[1][1].splitlines() == [
        f"{name}={('encoded:new-password' if name == 'MOONCEN_OPS_PASSWORD_HASH' else values[name])}"
        for name in rotation.ENVELOPE_ORDER
    ]
    assert not writes[1][1].startswith("#")
    assert "AUTH_SECRET=" not in writes[1][1]
    assert "L" * 64 not in "".join(content for _path, content in writes)
    assert [path for path, _values in secret_reads] == [
        ops_auth_secret,
        ops_auth_secret,
    ]
    assert secret_reads[0][1]["MOONCEN_OPS_PASSWORD_HASH"] == "old-hash"
    assert secret_reads[1][1]["MOONCEN_OPS_PASSWORD_HASH"] == "encoded:new-password"
    source_code = Path(rotation.__file__).read_text(encoding="utf-8")
    assert "subprocess" not in source_code
    assert "cloud-deploy" not in source_code
    assert "apply-ops-rotation --pair " in source_code
    assert "<active-finalized-pair>" in source_code
    assert "finalize-control --pair <pending-pair>" in source_code
    assert "pending first install" in source_code
    assert "already finalized pair" in source_code


def test_password_rotation_rejects_the_retired_production_auth_field() -> None:
    values = {name: f"value-{name.lower()}" for name in rotation.ENVELOPE_ORDER}
    values["AUTH_SECRET"] = "production-signing-key-must-not-cross"

    with pytest.raises(PreparationError, match="field set changed"):
        rotation._render_envelope(values)


def test_password_rotation_fails_if_local_signing_secret_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    values = {name: f"value-{name.lower()}" for name in rotation.ENVELOPE_ORDER}
    writes: list[Path] = []
    observed_secrets = iter(("A" * 64, "B" * 64))
    monkeypatch.setattr(rotation, "DEFAULT_SOURCE", tmp_path / "control-secrets.env")
    monkeypatch.setattr(rotation, "CREDENTIAL_PATH", tmp_path / "ops-credentials.txt")
    monkeypatch.setattr(rotation, "DEFAULT_OPS_AUTH_SECRET", tmp_path / "ops-auth-secret")
    monkeypatch.setattr(rotation, "load_protected_values", lambda _path: dict(values))
    monkeypatch.setattr(
        rotation,
        "load_ops_auth_secret",
        lambda _path, _values: next(observed_secrets),
    )
    monkeypatch.setattr(rotation.secrets, "token_urlsafe", lambda _length: "new-password")
    monkeypatch.setattr(rotation, "encode_password", lambda password: f"encoded:{password}")
    monkeypatch.setattr(
        rotation,
        "_atomic_root_write",
        lambda path, _content: writes.append(path),
    )

    with pytest.raises(PreparationError, match="signing secret changed"):
        rotation.stage_rotation()
    assert writes == [rotation.CREDENTIAL_PATH]
    assert rotation.DEFAULT_OPS_AUTH_SECRET not in writes
