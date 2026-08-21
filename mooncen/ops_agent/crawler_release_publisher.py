"""Publish one validated crawler desired-state document from the staging control DB."""

from __future__ import annotations

import argparse
import json
import os
import stat
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping

import psycopg2
from psycopg2.extras import RealDictCursor

from ops_agent.crawler_control_db import publisher_database_config
from ops_agent.crawler_release_control import (
    DESIRED_STATE_SCHEMA_VERSION,
    EXPECTED_DATABASE_CONTRACT,
    assert_expected_database_contract,
    parse_desired_state,
)


_STATUS_STATES: Mapping[str, frozenset[str]] = {
    "planned": frozenset({"paused"}),
    "running": frozenset({"canary", "rolling"}),
    "paused": frozenset({"paused"}),
    "success": frozenset({"complete"}),
    "failed": frozenset({"paused"}),
    "cancelled": frozenset({"paused"}),
    "rolling_back": frozenset({"rollback"}),
    "rolled_back": frozenset({"rollback"}),
}


class ReleasePublisherError(RuntimeError):
    pass


def _artifact_document(row: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        "code_version": row["code_version"],
        "relative_path": row["artifact_path"],
        "sha256": row["artifact_digest"],
        "size_bytes": int(row["size_bytes"]),
        "config_revision": row["config_revision"],
    }
    if row.get("signature") is not None or row.get("key_id") is not None:
        result["signature"] = row.get("signature")
        result["key_id"] = row.get("key_id")
    return result


def build_desired_state_document(
    rollout: Mapping[str, Any],
    workers: Iterable[Mapping[str, Any]],
    artifacts: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Map the relational contract to the strict worker contract and validate it."""
    strategy = rollout.get("strategy")
    if type(strategy) is not dict:
        raise ReleasePublisherError("rollout strategy must be an object")
    state = strategy.get("state")
    status = str(rollout.get("status") or "")
    if state not in _STATUS_STATES.get(status, frozenset()):
        raise ReleasePublisherError("rollout status and desired-state phase disagree")
    canaries = strategy.get("canary_workers")
    if type(canaries) is not list:
        raise ReleasePublisherError("rollout strategy must contain canary_workers")

    target_digest = str(rollout.get("artifact_digest") or "")
    baseline_digest = str(rollout.get("previous_artifact_digest") or "")
    if not target_digest or not baseline_digest or target_digest == baseline_digest:
        raise ReleasePublisherError("rollout requires distinct target and rollback artifacts")
    artifact_rows = {str(row.get("artifact_digest") or ""): row for row in artifacts}
    if set(artifact_rows) != {target_digest, baseline_digest}:
        raise ReleasePublisherError("rollout artifact set is incomplete or ambiguous")
    target = artifact_rows[target_digest]
    baseline = artifact_rows[baseline_digest]

    generation = int(rollout.get("rollout_epoch") or 0)
    rollout_id = str(rollout.get("id") or "")
    worker_rows = list(workers)
    if len(worker_rows) != int(rollout.get("requested_worker_count") or 0):
        raise ReleasePublisherError("rollout worker count does not match desired state")
    worker_documents: list[dict[str, Any]] = []
    for worker in worker_rows:
        if str(worker.get("rollout_id") or "") != rollout_id:
            raise ReleasePublisherError("worker desired state belongs to another rollout")
        if int(worker.get("generation") or 0) != generation:
            raise ReleasePublisherError("worker generation differs from rollout epoch")
        digest = str(worker.get("artifact_digest") or "")
        artifact = artifact_rows.get(digest)
        if artifact is None:
            raise ReleasePublisherError("worker references an artifact outside this rollout")
        if (
            worker.get("code_version") != artifact.get("code_version")
            or worker.get("config_revision") != artifact.get("config_revision")
        ):
            raise ReleasePublisherError("worker release identity differs from its artifact")
        desired_status = str(worker.get("desired_status") or "")
        if desired_status not in {"active", "draining", "disabled"}:
            raise ReleasePublisherError("worker desired status is invalid")
        worker_documents.append(
            {
                "worker_id": worker["worker_key"],
                "desired_version": artifact["code_version"],
                "config_revision": artifact["config_revision"],
                "cohort": worker["cohort"],
                "enabled": desired_status != "disabled",
            }
        )

    document = {
        "schema_version": DESIRED_STATE_SCHEMA_VERSION,
        "environment": rollout["environment"],
        "generation": generation,
        "rollout": {
            "id": rollout_id,
            "state": state,
            "target_version": target["code_version"],
            "baseline_version": baseline["code_version"],
            "canary_workers": canaries,
        },
        "artifacts": [_artifact_document(target), _artifact_document(baseline)],
        "workers": worker_documents,
    }
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    try:
        parse_desired_state(encoded)
    except ValueError as exc:
        raise ReleasePublisherError("relational desired state failed the worker contract") from exc
    return document


def load_desired_state_document(connection, environment: str) -> dict[str, Any]:
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = ANY(%s)
                """,
                (list(EXPECTED_DATABASE_CONTRACT),),
            )
            observed_columns: dict[str, set[str]] = {}
            for row in cursor.fetchall():
                observed_columns.setdefault(str(row["table_name"]), set()).add(
                    str(row["column_name"])
                )
            assert_expected_database_contract(observed_columns)
            cursor.execute(
                """
                SELECT id::text, environment, rollout_epoch, artifact_digest,
                       previous_artifact_digest, status, requested_worker_count, strategy
                FROM ops_crawler_release_rollouts
                WHERE environment = %s
                ORDER BY rollout_epoch DESC
                LIMIT 1
                """,
                (environment,),
            )
            rollout_row = cursor.fetchone()
            if not rollout_row:
                raise ReleasePublisherError("no crawler rollout exists for this environment")
            rollout = dict(rollout_row)
            cursor.execute(
                """
                SELECT environment, worker_key, rollout_id::text, generation,
                       desired_status, cohort, artifact_digest, code_version,
                       config_revision, not_before
                FROM ops_crawler_worker_desired_state
                WHERE environment = %s
                  AND rollout_id = %s
                  AND not_before <= CURRENT_TIMESTAMP
                ORDER BY worker_key
                """,
                (environment, rollout["id"]),
            )
            workers = [dict(row) for row in cursor.fetchall()]
            cursor.execute(
                """
                SELECT artifact_digest, code_version, config_revision, artifact_path,
                       size_bytes, signature, key_id
                FROM ops_crawler_release_artifacts
                WHERE artifact_digest = ANY(%s)
                ORDER BY artifact_digest
                """,
                ([rollout["artifact_digest"], rollout["previous_artifact_digest"]],),
            )
            artifacts = [dict(row) for row in cursor.fetchall()]
        connection.commit()
        return build_desired_state_document(rollout, workers, artifacts)
    except Exception:
        connection.rollback()
        raise


