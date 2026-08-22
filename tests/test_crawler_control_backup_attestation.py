from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path

import pytest

from tools import crawler_control_backup_attestation as attestation


ROOT = Path(__file__).resolve().parents[1]


def _live() -> dict[str, object]:
    return {
        "database": {
            "database_oid": 16384,
            "database_owner": "mooncen_staging_owner",
            "extensions": [
                {"name": "pgcrypto", "version": "1.3"},
                {"name": "plpgsql", "version": "1.0"},
                {"name": "uuid-ossp", "version": "1.1"},
            ],
            "postgres_server_version_num": "160010",
            "postgres_system_identifier": "7561234567890123456",
            "server_address": "100.104.152.24",
        },
        "hostname": "gen1db",
        "pg_dump_version": "pg_dump (PostgreSQL) 16.10",
        "schema_sha256": "a" * 64,
        "tls_ca_sha256": "b" * 64,
        "tls_certificate_sha256": "c" * 64,
    }


def _document(now: dt.datetime | None = None) -> tuple[dict[str, object], bytes, dt.datetime]:
    current = now or dt.datetime(2026, 8, 11, 3, 30, tzinfo=dt.timezone.utc)
    key = bytes(range(32))
    document = attestation._issue_document(
        key=key,
        live=_live(),
        backup_sha256="d" * 64,
        backup_size=1024,
        backup_name="mooncen_staging.dump",
        backup_path=(
            "/var/lib/mooncen-crawler-control-backup-attestation/evidence/"
            "20260811T032600Z-0123456789abcdef/mooncen_staging.dump"
        ),
        backup_kind="pg_custom_dump",
        backup_completed_at=attestation._format_timestamp(
            current - dt.timedelta(minutes=4)
        ),
        reviewed_manifest_sha256=None,
        restored_schema_sha256="a" * 64,
        restore_database="mooncen_restore_attest_0123456789abcdef01234567",
        restore_started_at=attestation._format_timestamp(
            current - dt.timedelta(minutes=3)
        ),
        restore_completed_at=attestation._format_timestamp(
            current - dt.timedelta(minutes=1)
        ),
        courses_count=153,
        branches_count=17,
        now=current,
    )
    return document, key, current


def test_canonical_hmac_attestation_round_trip_and_live_binding() -> None:
    document, key, now = _document()

    payload = attestation._verify_document(
        document,
        key=key,
        now=now,
        max_age=attestation.MAX_ATTESTATION_AGE_SECONDS,
        live=_live(),
    )

    assert payload["format"] == attestation.FORMAT
    assert payload["database"]["dns_host"] == "gen1db"
    assert payload["database"]["database_name"] == "mooncen_staging"
    assert payload["database"]["postgres_system_identifier"] == "7561234567890123456"
    assert payload["database"]["tls_certificate_sha256"] == "c" * 64
    assert payload["schema"]["source_sha256"] == payload["schema"]["restored_sha256"]
    assert document["authentication"]["algorithm"] == "hmac-sha256"


def test_attestation_rejects_tamper_staleness_and_live_identity_change() -> None:
    document, key, now = _document()
    tampered = json.loads(json.dumps(document))
    tampered["payload"]["restore"]["courses_count"] = 154
    with pytest.raises(attestation.AttestationError, match="authentication failed"):
        attestation._verify_document(
            tampered,
            key=key,
            now=now,
            max_age=86_400,
        )

    with pytest.raises(attestation.AttestationError, match="expired|stale"):
        attestation._verify_document(
            document,
            key=key,
            now=now + dt.timedelta(days=2),
            max_age=86_400,
        )

    changed_live = _live()
    changed_live["database"] = dict(changed_live["database"])
    changed_live["database"]["postgres_system_identifier"] = "7569999999999999999"
    with pytest.raises(attestation.AttestationError, match="system_identifier"):
        attestation._verify_document(
            document,
            key=key,
            now=now,
            max_age=86_400,
            live=changed_live,
        )


def test_attestation_json_rejects_duplicates_and_noncanonical_representation() -> None:
    with pytest.raises(attestation.AttestationError, match="duplicate JSON key"):
        attestation._parse_json(b'{"payload":{},"payload":{}}\n', "test")

    document, _, _ = _document()
    canonical = attestation._canonical_json(document)
    assert canonical.endswith(b"\n")
    assert attestation._canonical_json(attestation._parse_json(canonical, "test")) == canonical
    assert json.dumps(document, indent=2).encode("utf-8") != canonical


