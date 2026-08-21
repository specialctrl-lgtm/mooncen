from __future__ import annotations

import json
from pathlib import Path

import pytest

from deploy.docker.release_manifest import (
    ManifestError,
    VALIDATION_CHECKS,
    bind_promotion_evidence,
    bind_validation_evidence,
    create_release_manifest,
    create_validation_receipt,
    load_json_evidence,
    validate_release_manifest,
    validate_validation_receipt,
    write_json_evidence,
)


BASE = "1" * 40
TREE = "2" * 40
SNAPSHOT = "3" * 40
API_ID = "sha256:" + "a" * 64
FRONTEND_ID = "sha256:" + "b" * 64


def release() -> dict[str, object]:
    return create_release_manifest(
        base_commit=BASE,
        source_tree=TREE,
        snapshot_commit=SNAPSHOT,
        platform="linux/amd64",
        bundle_sha256="4" * 64,
        compose_sha256="5" * 64,
        build_policy_sha256="6" * 64,
        migration_ledger_sha256="7" * 64,
        images={
            "api": {"tag": f"mooncen/api:release-{TREE}", "image_id": API_ID},
            "frontend": {
                "tag": f"mooncen/frontend:release-{TREE}",
                "image_id": FRONTEND_ID,
            },
        },
        created_at="2026-08-19T12:00:00Z",
    )


def receipt(release_value: dict[str, object] | None = None) -> dict[str, object]:
    return create_validation_receipt(
        release=release_value or release(),
        target="an2p-dev",
        target_identity="8" * 64,
        checks={name: True for name in VALIDATION_CHECKS},
        validated_at="2026-08-19T12:10:00Z",
        expires_at="2026-08-20T12:10:00Z",
    )


def test_release_and_receipt_are_canonical_and_bound() -> None:
    release_value = release()
    receipt_value = receipt(release_value)

    evidence = bind_promotion_evidence(
        release_value,
        receipt_value,
        now="2026-08-19T13:00:00Z",
    )

    assert len(evidence.release["release_digest"]) == 64
    assert len(evidence.receipt["receipt_digest"]) == 64
    assert evidence.receipt["image_ids"]["api"] == API_ID


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("source_tree", "9" * 40),
        ("bundle_sha256", "9" * 64),
        ("compose_sha256", "9" * 64),
        ("platform", "linux/arm64"),
    ],
)
def test_tampered_release_is_rejected(field: str, replacement: str) -> None:
    value = release()
    value[field] = replacement
    with pytest.raises(ManifestError, match="release_digest|source tree"):
        validate_release_manifest(value)


def test_mutable_or_unbound_image_tag_is_rejected() -> None:
    value = release()
    value["images"]["api"]["tag"] = "mooncen/api:latest"  # type: ignore[index]
    with pytest.raises(ManifestError, match="tag"):
        validate_release_manifest(value)


def test_unknown_fields_fail_closed() -> None:
    value = release()
    value["secret"] = "must-not-be-accepted"
    with pytest.raises(ManifestError, match="fields"):
        validate_release_manifest(value)


def test_receipt_status_must_match_all_checks() -> None:
    value = receipt()
    value["checks"]["api_health"] = False  # type: ignore[index]
    with pytest.raises(ManifestError, match="status"):
        validate_validation_receipt(value)


@pytest.mark.parametrize(
    "now",
    ("2026-08-20T12:10:00Z", "2026-08-21T00:00:00Z"),
)
def test_expired_receipt_cannot_be_promoted(now: str) -> None:
    release_value = release()
    with pytest.raises(ManifestError, match="expired"):
        bind_promotion_evidence(
            release_value,
            receipt(release_value),
            now=now,
        )


def test_expired_pass_can_only_converge_the_same_already_validated_release() -> None:
    release_value = release()
    receipt_value = receipt(release_value)

    evidence = bind_validation_evidence(release_value, receipt_value)

    assert evidence.release["release_digest"] == release_value["release_digest"]
    assert evidence.receipt["receipt_digest"] == receipt_value["receipt_digest"]

    different_release = create_release_manifest(
        base_commit=BASE,
        source_tree=TREE,
        snapshot_commit=SNAPSHOT,
        platform="linux/amd64",
        bundle_sha256="c" * 64,
        compose_sha256="5" * 64,
        build_policy_sha256="6" * 64,
        migration_ledger_sha256="7" * 64,
        images=release_value["images"],
        created_at="2026-08-19T12:00:00Z",
    )
    with pytest.raises(ManifestError, match="release_digest"):
        bind_validation_evidence(different_release, receipt_value)


def test_receipt_from_another_release_cannot_be_promoted() -> None:
    first = release()
    second = release()
    second["bundle_sha256"] = "c" * 64
    second["release_digest"] = "0" * 64
    # Re-create a valid release with a different bundle rather than relying on
    # an intentionally invalid digest.
    second = create_release_manifest(
        base_commit=BASE,
        source_tree=TREE,
        snapshot_commit=SNAPSHOT,
        platform="linux/amd64",
        bundle_sha256="c" * 64,
        compose_sha256="5" * 64,
        build_policy_sha256="6" * 64,
        migration_ledger_sha256="7" * 64,
        images=second["images"],
        created_at="2026-08-19T12:00:00Z",
    )
    with pytest.raises(ManifestError, match="release_digest"):
        bind_promotion_evidence(
            second,
            receipt(first),
            now="2026-08-19T13:00:00Z",
        )


def test_atomic_round_trip_and_existing_output_refusal(tmp_path: Path) -> None:
    path = tmp_path / "release.json"
    value = release()

    write_json_evidence(path, value)

    assert path.stat().st_mode & 0o777 == 0o644
    assert load_json_evidence(path) == value
    assert json.loads(path.read_text(encoding="ascii"))["release_digest"] == value["release_digest"]
    with pytest.raises(ManifestError, match="already exists"):
        write_json_evidence(path, value)


def test_symlink_evidence_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="ascii")
    link = tmp_path / "link.json"
    link.symlink_to(target)

    with pytest.raises(ManifestError, match="unsafe"):
        load_json_evidence(link)