def encode_desired_state(document: Mapping[str, Any]) -> bytes:
    encoded = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    parse_desired_state(encoded)
    return encoded


def atomic_publish(path: Path, payload: bytes) -> None:
    destination = path.resolve(strict=False)
    parent = destination.parent
    if not destination.is_absolute() or parent.is_symlink() or not parent.is_dir():
        raise ReleasePublisherError("desired-state output directory is unavailable")
    if destination.exists():
        metadata = destination.lstat()
        if not stat.S_ISREG(metadata.st_mode) or destination.is_symlink():
            raise ReleasePublisherError("desired-state output must be a regular file")
    temporary = parent / f".{destination.name}.{uuid.uuid4().hex}.new"
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        if os.name == "posix":
            directory_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise ReleasePublisherError("desired-state atomic publication failed") from exc


def publish_once(environment: str, output: Path) -> None:
    connection = psycopg2.connect(**publisher_database_config())
    try:
        document = load_desired_state_document(connection, environment)
    finally:
        connection.close()
    atomic_publish(output, encode_desired_state(document))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish crawler worker desired state from PostgreSQL")
    parser.add_argument("--environment", default=os.getenv("OPS_CRAWLER_RELEASE_ENVIRONMENT", ""))
    parser.add_argument("--output", default=os.getenv("OPS_CRAWLER_DESIRED_STATE_OUTPUT", ""))
    args = parser.parse_args(argv)
    if args.environment not in {"production", "staging", "development"}:
        parser.error("--environment must be production, staging, or development")
    if not args.output or not Path(args.output).is_absolute():
        parser.error("--output must be an explicit absolute path")
    publish_once(args.environment, Path(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
