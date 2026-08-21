from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from tools import provision_crawler_worker_pair as pair


ROOT = Path(__file__).resolve().parents[1]
AGENT_ID = "d640b43e-369e-4a7c-a9ed-5ff609b9fb62"


def _environments() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    schema = {
        "OPS_CRAWLER_SCHEMA_DB_HOST": "gen1db",
        "OPS_CRAWLER_SCHEMA_DB_PORT": "5432",
        "OPS_CRAWLER_SCHEMA_DB_NAME": "mooncen_staging",
        "OPS_CRAWLER_SCHEMA_DB_USER": "mooncen_schema_admin",
        "OPS_CRAWLER_SCHEMA_DB_PASSWORD": "A9_schema-password-with-32-characters_",
        "OPS_CRAWLER_SCHEMA_OBJECT_OWNER": "mooncen_staging_owner",
        "DB_SSLMODE": "verify-full",
    }
    identity = {
        "OPS_CRAWLER_WORKER_ID": "wtr-linux",
        "OPS_AGENT_ID": AGENT_ID,
        "OPS_CRAWLER_WORKER_HOSTNAME": "sgm-standard-pc-i440fx-piix-1996",
        "ENVIRONMENT": "production",
        "DB_SSLMODE": "verify-full",
    }
    worker_password = "A9_worker-password-with-32-characters_"
    worker = {
        **identity,
        "OPS_CRAWLER_SHARED_DB_HOST": "gen1db",
        "OPS_CRAWLER_SHARED_DB_PORT": "5432",
        "OPS_CRAWLER_SHARED_DB_NAME": "mooncen_staging",
        "OPS_QUEUE_DB_HOST": "gen1db",
        "OPS_QUEUE_DB_PORT": "5432",
        "OPS_QUEUE_DB_NAME": "mooncen_staging",
        "OPS_QUEUE_DB_USER": "mooncen_worker_wtr_linux",
        "OPS_QUEUE_DB_PASSWORD": worker_password,
        "CRAWL_STAGING_DB_HOST": "gen1db",
        "CRAWL_STAGING_DB_PORT": "5432",
        "CRAWL_STAGING_DB_NAME": "mooncen_staging",
        "CRAWL_STAGING_DB_USER": "mooncen_worker_wtr_linux",
        "CRAWL_STAGING_DB_PASSWORD": worker_password,
        "CRAWL_WRITE_MODE": "staging",
    }
    reporter = {
        **identity,
        "OPS_CRAWLER_SHARED_DB_HOST": "gen1db",
        "OPS_CRAWLER_SHARED_DB_PORT": "5432",
        "OPS_CRAWLER_SHARED_DB_NAME": "mooncen_staging",
        "OPS_CRAWLER_REPORTER_DB_USER": "mooncen_reporter_wtr_linux",
        "OPS_CRAWLER_REPORTER_DB_PASSWORD": (
            "B8_reporter-password-with-32-characters_"
        ),
    }
    return schema, worker, reporter


def _contract() -> pair.PairContract:
    schema, worker, reporter = _environments()
    contract, _, _ = pair._pair_contract(
        schema,
        worker,
        reporter,
        confirmed_database="mooncen_staging",
    )
    return contract


def test_pair_contract_requires_distinct_exact_identity_and_endpoint() -> None:
    schema, worker, reporter = _environments()

    contract, admin, owner = pair._pair_contract(
        schema,
        worker,
        reporter,
        confirmed_database="mooncen_staging",
    )

    assert contract.agent_id == AGENT_ID
    assert contract.worker_key == "wtr-linux"
    assert contract.hostname == "sgm-standard-pc-i440fx-piix-1996"
    assert contract.worker_login != contract.reporter_login
    assert contract.worker_password != contract.reporter_password
    assert admin["database"] == "mooncen_staging"
    assert owner == "mooncen_staging_owner"

    reporter["OPS_CRAWLER_WORKER_HOSTNAME"] = "SGM-STANDARD-PC-I440FX-PIIX-1996"
    with pytest.raises(pair.WorkerPairEnrollmentError, match="exact lowercase"):
        pair._pair_contract(
            schema,
            worker,
            reporter,
            confirmed_database="mooncen_staging",
        )


def test_pair_registry_reserves_and_activates_both_in_single_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = _contract()
    stored = {
        "version": 1,
        "entries": {
            "mooncen_metrics": {
                "component": "observer",
                "fingerprint": "f" * 64,
                "state": "active",
            }
        },
    }
    writes: list[dict[str, object]] = []
    monkeypatch.setattr(pair, "_fingerprint_key", lambda: bytes(range(32)))
    monkeypatch.setattr(pair, "_load_credential_registry", lambda: stored)

    def write_registry(value: dict[str, object]) -> None:
        writes.append(value.copy())

    monkeypatch.setattr(pair, "_write_credential_registry", write_registry)

    previous, fingerprints = pair._reserve_pair_registry(contract)
    assert previous["entries"] == {
        "mooncen_metrics": {
            "component": "observer",
            "fingerprint": "f" * 64,
            "state": "active",
        }
    }
    assert fingerprints["worker"] != fingerprints["reporter"]
    assert stored["entries"][contract.worker_login]["state"] == "pending"
    assert stored["entries"][contract.reporter_login]["state"] == "pending"
    assert len(writes) == 1

    pair._activate_pair_registry(contract, fingerprints)
    assert stored["entries"][contract.worker_login]["state"] == "active"
    assert stored["entries"][contract.reporter_login]["state"] == "active"
    assert len(writes) == 2


