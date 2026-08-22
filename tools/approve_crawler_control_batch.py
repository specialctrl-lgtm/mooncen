from __future__ import annotations

import argparse
import json
import os
import stat
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg2
from psycopg2.extras import RealDictCursor

from tools.apply_staging_batch import (
    load_batch_metadata,
    load_rows,
    staging_selection_fingerprint,
)
from tools.preflight_distributed_crawler_control import (
    PreflightError,
    _check_required_paths,
    _assert_component_environment_permissions,
    _connection_config,
    _database_contract,
    _protected_environment,
)
from tools.validate_staging_activation_result import (
    ActivationResultError,
    load_result,
    validate_dry_run_result,
)


def canonical_batch_id(value: str) -> str:
    cleaned = value.strip()
    try:
        canonical = str(UUID(cleaned))
    except (ValueError, AttributeError) as exc:
        raise ValueError("control batch id must be a canonical UUID") from exc
    if canonical != cleaned:
        raise ValueError("control batch id must be a canonical UUID")
    return canonical


def reviewed_fingerprint(path: Path, *, batch_id: str) -> str:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ActivationResultError("reviewed dry-run result is unavailable") from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise ActivationResultError("reviewed dry-run result is not a regular file")
    if os.name == "posix" and (
        metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise ActivationResultError("reviewed dry-run result ownership or mode is unsafe")
    return validate_dry_run_result(load_result(path), batch_id=batch_id)


def _current_fingerprint(connection, batch_id: str) -> str:
    metadata = load_batch_metadata(connection, batch_id)
    if not metadata:
        raise RuntimeError("control batch metadata disappeared during approval")
    result = metadata.get("result") or {}
    branches, courses = load_rows(
        connection,
        batch_id,
        batch_result=result,
    )
    return staging_selection_fingerprint(metadata, branches, courses)


def approve_batch(
    connection,
    *,
    batch_id: str,
    expected_fingerprint: str,
) -> dict[str, Any]:
    connection.set_session(
        isolation_level="SERIALIZABLE",
        readonly=False,
        autocommit=False,
    )
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT staging.status AS staging_status,
                       staging.result,
                       control.status AS control_status
                FROM crawl_batches staging
                JOIN ops_crawler_batches control
                  ON control.id::text = staging.crawl_batch_id
                WHERE staging.crawl_batch_id = %s
                FOR UPDATE OF staging
                """,
                (batch_id,),
            )
            row = cursor.fetchone()
        if not row:
            raise RuntimeError("control batch does not exist in both control and staging metadata")
        result = dict(row.get("result") or {})
        if result.get("control_plane") is not True or result.get("control_batch_id") != batch_id:
            raise RuntimeError("batch is not sealed distributed control-plane output")
        if row.get("control_status") != "success" or row.get("staging_status") != "COLLECTED":
            raise RuntimeError("only a successful COLLECTED control batch can be approved")
        if (
            result.get("collection_outcome") != "success"
            or result.get("collection_complete") is not True
            or result.get("control_plane_rejected") is True
        ):
            raise RuntimeError("control batch does not contain complete successful evidence")

        if result.get("promotion_eligible") is True:
            if result.get("promotion_approval_fingerprint") == expected_fingerprint:
                connection.commit()
                return {
                    "status": "ALREADY_APPROVED",
                    "batch_id": batch_id,
                    "reviewed_staging_fingerprint": expected_fingerprint,
                }
            raise RuntimeError("control batch was already approved with different evidence")
        if result.get("promotion_policy") != "held":
            raise RuntimeError("control batch is not in the explicit approval workflow")

        current_fingerprint = _current_fingerprint(connection, batch_id)
        if current_fingerprint != expected_fingerprint:
            raise RuntimeError("staging evidence changed after the reviewed dry-run")

        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE crawl_batches
                SET result = result || jsonb_build_object(
                        'promotion_eligible', true,
                        'promotion_policy', 'approved',
                        'promotion_approval_fingerprint', %s,
                        'promotion_approved_at', CURRENT_TIMESTAMP
                    ),
                    updated_at = CURRENT_TIMESTAMP
                WHERE crawl_batch_id = %s
                  AND result->>'control_plane' = 'true'
                  AND result->>'promotion_eligible' = 'false'
                """,
                (expected_fingerprint, batch_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("control batch lost approval ownership")
        connection.commit()
        return {
            "status": "APPROVED",
            "batch_id": batch_id,
            "reviewed_staging_fingerprint": expected_fingerprint,
        }
    except Exception:
        connection.rollback()
        raise


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Approve one reviewed distributed crawler batch for pinned promotion",
    )
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--dry-run-result-file", required=True, type=Path)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path("/etc/mooncen/crawler-control-approver.env"),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    connection = None
    try:
        batch_id = canonical_batch_id(args.batch_id)
        fingerprint = reviewed_fingerprint(args.dry_run_result_file, batch_id=batch_id)
        environment = _protected_environment(args.env_file)
        _assert_component_environment_permissions(args.env_file, "approver")
        _check_required_paths("approver", environment)
        config = _connection_config("approver", environment)
        connection = psycopg2.connect(**config)
        _database_contract("approver", connection, config["database"])
        result = approve_batch(
            connection,
            batch_id=batch_id,
            expected_fingerprint=fingerprint,
        )
    except (
        ActivationResultError,
        OSError,
        PreflightError,
        RuntimeError,
        ValueError,
        psycopg2.Error,
    ) as exc:
        print(f"Crawler control batch approval rejected: {exc}")
        return 1
    finally:
        if connection is not None:
            connection.close()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