def test_schema_digest_ignores_only_postgres_transport_guard_tokens() -> None:
    first = (
        b"\\restrict 0123456789abcdef\n"
        b"-- schema\nCREATE TABLE x (id integer);  \n"
        b"\\unrestrict 0123456789abcdef\n"
    )
    second = (
        b"\\restrict fedcba9876543210\n"
        b"-- schema\nCREATE TABLE x (id integer);  \n"
        b"\\unrestrict fedcba9876543210\n"
    )
    changed = (
        b"\\restrict 0123456789abcdef\n"
        b"-- schema\nCREATE TABLE x (id integer); \n"
        b"\\unrestrict 0123456789abcdef\n"
    )

    assert attestation._schema_digest(first) == attestation._schema_digest(second)
    assert attestation._schema_digest(first) != attestation._schema_digest(changed)
    with pytest.raises(attestation.AttestationError, match="guard pair"):
        attestation._schema_digest(first.replace(b"0123456789abcdef\n", b"badbadbadbadbadb\n", 1))

    internal = b"CREATE FUNCTION f() RETURNS void AS $$\n\\restrict 0123456789abcdef\n$$ LANGUAGE sql;\n"
    assert attestation._schema_digest(internal) == hashlib.sha256(internal).hexdigest()


def test_protected_writer_retries_short_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    chunks: list[bytes] = []

    def short_write(descriptor: int, data: memoryview) -> int:
        assert descriptor == 41
        count = min(2, len(data))
        chunks.append(bytes(data[:count]))
        return count

    monkeypatch.setattr(attestation.os, "write", short_write)
    attestation._write_all(41, b"abcdefg")

    assert b"".join(chunks) == b"abcdefg"


def test_retained_evidence_inventory_is_bounded_and_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name != "posix":
        pytest.skip("POSIX ownership and mode semantics are required")
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir(mode=0o700)
    evidence_root.chmod(0o700)
    generation = evidence_root / "20260811T032600Z-0123456789abcdef"
    generation.mkdir(mode=0o700)
    generation.chmod(0o700)
    dump = generation / "mooncen_staging.dump"
    dump.write_bytes(b"reviewed-backup")
    dump.chmod(0o600)
    actual_lstat = Path.lstat

    def root_owned_evidence_lstat(path: Path) -> os.stat_result:
        details = actual_lstat(path)
        if path not in {generation, dump}:
            return details
        fields = list(details)
        fields[4] = 0
        return os.stat_result(fields)

    monkeypatch.setattr(Path, "lstat", root_owned_evidence_lstat)
    monkeypatch.setattr(attestation, "EVIDENCE_ROOT", evidence_root)
    monkeypatch.setattr(attestation, "_ensure_root_directory", lambda path, mode: None)

    assert attestation._inspect_retained_evidence() == (1, len(b"reviewed-backup"))

    (generation / "unexpected").write_bytes(b"x")
    with pytest.raises(attestation.AttestationError, match="unexpected files"):
        attestation._inspect_retained_evidence()


def test_protected_reader_rejects_symlink_and_permissive_mode(tmp_path: Path) -> None:
    target = tmp_path / "attestation.json"
    target.write_bytes(b"{}\n")
    link = tmp_path / "attestation-link.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(attestation.AttestationError, match="non-symlink"):
        attestation._read_regular_file(
            link,
            label="test attestation",
            maximum=1024,
            owner_only=True,
            require_root=False,
        )

    if os.name == "posix":
        target.chmod(0o644)
        with pytest.raises(attestation.AttestationError, match="owner-only"):
            attestation._read_regular_file(
                target,
                label="test attestation",
                maximum=1024,
                owner_only=True,
                require_root=False,
            )


def test_issue_cli_has_no_caller_asserted_restore_or_backup_evidence() -> None:
    parser = attestation._build_parser()
    parsed = parser.parse_args(["issue", "--database-env", "/root/schema.env"])

    assert vars(parsed).keys() == {"command", "database_env", "key", "output", "handler"}
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "issue",
                "--database-env",
                "/root/schema.env",
                "--courses-count",
                "153",
            ]
        )


