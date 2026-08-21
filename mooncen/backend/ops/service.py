from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from DB.connection_settings import (
    deployment_heartbeat_lease_seconds as deployment_heartbeat_lease_seconds,
)
from tools.ops_redaction import redact_text


TERMINAL_JOB_STATUSES = {
    "success",
    "partial_success",
    "failed",
    "cancelled",
    "timed_out",
    "blocked",
}
ACTIVE_JOB_STATUSES = {"queued", "assigned", "running"}
ACTIVE_DEPLOYMENT_TARGET_INDEX = "ux_ops_jobs_active_deployment_target"
DEPLOYMENT_TARGET_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
DEPLOYMENT_TARGET_KEY_PATTERN = re.compile(
    r"^deployment:[a-z][a-z0-9_-]{0,31}$"
)
JOB_TARGET_KEY_MAX_LENGTH = 500
JOB_TARGET_KEY_DIGEST_MARKER = ":sha256:"
SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "session",
    "signature",
    "token",
)


def current_environment() -> str:
    configured = os.getenv("ENVIRONMENT", "development").strip().lower()
    return {
        "prod": "production",
        "production": "production",
        "stage": "staging",
        "staging": "staging",
        "dev": "development",
        "development": "development",
        "test": "development",
    }.get(configured, "development")


def local_crawler_runtime_enabled() -> bool:
    return os.getenv("OPS_LOCAL_CRAWLER_RUNTIME_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def table_exists(db: Session, table_name: str) -> bool:
    if not table_name.replace("_", "").isalnum():
        return False
    value = db.execute(
        text("SELECT to_regclass(:table_name)"),
        {"table_name": f"public.{table_name}"},
    ).scalar()
    return bool(value)


def require_ops_schema(db: Session, *table_names: str) -> None:
    missing = [name for name in table_names if not table_exists(db, name)]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "ops_schema_unavailable",
                "message": "Ops database migration has not been applied",
                "missing_tables": missing,
            },
        )


def mapped_rows(result: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in result.mappings().all()]


def mapped_one(result: Any) -> dict[str, Any] | None:
    row = result.mappings().first()
    return dict(row) if row is not None else None


def sanitize_for_audit(value: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        return "<truncated>"
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:100]:
            key = str(raw_key)[:120]
            normalized = "".join(character for character in key.lower() if character.isalnum())
            if any(part in normalized for part in SENSITIVE_KEY_PARTS):
                sanitized[key] = "<redacted>"
            else:
                sanitized[key] = sanitize_for_audit(raw_value, depth=depth + 1)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [sanitize_for_audit(item, depth=depth + 1) for item in list(value)[:100]]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return redact_text(value, maximum=2_000)


def validate_job_parameters(value: Any, *, depth: int = 0) -> Any:
    """Validate an operational payload without silently changing its meaning."""

    if depth > 6:
        raise HTTPException(status_code=422, detail="Job parameters are nested too deeply")
    if isinstance(value, dict):
        if len(value) > 100:
            raise HTTPException(status_code=422, detail="Job parameters contain too many fields")
        validated: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if not key or len(key) > 120 or "\x00" in key:
                raise HTTPException(status_code=422, detail="Job parameter name is invalid")
            normalized = "".join(character for character in key.lower() if character.isalnum())
            if any(part in normalized for part in SENSITIVE_KEY_PARTS):
                raise HTTPException(status_code=422, detail=f"Secret-bearing job parameter is forbidden: {key}")
            validated[key] = validate_job_parameters(raw_value, depth=depth + 1)
        return validated
    if isinstance(value, (list, tuple)):
        if len(value) > 100:
            raise HTTPException(status_code=422, detail="Job parameter list is too long")
        return [validate_job_parameters(item, depth=depth + 1) for item in value]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    string_value = str(value)
    if len(string_value) > 4_096 or "\x00" in string_value:
        raise HTTPException(status_code=422, detail="Job parameter value is invalid")
    return string_value


def _request_ip(request: Request) -> str | None:
    candidate = request.client.host if request.client else ""
    try:
        peer = ipaddress.ip_address(candidate)
    except ValueError:
        return None
    if peer.is_loopback:
        forwarded = request.headers.get("cf-connecting-ip", "").strip()
        if not forwarded:
            forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        if forwarded:
            try:
                return str(ipaddress.ip_address(forwarded))
            except ValueError:
                pass
    return str(peer)


