from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


MAX_RESULT_BYTES = 4 * 1024 * 1024
FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ActivationResultError(ValueError):
    pass


def load_result(path: Path) -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ActivationResultError("activation result file is unavailable") from exc
    if size <= 0 or size > MAX_RESULT_BYTES:
        raise ActivationResultError("activation result file size is invalid")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ActivationResultError("activation result is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ActivationResultError("activation result must be a JSON object")
    return value


def require_bool(result: dict[str, Any], field: str, expected: bool) -> None:
    value = result.get(field)
    if not isinstance(value, bool) or value is not expected:
        raise ActivationResultError(f"{field} must be {str(expected).lower()}")


def require_nonnegative_int(result: dict[str, Any], field: str) -> int:
    value = result.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ActivationResultError(f"{field} must be a non-negative integer")
    return value


def require_string_list(result: dict[str, Any], field: str) -> list[str]:
    value = result.get(field)
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item.strip() for item in value)
        or len(value) != len(set(value))
    ):
        raise ActivationResultError(f"{field} must be a unique non-empty string list")
    return value


def require_fingerprint(result: dict[str, Any], field: str = "staging_fingerprint") -> str:
    value = str(result.get(field) or "").strip().lower()
    if not FINGERPRINT_PATTERN.fullmatch(value):
        raise ActivationResultError(f"{field} must be a SHA-256 fingerprint")
    return value


def validate_close_semantics(result: dict[str, Any]) -> None:
    close_enabled = result.get("close_missing_enabled")
    if not isinstance(close_enabled, bool):
        raise ActivationResultError("close_missing_enabled must be boolean")
    requested = require_string_list(result, "close_requested_providers")
    closed = require_string_list(result, "closed_providers")
    blocked = result.get("close_blocked")
    if not isinstance(blocked, dict) or blocked:
        raise ActivationResultError("close_blocked must be an empty object")
    closed_count = require_nonnegative_int(result, "closed")
    if close_enabled:
        if not requested or set(requested) != set(closed):
            raise ActivationResultError(
                "enabled close_missing requires every requested provider to be approved"
            )
    elif requested or closed or closed_count != 0:
        raise ActivationResultError(
            "disabled close_missing cannot request providers or close courses"
        )


def validate_complete_result(
    result: dict[str, Any],
    *,
    batch_id: str,
    dry_run: bool,
    expected_status: str,
) -> str:
    if result.get("batch_id") != batch_id:
        raise ActivationResultError("activation result batch_id does not match")
    require_bool(result, "dry_run", dry_run)
    if result.get("status") != expected_status:
        raise ActivationResultError(f"status must be {expected_status}")
    if result.get("batch_status") != "COLLECTED":
        raise ActivationResultError("batch_status must be COLLECTED")
    require_bool(result, "collection_complete", True)
    require_bool(result, "partial_batch", False)
    if result.get("collection_completion_reason") != "complete":
        raise ActivationResultError("collection completion reason must be complete")

    scheduled = require_string_list(result, "scheduled_owners")
    successful = require_string_list(result, "successful_owners")
    failed = require_string_list(result, "failed_owners")
    providers = require_string_list(result, "providers")
    if not scheduled or failed or set(scheduled) != set(successful):
        raise ActivationResultError("every scheduled owner must have succeeded")
    if require_nonnegative_int(result, "providers_failed") != 0:
        raise ActivationResultError("providers_failed must be zero")
    if require_nonnegative_int(result, "providers_completed") != len(scheduled):
        raise ActivationResultError("providers_completed does not match scheduled owners")
    if not providers:
        raise ActivationResultError("activation result has no course providers")
    if require_nonnegative_int(result, "excluded_failed_branches") != 0:
        raise ActivationResultError("failed-owner branches were excluded")
    if require_nonnegative_int(result, "excluded_failed_courses") != 0:
        raise ActivationResultError("failed-owner courses were excluded")

    courses = require_nonnegative_int(result, "courses")
    valid_courses = require_nonnegative_int(result, "valid_courses")
    invalid_courses = require_nonnegative_int(result, "invalid_courses")
    if courses <= 0 or valid_courses != courses or invalid_courses != 0:
        raise ActivationResultError("course validation counts are not complete")
    validate_close_semantics(result)
    return require_fingerprint(result)


def validate_dry_run_result(result: dict[str, Any], *, batch_id: str) -> str:
    return validate_complete_result(
        result,
        batch_id=batch_id,
        dry_run=True,
        expected_status="DRY_RUN",
    )


def validate_apply_result(
    result: dict[str, Any],
    *,
    batch_id: str,
    expected_fingerprint: str,
) -> None:
    if result.get("batch_id") != batch_id:
        raise ActivationResultError("activation result batch_id does not match")
    require_bool(result, "dry_run", False)
    fingerprint = validate_complete_result(
        result,
        batch_id=batch_id,
        dry_run=False,
        expected_status="SUCCESS",
    )
    if fingerprint != expected_fingerprint:
        raise ActivationResultError("apply fingerprint does not match dry-run")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate batch-pinned staging activation JSON",
    )
    parser.add_argument("--mode", choices=("dry-run", "apply"), required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--result-file", type=Path, required=True)
    parser.add_argument("--dry-run-result-file", type=Path)
    args = parser.parse_args()
    if args.mode == "apply" and args.dry_run_result_file is None:
        parser.error("apply mode requires --dry-run-result-file")
    if args.mode == "dry-run" and args.dry_run_result_file is not None:
        parser.error("--dry-run-result-file is only valid for apply mode")
    return args


def main() -> int:
    args = parse_args()
    try:
        result = load_result(args.result_file)
        if args.mode == "dry-run":
            validate_dry_run_result(result, batch_id=args.batch_id)
        else:
            dry_run_result = load_result(args.dry_run_result_file)
            expected_fingerprint = validate_dry_run_result(
                dry_run_result,
                batch_id=args.batch_id,
            )
            validate_apply_result(
                result,
                batch_id=args.batch_id,
                expected_fingerprint=expected_fingerprint,
            )
    except ActivationResultError as exc:
        print(f"Activation result rejected: {exc}")
        return 1
    print(
        f"Activation {args.mode} result accepted for batch: {args.batch_id}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
