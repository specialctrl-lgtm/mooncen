"""Root-operated, fail-closed distributed crawler release administration.

The command publishes only locally reviewed OpenSSH-signed archives and uses a
dedicated database role that cannot enqueue crawler work or approve collected
data.  Files become immutable and durable before PostgreSQL metadata points at
them; a failed database transaction can therefore leave only a harmless orphan
artifact, never metadata for a missing or partially-written file.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import stat
import subprocess
import uuid
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg2
from psycopg2.extras import Json, RealDictCursor

from ops_agent.crawler_release_control import MAX_ARTIFACT_BYTES, MAX_SIGNATURE_BYTES
from tools.preflight_distributed_crawler_control import (
    PreflightError,
    _assert_component_environment_permissions,
    _check_required_paths,
    _connection_config,
    _database_contract,
    _protected_environment,
)


SSHSIG_NAMESPACE = "mooncen-crawler-release-v1"
SSHSIG_VERIFY_PATH = Path("/usr/bin/ssh-keygen")
MAX_WORKERS_FILE_BYTES = 128 * 1024
COPY_CHUNK_BYTES = 1024 * 1024
ADVISORY_LOCK_NAME = "mooncen:crawler-release-admin:v1"
DEFAULT_RELEASE_FRESH_SECONDS = 360

_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_CONFIG_REVISION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,127}$")
_WORKER_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_HOSTNAME = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,62})(?:\.[a-z0-9](?:[a-z0-9-]{0,62}))*$"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ENVIRONMENTS = frozenset({"production", "staging", "development"})
_ROLLOUT_PHASES = frozenset({"paused", "canary", "rolling", "complete", "rollback", "rolled_back"})


class CrawlerReleaseAdminError(RuntimeError):
    pass


def _duplicates_rejected(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CrawlerReleaseAdminError("review document contains a duplicate field")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise CrawlerReleaseAdminError("review document contains a non-finite number")


def _canonical_uuid(value: str, *, label: str) -> str:
    cleaned = str(value).strip()
    try:
        parsed = UUID(cleaned)
    except (ValueError, AttributeError) as exc:
        raise CrawlerReleaseAdminError(f"{label} must be a canonical UUID") from exc
    canonical = str(parsed)
    if canonical != cleaned or parsed.int == 0:
        raise CrawlerReleaseAdminError(f"{label} must be a canonical non-nil UUID")
    return canonical


def _bounded_identity(value: str, pattern: re.Pattern[str], *, label: str) -> str:
    cleaned = str(value).strip()
    if not pattern.fullmatch(cleaned):
        raise CrawlerReleaseAdminError(f"{label} is invalid")
    return cleaned


def _safe_file_metadata(path: Path, *, label: str, maximum: int) -> os.stat_result:
    if not path.is_absolute():
        raise CrawlerReleaseAdminError(f"{label} path must be absolute")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise CrawlerReleaseAdminError(f"{label} is unavailable") from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise CrawlerReleaseAdminError(f"{label} must be a regular non-symlink file")
    if not 1 <= metadata.st_size <= maximum:
        raise CrawlerReleaseAdminError(f"{label} size is invalid")
    if os.name == "posix" and (
        metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise CrawlerReleaseAdminError(f"{label} ownership or mode is unsafe")
    return metadata


@contextmanager
def _secure_reader(path: Path, *, label: str, maximum: int):
    expected = _safe_file_metadata(path, label=label, maximum=maximum)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CrawlerReleaseAdminError(f"{label} could not be opened safely") from exc
    try:
        observed = os.fstat(descriptor)
        if (
            not stat.S_ISREG(observed.st_mode)
            or observed.st_dev != expected.st_dev
            or observed.st_ino != expected.st_ino
            or observed.st_size != expected.st_size
        ):
            raise CrawlerReleaseAdminError(f"{label} changed while it was opened")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            yield handle, observed
        final = os.fstat(descriptor)
        if final.st_size != observed.st_size or final.st_mtime_ns != observed.st_mtime_ns:
            raise CrawlerReleaseAdminError(f"{label} changed while it was read")
    finally:
        os.close(descriptor)


def _secure_directory(path: Path, *, label: str, owner_required: bool = True) -> Path:
    if not path.is_absolute():
        raise CrawlerReleaseAdminError(f"{label} must be absolute")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise CrawlerReleaseAdminError(f"{label} is unavailable") from exc
    if path.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
        raise CrawlerReleaseAdminError(f"{label} must be a regular directory")
    if os.name == "posix" and (
        (owner_required and metadata.st_uid != 0) or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise CrawlerReleaseAdminError(f"{label} ownership or mode is unsafe")
    return path


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _hash_regular_file(path: Path, *, expected_size: int | None = None) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with _secure_reader(path, label="published crawler artifact", maximum=MAX_ARTIFACT_BYTES) as (
        handle,
        metadata,
    ):
        if expected_size is not None and metadata.st_size != expected_size:
            raise CrawlerReleaseAdminError("published crawler artifact size differs from metadata")
        while chunk := handle.read(COPY_CHUNK_BYTES):
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def _read_signature(path: Path) -> bytes:
    with _secure_reader(path, label="crawler artifact signature", maximum=MAX_SIGNATURE_BYTES) as (
        handle,
        _metadata,
    ):
        signature = handle.read(MAX_SIGNATURE_BYTES + 1)
    if not signature or len(signature) > MAX_SIGNATURE_BYTES:
        raise CrawlerReleaseAdminError("crawler artifact signature size is invalid")
    return signature


def _verify_signature(
    archive: Path,
    signature: Path,
    allowed_signers: Path,
    *,
    key_id: str,
) -> bytes:
    signature_bytes = _read_signature(signature)
    _safe_file_metadata(
        allowed_signers,
        label="crawler allowed-signers policy",
        maximum=MAX_SIGNATURE_BYTES,
    )
    try:
        verifier = SSHSIG_VERIFY_PATH.lstat()
    except OSError as exc:
        raise CrawlerReleaseAdminError("fixed OpenSSH signature verifier is unavailable") from exc
    if (
        SSHSIG_VERIFY_PATH.is_symlink()
        or not stat.S_ISREG(verifier.st_mode)
        or not verifier.st_mode & stat.S_IXUSR
        or (os.name == "posix" and (verifier.st_uid != 0 or stat.S_IMODE(verifier.st_mode) & 0o022))
    ):
        raise CrawlerReleaseAdminError("fixed OpenSSH signature verifier is unavailable")
    try:
        with archive.open("rb") as source:
            completed = subprocess.run(
                [
                    str(SSHSIG_VERIFY_PATH),
                    "-Y",
                    "verify",
                    "-f",
                    str(allowed_signers),
                    "-I",
                    key_id,
                    "-n",
                    SSHSIG_NAMESPACE,
                    "-s",
                    str(signature),
                ],
                stdin=source,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60,
                check=False,
                shell=False,
                env={"PATH": "/usr/bin:/bin", "LANG": "C"},
            )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CrawlerReleaseAdminError("crawler artifact signature verification could not run") from exc
    if completed.returncode != 0:
        raise CrawlerReleaseAdminError("crawler artifact OpenSSH signature is invalid")
    return signature_bytes


def publish_reviewed_artifact(
    source: Path,
    signature: Path,
    public_root: Path,
    allowed_signers: Path,
    *,
    expected_digest: str,
    key_id: str,
) -> tuple[Path, int, str]:
    digest_expected = _bounded_identity(expected_digest, _SHA256, label="artifact SHA-256")
    normalized_key = _bounded_identity(key_id, _KEY_ID, label="artifact key id")
    root = _secure_directory(public_root, label="crawler release public root")
    artifacts = _secure_directory(root / "artifacts", label="crawler artifact directory")
    destination = artifacts / f"{digest_expected}.tar.gz"
    relative_path = f"artifacts/{destination.name}"

    if destination.exists() or destination.is_symlink():
        observed_digest, observed_size = _hash_regular_file(destination)
        if observed_digest != digest_expected:
            raise CrawlerReleaseAdminError("published artifact path contains conflicting bytes")
        _verify_signature(destination, signature, allowed_signers, key_id=normalized_key)
        return destination, observed_size, relative_path

    temporary = artifacts / f".{digest_expected}.{uuid.uuid4().hex}.new"
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        digest = hashlib.sha256()
        copied = 0
        with _secure_reader(source, label="crawler release archive", maximum=MAX_ARTIFACT_BYTES) as (
            source_handle,
            source_metadata,
        ):
            with os.fdopen(descriptor, "wb", closefd=False) as destination_handle:
                while chunk := source_handle.read(COPY_CHUNK_BYTES):
                    copied += len(chunk)
                    digest.update(chunk)
                    destination_handle.write(chunk)
                destination_handle.flush()
                os.fsync(destination_handle.fileno())
            if copied != source_metadata.st_size:
                raise CrawlerReleaseAdminError("crawler release archive changed during copy")
        if digest.hexdigest() != digest_expected:
            raise CrawlerReleaseAdminError("crawler release archive SHA-256 does not match review")
        os.close(descriptor)
        descriptor = None
        _verify_signature(temporary, signature, allowed_signers, key_id=normalized_key)
        os.chmod(temporary, 0o644)
        try:
            os.link(temporary, destination)
        except FileExistsError:
            observed_digest, observed_size = _hash_regular_file(destination)
            if observed_digest != digest_expected or observed_size != copied:
                raise CrawlerReleaseAdminError("concurrent artifact publication conflicts")
        _fsync_directory(artifacts)
        return destination, copied, relative_path
    except OSError as exc:
        raise CrawlerReleaseAdminError("crawler artifact publication failed") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def register_artifact(
    connection,
    *,
    artifact_digest: str,
    code_version: str,
    config_revision: str,
    artifact_path: str,
    size_bytes: int,
    signature_bytes: bytes,
    key_id: str,
) -> dict[str, Any]:
    signature_b64 = base64.b64encode(signature_bytes).decode("ascii")
    metadata = {
        "schema_version": 1,
        "registered_by": "crawler-release-admin",
        "signature_namespace": SSHSIG_NAMESPACE,
    }
    connection.set_session(isolation_level="SERIALIZABLE", readonly=False, autocommit=False)
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (ADVISORY_LOCK_NAME,))
            cursor.execute(
                """
                INSERT INTO ops_crawler_release_artifacts (
                    artifact_digest, code_version, config_revision, artifact_path,
                    size_bytes, signature, key_id, metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (artifact_digest) DO NOTHING
                """,
                (
                    artifact_digest,
                    code_version,
                    config_revision,
                    artifact_path,
                    size_bytes,
                    signature_b64,
                    key_id,
                    Json(metadata),
                ),
            )
            cursor.execute(
                """
                SELECT artifact_digest, code_version, config_revision, artifact_path,
                       size_bytes, signature, key_id, metadata
                FROM ops_crawler_release_artifacts
                WHERE artifact_digest = %s
                FOR SHARE
                """,
                (artifact_digest,),
            )
            row = cursor.fetchone()
            expected = {
                "artifact_digest": artifact_digest,
                "code_version": code_version,
                "config_revision": config_revision,
                "artifact_path": artifact_path,
                "size_bytes": size_bytes,
                "signature": signature_b64,
                "key_id": key_id,
                "metadata": metadata,
            }
            if row is None or dict(row) != expected:
                raise CrawlerReleaseAdminError("artifact digest is already registered with conflicting metadata")
        connection.commit()
        return expected
    except Exception:
        connection.rollback()
        raise


def load_reviewed_workers(path: Path, *, environment: str) -> list[dict[str, Any]]:
    with _secure_reader(path, label="crawler worker review document", maximum=MAX_WORKERS_FILE_BYTES) as (
        handle,
        _metadata,
    ):
        encoded = handle.read(MAX_WORKERS_FILE_BYTES + 1)
    try:
        payload = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_duplicates_rejected,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CrawlerReleaseAdminError("crawler worker review document is invalid JSON") from exc
    if type(payload) is not dict or set(payload) != {"schema_version", "environment", "workers"}:
        raise CrawlerReleaseAdminError("crawler worker review document contract is invalid")
    if payload["schema_version"] != 1 or payload["environment"] != environment:
        raise CrawlerReleaseAdminError("crawler worker review environment is invalid")
    raw_workers = payload["workers"]
    if type(raw_workers) is not list or not 1 <= len(raw_workers) <= 256:
        raise CrawlerReleaseAdminError("crawler worker review list is invalid")
    workers: list[dict[str, Any]] = []
    keys: set[str] = set()
    agent_ids: set[str] = set()
    for raw in raw_workers:
        if type(raw) is not dict or set(raw) != {
            "worker_key",
            "agent_id",
            "hostname",
            "cohort",
            "enabled",
        }:
            raise CrawlerReleaseAdminError("reviewed worker contract is invalid")
        worker = {
            "worker_key": _bounded_identity(raw["worker_key"], _WORKER_KEY, label="worker key"),
            "agent_id": _canonical_uuid(raw["agent_id"], label="worker agent id"),
            "hostname": _bounded_identity(raw["hostname"], _HOSTNAME, label="worker hostname"),
            "cohort": raw["cohort"],
            "enabled": raw["enabled"],
        }
        if worker["cohort"] not in {"canary", "stable"} or type(worker["enabled"]) is not bool:
            raise CrawlerReleaseAdminError("worker cohort or enabled state is invalid")
        if worker["worker_key"] in keys or worker["agent_id"] in agent_ids:
            raise CrawlerReleaseAdminError("reviewed workers repeat an identity")
        keys.add(worker["worker_key"])
        agent_ids.add(worker["agent_id"])
        workers.append(worker)
    if not any(worker["enabled"] and worker["cohort"] == "canary" for worker in workers):
        raise CrawlerReleaseAdminError("at least one enabled canary worker is required")
    return workers


def _lock_release_admin(cursor) -> None:
    cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (ADVISORY_LOCK_NAME,))


def _artifact_rows(cursor, digests: Sequence[str]) -> dict[str, dict[str, Any]]:
    cursor.execute(
        """
        SELECT artifact_digest, code_version, config_revision, artifact_path,
               size_bytes, signature, key_id
        FROM ops_crawler_release_artifacts
        WHERE artifact_digest = ANY(%s)
        FOR SHARE
        """,
        (list(digests),),
    )
    rows = {str(row["artifact_digest"]): dict(row) for row in cursor.fetchall()}
    if set(rows) != set(digests) or any(not row["signature"] or not row["key_id"] for row in rows.values()):
        raise CrawlerReleaseAdminError("rollout references an unavailable or unsigned artifact")
    return rows


def _verify_artifact_row_files(
    public_root: Path,
    artifacts: Mapping[str, Mapping[str, Any]],
) -> None:
    root = _secure_directory(public_root, label="crawler release public root")
    artifact_directory = _secure_directory(root / "artifacts", label="crawler artifact directory")
    for digest, row in artifacts.items():
        expected_name = f"{digest}.tar.gz"
        if row["artifact_path"] != f"artifacts/{expected_name}":
            raise CrawlerReleaseAdminError("rollout artifact path is not canonical")
        observed_digest, observed_size = _hash_regular_file(
            artifact_directory / expected_name,
            expected_size=int(row["size_bytes"]),
        )
        if observed_digest != digest or observed_size != int(row["size_bytes"]):
            raise CrawlerReleaseAdminError("rollout artifact bytes are unavailable or drifted")


def _validate_enrolled_workers(cursor, workers: Sequence[Mapping[str, Any]], environment: str) -> None:
    agent_ids = [worker["agent_id"] for worker in workers]
    cursor.execute(
        """
        SELECT id::text, name, hostname, environment, status, maintenance_mode
        FROM ops_agents
        WHERE id = ANY(%s::uuid[])
        FOR SHARE
        """,
        (agent_ids,),
    )
    agents = {str(row["id"]): row for row in cursor.fetchall()}
    cursor.execute(
        """
        SELECT agent_id::text, environment, binding_type
        FROM ops_crawler_agent_bindings
        WHERE agent_id = ANY(%s::uuid[])
        FOR SHARE
        """,
        (agent_ids,),
    )
    bindings: dict[str, set[str]] = {}
    for row in cursor.fetchall():
        if row["environment"] == environment:
            bindings.setdefault(str(row["agent_id"]), set()).add(str(row["binding_type"]))
    for worker in workers:
        agent = agents.get(worker["agent_id"])
        if (
            agent is None
            or agent["hostname"] != worker["hostname"]
            or agent["environment"] != environment
            or agent["status"] not in {"unknown", "healthy"}
            or agent["maintenance_mode"] is not False
            or bindings.get(worker["agent_id"]) != {"worker", "reporter"}
        ):
            raise CrawlerReleaseAdminError(
                f"worker {worker['worker_key']} is not exactly enrolled for release management"
            )


def _append_rollout_worker_snapshot(
    cursor,
    *,
    environment: str,
    rollout_id: str,
    generation: int,
    worker: Mapping[str, Any],
    desired_status: str,
    artifact: Mapping[str, Any],
) -> None:
    cursor.execute(
        """
        INSERT INTO ops_crawler_rollout_worker_snapshots (
            environment, rollout_id, generation, worker_key, agent_id,
            desired_status, cohort, artifact_digest, code_version,
            config_revision
        ) VALUES (%s, %s::uuid, %s, %s, %s::uuid, %s, %s, %s, %s, %s)
        """,
        (
            environment,
            rollout_id,
            generation,
            worker["worker_key"],
            worker["agent_id"],
            desired_status,
            worker["cohort"],
            artifact["artifact_digest"],
            artifact["code_version"],
            artifact["config_revision"],
        ),
    )
    if cursor.rowcount != 1:
        raise CrawlerReleaseAdminError("rollout worker snapshot was not appended")


def create_rollout(
    connection,
    *,
    environment: str,
    rollout_id: str,
    generation: int,
    target_digest: str,
    baseline_digest: str,
    workers: Sequence[Mapping[str, Any]],
    public_root: Path,
) -> dict[str, Any]:
    if target_digest == baseline_digest:
        raise CrawlerReleaseAdminError("target and baseline artifacts must differ")
    connection.set_session(isolation_level="SERIALIZABLE", readonly=False, autocommit=False)
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            _lock_release_admin(cursor)
            cursor.execute(
                "SELECT COALESCE(MAX(rollout_epoch), 0) AS latest_epoch "
                "FROM ops_crawler_release_rollouts WHERE environment = %s",
                (environment,),
            )
            if int(cursor.fetchone()["latest_epoch"]) + 1 != generation:
                raise CrawlerReleaseAdminError("rollout generation is not the next monotonic value")
            cursor.execute(
                """
                SELECT id::text FROM ops_crawler_release_rollouts
                WHERE environment = %s
                  AND status IN ('planned', 'running', 'paused', 'rolling_back')
                FOR UPDATE
                """,
                (environment,),
            )
            if cursor.fetchone() is not None:
                raise CrawlerReleaseAdminError("another crawler rollout is still active")
            artifacts = _artifact_rows(cursor, (target_digest, baseline_digest))
            _verify_artifact_row_files(public_root, artifacts)
            _validate_enrolled_workers(cursor, workers, environment)
            canaries = [
                worker["worker_key"]
                for worker in workers
                if worker["enabled"] and worker["cohort"] == "canary"
            ]
            strategy = {"schema_version": 1, "state": "canary", "canary_workers": canaries}
            cursor.execute(
                """
                INSERT INTO ops_crawler_release_rollouts (
                    id, environment, rollout_epoch, artifact_digest,
                    previous_artifact_digest, status, requested_worker_count,
                    strategy, started_at
                ) VALUES (%s::uuid, %s, %s, %s, %s, 'running', %s, %s, CURRENT_TIMESTAMP)
                """,
                (
                    rollout_id,
                    environment,
                    generation,
                    target_digest,
                    baseline_digest,
                    len(workers),
                    Json(strategy),
                ),
            )
            for worker in workers:
                use_target = worker["enabled"] and worker["worker_key"] in canaries
                artifact = artifacts[target_digest if use_target else baseline_digest]
                cursor.execute(
                    """
                    INSERT INTO ops_crawler_worker_desired_state (
                        environment, worker_key, agent_id, rollout_id, generation,
                        desired_status, cohort, artifact_digest, code_version,
                        config_revision, not_before
                    ) VALUES (%s, %s, %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s,
                              CURRENT_TIMESTAMP)
                    ON CONFLICT (environment, worker_key) DO UPDATE
                    SET agent_id = EXCLUDED.agent_id,
                        rollout_id = EXCLUDED.rollout_id,
                        generation = EXCLUDED.generation,
                        desired_status = EXCLUDED.desired_status,
                        cohort = EXCLUDED.cohort,
                        artifact_digest = EXCLUDED.artifact_digest,
                        code_version = EXCLUDED.code_version,
                        config_revision = EXCLUDED.config_revision,
                        not_before = EXCLUDED.not_before,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE ops_crawler_worker_desired_state.generation < EXCLUDED.generation
                    """,
                    (
                        environment,
                        worker["worker_key"],
                        worker["agent_id"],
                        rollout_id,
                        generation,
                        "active" if worker["enabled"] else "disabled",
                        worker["cohort"],
                        artifact["artifact_digest"],
                        artifact["code_version"],
                        artifact["config_revision"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise CrawlerReleaseAdminError("worker desired state generation did not advance")
                _append_rollout_worker_snapshot(
                    cursor,
                    environment=environment,
                    rollout_id=rollout_id,
                    generation=generation,
                    worker=worker,
                    desired_status="active" if worker["enabled"] else "disabled",
                    artifact=artifact,
                )
        connection.commit()
        return {
            "status": "CREATED",
            "environment": environment,
            "rollout_id": rollout_id,
            "generation": generation,
            "phase": "canary",
            "canary_workers": canaries,
        }
    except Exception:
        connection.rollback()
        raise


def _require_ready_reports(
    cursor,
    rollout: Mapping[str, Any],
    workers: Sequence[Mapping[str, Any]],
    *,
    fresh_seconds: int = DEFAULT_RELEASE_FRESH_SECONDS,
) -> None:
    if not 30 <= fresh_seconds <= 900:
        raise CrawlerReleaseAdminError("release evidence freshness is out of bounds")
    expected = {
        str(worker["worker_key"]): worker
        for worker in workers
        if worker["desired_status"] == "active"
    }
    if not expected:
        return
    cursor.execute(
        """
        SELECT DISTINCT ON (report.worker_key)
               report.worker_key, report.agent_id::text, report.desired_generation,
               report.status, report.artifact_digest, report.code_version,
               report.config_revision, report.health, report.reported_at,
               agent.status AS agent_status, agent.maintenance_mode,
               report.reported_at >= clock_timestamp()
                   - (%s * interval '1 second') AS report_fresh,
               agent.last_seen_at IS NOT NULL
                   AND agent.last_seen_at >= clock_timestamp()
                       - (%s * interval '1 second') AS agent_fresh
        FROM ops_crawler_release_reports report
        LEFT JOIN ops_agents agent ON agent.id = report.agent_id
        WHERE report.rollout_id = %s::uuid
          AND report.worker_key = ANY(%s)
        ORDER BY report.worker_key, report.reported_at DESC,
                 report.created_at DESC, report.id DESC
        """,
        (fresh_seconds, fresh_seconds, rollout["id"], list(expected)),
    )
    reports = {str(row["worker_key"]): row for row in cursor.fetchall()}
    for worker_key, desired in expected.items():
        report = reports.get(worker_key)
        if (
            report is None
            or str(report["agent_id"]) != str(desired["agent_id"])
            or int(report["desired_generation"]) != int(rollout["rollout_epoch"])
            or report["status"] not in {"ready", "rolled_back"}
            or report["artifact_digest"] != desired["artifact_digest"]
            or report["code_version"] != desired["code_version"]
            or report["config_revision"] != desired["config_revision"]
            or type(report["health"]) is not dict
            or report["health"].get("healthy") is not True
            or report["report_fresh"] is not True
            or report["agent_status"] != "healthy"
            or report["maintenance_mode"] is not False
            or report["agent_fresh"] is not True
        ):
            raise CrawlerReleaseAdminError(
                f"worker {worker_key} has no fresh exact healthy report and heartbeat "
                "for the current generation"
            )


def advance_rollout(
    connection,
    *,
    environment: str,
    rollout_id: str,
    expected_generation: int,
    next_generation: int,
    phase: str,
    target_workers: Sequence[str],
    public_root: Path | None = None,
    fresh_seconds: int = DEFAULT_RELEASE_FRESH_SECONDS,
) -> dict[str, Any]:
    if next_generation != expected_generation + 1:
        raise CrawlerReleaseAdminError("next generation must increment exactly once")
    connection.set_session(isolation_level="SERIALIZABLE", readonly=False, autocommit=False)
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            _lock_release_admin(cursor)
            cursor.execute(
                """
                SELECT id::text, environment, rollout_epoch, artifact_digest,
                       previous_artifact_digest, status, strategy,
                       requested_worker_count
                FROM ops_crawler_release_rollouts
                WHERE id = %s::uuid AND environment = %s
                FOR UPDATE
                """,
                (rollout_id, environment),
            )
            rollout_row = cursor.fetchone()
            if rollout_row is None or int(rollout_row["rollout_epoch"]) != expected_generation:
                raise CrawlerReleaseAdminError("rollout generation changed or rollout is unavailable")
            rollout = dict(rollout_row)
            cursor.execute(
                """
                SELECT environment, worker_key, agent_id::text, rollout_id::text,
                       generation, desired_status, cohort, artifact_digest,
                       code_version, config_revision
                FROM ops_crawler_worker_desired_state
                WHERE rollout_id = %s::uuid AND environment = %s
                ORDER BY worker_key
                FOR UPDATE
                """,
                (rollout_id, environment),
            )
            workers = [dict(row) for row in cursor.fetchall()]
            if not workers or any(int(worker["generation"]) != expected_generation for worker in workers):
                raise CrawlerReleaseAdminError("rollout desired state is incomplete or generation-drifted")
            current_phase = str((rollout.get("strategy") or {}).get("state") or "")
            canaries = list((rollout.get("strategy") or {}).get("canary_workers") or [])
            expected_status = {
                "canary": "running",
                "rolling": "running",
                "paused": "paused",
                "complete": "success",
                "rollback": "rolling_back",
            }
            if rollout["status"] != expected_status.get(current_phase):
                raise CrawlerReleaseAdminError("rollout phase and database status disagree")
            if len(workers) != int(rollout["requested_worker_count"]):
                raise CrawlerReleaseAdminError("rollout desired worker count is incomplete")
            if (
                type(canaries) is not list
                or not canaries
                or len(canaries) != len(set(canaries))
                or any(not _WORKER_KEY.fullmatch(str(item)) for item in canaries)
            ):
                raise CrawlerReleaseAdminError("rollout canary identity is invalid")
            if phase == current_phase and phase != "rolling":
                raise CrawlerReleaseAdminError("rollout phase is already current")
            if current_phase not in {"canary", "rolling", "paused", "complete", "rollback"}:
                raise CrawlerReleaseAdminError("current rollout phase is invalid")
            allowed_transitions = {
                "canary": {"paused", "rolling", "complete", "rollback"},
                "rolling": {"paused", "rolling", "complete", "rollback"},
                "paused": {"canary", "rolling", "rollback"},
                "complete": {"rollback"},
                "rollback": {"rolled_back"},
            }
            if phase not in allowed_transitions[current_phase]:
                raise CrawlerReleaseAdminError("requested rollout phase transition is not allowed")

            target_digest = str(rollout["artifact_digest"])
            baseline_digest = str(rollout["previous_artifact_digest"])
            artifacts = _artifact_rows(cursor, (target_digest, baseline_digest))
            if public_root is None:
                raise CrawlerReleaseAdminError("rollout transition requires the protected artifact root")
            _verify_artifact_row_files(public_root, artifacts)
            enabled_keys = {
                str(worker["worker_key"])
                for worker in workers
                if worker["desired_status"] == "active"
            }
            current_target = {
                str(worker["worker_key"])
                for worker in workers
                if worker["desired_status"] == "active"
                and worker["artifact_digest"] == target_digest
            }
            if any(
                worker["agent_id"] is None
                or worker["artifact_digest"] not in {target_digest, baseline_digest}
                for worker in workers
            ):
                raise CrawlerReleaseAdminError("worker desired state escaped the rollout artifact set")
            if phase != "rolling" and target_workers:
                raise CrawlerReleaseAdminError("target workers are accepted only for a rolling wave")
            if phase in {"rolling", "complete", "rolled_back"}:
                _require_ready_reports(
                    cursor,
                    rollout,
                    [worker for worker in workers if worker["worker_key"] in current_target]
                    if phase != "rolled_back"
                    else [worker for worker in workers if worker["desired_status"] == "active"],
                    fresh_seconds=fresh_seconds,
                )

            if phase == "rolling":
                if len(target_workers) != len(set(target_workers)):
                    raise CrawlerReleaseAdminError("rolling wave repeats a target worker")
                requested = {
                    _bounded_identity(item, _WORKER_KEY, label="target worker")
                    for item in target_workers
                }
                desired_target = requested or current_target
                if not current_target.issubset(desired_target) or not set(canaries).issubset(desired_target):
                    raise CrawlerReleaseAdminError("rolling wave cannot move an existing target back")
                if not desired_target.issubset(enabled_keys):
                    raise CrawlerReleaseAdminError("rolling wave references a disabled or unknown worker")
            elif phase == "complete":
                if current_target != enabled_keys:
                    raise CrawlerReleaseAdminError("complete requires every enabled worker already on target")
                desired_target = enabled_keys
            elif phase in {"rollback", "rolled_back"}:
                desired_target = set()
            elif phase in {"paused", "canary"}:
                desired_target = current_target
                if phase == "canary" and desired_target != set(canaries):
                    raise CrawlerReleaseAdminError("canary phase must target exactly the reviewed canaries")
            else:
                raise CrawlerReleaseAdminError("unsupported rollout phase")

            status_by_phase = {
                "paused": "paused",
                "canary": "running",
                "rolling": "running",
                "complete": "success",
                "rollback": "rolling_back",
                "rolled_back": "rolled_back",
            }
            strategy_phase = "rollback" if phase == "rolled_back" else phase
            strategy = {
                "schema_version": 1,
                "state": strategy_phase,
                "canary_workers": canaries,
            }
            cursor.execute(
                """
                UPDATE ops_crawler_release_rollouts
                SET rollout_epoch = %s,
                    status = %s,
                    strategy = %s,
                    finished_at = CASE WHEN %s IN ('success', 'rolled_back')
                                       THEN CURRENT_TIMESTAMP ELSE NULL END
                WHERE id = %s::uuid
                  AND environment = %s
                  AND rollout_epoch = %s
                """,
                (
                    next_generation,
                    status_by_phase[phase],
                    Json(strategy),
                    status_by_phase[phase],
                    rollout_id,
                    environment,
                    expected_generation,
                ),
            )
            if cursor.rowcount != 1:
                raise CrawlerReleaseAdminError("rollout transition lost its generation fence")
            for worker in workers:
                digest = target_digest if worker["worker_key"] in desired_target else baseline_digest
                artifact = artifacts[digest]
                cursor.execute(
                    """
                    UPDATE ops_crawler_worker_desired_state
                    SET generation = %s,
                        artifact_digest = %s,
                        code_version = %s,
                        config_revision = %s,
                        not_before = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE environment = %s
                      AND worker_key = %s
                      AND rollout_id = %s::uuid
                      AND generation = %s
                    """,
                    (
                        next_generation,
                        digest,
                        artifact["code_version"],
                        artifact["config_revision"],
                        environment,
                        worker["worker_key"],
                        rollout_id,
                        expected_generation,
                    ),
                )
                if cursor.rowcount != 1:
                    raise CrawlerReleaseAdminError("worker transition lost its generation fence")
                _append_rollout_worker_snapshot(
                    cursor,
                    environment=environment,
                    rollout_id=rollout_id,
                    generation=next_generation,
                    worker=worker,
                    desired_status=str(worker["desired_status"]),
                    artifact=artifact,
                )
        connection.commit()
        return {
            "status": "ADVANCED",
            "environment": environment,
            "rollout_id": rollout_id,
            "generation": next_generation,
            "phase": phase,
            "target_workers": sorted(desired_target),
        }
    except Exception:
        connection.rollback()
        raise


def release_status(connection, *, environment: str) -> dict[str, Any]:
    connection.set_session(readonly=True, autocommit=False)
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT id::text, rollout_epoch, artifact_digest,
                       previous_artifact_digest, status, strategy,
                       requested_worker_count, created_at, started_at, finished_at
                FROM ops_crawler_release_rollouts
                WHERE environment = %s
                ORDER BY rollout_epoch DESC
                LIMIT 1
                """,
                (environment,),
            )
            rollout = cursor.fetchone()
            if rollout is None:
                return {"environment": environment, "rollout": None, "workers": []}
            cursor.execute(
                """
                SELECT desired.worker_key, desired.agent_id::text, desired.generation,
                       desired.desired_status, desired.cohort,
                       desired.artifact_digest, desired.code_version,
                       desired.config_revision,
                       report.status AS report_status,
                       report.artifact_digest AS reported_artifact_digest,
                       report.desired_generation AS reported_generation,
                       report.health AS reported_health,
                       report.reported_at
                FROM ops_crawler_worker_desired_state desired
                LEFT JOIN LATERAL (
                    SELECT status, artifact_digest, desired_generation, health, reported_at
                    FROM ops_crawler_release_reports
                    WHERE rollout_id = desired.rollout_id
                      AND worker_key = desired.worker_key
                    ORDER BY reported_at DESC, created_at DESC, id DESC
                    LIMIT 1
                ) report ON TRUE
                WHERE desired.rollout_id = %s::uuid
                ORDER BY desired.worker_key
                """,
                (rollout["id"],),
            )
            workers = [dict(row) for row in cursor.fetchall()]
        connection.commit()
        return {"environment": environment, "rollout": dict(rollout), "workers": workers}
    except Exception:
        connection.rollback()
        raise


