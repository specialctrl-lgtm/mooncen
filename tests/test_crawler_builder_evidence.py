from __future__ import annotations

import base64
import hashlib
from copy import deepcopy

import pytest

from ops_agent.crawler_builder_evidence import (
    BLOCKED_REGISTRATION_REASONS,
    BUILDER_EVIDENCE_FORMAT,
    SIGNATURE_NAMESPACE,
    SIGNER_EVIDENCE_FORMAT,
    TICKET_FORMAT,
    BuilderEvidence,
    BuilderEvidenceError,
    BuilderTicket,
    SignerEvidence,
    canonical_json_bytes,
    load_ticket_json,
)


def _ticket_document() -> dict[str, object]:
    return {
        "format": TICKET_FORMAT,
        "ticket_id": "00000000-0000-4000-8000-000000000001",
        "build_request_id": "00000000-0000-4000-8000-000000000002",
        "environment": "staging",
        "request_digest": "1" * 64,
        "source_commit": "2" * 40,
        "source_tree": "3" * 40,
        "code_version": "2026.08.12.1",
        "config_revision": "crawler-config-1",
        "test_profile": "crawler",
        "source_approval_receipt_id": "00000000-0000-4000-8000-000000000003",
        "source_approval_digest": "4" * 64,
        "source_approver_login": "crawler_source_approver",
        "source_approved_at": "2026-08-12T00:00:00.000000Z",
        "sources": [
            {
                "draft_id": "00000000-0000-4000-8000-000000000004",
                "revision": 7,
                "source_path": "Crawler/Crawler_Emart.py",
                "source_sha256": "5" * 64,
            }
        ],
    }


def _builder_evidence_document() -> dict[str, object]:
    return {
        "format": BUILDER_EVIDENCE_FORMAT,
        "ticket_id": "00000000-0000-4000-8000-000000000001",
        "ticket_digest": "0" * 64,
        "build_request_id": "00000000-0000-4000-8000-000000000002",
        "environment": "staging",
        "request_digest": "1" * 64,
        "source_commit": "2" * 40,
        "source_commit_object_sha256": "3" * 64,
        "source_tree": "4" * 40,
        "source_tree_object_sha256": "5" * 64,
        "runtime_allowlist_sha256": "6" * 64,
        "code_version": "2026.08.12.1",
        "config_revision": "crawler-config-1",
        "test_profile": "crawler",
        "source_approval_receipt_id": "00000000-0000-4000-8000-000000000003",
        "source_approval_digest": "7" * 64,
        "archive_sha256": "8" * 64,
        "archive_size_bytes": 100,
        "content_manifest_sha256": "9" * 64,
        "content_manifest_size_bytes": 200,
        "file_count": 2,
        "input_size_bytes": 300,
        "object_count": 4,
        "registration_ready": False,
        "blocked_reasons": list(BLOCKED_REGISTRATION_REASONS),
    }


def test_ticket_requires_exact_canonical_bytes_and_sorted_sources() -> None:
    document = _ticket_document()
    ticket = load_ticket_json(canonical_json_bytes(document))
    assert ticket == BuilderTicket.parse(document)
    assert len(ticket.digest) == 64

    noncanonical = canonical_json_bytes(document).replace(b'"format":', b'"format" :', 1)
    with pytest.raises(BuilderEvidenceError, match="not canonical"):
        load_ticket_json(noncanonical)

    duplicate = canonical_json_bytes(document).replace(
        b'{"build_request_id":', b'{"format":"duplicate","build_request_id":', 1
    )
    with pytest.raises(BuilderEvidenceError, match="duplicate"):
        load_ticket_json(duplicate)


@pytest.mark.parametrize(
    "source_path",
    [
        "../Crawler/main.py",
        "Crawler/../main.py",
        "Crawler\\main.py",
        "/Crawler/main.py",
        "Crawler/main.pem",
        "Crawler/é.py",
    ],
)
def test_ticket_rejects_unapproved_or_unsafe_source_paths(source_path: str) -> None:
    document = _ticket_document()
    sources = document["sources"]
    assert isinstance(sources, list)
    sources[0]["source_path"] = source_path
    with pytest.raises(BuilderEvidenceError, match="source path"):
        BuilderTicket.parse(document)


def test_builder_evidence_can_never_claim_registration_ready() -> None:
    document = _builder_evidence_document()
    evidence = BuilderEvidence.parse(document)
    assert evidence.document["registration_ready"] is False
    assert len(evidence.digest) == 64

    forged = deepcopy(document)
    forged["registration_ready"] = True
    with pytest.raises(BuilderEvidenceError, match="non-registrable"):
        BuilderEvidence.parse(forged)

    missing_gate = deepcopy(document)
    missing_gate["blocked_reasons"] = list(BLOCKED_REGISTRATION_REASONS[:-1])
    with pytest.raises(BuilderEvidenceError, match="blocked reasons"):
        BuilderEvidence.parse(missing_gate)


def test_signer_evidence_carries_only_detached_public_material() -> None:
    signature = b"-----BEGIN SSH SIGNATURE-----\nreviewed\n-----END SSH SIGNATURE-----\n"
    document = {
        "format": SIGNER_EVIDENCE_FORMAT,
        "builder_evidence_digest": "1" * 64,
        "artifact_digest": "2" * 64,
        "content_manifest_digest": "3" * 64,
        "signature_namespace": SIGNATURE_NAMESPACE,
        "key_id": "crawler-release@example",
        "signature": base64.b64encode(signature).decode("ascii"),
        "signature_sha256": hashlib.sha256(signature).hexdigest(),
        "signed_at": "2026-08-12T01:02:03.000000Z",
    }
    evidence = SignerEvidence.parse(document)
    assert evidence.signature_bytes == signature
    assert not any("path" in key or "private" in key or "secret" in key for key in document)

    forged = dict(document, signature_sha256="f" * 64)
    with pytest.raises(BuilderEvidenceError, match="digest differs"):
        SignerEvidence.parse(forged)
