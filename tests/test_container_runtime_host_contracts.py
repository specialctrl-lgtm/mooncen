from __future__ import annotations

import json
import os
import shutil
import subprocess
from types import SimpleNamespace
from pathlib import Path

import pytest

from deploy.ubuntu import configure_container_pg_hba as hba
from deploy.ubuntu import mooncen_native_runtime_condition as native_condition
from deploy.docker import native_baseline


DEFAULT_HBA = b"""# PostgreSQL Client Authentication Configuration File
local   all             postgres                                peer

# TYPE  DATABASE        USER            ADDRESS                 METHOD
local   all             all                                     peer
host    all             all             127.0.0.1/32            scram-sha-256
local   replication     all                                     peer
"""


def _contract(database: str = "mooncen") -> hba.Contract:
    return hba.Contract(
        database=database,
        roles=(
            "mooncen_admin",
            "mooncen_api_login",
            "mooncen_ai_login",
            "mooncen_deployment_worker_login",
        ),
    )


def test_hba_renderer_inserts_only_exact_scram_rules_before_peer_fallback() -> None:
    rendered = hba.render_hba(DEFAULT_HBA, _contract())
    text = rendered.decode("utf-8")
    expected_block = "\n".join(
        (
            hba.BEGIN_MARKER,
            "local\tmooncen\tmooncen_admin\tscram-sha-256",
            "local\tmooncen\tmooncen_api_login\tscram-sha-256",
            "local\tmooncen\tmooncen_ai_login\tscram-sha-256",
            "local\tall\tmooncen_deployment_worker_login\treject",
            (
                "hostssl\tmooncen\tmooncen_deployment_worker_login"
                "\t127.0.0.1/32\tscram-sha-256"
            ),
            (
                "host\tall\tmooncen_deployment_worker_login"
                "\t0.0.0.0/0\treject"
            ),
            "host\tall\tmooncen_deployment_worker_login\t::/0\treject",
            hba.END_MARKER,
        )
    )
    assert expected_block in text
    assert text.index(expected_block) < text.index(
        "local   all             all                                     peer"
    )
    assert text.count("local   all             all") == 1
    assert hba.render_hba(rendered, _contract()) == rendered


def test_hba_renderer_replaces_only_its_canonical_managed_block() -> None:
    original = hba.render_hba(DEFAULT_HBA, _contract("mooncen"))
    replaced = hba.render_hba(original, _contract("mooncen_next"))
    assert b"local\tmooncen_next\tmooncen_admin\tscram-sha-256" in replaced
    assert b"local\tmooncen\tmooncen_admin\tscram-sha-256" not in replaced
    assert replaced.count(hba.BEGIN_MARKER.encode("ascii")) == 1


def test_scram_verifier_compares_matching_roles_without_boolean_text_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = _contract()

    def query(sql: str) -> str:
        assert "AND rolpassword LIKE 'SCRAM-SHA-256$%'" in sql
        assert "::text" not in sql
        return "\n".join(sorted(contract.roles))

    monkeypatch.setattr(hba, "_postgres_query", query)
    hba._verify_scram_passwords(contract)


def test_scram_verifier_rejects_a_missing_matching_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = _contract()
    monkeypatch.setattr(
        hba,
        "_postgres_query",
        lambda _sql: "\n".join(sorted(contract.roles)[:-1]),
    )

    with pytest.raises(hba.HbaContractError, match="do not have SCRAM credentials"):
        hba._verify_scram_passwords(contract)


@pytest.mark.parametrize(
    "unsafe",
    (
        b"local all postgres peer\nlocal mooncen all trust\nlocal all all peer\n",
        b"local all postgres peer\ninclude_dir conf.d\nlocal all all peer\n",
        b"local all postgres peer\n# BEGIN MOONCEN CONTAINER LOCAL SCRAM\nlocal all all peer\n",
        b"local all postgres peer\nlocal all all peer\nlocal all all peer\n",
    ),
)
def test_hba_renderer_fails_closed_on_shadowing_or_malformed_input(
    unsafe: bytes,
) -> None:
    with pytest.raises(hba.HbaContractError):
        hba.render_hba(unsafe, _contract())


def test_hba_install_restores_exact_bytes_when_live_login_probe_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hba_path = tmp_path / "pg_hba.conf"
    hba_path.write_bytes(DEFAULT_HBA)
    hba_path.chmod(0o640)
    lock_path = tmp_path / "operation.lock"
    reloads: list[bytes] = []

    monkeypatch.setattr(hba, "HBA_PATH", hba_path)
    monkeypatch.setattr(hba, "LOCK_PATH", lock_path)
    monkeypatch.setattr(hba, "_require_root", lambda: None)
    monkeypatch.setattr(hba, "_postgres_uid_gid", lambda: (os.getuid(), os.getgid()))
    monkeypatch.setattr(hba, "_require_safe_parent", lambda *_args: None)
    monkeypatch.setattr(hba, "_require_socket_contract", lambda *_args: None)
    monkeypatch.setattr(
        hba,
        "_read_server_settings",
        lambda: ("160000", str(hba_path), str(hba.SOCKET_DIRECTORY), "0777"),
    )
    monkeypatch.setattr(
        hba,
        "_open_lock",
        lambda: os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600),
    )
    monkeypatch.setattr(hba, "_reload", lambda: reloads.append(hba_path.read_bytes()))
    monkeypatch.setattr(hba, "_verify_effective_rules", lambda _contract: None)
    monkeypatch.setattr(hba, "_verify_scram_passwords", lambda _contract: None)

    def reject_login(_database: str, _role: str, _password: str) -> None:
        raise hba.HbaContractError("login rejected")

    monkeypatch.setattr(hba, "_verify_password_login", reject_login)

    with pytest.raises(hba.HbaContractError, match="login rejected"):
        hba.install(
            _contract(),
            ("a" * 32, "b" * 32, "c" * 32, "d" * 32),
        )

    assert hba_path.read_bytes() == DEFAULT_HBA
    assert len(reloads) == 2
    assert hba.BEGIN_MARKER.encode("ascii") in reloads[0]
    assert reloads[1] == DEFAULT_HBA