def test_issuer_performs_fresh_dump_isolated_restore_and_force_cleanup() -> None:
    source = (ROOT / "tools" / "crawler_control_backup_attestation.py").read_text(
        encoding="utf-8"
    )
    issue_flow = source.split("def _create_backup_and_verify_restore", 1)[1].split(
        "def _dump_live_schema", 1
    )[0]

    assert '"--format=custom"' in issue_flow
    assert '"--list"' in issue_flow
    assert "secrets.token_hex(12)" in issue_flow
    assert "WITH TEMPLATE template0 OWNER postgres ALLOW_CONNECTIONS false" in issue_flow
    assert '"--exit-on-error"' in issue_flow
    assert '"--single-transaction"' in issue_flow
    assert '"--no-owner"' in issue_flow
    assert '"--no-privileges"' in issue_flow
    assert issue_flow.count('"--no-tablespaces"') >= 3
    assert "_assert_no_orphan_restore_databases()" in issue_flow
    assert "create_attempted = True" in issue_flow
    assert "restored_objects != REQUIRED_OBJECTS" in issue_flow
    assert "courses_count < 1 or branches_count < 1" in issue_flow
    assert "DROP DATABASE IF EXISTS {quoted} WITH (FORCE)" in issue_flow
    assert "cleanup failed; attestation was not issued" in issue_flow
    cli_issue = source.index("def _issue(args")
    assert source.index("_create_backup_and_verify_restore(", cli_issue) < source.index(
        "_atomic_write_attestation(", cli_issue
    )


def test_issue_is_serialized_space_bounded_and_repeats_live_contract() -> None:
    source = (ROOT / "tools" / "crawler_control_backup_attestation.py").read_text(
        encoding="utf-8"
    )

    assert "fcntl.LOCK_EX | fcntl.LOCK_NB" in source
    assert "MAX_BACKUP_BYTES = 16 * 1024 * 1024 * 1024" in source
    assert "pg_database_size(current_database())" in source
    assert "os.statvfs(path)" in source
    assert "shared backup/PostgreSQL filesystem lacks dump, restore" in source
    assert "resource.RLIMIT_FSIZE" in source
    assert "file_size_limit=MAX_BACKUP_BYTES" in source
    assert "default_transaction_read_only=on" in source
    assert "statement_timeout=10000" in source
    assert "ssl.CERT_REQUIRED" in source
    assert "context.check_hostname = True" in source
    assert "live database schema changed after backup attestation" in source
    assert "O_NOFOLLOW" in source
    assert "st_uid != 0" in source
    assert "unsafe_mode = 0o077 if owner_only else 0o022" in source
    assert source.count("owner_only=True") >= 2
    assert "retained backup evidence directories must be root-owned mode 0700" in source
    assert "retained_backup = Path(backup[\"object_path\"])" in source
    assert "_validate_private_evidence_path(retained_backup)" in source
    assert "owner_only=True" in source
    assert "retained backup evidence digest or size changed" in source
    assert "MAX_RETAINED_EVIDENCE_DIRECTORIES = 2" in source
    assert "backup evidence retention cap reached" in source
    assert "custom PostgreSQL tablespaces are unsupported" in source
    assert "PostgreSQL pg_wal must remain on the reviewed data filesystem" in source
    assert "def _write_all(" in source
    assert "_fsync_directory(evidence_directory)" in source


def test_first_issue_creates_and_validates_missing_lock_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_directory = tmp_path / "run" / "mooncen-attestation"
    lock_directory.parent.mkdir()
    observed: list[tuple[Path, int]] = []

    def observe(path: Path, mode: int) -> None:
        assert path == lock_directory
        assert path.is_dir()
        assert not path.is_symlink()
        observed.append((path, mode))

    monkeypatch.setattr(attestation, "_ensure_root_directory", observe)
    attestation._prepare_issue_lock_directory(lock_directory)

    assert observed == [(lock_directory, 0o700)]


def test_control_installer_requires_root_verified_receipt_before_first_database_write() -> None:
    source = (ROOT / "deploy" / "ubuntu" / "setup_distributed_crawler_control.sh").read_text(
        encoding="utf-8"
    )
    rejection = source.index("NOT READY: distributed crawler control installation is disabled")
    verifier = source.index('"$root_trust_helper" verify-receipt')
    consumer = source.index('"$root_trust_helper" consume-receipt', verifier)
    first_write = source.index(
        '"$PYTHON" -X utf8 -m tools.ensure_crawler_control_schema', consumer
    )

    assert "--backup-receipt" in source
    assert "--backup-receipt-signature" in source
    assert "--backup-receipt-nonce" in source
    assert "/var/lib/mooncen-crawler-control-root-trust/receipts/" in source
    assert rejection < verifier < consumer < first_write
    assert "/usr/local/libexec/mooncen-crawler-control-root-trust" in source