def append_audit(
    db: Session,
    request: Request,
    *,
    user_id: UUID | str | None,
    action: str,
    resource_type: str,
    resource_id: str | UUID | None = None,
    before_data: dict[str, Any] | None = None,
    after_data: dict[str, Any] | None = None,
    result: str = "success",
    job_id: UUID | str | None = None,
) -> None:
    require_ops_schema(db, "ops_audit_logs")
    user_agent = redact_text(request.headers.get("user-agent", ""), maximum=500)
    db.execute(
        text(
            """
            INSERT INTO ops_audit_logs (
                user_id, action, resource_type, resource_id, ip_address,
                user_agent, before_data, after_data, result, job_id
            )
            VALUES (
                :user_id, :action, :resource_type, :resource_id,
                CAST(:ip_address AS inet), :user_agent,
                CAST(:before_data AS jsonb), CAST(:after_data AS jsonb),
                :result, :job_id
            )
            """
        ),
        {
            "user_id": str(user_id) if user_id else None,
            "action": action[:120],
            "resource_type": resource_type[:120],
            "resource_id": str(resource_id)[:250] if resource_id is not None else None,
            "ip_address": _request_ip(request),
            "user_agent": user_agent or None,
            "before_data": json.dumps(sanitize_for_audit(before_data), ensure_ascii=False)
            if before_data is not None
            else None,
            "after_data": json.dumps(sanitize_for_audit(after_data), ensure_ascii=False)
            if after_data is not None
            else None,
            "result": result,
            "job_id": str(job_id) if job_id else None,
        },
    )


def add_job_log(
    db: Session,
    job_id: UUID | str,
    message: str,
    *,
    level: str = "info",
    metadata: dict[str, Any] | None = None,
) -> None:
    db.execute(
        text(
            """
            INSERT INTO ops_job_logs (job_id, log_level, message, metadata)
            VALUES (:job_id, :level, :message, CAST(:metadata AS jsonb))
            """
        ),
        {
            "job_id": str(job_id),
            "level": level,
            "message": redact_text(message, maximum=4_000),
            "metadata": json.dumps(sanitize_for_audit(metadata or {}), ensure_ascii=False),
        },
    )


def deduplication_key(job_type: str, environment: str, parameters: dict[str, Any]) -> str:
    canonical = json.dumps(
        sanitize_for_audit(parameters),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return f"{job_type}:{environment}:{digest}"


def validated_job_target_key(
    job_type: str,
    parameters: dict[str, Any],
    target_key: str,
) -> str:
    """Validate without silently truncating or normalizing queue identity."""

    if not isinstance(target_key, str) or not target_key or "\x00" in target_key:
        raise HTTPException(status_code=422, detail="Job target key is invalid")
    if job_type != "deployment":
        if len(target_key) <= JOB_TARGET_KEY_MAX_LENGTH:
            return target_key
        digest = hashlib.sha256(target_key.encode("utf-8")).hexdigest()
        readable_length = (
            JOB_TARGET_KEY_MAX_LENGTH
            - len(JOB_TARGET_KEY_DIGEST_MARKER)
            - len(digest)
        )
        return (
            target_key[:readable_length]
            + JOB_TARGET_KEY_DIGEST_MARKER
            + digest
        )

    target = parameters.get("target")
    if not isinstance(target, str) or not DEPLOYMENT_TARGET_NAME_PATTERN.fullmatch(
        target
    ):
        raise HTTPException(
            status_code=422,
            detail="Deployment parameters must contain a canonical target",
        )
    expected = f"deployment:{target}"
    if target_key != expected:
        raise HTTPException(
            status_code=422,
            detail="Deployment target key does not match parameters.target",
        )
    return target_key


def reserve_deployment_target(
    db: Session,
    *,
    environment: str,
    target_key: str,
) -> None:
    """Serialize deployment creation for one environment/target pair.

    The transaction-scoped advisory lock keeps this safe while the migration is
    being rolled out. The partial unique index remains the final database-level
    invariant for all insert paths.
    """

    if not isinstance(target_key, str) or not DEPLOYMENT_TARGET_KEY_PATTERN.fullmatch(
        target_key
    ):
        raise HTTPException(status_code=422, detail="Deployment target key is invalid")
    normalized_target = target_key
    lock_key = f"mooncen:ops:deployment:{environment}:{normalized_target}"
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": lock_key},
    )
    active = mapped_one(
        db.execute(
            text(
                """
                SELECT id::text, status, environment, target_key
                FROM ops_jobs
                WHERE job_type = 'deployment'
                  AND environment = :environment
                  AND target_key = :target_key
                  AND status IN ('queued', 'assigned', 'running')
                ORDER BY queued_at, created_at
                LIMIT 1
                """
            ),
            {"environment": environment, "target_key": normalized_target},
        )
    )
    if active is not None:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "active_deployment_target",
                "message": "A deployment for this environment and target is already active",
                "active_job_id": active["id"],
                "active_status": active["status"],
            },
        )