def test_write_all_retries_partial_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    observed = bytearray()

    def partial_write(_descriptor: int, payload: memoryview) -> int:
        chunk = bytes(payload[:2])
        observed.extend(chunk)
        return len(chunk)

    monkeypatch.setattr(pair.os, "write", partial_write)
    pair._write_all(19, b"abcdefg")
    assert bytes(observed) == b"abcdefg"


class _Connection:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed += 1


@contextmanager
def _lock():
    yield


def _patch_provision_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    connection: _Connection,
) -> tuple[Path, list[dict[str, object]]]:
    contract = _contract()
    schema, worker, reporter = _environments()
    values = {
        Path("schema.env"): schema,
        Path("worker.env"): worker,
        Path("reporter.env"): reporter,
    }
    monkeypatch.setattr(pair, "_protected_environment", lambda path, **_: values[path])
    monkeypatch.setattr(
        pair,
        "_pair_contract",
        lambda *_args, **_kwargs: (contract, {"database": "mooncen_staging"}, "owner"),
    )
    monkeypatch.setattr(pair, "build_scram_sha_256_verifier", lambda value: f"scram:{value}")
    monkeypatch.setattr(pair, "_installer_lock", _lock)
    monkeypatch.setattr(
        pair,
        "_reserve_pair_registry",
        lambda _contract: ({"version": 1, "entries": {}}, {"worker": "a", "reporter": "b"}),
    )
    staged = tmp_path / ".pair.pending"
    staged.write_bytes(b"secret")
    monkeypatch.setattr(pair, "_stage_secret_envelope", lambda *_args: staged)
    monkeypatch.setattr(pair.psycopg2, "connect", lambda **_kwargs: connection)
    monkeypatch.setattr(pair, "_fsync_directory", lambda _path: None)
    registry_restores: list[dict[str, object]] = []
    monkeypatch.setattr(pair, "_write_credential_registry", registry_restores.append)
    return staged, registry_restores


def test_database_failure_rolls_back_pair_and_removes_staged_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = _Connection()
    staged, registry_restores = _patch_provision_dependencies(
        monkeypatch, tmp_path, connection
    )
    monkeypatch.setattr(
        pair,
        "_database_pair_transaction",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        pair.provision_worker_reporter_pair(
            Path("schema.env"),
            Path("worker.env"),
            Path("reporter.env"),
            confirmed_database="mooncen_staging",
            secret_envelope=tmp_path / "pair.json",
        )

    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.closed == 1
    assert not staged.exists()
    assert registry_restores == [{"version": 1, "entries": {}}]


def test_pair_transaction_source_has_one_commit_and_rotation_fences() -> None:
    source = (ROOT / "tools" / "provision_crawler_worker_pair.py").read_text(
        encoding="utf-8"
    )
    transaction = source.split("def _database_pair_transaction", 1)[1].split(
        "def provision_worker_reporter_pair", 1
    )[0]
    provisioning = source.split("def provision_worker_reporter_pair", 1)[1].split(
        "def main", 1
    )[0]

    assert 'isolation_level="SERIALIZABLE"' in transaction
    assert "pg_advisory_xact_lock" in transaction
    assert transaction.index('component="worker"') < transaction.index(
        'component="reporter"'
    )
    assert "_assert_rotation_is_idle" in transaction
    assert "_assert_rls_contract" in transaction
    assert "job.status IN ('assigned', 'running')" in source
    assert "attempt.status = 'running'" in source
    assert "last_seen_at >= clock_timestamp()" in source
    assert "desired_status IN ('active', 'draining')" in source
    assert provisioning.count("connection.commit()") == 1
    assert "connection.rollback()" in provisioning
    assert provisioning.index("connection.commit()") < provisioning.index(
        "_activate_secret_envelope"
    )
    assert "_activate_pair_registry" in provisioning
    assert "password" not in provisioning.split("return {", 1)[1]


def test_enroll_shell_remains_fail_closed_before_pair_helper_consumption() -> None:
    source = (
        ROOT / "deploy" / "ubuntu" / "enroll_distributed_crawler_worker.sh"
    ).read_text(encoding="utf-8")
    gate = source.index("NOT READY: crawler worker enrollment")
    first_mutation = source.index("installer_lock_dir=")

    assert gate < first_mutation
    assert "No database or filesystem state was changed" in source
    assert "systemctl enable" not in source