def _positive_generation(value: str) -> int:
    try:
        generation = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("generation must be a positive integer") from exc
    if generation <= 0:
        raise argparse.ArgumentTypeError("generation must be a positive integer")
    return generation


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage signed distributed crawler releases")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path("/etc/mooncen/crawler-release-admin.env"),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    artifact = subparsers.add_parser("register-artifact")
    artifact.add_argument("--archive", required=True, type=Path)
    artifact.add_argument("--signature", required=True, type=Path)
    artifact.add_argument("--expected-sha256", required=True)
    artifact.add_argument("--code-version", required=True)
    artifact.add_argument("--config-revision", required=True)
    artifact.add_argument("--key-id", required=True)

    create = subparsers.add_parser("create-rollout")
    create.add_argument("--environment", required=True, choices=sorted(_ENVIRONMENTS))
    create.add_argument("--rollout-id", required=True)
    create.add_argument("--generation", required=True, type=_positive_generation)
    create.add_argument("--target-sha256", required=True)
    create.add_argument("--baseline-sha256", required=True)
    create.add_argument("--workers-file", required=True, type=Path)

    advance = subparsers.add_parser("advance-rollout")
    advance.add_argument("--environment", required=True, choices=sorted(_ENVIRONMENTS))
    advance.add_argument("--rollout-id", required=True)
    advance.add_argument("--expected-generation", required=True, type=_positive_generation)
    advance.add_argument("--next-generation", required=True, type=_positive_generation)
    advance.add_argument("--phase", required=True, choices=sorted(_ROLLOUT_PHASES))
    advance.add_argument("--target-worker", action="append", default=[])

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--environment", required=True, choices=sorted(_ENVIRONMENTS))
    return parser.parse_args(argv)