def enqueue_job(
    db: Session,
    *,
    job_type: str,
    requested_by: UUID | str | None,
    parameters: dict[str, Any],
    target_key: str,
    max_retries: int,
    parent_job_id: UUID | str | None = None,
) -> dict[str, Any]:
    require_ops_schema(db, "ops_jobs", "ops_job_logs")
    environment = current_environment()
    safe_parameters = validate_job_parameters(parameters)
    safe_target_key = validated_job_target_key(
        job_type,
        safe_parameters,
        target_key,
    )
    if job_type == "deployment":
        reserve_deployment_target(
            db,
            environment=environment,
            target_key=safe_target_key,
        )
    key = deduplication_key(job_type, environment, safe_parameters)
    try:
        job = mapped_one(
            db.execute(
                text(
                    """
                    INSERT INTO ops_jobs (
                        job_type, status, environment, parent_job_id,
                        requested_by, target_key, deduplication_key,
                        parameters, progress, retry_count, max_retries
                    )
                    VALUES (
                        :job_type, 'queued', :environment, :parent_job_id,
                        :requested_by, :target_key, :deduplication_key,
                        CAST(:parameters AS jsonb), 0, 0, :max_retries
                    )
                    RETURNING id, job_type, status, environment, target_key,
                              parameters, progress, retry_count, max_retries,
                              queued_at, created_at
                    """
                ),
                {
                    "job_type": job_type,
                    "environment": environment,
                    "parent_job_id": str(parent_job_id) if parent_job_id else None,
                    "requested_by": str(requested_by) if requested_by is not None else None,
                    "target_key": safe_target_key,
                    "deduplication_key": key,
                    "parameters": json.dumps(safe_parameters, ensure_ascii=False),
                    "max_retries": max_retries,
                },
            )
        )
    except IntegrityError as exc:
        db.rollback()
        diagnostic = getattr(exc.orig, "diag", None)
        constraint = str(getattr(diagnostic, "constraint_name", "") or exc.orig)
        if ACTIVE_DEPLOYMENT_TARGET_INDEX in constraint:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "active_deployment_target",
                    "message": "A deployment for this environment and target is already active",
                },
            ) from None
        if "ux_ops_jobs_active_deduplication" in constraint:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "duplicate_active_job",
                    "message": "An identical operation is already queued or running",
                },
            ) from None
        raise
    if job is None:
        raise RuntimeError("Job insert did not return a row")
    add_job_log(
        db,
        job["id"],
        "작업이 대기열에 등록되었습니다.",
        metadata={"target_key": safe_target_key},
    )
    return job


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def register_container_release_evidence(
    db: Session,
    manifest: dict[str, Any],
    *,
    builder_target_identity: str,
    builder_hostname: str,
    built_by: UUID | str | None = None,
) -> dict[str, Any]:
    """Append one revalidated canonical ``release.json`` to Ops evidence.

    The dedicated deployment worker calls this only after its bounded file
    reader has parsed the document. The canonical validator is imported lazily
    because the public API runtime never registers build evidence.
    """

    from deploy.docker.release_manifest import validate_release_manifest

    normalized = validate_release_manifest(dict(manifest))
    identity = str(builder_target_identity or "").strip().lower()
    hostname = str(builder_hostname or "").strip()
    if re.fullmatch(r"[0-9a-f]{64}", identity) is None:
        raise ValueError("builder target identity is invalid")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,252}", hostname) is None:
        raise ValueError("builder hostname is invalid")
    values = {
        "release_digest": normalized["release_digest"],
        "base_commit": normalized["base_commit"],
        "source_tree": normalized["source_tree"],
        "snapshot_commit": normalized["snapshot_commit"],
        "platform": normalized["platform"],
        "api_image_digest": normalized["images"]["api"]["image_id"],
        "frontend_image_digest": normalized["images"]["frontend"]["image_id"],
        "bundle_sha256": normalized["bundle_sha256"],
        "compose_sha256": normalized["compose_sha256"],
        "build_policy_sha256": normalized["build_policy_sha256"],
        "migration_ledger_sha256": normalized["migration_ledger_sha256"],
        "manifest_json": json.dumps(normalized, ensure_ascii=True, sort_keys=True),
        "builder_target_identity": identity,
        "builder_hostname": hostname,
        "built_by": str(built_by) if built_by else None,
        "built_at": normalized["created_at"],
    }
    row = mapped_one(
        db.execute(
            text(
                """
                INSERT INTO ops_container_releases (
                    release_digest, base_commit, source_tree, snapshot_commit,
                    platform, api_image_digest, frontend_image_digest,
                    bundle_sha256, compose_sha256, build_policy_sha256,
                    migration_ledger_sha256, manifest_json,
                    builder_target_identity, builder_hostname, built_by, built_at
                )
                VALUES (
                    :release_digest, :base_commit, :source_tree, :snapshot_commit,
                    :platform, :api_image_digest, :frontend_image_digest,
                    :bundle_sha256, :compose_sha256, :build_policy_sha256,
                    :migration_ledger_sha256, CAST(:manifest_json AS jsonb),
                    :builder_target_identity, :builder_hostname, :built_by, :built_at
                )
                ON CONFLICT (release_digest) DO NOTHING
                RETURNING id::text, release_digest, source_tree, platform,
                          api_image_digest, frontend_image_digest,
                          bundle_sha256, created_at
                """
            ),
            values,
        )
    )
    if row is not None:
        return row
    existing = mapped_one(
        db.execute(
            text(
                """
                SELECT id::text, release_digest, source_tree, platform,
                       api_image_digest, frontend_image_digest,
                       bundle_sha256, manifest_json, builder_target_identity,
                       builder_hostname, created_at
                FROM ops_container_releases
                WHERE release_digest = :release_digest
                """
            ),
            {"release_digest": normalized["release_digest"]},
        )
    )
    if existing is None or existing.get("manifest_json") != normalized:
        raise RuntimeError("existing container release evidence is not canonical")
    return existing