def test_hba_install_reports_a_failed_restore_reload_without_hiding_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hba_path = tmp_path / "pg_hba.conf"
    hba_path.write_bytes(DEFAULT_HBA)
    hba_path.chmod(0o640)
    lock_path = tmp_path / "operation.lock"
    reload_count = 0

    monkeypatch.setattr(hba, "HBA_PATH", hba_path)
    monkeypatch.setattr(hba, "LOCK_PATH", lock_path)
    monkeypatch.setattr(hba, "_require_root", lambda: None)
    monkeypatch.setattr(hba, "_postgres_uid_gid", lambda: (os.getuid(), os.getgid()))
    monkeypatch.setattr(hba, "_require_safe_parent", lambda *_args: None)
    monkeypatch.setattr(hba, "_require_socket_contract", lambda *_args: None)
    monkeypatch.setattr(
        hba,
        "_read_server_settings",
        lambda: ("160000", str(hba_path), str(hba.SOCKET_DIRECTORY), "0777"),
    )
    monkeypatch.setattr(
        hba,
        "_open_lock",
        lambda: os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600),
    )

    def reload_once() -> None:
        nonlocal reload_count
        reload_count += 1
        if reload_count == 2:
            raise hba.HbaContractError("restore reload rejected")

    monkeypatch.setattr(hba, "_reload", reload_once)
    monkeypatch.setattr(hba, "_verify_effective_rules", lambda _contract: None)
    monkeypatch.setattr(hba, "_verify_scram_passwords", lambda _contract: None)
    monkeypatch.setattr(
        hba,
        "_verify_password_login",
        lambda *_args: (_ for _ in ()).throw(hba.HbaContractError("login rejected")),
    )

    with pytest.raises(hba.HbaContractError, match="HBA rollback failed"):
        hba.install(
            _contract(),
            ("a" * 32, "b" * 32, "c" * 32, "d" * 32),
        )

    assert hba_path.read_bytes() == DEFAULT_HBA
    assert reload_count == 2


def test_effective_hba_verifier_requires_error_free_rules_before_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = _contract()
    rows = [
        {
            "auth_method": "scram-sha-256",
            "database": ["mooncen"],
            "error": None,
            "line_number": index,
            "type": "local",
            "user_name": [role],
        }
        for index, role in enumerate(contract.local_roles, start=10)
    ]
    rows.extend(
        (
            {
                "address": None,
                "auth_method": "reject",
                "database": ["all"],
                "error": None,
                "line_number": 13,
                "netmask": None,
                "type": "local",
                "user_name": [contract.worker_role],
            },
            {
                "address": "127.0.0.1",
                "auth_method": "scram-sha-256",
                "database": [contract.database],
                "error": None,
                "line_number": 14,
                "netmask": "255.255.255.255",
                "type": "hostssl",
                "user_name": [contract.worker_role],
            },
            {
                "address": "0.0.0.0",
                "auth_method": "reject",
                "database": ["all"],
                "error": None,
                "line_number": 15,
                "netmask": "0.0.0.0",
                "type": "host",
                "user_name": [contract.worker_role],
            },
            {
                "address": "::",
                "auth_method": "reject",
                "database": ["all"],
                "error": None,
                "line_number": 16,
                "netmask": "::",
                "type": "host",
                "user_name": [contract.worker_role],
            },
        )
    )
    rows.append(
        {
            "auth_method": "peer",
            "database": ["all"],
            "error": None,
            "line_number": 20,
            "type": "local",
            "user_name": ["all"],
        }
    )
    monkeypatch.setattr(hba, "_hba_rows", lambda: rows)
    hba._verify_effective_rules(contract)

    rows[0]["error"] = "invalid authentication method"
    with pytest.raises(hba.HbaContractError, match="parse error"):
        hba._verify_effective_rules(contract)
    rows[0]["error"] = None
    rows[0]["line_number"] = 21
    with pytest.raises(hba.HbaContractError, match="SCRAM rule"):
        hba._verify_effective_rules(contract)

    rows[0]["line_number"] = 10
    rows[4]["netmask"] = "255.255.255.0"
    with pytest.raises(hba.HbaContractError, match="worker HBA fence"):
        hba._verify_effective_rules(contract)