def _paths_from_environment(environment: Mapping[str, str]) -> tuple[Path, Path]:
    public_root = Path(environment.get("OPS_CRAWLER_RELEASE_PUBLIC_ROOT", ""))
    allowed_signers = Path(environment.get("OPS_CRAWLER_ALLOWED_SIGNERS", ""))
    if not public_root.is_absolute() or not allowed_signers.is_absolute():
        raise CrawlerReleaseAdminError(
            "release admin requires absolute public root and allowed-signers paths"
        )
    return public_root, allowed_signers


def _runtime_environment(environment: Mapping[str, str]) -> str:
    aliases = {"prod": "production", "stage": "staging"}
    value = aliases.get(environment.get("ENVIRONMENT", "").strip().lower(), environment.get("ENVIRONMENT", "").strip().lower())
    if value not in _ENVIRONMENTS:
        raise CrawlerReleaseAdminError("release admin environment is invalid")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    connection = None
    try:
        if os.name == "posix" and os.geteuid() != 0:
            raise CrawlerReleaseAdminError("crawler release administration must run as root")
        environment = _protected_environment(args.env_file)
        _assert_component_environment_permissions(args.env_file, "release_admin")
        _check_required_paths("release_admin", environment)
        runtime_environment = _runtime_environment(environment)
        requested_environment = getattr(args, "environment", runtime_environment)
        if requested_environment != runtime_environment:
            raise CrawlerReleaseAdminError(
                "command environment differs from the protected release-admin environment"
            )
        config = _connection_config("release_admin", environment)
        connection = psycopg2.connect(**config)
        _database_contract("release_admin", connection, config["database"])

        if args.command == "register-artifact":
            public_root, allowed_signers = _paths_from_environment(environment)
            code_version = _bounded_identity(args.code_version, _VERSION, label="code version")
            config_revision = _bounded_identity(
                args.config_revision,
                _CONFIG_REVISION,
                label="config revision",
            )
            key_id = _bounded_identity(args.key_id, _KEY_ID, label="artifact key id")
            destination, size_bytes, relative_path = publish_reviewed_artifact(
                args.archive,
                args.signature,
                public_root,
                allowed_signers,
                expected_digest=args.expected_sha256,
                key_id=key_id,
            )
            signature_bytes = _read_signature(args.signature)
            result = register_artifact(
                connection,
                artifact_digest=args.expected_sha256,
                code_version=code_version,
                config_revision=config_revision,
                artifact_path=relative_path,
                size_bytes=size_bytes,
                signature_bytes=signature_bytes,
                key_id=key_id,
            )
            result["published_path"] = str(destination)
        elif args.command == "create-rollout":
            public_root, _allowed_signers = _paths_from_environment(environment)
            target_digest = _bounded_identity(args.target_sha256, _SHA256, label="target SHA-256")
            baseline_digest = _bounded_identity(
                args.baseline_sha256,
                _SHA256,
                label="baseline SHA-256",
            )
            workers = load_reviewed_workers(args.workers_file, environment=args.environment)
            result = create_rollout(
                connection,
                environment=args.environment,
                rollout_id=_canonical_uuid(args.rollout_id, label="rollout id"),
                generation=args.generation,
                target_digest=target_digest,
                baseline_digest=baseline_digest,
                workers=workers,
                public_root=public_root,
            )
        elif args.command == "advance-rollout":
            public_root, _allowed_signers = _paths_from_environment(environment)
            result = advance_rollout(
                connection,
                environment=args.environment,
                rollout_id=_canonical_uuid(args.rollout_id, label="rollout id"),
                expected_generation=args.expected_generation,
                next_generation=args.next_generation,
                phase=args.phase,
                target_workers=args.target_worker,
                public_root=public_root,
                fresh_seconds=int(
                    environment.get(
                        "OPS_CRAWLER_RELEASE_BASELINE_FRESH_SECONDS",
                        DEFAULT_RELEASE_FRESH_SECONDS,
                    )
                ),
            )
        else:
            result = release_status(connection, environment=args.environment)
    except (
        CrawlerReleaseAdminError,
        OSError,
        PreflightError,
        RuntimeError,
        ValueError,
        psycopg2.Error,
    ) as exc:
        print(f"Crawler release administration rejected: {exc}")
        return 1
    finally:
        if connection is not None:
            connection.close()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
