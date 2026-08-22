from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from tools import ensure_crawler_control_schema as schema


ROOT = Path(__file__).resolve().parents[1]


def _receipt(now: dt.datetime) -> bytes:
    document = {
        "candidate": {
            "archive_size_bytes": 123,
            "metadata_sha256": "5" * 64,
            "signature_sha256": "6" * 64,
            "tree_size_bytes": 456,
        },
        "format": schema.RECEIPT_FORMAT,
        "issued_at": (now - dt.timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "issuer": {
            "helper_format": "mooncen-crawler-control-root-trust-v1",
            "hostname": "gen1db",
            "signature_namespace": "mooncen-crawler-control-backup-receipt-v1",
            "signature_principal": schema.RECEIPT_PRINCIPAL,
        },
        "nonce": "7" * 64,
        "recovery_evidence": {
            "attestation_format": "mooncen-crawler-control-backup-attestation-v1",
            "attestation_key_id": "sha256:" + "9" * 64,
            "attestation_path_basename": "backup-attestation.json",
            "attestation_sha256": "8" * 64,
            "database_host": "gen1db",
            "database_name": "mooncen_staging",
            "database_port": 5432,
            "database_sslmode": "verify-full",
            "issued_at": (now - dt.timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "valid_until": (now + dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "release": {
            "archive_sha256": "3" * 64,
            "deploy_commit": "2" * 40,
            "node_role": "crawler-control",
            "release_id": "1" * 32,
            "signer_principal": schema.RELEASE_PRINCIPAL,
            "target_host": "gen1db",
            "tree_sha256": "4" * 64,
        },
        "valid_until": (now + dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return schema._canonical_json(document)


def _claim(now: dt.datetime) -> schema.InstallReceiptClaim:
    data = _receipt(now)
    claim = schema._parse_install_receipt_claim(
        data,
        expected_nonce="7" * 64,
        expected_release_id="1" * 32,
        expected_commit="2" * 40,
        expected_archive_sha256="3" * 64,
        expected_tree_sha256="4" * 64,
        receipt_signature_sha256="a" * 64,
        now=now,
    )
    assert claim.receipt_sha256 == hashlib.sha256(data).hexdigest()
    return claim


def test_receipt_claim_rejects_wrong_release_tree_nonce_and_expiry() -> None:
    now = dt.datetime(2026, 8, 12, 1, 0, tzinfo=dt.timezone.utc)
    common = {
        "data": _receipt(now),
        "expected_nonce": "7" * 64,
        "expected_release_id": "1" * 32,
        "expected_commit": "2" * 40,
        "expected_archive_sha256": "3" * 64,
        "expected_tree_sha256": "4" * 64,
        "receipt_signature_sha256": "a" * 64,
        "now": now,
    }
    for field, value in (
        ("expected_nonce", "a" * 64),
        ("expected_release_id", "b" * 32),
        ("expected_commit", "c" * 40),
        ("expected_archive_sha256", "d" * 64),
        ("expected_tree_sha256", "e" * 64),
    ):
        changed = dict(common)
        changed[field] = value
        with pytest.raises(schema.SchemaInstallError, match="another release or nonce"):
            schema._parse_install_receipt_claim(**changed)
    expired = dict(common)
    expired["now"] = now + dt.timedelta(hours=2)
    with pytest.raises(schema.SchemaInstallError, match="expired"):
        schema._parse_install_receipt_claim(**expired)
    wrong_database = dict(common)
    wrong_database["data"] = common["data"].replace(
        b'"database_name":"mooncen_staging"', b'"database_name":"cloud"'
    )
    with pytest.raises(schema.SchemaInstallError, match="database identity"):
        schema._parse_install_receipt_claim(**wrong_database)


class _Cursor:
    def __init__(self, fetches: list[object], *, replay: bool = False) -> None:
        self.fetches = list(fetches)
        self.replay = replay
        self.executions: list[tuple[object, object | None]] = []

    def execute(self, statement: object, parameters: object | None = None) -> None:
        self.executions.append((statement, parameters))
        if self.replay and "INSERT INTO public.ops_crawler_control_install" in str(statement):
            raise schema.psycopg2.IntegrityError("duplicate")

    def fetchone(self) -> object:
        return self.fetches.pop(0)


def test_receipt_replay_or_concurrent_consumer_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    now = dt.datetime(2026, 8, 12, 1, 0, tzinfo=dt.timezone.utc)
    claim = _claim(now)
    migration = "CREATE TABLE exact_receipt_fixture();\n"
    checksum = hashlib.sha256(migration.encode()).hexdigest()
    monkeypatch.setattr(schema, "_install_receipt_migration", lambda: (migration, checksum))
    cursor = _Cursor(
        [
            (checksum,),
            ("ops_crawler_control_install_receipt_consumptions",),
            (True, True, True, True, True),
        ],
        replay=True,
    )
    with pytest.raises(schema.SchemaInstallError, match="already consumed|races"):
        schema._consume_install_receipt(
            cursor,
            claim,
            object_owner="mooncen_staging_owner",
            schema_user="mooncen_crawler_schema_admin",
        )
    insert = next(
        str(statement)
        for statement, _ in cursor.executions
        if "INSERT INTO public.ops_crawler_control_install" in str(statement)
    )
    assert "transaction_timestamp()" in insert
    assert "ON CONFLICT" not in insert


def test_receipt_migration_is_dedicated_append_only_and_rls_forced() -> None:
    migration = (
        ROOT
        / "DB/crawler_control_migrations/20260812_001_install_receipt_consumption.sql"
    ).read_text(encoding="utf-8")
    installer = (ROOT / "tools/ensure_crawler_control_schema.py").read_text(encoding="utf-8")
    roles = (ROOT / "DB/roles.sql").read_text(encoding="utf-8")
    assert migration.startswith("-- FUTURE INTENDED PATH / DIRECT EXECUTION FORBIDDEN.")
    assert "current --apply path" in migration
    assert "REVOKE ALL" in migration
    assert "ROW LEVEL SECURITY" not in migration
    assert "SECURITY DEFINER" not in migration
    assert "no runtime role receives privileges" in migration
    assert "ux_crawler_install_receipt_nonce UNIQUE (nonce)" in migration
    assert "ux_crawler_install_receipt_release_id UNIQUE (release_id)" in migration
    assert "ON CONFLICT DO NOTHING" not in installer
    assert "SELECT pg_advisory_lock(hashtext(%s))" in installer
    gate = installer.index("NOT READY: install receipt atomic DB apply")
    atomic_return = installer.index("return _apply_control_contract_atomically", gate)
    dormant = installer.index("DORMANT LEGACY REFERENCE", atomic_return)
    first_existing_write = installer.index("connection.autocommit = True", gate)
    assert gate < atomic_return < dormant < first_existing_write
    assert roles.lstrip().startswith("-- Least-privilege") and "\nBEGIN;" in roles


def test_claim_is_immutable_value_object() -> None:
    now = dt.datetime(2026, 8, 12, 1, 0, tzinfo=dt.timezone.utc)
    claim = _claim(now)
    with pytest.raises(FrozenInstanceError):
        claim.nonce = "bad"  # type: ignore[misc]


def test_roles_body_is_exact_idempotent_wrapper_body_without_transaction_tokens() -> None:
    wrapper = (ROOT / "DB/roles.sql").read_text(encoding="utf-8")
    body = (ROOT / "DB/roles_body.sql").read_text(encoding="utf-8")
    assert wrapper.count("\nBEGIN;\n") == 1
    assert wrapper.count("\nCOMMIT;\n") == 1
    expected = wrapper.replace("\nBEGIN;\n", "\n").replace("\nCOMMIT;\n", "\n")
    assert expected.rstrip("\n") + "\n" == body.rstrip("\n") + "\n"
    schema._assert_include_safe_roles(body)
    with pytest.raises(schema.SchemaInstallError, match="must not own"):
        schema._assert_include_safe_roles("BEGIN;\nSELECT 1;\nCOMMIT;\n")


class _AtomicCursor:
    def __init__(self, events: list[str], fail_at: str | None) -> None:
        self.events = events
        self.fail_at = fail_at
        self.last = ""
        self.roles_pass = 0

    def __enter__(self) -> "_AtomicCursor":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: object, _parameters: object | None = None) -> None:
        self.last = str(statement)
        if "pg_advisory_xact_lock" in self.last:
            self.events.append("lock")
        elif self.last == "ROLES_BODY":
            self.roles_pass += 1
            stage = f"roles_{self.roles_pass}"
            self.events.append(stage)
            if self.fail_at == stage:
                raise RuntimeError(stage)
        elif self.last in {"MARKER", "MIGRATION", "STAGING"}:
            stage = self.last.lower()
            self.events.append(stage)
            if self.fail_at == stage:
                raise RuntimeError(stage)

    def fetchone(self) -> object:
        if "current_database(), session_user" in self.last:
            return ("mooncen_staging", "mooncen_crawler_schema_admin")
        if "SELECT checksum" in self.last:
            return None
        if "mooncen_crawler_worker" in self.last:
            return (1,)
        return (True,)


class _AtomicConnection:
    def __init__(self, events: list[str], fail_at: str | None) -> None:
        self.events = events
        self.cursor_value = _AtomicCursor(events, fail_at)
        self.commits = 0
        self.rollbacks = 0
        self.session: dict[str, object] = {}

    def cursor(self) -> _AtomicCursor:
        return self.cursor_value

    def rollback(self) -> None:
        self.rollbacks += 1

    def commit(self) -> None:
        self.commits += 1
        self.events.append("commit")

    def set_session(self, **values: object) -> None:
        self.session = values


@pytest.mark.parametrize(
    "fail_at",
    ["roles_1", "marker", "migration", "staging", "roles_2", "postcheck", "insert"],
)
def test_atomic_apply_faults_never_commit(
    monkeypatch: pytest.MonkeyPatch, fail_at: str
) -> None:
    now = dt.datetime(2026, 8, 12, 1, 0, tzinfo=dt.timezone.utc)
    events: list[str] = []
    connection = _AtomicConnection(events, fail_at)

    def prepare(*_args: object, **_kwargs: object) -> str:
        events.append("prepare")
        return "f" * 64

    def postcheck(*_args: object, **_kwargs: object) -> None:
        events.append("postcheck")
        if fail_at == "postcheck":
            raise RuntimeError("postcheck")

    def insert(*_args: object, **_kwargs: object) -> None:
        events.append("insert")
        if fail_at == "insert":
            raise RuntimeError("insert")

    monkeypatch.setattr(schema, "_prepare_install_receipt_ledger", prepare)
    monkeypatch.setattr(schema, "_post_contract", postcheck)
    monkeypatch.setattr(schema, "_insert_install_receipt", insert)
    monkeypatch.setattr(schema, "_crawler_policy_digest", lambda _cursor: "a" * 64)
    monkeypatch.setattr(schema, "_crawler_acl_digest", lambda _cursor: "b" * 64)
    with pytest.raises(RuntimeError, match=fail_at):
        schema._apply_control_contract_atomically(
            connection,
            confirmed_database="mooncen_staging",
            object_owner="mooncen_staging_owner",
            schema_user="mooncen_crawler_schema_admin",
            claim=_claim(now),
            migration="MIGRATION",
            marker="MARKER",
            staging="STAGING",
            roles="ROLES_BODY",
            checksum="1" * 64,
            marker_checksum="2" * 64,
            staging_checksum="3" * 64,
            roles_checksum="4" * 64,
        )
    assert connection.commits == 0
    assert connection.rollbacks >= 2
    assert events[0] == "lock"


def test_atomic_apply_inserts_receipt_last_and_commits_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = dt.datetime(2026, 8, 12, 1, 0, tzinfo=dt.timezone.utc)
    events: list[str] = []
    connection = _AtomicConnection(events, None)
    monkeypatch.setattr(
        schema,
        "_prepare_install_receipt_ledger",
        lambda *_args, **_kwargs: events.append("prepare") or "f" * 64,
    )
    monkeypatch.setattr(
        schema, "_post_contract", lambda *_args, **_kwargs: events.append("postcheck")
    )
    monkeypatch.setattr(
        schema, "_insert_install_receipt", lambda *_args, **_kwargs: events.append("insert")
    )
    monkeypatch.setattr(schema, "_crawler_policy_digest", lambda _cursor: "a" * 64)
    monkeypatch.setattr(schema, "_crawler_acl_digest", lambda _cursor: "b" * 64)
    schema._apply_control_contract_atomically(
        connection,
        confirmed_database="mooncen_staging",
        object_owner="mooncen_staging_owner",
        schema_user="mooncen_crawler_schema_admin",
        claim=_claim(now),
        migration="MIGRATION",
        marker="MARKER",
        staging="STAGING",
        roles="ROLES_BODY",
        checksum="1" * 64,
        marker_checksum="2" * 64,
        staging_checksum="3" * 64,
        roles_checksum="4" * 64,
    )
    assert connection.session == {
        "isolation_level": "SERIALIZABLE",
        "readonly": False,
        "autocommit": False,
    }
    assert connection.commits == 1
    assert events[0] == "lock"
    assert events[-3:] == ["postcheck", "prepare", "insert"] or events[-4:] == [
        "postcheck",
        "prepare",
        "insert",
        "commit",
    ]
    assert events[-2:] == ["insert", "commit"]