def test_worker_hba_probe_requires_tls_channel_binding_and_rejects_other_databases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = _contract()
    calls: list[tuple[str, str | None, str | None, str | None]] = []

    def login(
        database: str,
        _role: str,
        _password: str,
        *,
        host: str | None = None,
        sslmode: str | None = None,
        channel_binding: str | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append((database, host, sslmode, channel_binding))
        accepted = (
            database == contract.database
            and host == "127.0.0.1"
            and sslmode == "require"
            and channel_binding == "require"
        )
        return subprocess.CompletedProcess(
            args=("psql",),
            returncode=0 if accepted else 2,
            stdout=(
                f"{contract.worker_role}\n".encode("ascii") if accepted else b""
            ),
            stderr=b"",
        )

    monkeypatch.setattr(hba, "_password_login", login)
    monkeypatch.setattr(hba, "_postgres_query", lambda _query: "mooncen_shadow\npostgres")
    hba._verify_worker_transport(contract, "w" * 32)

    assert calls == [
        ("mooncen", "127.0.0.1", "require", "require"),
        ("mooncen", None, None, None),
        ("mooncen", "127.0.0.1", "disable", None),
        ("mooncen_shadow", "127.0.0.1", "require", "require"),
        ("postgres", "127.0.0.1", "require", "require"),
    ]

    def escaped_login(
        database: str,
        role: str,
        password: str,
        *,
        host: str | None = None,
        sslmode: str | None = None,
        channel_binding: str | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        result = login(
            database,
            role,
            password,
            host=host,
            sslmode=sslmode,
            channel_binding=channel_binding,
        )
        if database == "postgres":
            return subprocess.CompletedProcess(
                args=("psql",),
                returncode=0,
                stdout=f"{contract.worker_role}\n".encode("ascii"),
                stderr=b"",
            )
        return result

    monkeypatch.setattr(hba, "_password_login", escaped_login)
    with pytest.raises(hba.HbaContractError, match="escaped its exact database"):
        hba._verify_worker_transport(contract, "w" * 32)


def test_hba_cli_requires_four_distinct_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: list[hba.Contract] = []
    monkeypatch.setattr(hba.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(
        hba,
        "_read_passwords",
        lambda: ("a" * 32, "b" * 32, "c" * 32, "d" * 32),
    )
    monkeypatch.setattr(
        hba,
        "install",
        lambda contract, _passwords: observed.append(contract) or "f" * 64,
    )
    arguments = [
        "install",
        "--database",
        "mooncen",
        "--migrator-role",
        "mooncen_admin",
        "--api-role",
        "mooncen_api_login",
        "--ai-role",
        "mooncen_ai_login",
        "--worker-role",
        "mooncen_deployment_worker_login",
    ]
    assert hba.main(arguments) == 0
    assert observed == [_contract()]

    arguments[-1] = "mooncen_ai_login"
    assert hba.main(arguments) == 1
    assert observed == [_contract()]


def test_native_condition_allows_only_null_runtime_or_exact_restore_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "a" * 32
    status: dict[str, object] = {
        "native_intent": None,
        "schema_version": 1,
        "state": None,
        "transaction": None,
        "worker_lease": None,
    }
    monkeypatch.setattr(native_condition.os, "geteuid", lambda: 0)
    monkeypatch.setattr(native_condition, "_controller_status", lambda: status)
    native_condition.assert_native_runtime_allowed()

    status["state"] = {"active": {"release_digest": "b" * 64}}
    with pytest.raises(native_condition.NativeRuntimeConditionError):
        native_condition.assert_native_runtime_allowed()

    authorization_dir = tmp_path / "mooncen-container-release"
    authorization_dir.mkdir(mode=0o700)
    authorization = authorization_dir / "native-restore.json"
    authorization.write_text(
        f'{{"schema_version":1,"transaction_token":"{token}"}}\n',
        encoding="ascii",
    )
    authorization.chmod(0o600)
    monkeypatch.setattr(
        native_condition, "NATIVE_RESTORE_AUTHORIZATION", authorization
    )
    status["transaction"] = {"token": token}
    monkeypatch.setattr(
        native_condition, "_restore_authorization_token", lambda: token
    )
    native_condition.assert_native_runtime_allowed()

    monkeypatch.setattr(
        native_condition, "_restore_authorization_token", lambda: "c" * 32
    )
    with pytest.raises(native_condition.NativeRuntimeConditionError):
        native_condition.assert_native_runtime_allowed()


def test_native_condition_blocks_only_a_live_active_worker_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status: dict[str, object] = {
        "native_intent": None,
        "schema_version": 1,
        "state": None,
        "transaction": None,
        "worker_lease": {
            "active": True,
            "claim_epoch": 7,
            "claim_token_sha256": "b" * 64,
            "expires_epoch": 2_000,
            "job_id": "a" * 32,
            "schema_version": 1,
        },
    }
    monkeypatch.setattr(native_condition.os, "geteuid", lambda: 0)
    monkeypatch.setattr(native_condition, "_controller_status", lambda: status)
    monkeypatch.setattr(native_condition.time, "time", lambda: 1_000)

    with pytest.raises(
        native_condition.NativeRuntimeConditionError,
        match="active deployment worker lease",
    ):
        native_condition.assert_native_runtime_allowed()

    lease = status["worker_lease"]
    assert isinstance(lease, dict)
    lease["expires_epoch"] = 1_000
    native_condition.assert_native_runtime_allowed()
    lease["expires_epoch"] = 2_000
    lease["active"] = False
    native_condition.assert_native_runtime_allowed()


def test_native_condition_parses_the_exact_controller_worker_lease_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = tmp_path / "controller"
    controller.write_text("#!/bin/sh\n", encoding="ascii")
    controller.chmod(0o755)
    status = {
        "native_intent": None,
        "schema_version": 1,
        "state": None,
        "transaction": None,
        "worker_lease": {
            "active": True,
            "claim_epoch": 17,
            "claim_token_sha256": "b" * 64,
            "expires_epoch": 2_000_000_000,
            "job_id": "a" * 32,
            "schema_version": 1,
        },
    }
    monkeypatch.setattr(native_condition, "CONTROLLER", controller)
    monkeypatch.setattr(native_condition, "_safe_root_file", lambda *_args: None)

    def result() -> SimpleNamespace:
        return SimpleNamespace(
            returncode=0,
            stdout=(
                json.dumps(
                    status,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("ascii")
                + b"\n"
            ),
        )

    monkeypatch.setattr(
        native_condition.subprocess,
        "run",
        lambda *_args, **_kwargs: result(),
    )
    assert native_condition._controller_status() == status

    lease = status["worker_lease"]
    assert isinstance(lease, dict)
    lease["active"] = "true"
    with pytest.raises(
        native_condition.NativeRuntimeConditionError,
        match="worker lease is invalid",
    ):
        native_condition._controller_status()

def test_native_condition_fails_closed_without_controller_when_state_remains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = tmp_path / "missing-controller"
    state_root = tmp_path / "state"
    state_root.mkdir()
    (state_root / "active.json").write_text("{}\n", encoding="ascii")
    monkeypatch.setattr(native_condition, "CONTROLLER", controller)
    monkeypatch.setattr(native_condition, "STATE_ROOT", state_root)
    with pytest.raises(
        native_condition.NativeRuntimeConditionError,
        match="runtime state exists without",
    ):
        native_condition._controller_status()


def _write_native_deploy_authorization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    phase: str = "activated",
    mode: str = "candidate",
    intent_token: str = "b" * 32,
    arm_boot_id: str = "11111111-1111-4111-8111-111111111111",
    authorization_boot_id: str | None = None,
    arm_deadline_epoch: int = 9_999_999_999,
) -> tuple[str, str]:
    token = "a" * 32
    boot_id = authorization_boot_id or arm_boot_id
    lock = tmp_path / ".mooncen-deploy.lock"
    lock.mkdir(mode=0o700)
    (lock / "token").write_text(f"{token}\n", encoding="ascii")
    (lock / "token").chmod(0o600)
    journal = {
        "VERSION": "1",
        "TOKEN": token,
        "PHASE": phase,
        "REMOTE_DIR": "/opt/mooncen",
        "RELEASE_DIR": f"/opt/.mooncen-release-{token}",
        "PREVIOUS_DIR": f"/opt/.mooncen-previous-{token}",
        "FAILED_DIR": f"/opt/.mooncen-failed-{token}",
        "HEARTBEAT": f"/opt/.mooncen-deploy-heartbeat-{token}",
        "EXPECTED_COMMIT": "c" * 40,
        "HAD_ACTIVE": "1",
        "ARM_BOOT_ID": arm_boot_id,
        "DEADLINE_EPOCH": str(arm_deadline_epoch),
        "NATIVE_INTENT_TOKEN": intent_token,
    }
    (lock / "journal.env").write_text(
        "".join(f"{key}={value}\n" for key, value in journal.items()),
        encoding="ascii",
    )
    (lock / "journal.env").chmod(0o600)
    authorization_directory = tmp_path / "native-start"
    authorization_directory.mkdir(mode=0o700)
    authorization = authorization_directory / "authorization.json"
    authorization.write_text(
        json.dumps(
            {
                "arm_boot_id": arm_boot_id,
                "arm_deadline_epoch": arm_deadline_epoch,
                "authorization_boot_id": boot_id,
                "authorization_deadline_epoch": 1_900_000_120,
                "guard_token": token,
                "intent_token": intent_token,
                "mode": mode,
                "phase": phase,
                "schema_version": 1,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="ascii",
    )
    authorization.chmod(0o600)
    boot_id_path = tmp_path / "boot_id"
    boot_id_path.write_text(f"{boot_id}\n", encoding="ascii")
    monkeypatch.setattr(native_condition, "NATIVE_DEPLOY_LOCK", lock)
    monkeypatch.setattr(
        native_condition,
        "NATIVE_DEPLOY_AUTHORIZATION_DIRECTORY",
        authorization_directory,
    )
    monkeypatch.setattr(
        native_condition,
        "NATIVE_DEPLOY_AUTHORIZATION",
        authorization,
    )
    monkeypatch.setattr(native_condition, "BOOT_ID_PATH", boot_id_path)
    monkeypatch.setattr(native_condition, "_safe_root_file", lambda *_args: None)
    monkeypatch.setattr(
        native_condition,
        "_safe_root_directory",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(native_condition.time, "time", lambda: 1_900_000_000)
    return token, intent_token


def test_native_condition_requires_exact_live_deploy_authorization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _token, intent_token = _write_native_deploy_authorization(
        tmp_path,
        monkeypatch,
    )
    status = {
        "native_intent": {"schema_version": 1, "token": intent_token},
        "schema_version": 1,
        "state": None,
        "transaction": None,
        "worker_lease": None,
    }
    monkeypatch.setattr(native_condition.os, "geteuid", lambda: 0)
    monkeypatch.setattr(native_condition, "_controller_status", lambda: status)
    observed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        native_condition,
        "_guard_is_running",
        lambda token, *, mode: observed.append((token, mode)),
    )

    native_condition.assert_native_runtime_allowed()
    assert observed == [("a" * 32, "candidate")]

    status["native_intent"] = {"schema_version": 1, "token": "d" * 32}
    with pytest.raises(
        native_condition.NativeRuntimeConditionError,
        match="authorization is invalid",
    ):
        native_condition.assert_native_runtime_allowed()


def test_native_condition_denies_missing_stale_or_unattended_deploy_auth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _token, intent_token = _write_native_deploy_authorization(
        tmp_path,
        monkeypatch,
    )
    status = {
        "native_intent": {"schema_version": 1, "token": intent_token},
        "schema_version": 1,
        "state": None,
        "transaction": None,
        "worker_lease": None,
    }
    monkeypatch.setattr(native_condition.os, "geteuid", lambda: 0)
    monkeypatch.setattr(native_condition, "_controller_status", lambda: status)
    monkeypatch.setattr(
        native_condition,
        "_guard_is_running",
        lambda *_args, **_kwargs: None,
    )

    native_condition.BOOT_ID_PATH.write_text(
        "22222222-2222-4222-8222-222222222222\n",
        encoding="ascii",
    )
    with pytest.raises(native_condition.NativeRuntimeConditionError, match="stale"):
        native_condition.assert_native_runtime_allowed()

    native_condition.BOOT_ID_PATH.write_text(
        "11111111-1111-4111-8111-111111111111\n",
        encoding="ascii",
    )
    monkeypatch.setattr(native_condition.time, "time", lambda: 10_000_000_000)
    with pytest.raises(native_condition.NativeRuntimeConditionError, match="stale"):
        native_condition.assert_native_runtime_allowed()


def test_native_condition_accepts_activating_guard_only_during_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        native_condition.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=b"activating\n",
        ),
    )
    with pytest.raises(
        native_condition.NativeRuntimeConditionError,
        match="not running",
    ):
        native_condition._guard_is_running("a" * 32, mode="candidate")
    native_condition._guard_is_running("a" * 32, mode="recovery")


@pytest.mark.parametrize("partial_name", ("state", "receipt", "library"))
def test_native_condition_allows_only_a_clean_controller_absent_bootstrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    partial_name: str,
) -> None:
    controller = tmp_path / "missing-controller"
    state_root = tmp_path / "state"
    receipt = tmp_path / "receipt.json"
    library = tmp_path / "controller-library"
    monkeypatch.setattr(native_condition, "CONTROLLER", controller)
    monkeypatch.setattr(native_condition, "STATE_ROOT", state_root)
    monkeypatch.setattr(native_condition, "INSTALLATION_RECEIPT", receipt)
    monkeypatch.setattr(native_condition, "CONTROLLER_LIBRARY", library)

    assert native_condition._controller_status() is None
    if partial_name == "state":
        state_root.mkdir()
    elif partial_name == "receipt":
        receipt.write_text("{}\n", encoding="ascii")
    else:
        library.mkdir()
    with pytest.raises(
        native_condition.NativeRuntimeConditionError,
        match="runtime state exists without",
    ):
        native_condition._controller_status()


def test_native_systemd_units_run_the_root_condition() -> None:
    root = Path(__file__).resolve().parents[1]
    for unit_name in (
        "mooncen-api.service",
        "mooncen-frontend.service",
        "mooncen-ai-worker.service",
    ):
        unit = (root / "deploy/ubuntu/systemd" / unit_name).read_text(
            encoding="utf-8"
        )
        assert (
            "ExecCondition=+/usr/local/libexec/mooncen-native-runtime-condition"
            in unit
        )
        assert "Environment=PYTHONDONTWRITEBYTECODE=1" in unit


def test_container_systemd_units_create_the_private_runtime_directory() -> None:
    root = Path(__file__).resolve().parents[1]
    for unit_name in (
        "mooncen-container-stack.service",
        "mooncen-container-release-guard@.service",
    ):
        unit = (root / "deploy/ubuntu/systemd" / unit_name).read_text(
            encoding="utf-8"
        )
        assert "RuntimeDirectory=mooncen-container-release" in unit
        assert "RuntimeDirectoryMode=0700" in unit
        assert "RuntimeDirectoryPreserve=yes" in unit
        assert "ReadWritePaths=-/run/mooncen-container-release" in unit


@pytest.mark.skipif(
    shutil.which("systemd-analyze") is None,
    reason="systemd-analyze unavailable",
)
def test_container_systemd_sandboxes_pass_the_offline_unit_parser() -> None:
    root = Path(__file__).resolve().parents[1]
    units = [
        root / "deploy/ubuntu/systemd/mooncen-container-stack.service",
        root
        / "deploy/ubuntu/systemd/mooncen-container-release-guard@.service",
        root / "deploy/ubuntu/systemd/mooncen-api.service",
        root / "deploy/ubuntu/systemd/mooncen-frontend.service",
        root / "deploy/ubuntu/systemd/mooncen-ai-worker.service",
        root / "deploy/an2p/mooncen-an2p-runtime-recovery.service",
        root / "deploy/an2p/mooncen-ops-api.service",
        root / "deploy/an2p/mooncen-deployment-worker.service",
        root / "deploy/an2p/mooncen-docker-dev.service",
    ]
    completed = subprocess.run(
        [
            shutil.which("systemd-analyze") or "systemd-analyze",
            "security",
            "--offline=yes",
            *(str(unit) for unit in units),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert "mooncen-container-stack.service" in completed.stdout
    assert "mooncen-container-release-guard@i.service" in completed.stdout
    assert "mooncen-an2p-runtime-recovery.service" in completed.stdout
    assert "mooncen-docker-dev.service" in completed.stdout


def test_direct_native_deploy_holds_intent_through_guard_terminal() -> None:
    root = Path(__file__).resolve().parents[1]
    deploy = (root / "deploy/ubuntu/deploy_from_windows.ps1").read_text(
        encoding="utf-8"
    )
    guard = (root / "deploy/ubuntu/mooncen_release_guard.sh").read_text(
        encoding="utf-8"
    )
    assert "[string]$DeploymentIntentToken" in deploy
    assert 'native-begin "$intent_token"' in deploy
    assert "partial container runtime exists without its root controller" in deploy
    assert deploy.index("$beginNativeIntentScript = @'") < deploy.index(
        "$lockAndCleanupScript = @'"
    )
    assert "NATIVE_INTENT_TOKEN=%s" in deploy
    assert "native deployment intent remains fenced after pre-guard failure" in deploy
    assert "native_intent_token='__NATIVE_INTENT_TOKEN__'" in deploy
    assert "is_container_runtime_unit" in deploy

    finish = guard.split("finish_lock() {", 1)[1].split(
        "backup_systemd_units() {", 1
    )[0]
    assert finish.index("sync_recovery_filesystems") < finish.index(
        'end_native_intent "$NATIVE_INTENT_TOKEN"'
    ) < finish.index('mv -T -- "$lock_dir" "$finalized_lock"')
    assert "BOOTSTRAP_NATIVE_INTENT_TOKEN" in guard
    assert 'end_native_intent "$BOOTSTRAP_NATIVE_INTENT_TOKEN"' in guard
    assert "is_container_runtime_unit_name" in guard
    installer = (root / "deploy/docker/install_production_runtime.sh").read_text(
        encoding="utf-8"
    )
    for source in (deploy, guard, installer):
        assert "/var/lib/mooncen-runtime-transition" in source
        assert "native-bootstrap-intent.json" in source
    assert 'sudo /usr/bin/flock -x "$transition_lock"' in deploy
    assert "/usr/bin/flock -x 8" in installer
    assert "an active first-bootstrap native deployment" in installer


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash unavailable")
def test_first_bootstrap_native_intent_wins_installer_flock_without_toctou(
    tmp_path: Path,
) -> None:
    import grp
    import pwd
    import time

    root = Path(__file__).resolve().parents[1]
    installer = (root / "deploy/docker/install_production_runtime.sh").read_text(
        encoding="utf-8"
    )
    gate = 'if [ -e "$transition_root"' + installer.split(
        'if [ -e "$transition_root"', 1
    )[1].split("for relative in \\", 1)[0]
    identity = (
        f"{pwd.getpwuid(os.getuid()).pw_name}:"
        f"{grp.getgrgid(os.getgid()).gr_name}"
    )
    transition_root = tmp_path / "transition"
    transition_root.mkdir(mode=0o700)
    transition_lock = transition_root / "control.lock"
    transition_lock.write_bytes(b"")
    transition_lock.chmod(0o600)
    intent = transition_root / "native-bootstrap-intent.json"
    ready = tmp_path / "writer-ready"
    writer = subprocess.Popen(
        [
            shutil.which("bash") or "bash",
            "-c",
            """
set -euo pipefail
exec 7<>"$1"
flock -x 7
: >"$2"
sleep 0.2
printf '%s\n' '{"schema_version":1,"token":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}' >"$3"
chmod 0600 "$3"
sync -f -- "$3"
sleep 0.2
""",
            "bootstrap-writer",
            str(transition_lock),
            str(ready),
            str(intent),
        ],
        cwd=root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    for _attempt in range(100):
        if ready.exists():
            break
        time.sleep(0.01)
    assert ready.exists()
    marker = tmp_path / "mutated"
    deploy_lock = tmp_path / "deploy.lock"
    script = (
        "set -euo pipefail\n"
        "die() { printf '%s\\n' \"$*\" >&2; exit 64; }\n"
        f"transition_root={str(transition_root)!r}\n"
        'transition_lock="$transition_root/control.lock"\n'
        'bootstrap_native_intent="$transition_root/native-bootstrap-intent.json"\n'
        + gate.replace("root:root:700", f"{identity}:700")
        .replace("root:root:600", f"{identity}:600")
        .replace("/opt/.mooncen-deploy.lock", str(deploy_lock))
        + f"printf mutated >{str(marker)!r}\n"
    )
    completed = subprocess.run(
        [shutil.which("bash") or "bash", "-c", script],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    writer.wait(timeout=5)
    assert completed.returncode == 64
    assert "active first-bootstrap native deployment" in completed.stderr
    assert not marker.exists()


def test_root_runtime_gates_have_exact_sandbox_write_paths() -> None:
    root = Path(__file__).resolve().parents[1]
    for name in (
        "mooncen-api.service",
        "mooncen-frontend.service",
        "mooncen-ai-worker.service",
    ):
        unit = (root / "deploy/ubuntu/systemd" / name).read_text(encoding="utf-8")
        assert "ProtectSystem=strict" in unit
        assert "ReadWritePaths=-/var/lib/mooncen-container-release" in unit
        assert "ReadWritePaths=-/run/mooncen-container-release" in unit

    for name in (
        "mooncen-ops-api.service",
        "mooncen-deployment-worker.service",
        "mooncen-docker-dev.service",
    ):
        unit = (root / "deploy/an2p" / name).read_text(encoding="utf-8")
        assert "ProtectSystem=strict" in unit
        assert "gate-service-start" in unit
        assert "ReadWritePaths=/var/lib/mooncen-an2p-runtime" in unit
        assert "ReadWritePaths=/run/mooncen-an2p-runtime" in unit


def test_container_start_timeouts_cover_the_bounded_controller_operations() -> None:
    root = Path(__file__).resolve().parents[1]
    production = (
        root / "deploy/ubuntu/systemd/mooncen-container-stack.service"
    ).read_text(encoding="utf-8")
    development = (root / "deploy/an2p/mooncen-docker-dev.service").read_text(
        encoding="utf-8"
    )
    manager = (root / "deploy/an2p/runtime_pair_manager.py").read_text(
        encoding="utf-8"
    )
    selector = (root / "deploy/an2p/mooncen_an2p_service_control.py").read_text(
        encoding="utf-8"
    )

    assert "TimeoutStartSec=2400s" in production
    assert "TimeoutStartSec=1800" in development
    assert "timeout=1860" in manager
    assert "timeout=1860" in selector


def test_native_operator_cannot_bypass_ops_claim_for_maintenance_transition() -> None:
    root = Path(__file__).resolve().parents[1]
    sudoers = (root / "deploy/ubuntu/install_sudoers.sh").read_text(
        encoding="utf-8"
    )
    document = (root / "docs/docker-production.md").read_text(encoding="utf-8")
    assert "${CONTAINER_CONTROLLER} rollback-native" not in sudoers
    assert "${CONTAINER_CONTROLLER} lease-bind" not in sudoers
    assert "${CONTAINER_CONTROLLER} native-begin ${CONTAINER_TOKEN_ARG}" in sudoers
    assert "${CONTAINER_CONTROLLER} native-end ${CONTAINER_TOKEN_ARG}" in sudoers
    assert "rollback-native <10digit" not in document
    assert (
        "rollback-native GENERATION10 EXPECTED_ACTIVE64 EXPECTED_PREVIOUS64 "
        "EXPECTED_STATE_SHA25664 \\" in document
    )
    assert "JOB32 EPOCH20 TOKEN32" in document
    assert "mooncen-container-release rollback\n```" not in document
    normalized_document = " ".join(document.split())
    assert "한 CAS tuple으로 두 명령을 연속 실행하지 않는다" in normalized_document


def test_postgresql_scram_setup_and_review_policy_are_pinned() -> None:
    root = Path(__file__).resolve().parents[1]
    setup = (root / "deploy/ubuntu/setup_project.sh").read_text(encoding="utf-8")
    roles = (root / "DB/provision_login_roles.sql").read_text(encoding="utf-8")
    assert "SET password_encryption = 'scram-sha-256';" in setup
    assert "SET LOCAL password_encryption = 'scram-sha-256';" in roles
    assert "mooncen-configure-container-pg-hba" in setup
    assert "printf '%s\\n%s\\n%s\\n%s\\n'" in setup
    for password in (
        '"$DB_PASSWORD"',
        '"$DB_API_PASSWORD"',
        '"$DB_AI_PASSWORD"',
        '"$DB_DEPLOYMENT_WORKER_PASSWORD"',
    ):
        assert password in setup
    assert '--migrator-role "$DB_MIGRATOR_USER"' in setup
    assert '--api-role "$DB_API_USER"' in setup
    assert '--ai-role "$DB_AI_USER"' in setup
    assert '--worker-role "$DB_DEPLOYMENT_WORKER_USER"' in setup

    guard = (root / "deploy/ubuntu/mooncen_release_guard.sh").read_text(
        encoding="utf-8"
    )
    assert "file-postgres" in guard
    assert (
        '"$(stat -c \'%U:%G:%a\' -- "$path")" = postgres:postgres:640'
        in guard
    )

    from deploy.docker.production_runtime_integrity import BUILD_POLICY_PATHS
    from deploy.docker.verify_clean_source import REQUIRED_CONTROL_PATHS

    required = {
        "DB/connection_settings.py",
        "DB/provision_login_roles.sql",
        "deploy/ubuntu/configure_container_pg_hba.py",
        "deploy/ubuntu/deploy_from_windows.ps1",
        "deploy/ubuntu/mooncen_native_runtime_condition.py",
        "deploy/ubuntu/mooncen_release_guard.sh",
        "deploy/ubuntu/systemd/mooncen-api.service",
        "deploy/ubuntu/systemd/mooncen-frontend.service",
        "deploy/ubuntu/systemd/mooncen-ai-worker.service",
        "deploy/ubuntu/systemd/mooncen-deploy-guard@.service",
    }
    assert required.issubset(BUILD_POLICY_PATHS)
    assert required.issubset(REQUIRED_CONTROL_PATHS)

    installer = (root / "deploy/docker/install_production_runtime.sh").read_text(
        encoding="utf-8"
    )
    assert '"$state_root/native-intent.json"' in installer
    assert "native deployment intent blocks controller replacement" in installer
    assert '"$state_root/active.json"' in installer
    assert "active Docker runtime blocks controller replacement" in installer
    assert '"$state_root/worker-lease.json"' in installer
    assert "deployment worker lease blocks controller replacement" in installer
    for installed_path in (
        "/usr/local/libexec/mooncen-configure-container-pg-hba",
        "/usr/local/libexec/mooncen-native-runtime-condition",
        "/etc/systemd/system/mooncen-api.service",
        "/etc/systemd/system/mooncen-frontend.service",
        "/etc/systemd/system/mooncen-ai-worker.service",
        "/etc/systemd/system/mooncen-deploy-guard@.service",
    ):
        assert installed_path in installer


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash unavailable")
@pytest.mark.parametrize("active_kind", ("file", "symlink"))
def test_runtime_installer_active_state_gate_precedes_installed_byte_mutation(
    tmp_path: Path,
    active_kind: str,
) -> None:
    import grp
    import pwd

    root = Path(__file__).resolve().parents[1]
    installer = (root / "deploy/docker/install_production_runtime.sh").read_text(
        encoding="utf-8"
    )
    start = installer.index(
        'install -d -o root -g root -m 0700 "$release_root" "$state_root"'
    )
    end = installer.index(
        "\ninstall -d -o root -g root -m 0755 /usr/local/libexec", start
    )
    gate = installer[start:end]
    user_group = (
        f"{pwd.getpwuid(os.getuid()).pw_name}:"
        f"{grp.getgrgid(os.getgid()).gr_name}"
    )
    gate = gate.replace("-o root -g root ", "")
    gate = gate.replace("root:root:600", f"{user_group}:600")

    release_root = tmp_path / "releases"
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    active = state_root / "active.json"
    if active_kind == "file":
        active.write_text("do-not-touch\n", encoding="ascii")
    else:
        active.symlink_to(tmp_path / "missing-active-target")
    marker = tmp_path / "installed-byte-mutation"
    script = (
        "set -euo pipefail\n"
        "die() { printf '%s\\n' \"$*\" >&2; exit 64; }\n"
        f"release_root={release_root.as_posix()!r}\n"
        f"state_root={state_root.as_posix()!r}\n"
        'control_lock="$state_root/control.lock"\n'
        f"{gate}\n"
        f"printf 'mutated\\n' > {marker.as_posix()!r}\n"
    )

    completed = subprocess.run(
        [shutil.which("bash") or "bash", "-c", script],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 64
    assert "active Docker runtime blocks controller replacement" in completed.stderr
    assert not marker.exists()
    if active_kind == "file":
        assert active.read_text(encoding="ascii") == "do-not-touch\n"
    else:
        assert active.is_symlink()


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash unavailable")
@pytest.mark.parametrize(
    ("active", "expiry_offset", "extra_key", "allowed"),
    (
        (True, 600, False, False),
        (True, -1, False, True),
        (False, 600, False, True),
        (False, 600, True, False),
    ),
)
def test_runtime_installer_blocks_only_a_valid_live_worker_lease_before_mutation(
    tmp_path: Path,
    active: bool,
    expiry_offset: int,
    extra_key: bool,
    allowed: bool,
) -> None:
    import grp
    import pwd
    import time

    root = Path(__file__).resolve().parents[1]
    installer = (root / "deploy/docker/install_production_runtime.sh").read_text(
        encoding="utf-8"
    )
    start = installer.index(
        'install -d -o root -g root -m 0700 "$release_root" "$state_root"'
    )
    end = installer.index(
        "\ninstall -d -o root -g root -m 0755 /usr/local/libexec", start
    )
    gate = installer[start:end]
    user_group = (
        f"{pwd.getpwuid(os.getuid()).pw_name}:"
        f"{grp.getgrgid(os.getgid()).gr_name}"
    )
    gate = gate.replace("-o root -g root ", "")
    gate = gate.replace("root:root:600", f"{user_group}:600")
    release_root = tmp_path / "releases"
    state_root = tmp_path / "state"
    state_root.mkdir(mode=0o700)
    lease: dict[str, object] = {
        "active": active,
        "claim_epoch": 7,
        "claim_token_sha256": "b" * 64,
        "expires_epoch": int(time.time()) + expiry_offset,
        "job_id": "a" * 32,
        "schema_version": 1,
    }
    if extra_key:
        lease["unexpected"] = True
    lease_path = state_root / "worker-lease.json"
    lease_path.write_text(
        json.dumps(lease, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="ascii",
    )
    lease_path.chmod(0o600)
    marker = tmp_path / "installed-byte-mutation"
    script = (
        "set -euo pipefail\n"
        "die() { printf '%s\\n' \"$*\" >&2; exit 64; }\n"
        f"release_root={release_root.as_posix()!r}\n"
        f"state_root={state_root.as_posix()!r}\n"
        'control_lock="$state_root/control.lock"\n'
        f"{gate}\n"
        f"printf 'mutated\\n' > {marker.as_posix()!r}\n"
    )
    completed = subprocess.run(
        [shutil.which("bash") or "bash", "-c", script],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert marker.exists() is allowed
    if allowed:
        assert completed.returncode == 0, completed.stderr
    else:
        assert completed.returncode == 64
        assert "deployment worker lease blocks controller replacement" in completed.stderr


def test_native_baseline_seals_the_actual_executable_tree_after_setup(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    setup = (root / "deploy/ubuntu/setup_project.sh").read_text(encoding="utf-8")
    seal = setup.index('immutable_tree_sha256="$(')
    for mutation in (
        'sudo chown -R root:"$APP_GROUP" "$APP_DIR"',
        'sudo chmod 0640 "$sitemap_file"',
        'sudo chmod -R u+rwX,go-rwx "$APP_DIR/logs"',
        'sudo chmod -R u+rwX,g+rX,g-w,o-rwx "$APP_DIR/failover"',
        'sudo chmod 0644 "$APP_DIR/.env"',
    ):
        assert setup.index(mutation) < seal
    assert 'sudo chown -R "$APP_USER":"$APP_GROUP" "$APP_DIR"' not in setup

    application = tmp_path / "mooncen"
    executable_inputs = {
        ".venv/lib/python3.12/site-packages/example.pyc": b"bytecode-a",
        "frontend2/node_modules/example/index.js": b"node-a",
        "backend/main.py": b"source-a",
    }
    for relative, content in executable_inputs.items():
        path = application / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    runtime_log = application / "logs/runtime.log"
    runtime_log.parent.mkdir()
    runtime_log.write_text("mutable-a\n", encoding="ascii")
    marker = application / ".mooncen-prebuilt-release"
    marker.write_text("PREBUILD_VERSION=1\n", encoding="ascii")

    sealed = native_baseline.inventory_sha256(application)
    marker.write_text(
        f"PREBUILD_VERSION=1\nIMMUTABLE_TREE_SHA256={sealed}\n",
        encoding="ascii",
    )
    marker.chmod(0o600)
    assert native_baseline.inventory_sha256(application) == sealed

    runtime_log.write_text("mutable-b\n", encoding="ascii")
    assert native_baseline.inventory_sha256(application) == sealed

    for relative, content in executable_inputs.items():
        path = application / relative
        original = path.read_bytes()
        path.write_bytes(content + b"-tampered")
        assert native_baseline.inventory_sha256(application) != sealed
        path.write_bytes(original)
        assert native_baseline.inventory_sha256(application) == sealed
