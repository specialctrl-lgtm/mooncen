"""Append one independent DB-attested approval receipt for a rollout proposal."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg2
from psycopg2.extras import RealDictCursor

from tools.preflight_distributed_crawler_control import (
    PreflightError,
    _assert_component_environment_permissions,
    _check_required_paths,
    _connection_config,
    _database_contract,
    _protected_environment,
)


SHA256 = re.compile(r"[0-9a-f]{64}")
OPERATOR_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{2,199}")


def canonical_uuid(value: str) -> str:
    cleaned = value.strip()
    try:
        parsed = UUID(cleaned)
    except (ValueError, AttributeError) as exc:
        raise ValueError("request id must be a canonical non-nil UUID") from exc
    if parsed.int == 0 or str(parsed) != cleaned:
        raise ValueError("request id must be a canonical non-nil UUID")
    return cleaned


def preview_release_action(
    connection: Any, *, request_id: str, request_digest: str
) -> dict[str, Any]:
    if not SHA256.fullmatch(request_digest):
        raise ValueError("request digest must be lowercase SHA-256")
    connection.set_session(readonly=True, autocommit=False)
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT request_id::text, action, environment,
                       expected_generation, request_payload, requested_by::text,
                       requester_login::text, requester_role, request_reason,
                       confirmation, request_digest, approval_status, proposal_valid
                FROM preview_crawler_release_action_for_approval(%s::uuid, %s)
                """,
                (request_id, request_digest),
            )
            row = cursor.fetchone()
        connection.rollback()
    except Exception:
        connection.rollback()
        raise
    if not row:
        raise RuntimeError("exact release proposal is unavailable to this approver")
    return dict(row)


def approve_release_action(
    connection: Any,
    *,
    request_id: str,
    request_digest: str,
    operator_label: str,
    reason: str,
    expected_confirmation: str,
    ttl_seconds: int,
) -> dict[str, Any]:
    if not SHA256.fullmatch(request_digest):
        raise ValueError("request digest must be lowercase SHA-256")
    if not OPERATOR_LABEL.fullmatch(operator_label):
        raise ValueError("operator label is not canonical")
    if reason != reason.strip() or not 3 <= len(reason) <= 500 or "\x00" in reason:
        raise ValueError("approval reason must be canonical text between 3 and 500 characters")
    if not 60 <= ttl_seconds <= 900:
        raise ValueError("approval TTL must be between 60 and 900 seconds")

    preview = preview_release_action(
        connection, request_id=request_id, request_digest=request_digest
    )
    if preview["approval_status"] != "pending":
        raise RuntimeError("release proposal is not pending independent approval")
    if preview["proposal_valid"] is not True:
        raise RuntimeError("release proposal is not a canonical rollout transition")
    if preview["confirmation"] != expected_confirmation:
        raise ValueError("typed confirmation differs from the immutable proposal")

    connection.set_session(isolation_level="SERIALIZABLE", readonly=False, autocommit=False)
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT request_id::text, receipt_id::text, environment,
                       request_digest, approver_login::text,
                       operator_identity AS operator_label,
                       approval_reason, approved_at, expires_at
                FROM approve_crawler_release_action(
                    %s::uuid, %s, %s, %s, %s
                )
                """,
                (request_id, request_digest, operator_label, reason, ttl_seconds),
            )
            row = cursor.fetchone()
        if not row:
            raise RuntimeError("release approval did not return an attested receipt")
        connection.commit()
        return dict(row)
    except Exception:
        connection.rollback()
        raise


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Approve one exact Ops-admin crawler rollout proposal",
    )
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--request-digest", required=True)
    parser.add_argument(
        "--operator-label",
        help="Audit label only; the authoritative identity is the DB-stamped approver login",
    )
    parser.add_argument("--reason")
    parser.add_argument(
        "--confirm",
        help="Retype the exact confirmation from a previous preview-only invocation",
    )
    parser.add_argument(
        "--approve-reviewed",
        action="store_true",
        help="Append a receipt after the proposal was reviewed in a separate preview run",
    )
    parser.add_argument("--ttl-seconds", type=int, default=300)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path("/etc/mooncen/crawler-release-approver.env"),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    connection = None
    try:
        if os.name == "posix" and os.geteuid() != 0:
            raise RuntimeError("release approval must load its credential through the root boundary")
        request_id = canonical_uuid(args.request_id)
        environment = _protected_environment(args.env_file, owner_only=True)
        _assert_component_environment_permissions(args.env_file, "release_approver")
        _check_required_paths("release_approver", environment)
        config = _connection_config("release_approver", environment)
        connection = psycopg2.connect(**config)
        _database_contract("release_approver", connection, config["database"])
        preview = preview_release_action(
            connection,
            request_id=request_id,
            request_digest=args.request_digest,
        )
        print(
            json.dumps(
                {"proposal": preview, "authoritative_identity": "database_login"},
                default=str,
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        if not args.approve_reviewed:
            return 0
        if not args.operator_label or not args.reason or not args.confirm:
            raise ValueError(
                "--approve-reviewed requires --operator-label, --reason, and --confirm"
            )
        result = approve_release_action(
            connection,
            request_id=request_id,
            request_digest=args.request_digest,
            operator_label=args.operator_label,
            reason=args.reason,
            expected_confirmation=args.confirm,
            ttl_seconds=args.ttl_seconds,
        )
    except (OSError, PreflightError, RuntimeError, ValueError, psycopg2.Error) as exc:
        print(f"Crawler release approval rejected: {exc}")
        return 1
    finally:
        if connection is not None:
            connection.close()
    print(json.dumps(result, default=str, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
