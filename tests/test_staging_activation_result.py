from __future__ import annotations

import pytest

from tools.validate_staging_activation_result import (
    ActivationResultError,
    validate_apply_result,
    validate_dry_run_result,
)


BATCH_ID = "reviewed-batch-1"
FINGERPRINT = "a" * 64


def complete_result(*, dry_run: bool, status: str) -> dict:
    return {
        "batch_id": BATCH_ID,
        "dry_run": dry_run,
        "status": status,
        "batch_status": "COLLECTED",
        "collection_complete": True,
        "collection_completion_reason": "complete",
        "partial_batch": False,
        "scheduled_owners": ["SCHEDULED_OWNER"],
        "successful_owners": ["SCHEDULED_OWNER"],
        "failed_owners": [],
        "providers": ["CONCRETE_PROVIDER"],
        "providers_completed": 1,
        "providers_failed": 0,
        "excluded_failed_branches": 0,
        "excluded_failed_courses": 0,
        "courses": 2,
        "valid_courses": 2,
        "invalid_courses": 0,
        "close_missing_enabled": False,
        "close_requested_providers": [],
        "closed_providers": [],
        "close_blocked": {},
        "closed": 0,
        "staging_fingerprint": FINGERPRINT,
    }


def test_reviewed_dry_run_accepts_exact_complete_batch() -> None:
    result = complete_result(dry_run=True, status="DRY_RUN")

    assert validate_dry_run_result(result, batch_id=BATCH_ID) == FINGERPRINT


def test_reviewed_dry_run_accepts_fully_approved_close_missing() -> None:
    result = complete_result(dry_run=True, status="DRY_RUN")
    result.update(
        {
            "close_missing_enabled": True,
            "close_requested_providers": ["CONCRETE_PROVIDER"],
            "closed_providers": ["CONCRETE_PROVIDER"],
            "closed": 1,
        }
    )

    assert validate_dry_run_result(result, batch_id=BATCH_ID) == FINGERPRINT


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("batch_id", "newer-batch", "batch_id"),
        ("dry_run", False, "dry_run"),
        ("status", "PARTIAL_SUCCESS", "status"),
        ("collection_complete", False, "collection_complete"),
        ("partial_batch", True, "partial_batch"),
        ("providers_failed", 1, "providers_failed"),
        ("invalid_courses", 1, "validation counts"),
        ("close_blocked", {"CONCRETE_PROVIDER": "sharp drop"}, "close_blocked"),
    ],
)
def test_reviewed_dry_run_rejects_incomplete_or_ambiguous_semantics(
    field: str,
    value,
    message: str,
) -> None:
    result = complete_result(dry_run=True, status="DRY_RUN")
    result[field] = value

    with pytest.raises(ActivationResultError, match=message):
        validate_dry_run_result(result, batch_id=BATCH_ID)


def test_disabled_close_missing_rejects_hidden_close_scope() -> None:
    result = complete_result(dry_run=True, status="DRY_RUN")
    result["close_requested_providers"] = ["CONCRETE_PROVIDER"]

    with pytest.raises(ActivationResultError, match="disabled close_missing"):
        validate_dry_run_result(result, batch_id=BATCH_ID)


def test_pinned_apply_must_match_reviewed_dry_run_fingerprint() -> None:
    result = complete_result(dry_run=False, status="SUCCESS")

    validate_apply_result(
        result,
        batch_id=BATCH_ID,
        expected_fingerprint=FINGERPRINT,
    )

    with pytest.raises(ActivationResultError, match="does not match dry-run"):
        validate_apply_result(
            result,
            batch_id=BATCH_ID,
            expected_fingerprint="b" * 64,
        )


def test_already_applied_batch_is_not_enough_for_activation() -> None:
    result = {
        "batch_id": BATCH_ID,
        "dry_run": False,
        "status": "SKIPPED_ALREADY_APPLIED",
        "staging_fingerprint": FINGERPRINT,
        "successful_apply_fingerprint": FINGERPRINT,
    }

    with pytest.raises(ActivationResultError, match="status must be SUCCESS"):
        validate_apply_result(
            result,
            batch_id=BATCH_ID,
            expected_fingerprint=FINGERPRINT,
        )
