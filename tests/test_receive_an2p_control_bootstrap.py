from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest

from deploy.an2p import receive_control_bootstrap as receiver
from deploy.ubuntu import export_an2p_control_secrets as exporter
from tools import prepare_an2p_ops_control as prepare


PAIR = f"runtime-pair.{'1' * 40}.{'2' * 40}.{'3' * 64}"
TARGET_IDENTITY = "8" * 64
OPS_HASH = f"pbkdf2_sha256$600000${'s' * 16}${'a' * 64}"


def _canonical(value: dict[str, object]) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")
        + b"\n"
    )


def _pending(path: Path) -> None:
    value = {
        "environment": "development",
        "environment_sha256": "4" * 64,
        "pair": PAIR,
        "receipt_digest": "5" * 64,
        "release_digest": "6" * 64,
        "schema_version": 1,
        "source_tree": "2" * 40,
        "target": "an2p-dev",
        "target_identity": TARGET_IDENTITY,
    }
    path.write_bytes(_canonical(value))
    path.chmod(0o600)


def _production_inputs(tmp_path: Path) -> tuple[Path, Path, str]:
    direct = {name: f"value-{name.lower()}" for name in exporter.DIRECT_SOURCE_NAMES}
    direct.update(
        {
            "DB_API_USER": "mooncen_api_login",
            "DB_DEPLOYMENT_WORKER_USER": "mooncen_deployment_worker_login",
            "DB_NAME": "mooncen",
        }
    )
    paired = {
        name: f"value-{name.lower().replace('_', '-')}-independent"
        for name in exporter.PAIRED_SOURCE_NAMES
    }
    public_auth = "public-auth-secret-that-must-never-cross-the-control-pipe"
    paired.update(
        {
            "AUTH_SECRET": public_auth,
            "DB_API_PASSWORD": "api-password-independent-canary",
            "DB_DEPLOYMENT_WORKER_PASSWORD": "worker-password-independent-canary",
            "DB_PASSWORD": "owner-password-independent-canary",
            "DB_CRAWLER_PASSWORD": "crawler-password-independent-canary",
            "DB_AI_PASSWORD": "ai-password-independent-canary",
            "PRIMARY_DB_PASSWORD": "primary-password-independent-canary",
            "DB_BACKUP_PASSWORD": "backup-password-independent-canary",
            "DB_CHECK_PASSWORD": "check-password-independent-canary",
            "MOONCEN_OPS_LOGIN_ID": "opsadmin",
            "MOONCEN_OPS_PASSWORD_HASH": OPS_HASH,
        }
    )
    values = dict(direct)
    for name, value in paired.items():
        values[name] = value
        values[f"{name}_B64"] = base64.b64encode(value.encode()).decode("ascii")
    assert set(values) == exporter.EXPECTED_SOURCE_NAMES
    secrets_path = tmp_path / "deploy-secrets.env"
    identity_path = tmp_path / "target-identity"
    secrets_path.write_text(
        "".join(f"{name}={values[name]}\n" for name in sorted(values)),
        encoding="utf-8",
    )
    identity_path.write_text(f"{TARGET_IDENTITY}\n", encoding="ascii")
    return secrets_path, identity_path, public_auth


def _allow_fixture_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    original_receiver_assert = receiver._assert_private_file
    monkeypatch.setattr(
        receiver,
        "_assert_private_file",
        lambda path, **_kwargs: original_receiver_assert(
            path,
            uid=os.getuid(),
            gid=os.getgid(),
        ),
    )
    monkeypatch.setattr(receiver.os, "fchown", lambda *_args: None)
    monkeypatch.setattr(
        receiver,
        "_ensure_bootstrap_root",
        lambda root: root.mkdir(mode=0o700, parents=True, exist_ok=True),
    )
    monkeypatch.setattr(exporter, "_assert_root_private_file", lambda path, *, label: path)
    monkeypatch.setattr(prepare, "_assert_root_private_file", lambda path: path)