def register_container_validation_evidence(
    db: Session,
    receipt: dict[str, Any],
    *,
    created_by: UUID | str | None = None,
) -> dict[str, Any]:
    """Append an exact canonical ``validation.json`` bound to its release."""

    from deploy.docker.release_manifest import validate_validation_receipt

    normalized = validate_validation_receipt(dict(receipt))
    release = mapped_one(
        db.execute(
            text(
                """
                SELECT id::text, release_digest
                FROM ops_container_releases
                WHERE release_digest = :release_digest
                """
            ),
            {"release_digest": normalized["release_digest"]},
        )
    )
    if release is None:
        raise ValueError("validation receipt release is not registered")
    values = {
        "receipt_digest": normalized["receipt_digest"],
        "release_id": release["id"],
        "release_digest": normalized["release_digest"],
        "source_tree": normalized["source_tree"],
        "target": normalized["target"],
        "target_identity": normalized["target_identity"],
        "platform": normalized["platform"],
        "bundle_sha256": normalized["bundle_sha256"],
        "compose_sha256": normalized["compose_sha256"],
        "api_image_digest": normalized["image_ids"]["api"],
        "frontend_image_digest": normalized["image_ids"]["frontend"],
        "checks": json.dumps(normalized["checks"], ensure_ascii=True, sort_keys=True),
        "status": normalized["status"],
        "receipt_json": json.dumps(normalized, ensure_ascii=True, sort_keys=True),
        "validated_at": normalized["validated_at"],
        "expires_at": normalized["expires_at"],
        "created_by": str(created_by) if created_by else None,
    }
    row = mapped_one(
        db.execute(
            text(
                """
                INSERT INTO ops_container_validation_receipts (
                    receipt_digest, release_id, release_digest, source_tree,
                    target, target_identity, platform, bundle_sha256,
                    compose_sha256, api_image_digest, frontend_image_digest,
                    checks, status, receipt_json, validated_at, expires_at,
                    created_by
                )
                VALUES (
                    :receipt_digest, :release_id, :release_digest, :source_tree,
                    :target, :target_identity, :platform, :bundle_sha256,
                    :compose_sha256, :api_image_digest, :frontend_image_digest,
                    CAST(:checks AS jsonb), :status, CAST(:receipt_json AS jsonb),
                    :validated_at, :expires_at, :created_by
                )
                ON CONFLICT (receipt_digest) DO NOTHING
                RETURNING id::text, receipt_digest, release_id::text,
                          release_digest, target, target_identity, status,
                          validated_at, expires_at, created_at
                """
            ),
            values,
        )
    )
    if row is not None:
        return row
    existing = mapped_one(
        db.execute(
            text(
                """
                SELECT id::text, receipt_digest, release_id::text,
                       release_digest, target, target_identity, status,
                       receipt_json, validated_at, expires_at, created_at
                FROM ops_container_validation_receipts
                WHERE receipt_digest = :receipt_digest
                """
            ),
            {"receipt_digest": normalized["receipt_digest"]},
        )
    )
    if existing is None or existing.get("receipt_json") != normalized:
        raise RuntimeError("existing container validation evidence is not canonical")
    return existing
