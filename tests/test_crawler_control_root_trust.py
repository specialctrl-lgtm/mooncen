from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "deploy" / "ubuntu" / "crawler_control_root_trust.py"
SPEC = importlib.util.spec_from_file_location("crawler_control_root_trust_test", HELPER_PATH)
assert SPEC is not None and SPEC.loader is not None
trust = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = trust
SPEC.loader.exec_module(trust)


def _candidate() -> trust.CandidateIdentity:
    return trust.CandidateIdentity(
        release_id="1" * 32,
        deploy_commit="2" * 40,
        archive_sha256="3" * 64,
        tree_sha256="4" * 64,
        node_role="crawler-control",
        target_host="gen1db",
        signer_principal="mooncen-crawler-control-release",
        metadata_sha256="5" * 64,
        signature_sha256="6" * 64,
        archive_size_bytes=123,
        tree_size_bytes=456,
    )


def _receipt(now: dt.datetime) -> dict[str, object]:
    return trust._build_receipt(
        _candidate(),
        nonce="7" * 64,
        evidence_sha256="8" * 64,
        evidence_key_id="sha256:" + "9" * 64,
        evidence_issued_at=trust._format_timestamp(now - dt.timedelta(minutes=1)),
        evidence_valid_until=trust._format_timestamp(now + dt.timedelta(hours=1)),
        now=now,
    )


def test_release_metadata_is_exact_ordered_and_target_pinned() -> None:
    data = (
        "FORMAT=mooncen-crawler-control-release-v1\n"
        f"DEPLOY_COMMIT={'2' * 40}\n"
        f"DEPLOY_ARCHIVE_SHA256={'3' * 64}\n"
        f"DEPLOY_TREE_SHA256={'4' * 64}\n"
        "NODE_ROLE=crawler-control\n"
        "TARGET_HOST=gen1db\n"
    ).encode("ascii")
    assert trust._parse_release_metadata(data)["TARGET_HOST"] == "gen1db"
    with pytest.raises(trust.TrustError, match="field order"):
        trust._parse_release_metadata(data.replace(b"NODE_ROLE=", b"TARGET_HOST=", 1))
    with pytest.raises(trust.TrustError, match="pinned"):
        trust._parse_release_metadata(data.replace(b"TARGET_HOST=gen1db", b"TARGET_HOST=cloud"))


def test_receipt_binds_release_nonce_evidence_and_expiry() -> None:
    now = dt.datetime(2026, 8, 11, 12, 0, tzinfo=dt.timezone.utc)
    receipt = _receipt(now)
    arguments = {
        "expected_nonce": "7" * 64,
        "expected_release_id": "1" * 32,
        "expected_commit": "2" * 40,
        "expected_archive_sha256": "3" * 64,
        "expected_tree_sha256": "4" * 64,
    }
    trust._validate_receipt(receipt, now=now, **arguments)
    changed = json.loads(json.dumps(receipt))
    changed["release"]["tree_sha256"] = "a" * 64
    with pytest.raises(trust.TrustError, match="another release"):
        trust._validate_receipt(changed, now=now, **arguments)
    with pytest.raises(trust.TrustError, match="currently valid"):
        trust._validate_receipt(receipt, now=now + dt.timedelta(hours=2), **arguments)


def test_receipt_json_is_canonical_and_duplicate_keys_fail() -> None:
    now = dt.datetime(2026, 8, 11, 12, 0, tzinfo=dt.timezone.utc)
    canonical = trust._canonical_json(_receipt(now))
    assert trust._canonical_json(trust._parse_json(canonical, "receipt")) == canonical
    with pytest.raises(trust.TrustError, match="duplicate JSON key"):
        trust._parse_json(b'{"format":"a","format":"b"}\n', "receipt")


def test_root_evidence_write_retries_short_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    written = bytearray()

    def short_write(_descriptor: int, data: bytes) -> int:
        count = min(2, len(data))
        written.extend(data[:count])
        return count

    monkeypatch.setattr(trust.os, "write", short_write)
    trust._write_all(99, b"abcdefg")
    assert written == b"abcdefg"


def test_install_paths_keep_every_mutation_gate_closed() -> None:
    helper = HELPER_PATH.read_text(encoding="utf-8")
    setup = (ROOT / "deploy/ubuntu/setup_distributed_crawler_control.sh").read_text(encoding="utf-8")
    activator = (ROOT / "deploy/ubuntu/activate_crawler_control_release.sh").read_text(encoding="utf-8")
    docs = (ROOT / "docs/distributed-crawler-control-plane.md").read_text(encoding="utf-8")
    sql = (
        ROOT
        / "DB/crawler_control_migrations/20260812_001_install_receipt_consumption.sql"
    ).read_text(encoding="utf-8")
    consume_code = helper.index("_verify_receipt_command(args, print_proof=False)")
    assert consume_code < helper.index("NOT READY: receipt consumption", consume_code)
    consume_call = setup.index('"$root_trust_helper" consume-receipt')
    assert setup.index("NOT READY: distributed crawler control installation") < consume_call
    assert consume_call < setup.index("-m tools.ensure_crawler_control_schema", consume_call)
    assert activator.index('"$trust_helper" verify-candidate') < activator.index(
        "NOT READY: direct crawler-control activation"
    )
    assert "REVOKE ALL" in sql
    assert "SECURITY DEFINER" not in sql
    assert "pg_advisory" not in helper
    assert "not installed\non gen1db by this repository change" in docs
    assert "/usr/local/libexec/mooncen-crawler-control-root-trust" in docs


def test_receipt_json_schema_is_closed_and_secret_free() -> None:
    schema = json.loads(
        (ROOT / "config/crawler_control_backup_receipt.schema.json").read_text(encoding="utf-8")
    )
    assert schema["additionalProperties"] is False
    assert schema["properties"]["release"]["additionalProperties"] is False
    serialized = json.dumps(schema, sort_keys=True)
    assert "private_key" not in serialized
    assert "hmac_key" not in serialized
    assert schema["properties"]["issuer"]["properties"]["hostname"]["const"] == "gen1db"


def test_root_trust_bootstrap_is_exact_clean_commit_and_out_of_band_only() -> None:
    builder = (ROOT / "tools/build_crawler_control_root_trust_bundle.py").read_text(
        encoding="utf-8"
    )
    helper = HELPER_PATH.read_text(encoding="utf-8")
    activator = (ROOT / "deploy/ubuntu/activate_crawler_control_release.sh").read_text(
        encoding="utf-8"
    )
    for target in (
        "/usr/local/libexec/mooncen-crawler-control-root-trust",
        "/usr/local/libexec/mooncen-crawler-control-backup-attestation",
        "/usr/local/share/mooncen/crawler-control-backup-receipt.schema.json",
        "/etc/mooncen/crawler-control-root-trust.policy",
    ):
        assert target in builder
    assert '"status", "--porcelain=v1", "--untracked-files=all"' in builder
    assert '"remote_automation_allowed": False' in builder
    assert '"install_method": "manual-out-of-band-only"' in builder
    assert "ROOT_TRUST_HELPER_SHA256" in helper
    assert "RECEIPT_SCHEMA_SHA256" in helper
    assert "ROOT_TRUST_HELPER_SHA256" in activator
    assert "differs from the out-of-band policy" in activator