def test_export_receive_prepare_pipeline_uses_independent_local_ops_secret(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _allow_fixture_metadata(monkeypatch)
    monkeypatch.setattr(receiver.secrets, "token_urlsafe", lambda _bytes: "z" * 64)
    source, identity, public_auth = _production_inputs(tmp_path)
    pending = tmp_path / "pending.json"
    bootstrap = tmp_path / "bootstrap"
    _pending(pending)

    payload = exporter.render_control_envelope(source, identity)
    assert b"AUTH_SECRET=" not in payload
    assert public_auth.encode() not in payload
    assert len(payload.splitlines()) == 8

    assert receiver.receive(
        PAIR,
        "control-secrets.env",
        payload,
        pending_path=pending,
        bootstrap_root=bootstrap,
        pair_root=tmp_path / "pairs",
    )
    assert not receiver.receive(
        PAIR,
        "control-secrets.env",
        payload,
        pending_path=pending,
        bootstrap_root=bootstrap,
        pair_root=tmp_path / "pairs",
    )

    values = prepare.load_protected_values(bootstrap / "control-secrets.env")
    ops_secret = prepare.load_ops_auth_secret(bootstrap / "ops-auth-secret", values)
    api, worker = prepare.render_environments(values, ops_auth_secret=ops_secret)
    assert ops_secret == "z" * 64
    assert f"AUTH_SECRET={ops_secret}\n" in api
    assert public_auth not in api
    assert "AUTH_SECRET=" not in worker
    for path in (bootstrap / "control-secrets.env", bootstrap / "ops-auth-secret"):
        assert path.stat().st_mode & 0o777 == 0o600


def test_receiver_rejects_contamination_identity_drift_and_different_residue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _allow_fixture_metadata(monkeypatch)
    monkeypatch.setattr(receiver.secrets, "token_urlsafe", lambda _bytes: "z" * 64)
    source, identity, _public_auth = _production_inputs(tmp_path)
    pending = tmp_path / "pending.json"
    bootstrap = tmp_path / "bootstrap"
    _pending(pending)
    payload = exporter.render_control_envelope(source, identity)

    with pytest.raises(receiver.ReceiveError, match="control envelope"):
        receiver.receive(
            PAIR,
            "control-secrets.env",
            b"# browser re-auth required\n" + payload,
            pending_path=pending,
            bootstrap_root=bootstrap,
        )
    with pytest.raises(receiver.ReceiveError, match="phase 1"):
        receiver.receive(
            PAIR,
            "control-secrets.env",
            payload.replace(TARGET_IDENTITY.encode(), b"9" * 64),
            pending_path=pending,
            bootstrap_root=bootstrap,
        )
    with pytest.raises(receiver.ReceiveError, match="phase 1"):
        receiver.receive(
            PAIR,
            "control-secrets.env",
            payload.replace(
                b"DB_DEPLOYMENT_WORKER_USER=mooncen_deployment_worker_login\n",
                b"DB_DEPLOYMENT_WORKER_USER=custom_worker_login\n",
            ),
            pending_path=pending,
            bootstrap_root=bootstrap,
        )

    receiver.receive(
        PAIR,
        "control-secrets.env",
        payload,
        pending_path=pending,
        bootstrap_root=bootstrap,
    )
    changed = payload.replace(b"api-password-independent-canary", b"api-password-independent-change")
    with pytest.raises(receiver.ReceiveError, match="different bootstrap residue"):
        receiver.receive(
            PAIR,
            "control-secrets.env",
            changed,
            pending_path=pending,
            bootstrap_root=bootstrap,
        )


def test_receiver_rejects_regular_file_stdin(tmp_path: Path) -> None:
    source = tmp_path / "forbidden-regular-input"
    source.write_bytes(b"secret\n")
    with source.open("rb") as stream:
        with pytest.raises(receiver.ReceiveError, match="pipe or socket"):
            receiver._read_protected_stdin(stream)


def test_private_key_distinctness_uses_derived_public_blob_not_file_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bootstrap = tmp_path / "bootstrap"
    bootstrap.mkdir()
    incoming = bootstrap / ".deploy-new"
    existing = bootstrap / "status-id_ed25519"
    incoming.write_bytes(b"different-private-serialization-one\n")
    existing.write_bytes(b"different-private-serialization-two\n")
    existing.chmod(0o600)
    monkeypatch.setattr(receiver, "_assert_private_file", lambda path: path.read_bytes())
    monkeypatch.setattr(
        receiver,
        "_public_key_blob",
        lambda _path, *, expected_comment: b"same-ed25519-blob",
    )

    with pytest.raises(receiver.ReceiveError, match="must be distinct"):
        receiver._validate_key_distinctness(
            "deploy-id_ed25519",
            incoming,
            root=bootstrap,
        )


def test_transport_template_drift_is_rejected_before_publication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        receiver,
        "_pending",
        lambda _pair, *, path: {
            "source_tree": "2" * 40,
            "target_identity": TARGET_IDENTITY,
        },
    )
    monkeypatch.setattr(receiver, "_ensure_bootstrap_root", lambda _root: None)
    monkeypatch.setattr(
        receiver,
        "_template_payload",
        lambda _pair, _name, *, pair_root: b"reviewed-config\n",
    )
    monkeypatch.setattr(
        receiver,
        "_atomic_private_install",
        lambda *_args, **_kwargs: pytest.fail("drifted bytes must not publish"),
    )

    with pytest.raises(receiver.ReceiveError, match="differ from the reviewed pair"):
        receiver.receive(
            PAIR,
            "deploy-ssh_config",
            b"drifted-config\n",
            pending_path=tmp_path / "pending",
            bootstrap_root=tmp_path / "bootstrap",
            pair_root=tmp_path / "pairs",
        )
