"""Environment-bound Crawler Studio source storage and review API.

This router only stores append-only source evidence.  It has no execution,
fixture-runner, build, signing, artifact, rollout, or deployment integration.
"""

from __future__ import annotations

import hashlib
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend import models
from backend.crawler_control_database import get_crawler_control_db
from backend.ops.service import append_audit, current_environment, mapped_one, mapped_rows
from backend.routers.auth import (
    ops_role_for_user,
    rate_limit,
    require_ops_operator,
    require_ops_viewer,
)
from backend.services.crawler_studio import (
    CrawlerStudioValidationError,
    source_capabilities,
    validate_provider_path,
    validate_source_text,
)


# Independent code-side identity for the server-side catalog verifier. A DB
# owner replacing the function with `RETURN TRUE` cannot make Studio available.
_STUDIO_CONTRACT_SOURCE_SHA256 = (
    "fcdf9cc251a397f31321909014ab2e011e264bd04ee322a32fb95fc954063d8e"
)


router = APIRouter(
    prefix="/api/ops/crawler-studio",
    tags=["ops-crawler-studio"],
    dependencies=[
        Depends(rate_limit("ops-crawler-studio", 180, 60)),
        Depends(require_ops_viewer),
    ],
)

_STUDIO_TABLES = (
    "ops_crawler_studio_provider_paths",
    "ops_crawler_studio_drafts",
    "ops_crawler_studio_revisions",
    "ops_crawler_studio_reviews",
    "ops_crawler_api_bindings",
    "ops_audit_logs",
)
_DRAFT_STATUSES = {"draft", "in_review", "approved", "changes_requested", "archived"}
_REVIEW_TARGET_STATUS = {
    "submit": "in_review",
    "approve": "approved",
    "request_changes": "changes_requested",
    "archive": "archived",
}


def _canonical_text(value: str, *, minimum: int, maximum: int, label: str) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or "\x00" in value
        or not minimum <= len(value) <= maximum
    ):
        raise ValueError(f"{label} must be canonical text between {minimum} and {maximum} characters")
    return value


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateDraftRequest(_StrictModel):
    provider: str = Field(min_length=1, max_length=100)
    source_path: str = Field(min_length=1, max_length=240)
    title: str = Field(min_length=3, max_length=160)
    source_text: str = Field(min_length=1, max_length=524_288)
    source_sha256: str = Field(min_length=64, max_length=64)
    change_summary: str = Field(min_length=3, max_length=500)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _canonical_text(value, minimum=3, maximum=160, label="title")

    @field_validator("change_summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        return _canonical_text(value, minimum=3, maximum=500, label="change_summary")


class AppendRevisionRequest(_StrictModel):
    expected_revision: int = Field(ge=1, le=2_147_483_646)
    source_text: str = Field(min_length=1, max_length=524_288)
    source_sha256: str = Field(min_length=64, max_length=64)
    change_summary: str = Field(min_length=3, max_length=500)

    @field_validator("change_summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        return _canonical_text(value, minimum=3, maximum=500, label="change_summary")


class ReviewDraftRequest(_StrictModel):
    expected_revision: int = Field(ge=1, le=2_147_483_647)
    expected_source_sha256: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    decision: Literal["submit", "approve", "request_changes", "archive"]
    comment: str = Field(min_length=3, max_length=1_000)

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, value: str) -> str:
        return _canonical_text(value, minimum=3, maximum=1_000, label="comment")


def _reported_capabilities(available: bool) -> dict[str, dict[str, Any]]:
    capabilities = {key: dict(value) for key, value in source_capabilities().items()}
    if not available:
        for key in ("draft_storage", "revision_history", "review_decision", "source_approval"):
            capabilities[key] = {
                "available": False,
                "reason": "crawler_studio_schema_unavailable",
            }
    return capabilities


def _studio_schema_available(db: Session | None) -> bool:
    if db is None:
        return False
    try:
        rows = db.execute(
            text(
                """
                SELECT required.name, to_regclass('public.' || required.name) IS NOT NULL
                FROM unnest(CAST(:required AS text[])) AS required(name)
                """
            ),
            {"required": [*_STUDIO_TABLES, "ops_crawler_control_database_marker"]},
        ).all()
        if len(rows) != len(_STUDIO_TABLES) + 1 or any(not bool(row[1]) for row in rows):
            db.rollback()
            return False
        marker = db.execute(
            text(
                """
                SELECT count(*) = 1
                   AND bool_and(singleton IS TRUE)
                   AND bool_and(database_name = current_database()::name)
                FROM ops_crawler_control_database_marker
                """
            )
        ).scalar()
        bound_environment = db.execute(text("SELECT current_crawler_api_environment()")) .scalar()
        contract_identity = db.execute(
            text(
                """
                SELECT procedure.prosrc,
                       procedure.prokind = 'f'
                       AND procedure.pronargs = 0
                       AND procedure.prorettype = 'boolean'::regtype
                       AND procedure.provolatile = 's'
                       AND procedure.proparallel = 'u'
                       AND NOT procedure.prosecdef
                       AND NOT procedure.proleakproof
                       AND language_row.lanname = 'plpgsql'
                       AND procedure.proconfig =
                           ARRAY['search_path=pg_catalog, public']::text[]
                       AND procedure.proowner = public_namespace.nspowner
                       AND NOT owner_row.rolcanlogin
                       AND NOT owner_row.rolsuper
                       AND NOT owner_row.rolcreaterole
                       AND NOT owner_row.rolcreatedb
                       AND NOT owner_row.rolreplication
                       AND NOT owner_row.rolbypassrls
                FROM pg_proc procedure
                JOIN pg_namespace namespace_row
                  ON namespace_row.oid = procedure.pronamespace
                JOIN pg_namespace public_namespace
                  ON public_namespace.nspname = 'public'
                JOIN pg_language language_row
                  ON language_row.oid = procedure.prolang
                JOIN pg_roles owner_row ON owner_row.oid = procedure.proowner
                WHERE namespace_row.nspname = 'public'
                  AND procedure.proname = 'crawler_studio_contract_is_valid'
                  AND procedure.pronargs = 0
                """
            )
        ).first()
        live_contract_sha256 = None
        if contract_identity is not None:
            normalized_source = (
                str(contract_identity[0])
                .replace("\r\n", "\n")
                .replace("\r", "\n")
                .strip()
            )
            live_contract_sha256 = hashlib.sha256(
                normalized_source.encode("utf-8")
            ).hexdigest()
        if (
            contract_identity is None
            or contract_identity[1] is not True
            or live_contract_sha256 != _STUDIO_CONTRACT_SOURCE_SHA256
        ):
            db.rollback()
            return False
        contract_valid = db.execute(
            text("SELECT public.crawler_studio_contract_is_valid()")
        ).scalar()
        db.rollback()
        return (
            marker is True
            and bound_environment == current_environment()
            and contract_valid is True
        )
    except (CrawlerStudioValidationError, SQLAlchemyError, RuntimeError):
        db.rollback()
        return False


def _require_studio(db: Session | None) -> Session:
    if not _studio_schema_available(db):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "crawler_studio_unavailable",
                "message": "The environment-bound Crawler Studio schema is unavailable",
            },
        )
    assert db is not None
    return db


def _validation_error(exc: CrawlerStudioValidationError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": "crawler_studio_source_invalid", "message": str(exc)},
    )


def _conflict(message: str = "Crawler Studio revision changed") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": "crawler_studio_revision_conflict", "message": message},
    )


def _database_failure(exc: SQLAlchemyError) -> HTTPException:
    sqlstate = getattr(getattr(exc, "orig", None), "pgcode", None)
    if sqlstate in {"23505", "40001", "55000"}:
        return _conflict()
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": "crawler_studio_database_error", "message": "Crawler Studio storage failed closed"},
    )


def _draft_row(db: Session, draft_id: UUID, *, lock: bool = False) -> dict[str, Any] | None:
    lock_clause = " FOR UPDATE" if lock else ""
    return mapped_one(
        db.execute(
            text(
                """
                SELECT id::text, environment, provider, source_path, title, status,
                       latest_revision, created_by::text, created_at, updated_at
                FROM ops_crawler_studio_drafts
                WHERE id = CAST(:draft_id AS uuid) AND environment = :environment
                """
                + lock_clause
            ),
            {"draft_id": str(draft_id), "environment": current_environment()},
        )
    )


def _draft_detail(db: Session, draft_id: UUID) -> dict[str, Any] | None:
    draft = _draft_row(db, draft_id)
    if draft is None:
        return None
    revision = mapped_one(
        db.execute(
            text(
                """
                SELECT id::text, draft_id::text, environment, revision, source_sha256,
                       source_size_bytes, impacted_providers, source_text, change_summary,
                       created_by::text, created_at
                FROM ops_crawler_studio_revisions
                WHERE draft_id = CAST(:draft_id AS uuid)
                  AND environment = :environment AND revision = :revision
                """
            ),
            {
                "draft_id": str(draft_id),
                "environment": current_environment(),
                "revision": int(draft["latest_revision"]),
            },
        )
    )
    reviews = mapped_rows(
        db.execute(
            text(
                """
                SELECT id::text, draft_id::text, environment, revision, decision,
                       comment, reviewed_by::text, created_at
                FROM ops_crawler_studio_reviews
                WHERE draft_id = CAST(:draft_id AS uuid) AND environment = :environment
                ORDER BY created_at DESC, id DESC
                LIMIT 200
                """
            ),
            {"draft_id": str(draft_id), "environment": current_environment()},
        )
    )
    return {**draft, "latest_revision_item": revision, "reviews": reviews}


def _audit_revision_data(detail: dict[str, Any]) -> dict[str, Any]:
    revision = detail.get("latest_revision_item") or {}
    return {
        "environment": detail["environment"],
        "provider": detail["provider"],
        "source_path": detail["source_path"],
        "status": detail["status"],
        "revision": detail["latest_revision"],
        "source_sha256": revision.get("source_sha256"),
        "source_size_bytes": revision.get("source_size_bytes"),
        "impacted_providers": revision.get("impacted_providers", []),
    }


@router.get("/capabilities")
def studio_capabilities(
    user: models.User = Depends(require_ops_viewer),
    db: Session | None = Depends(get_crawler_control_db),
) -> dict[str, Any]:
    available = _studio_schema_available(db)
    return {
        "available": available,
        "environment": current_environment(),
        "role": ops_role_for_user(user),
        "capabilities": _reported_capabilities(available),
    }


@router.get("/providers")
def studio_providers(
    db: Session | None = Depends(get_crawler_control_db),
) -> dict[str, Any]:
    if not _studio_schema_available(db):
        return {"available": False, "items": [], "total": 0}
    assert db is not None
    try:
        items = mapped_rows(
            db.execute(
                text(
                    "SELECT provider, source_path FROM ops_crawler_studio_provider_paths "
                    "ORDER BY provider, source_path"
                )
            )
        )
        providers_by_path: dict[str, list[str]] = {}
        for item in items:
            providers_by_path.setdefault(str(item["source_path"]), []).append(str(item["provider"]))
        for item in items:
            item["impacted_providers"] = sorted(providers_by_path[str(item["source_path"])])
        db.rollback()
        return {"available": True, "items": items, "total": len(items)}
    except SQLAlchemyError:
        db.rollback()
        return {"available": False, "items": [], "total": 0}


@router.get("/drafts")
def studio_drafts(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    provider: str | None = Query(default=None, min_length=1, max_length=100),
    draft_status: Literal["draft", "in_review", "approved", "changes_requested", "archived"] | None = Query(
        default=None, alias="status"
    ),
    db: Session | None = Depends(get_crawler_control_db),
) -> dict[str, Any]:
    if not _studio_schema_available(db):
        return {"available": False, "items": [], "total": 0, "limit": limit, "offset": offset}
    assert db is not None
    normalized_provider = provider.strip().upper() if provider else None
    if provider is not None and normalized_provider != provider:
        raise HTTPException(status_code=422, detail="provider must be canonical")
    parameters = {
        "environment": current_environment(),
        "provider": normalized_provider,
        "status": draft_status,
        "limit": limit,
        "offset": offset,
    }
    where = (
        "draft.environment = :environment "
        "AND (:provider IS NULL OR EXISTS ("
        "SELECT 1 FROM ops_crawler_studio_provider_paths impact "
        "WHERE impact.source_path = draft.source_path AND impact.provider = :provider)) "
        "AND (:status IS NULL OR draft.status = :status)"
    )
    try:
        total = int(
            db.execute(
                text(f"SELECT count(*) FROM ops_crawler_studio_drafts draft WHERE {where}"),
                parameters,
            ).scalar()
            or 0
        )
        items = mapped_rows(
            db.execute(
                text(
                    "SELECT draft.id::text, draft.environment, draft.provider, draft.source_path, "
                    "draft.title, draft.status, draft.latest_revision, revision.impacted_providers, "
                    "draft.created_by::text, draft.created_at, draft.updated_at "
                    "FROM ops_crawler_studio_drafts draft "
                    "JOIN ops_crawler_studio_revisions revision "
                    "ON revision.draft_id = draft.id AND revision.revision = draft.latest_revision "
                    f"WHERE {where} ORDER BY draft.updated_at DESC, draft.id DESC "
                    "LIMIT :limit OFFSET :offset"
                ),
                parameters,
            )
        )
        db.rollback()
        return {"available": True, "items": items, "total": total, "limit": limit, "offset": offset}
    except SQLAlchemyError:
        db.rollback()
        return {"available": False, "items": [], "total": 0, "limit": limit, "offset": offset}


@router.get("/drafts/{draft_id}")
def studio_draft(
    draft_id: UUID,
    db: Session | None = Depends(get_crawler_control_db),
) -> dict[str, Any]:
    if not _studio_schema_available(db):
        return {"available": False, "item": None, "capabilities": _reported_capabilities(False)}
    assert db is not None
    try:
        item = _draft_detail(db, draft_id)
        db.rollback()
        if item is None:
            raise HTTPException(status_code=404, detail="Crawler Studio draft not found")
        return {"available": True, "item": item, "capabilities": _reported_capabilities(True)}
    except HTTPException:
        raise
    except SQLAlchemyError:
        db.rollback()
        return {"available": False, "item": None, "capabilities": _reported_capabilities(False)}


@router.get("/drafts/{draft_id}/revisions")
def studio_revisions(
    draft_id: UUID,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    db: Session | None = Depends(get_crawler_control_db),
) -> dict[str, Any]:
    if not _studio_schema_available(db):
        return {"available": False, "items": [], "total": 0, "limit": limit, "offset": offset}
    assert db is not None
    parameters = {
        "draft_id": str(draft_id),
        "environment": current_environment(),
        "limit": limit,
        "offset": offset,
    }
    try:
        if _draft_row(db, draft_id) is None:
            db.rollback()
            raise HTTPException(status_code=404, detail="Crawler Studio draft not found")
        total = int(
            db.execute(
                text(
                    "SELECT count(*) FROM ops_crawler_studio_revisions "
                    "WHERE draft_id = CAST(:draft_id AS uuid) AND environment = :environment"
                ),
                parameters,
            ).scalar()
            or 0
        )
        items = mapped_rows(
            db.execute(
                text(
                    """
                    SELECT id::text, draft_id::text, environment, revision, source_sha256,
                           source_size_bytes, impacted_providers, change_summary,
                           created_by::text, created_at
                    FROM ops_crawler_studio_revisions
                    WHERE draft_id = CAST(:draft_id AS uuid) AND environment = :environment
                    ORDER BY revision DESC LIMIT :limit OFFSET :offset
                    """
                ),
                parameters,
            )
        )
        db.rollback()
        return {"available": True, "items": items, "total": total, "limit": limit, "offset": offset}
    except HTTPException:
        raise
    except SQLAlchemyError:
        db.rollback()
        return {"available": False, "items": [], "total": 0, "limit": limit, "offset": offset}


@router.get("/drafts/{draft_id}/revisions/{revision}")
def studio_revision(
    draft_id: UUID,
    revision: int = Path(ge=1, le=2_147_483_647),
    db: Session | None = Depends(get_crawler_control_db),
) -> dict[str, Any]:
    if not _studio_schema_available(db):
        return {"available": False, "item": None}
    assert db is not None
    try:
        item = mapped_one(
            db.execute(
                text(
                    """
                    SELECT id::text, draft_id::text, environment, revision, source_sha256,
                           source_size_bytes, impacted_providers, source_text, change_summary,
                           created_by::text, created_at
                    FROM ops_crawler_studio_revisions
                    WHERE draft_id = CAST(:draft_id AS uuid)
                      AND environment = :environment AND revision = :revision
                    """
                ),
                {
                    "draft_id": str(draft_id),
                    "environment": current_environment(),
                    "revision": revision,
                },
            )
        )
        db.rollback()
        if item is None:
            raise HTTPException(status_code=404, detail="Crawler Studio revision not found")
        return {"available": True, "item": item}
    except HTTPException:
        raise
    except SQLAlchemyError:
        db.rollback()
        return {"available": False, "item": None}


@router.post("/drafts", status_code=status.HTTP_201_CREATED)
def create_studio_draft(
    payload: CreateDraftRequest,
    request: Request,
    user: models.User = Depends(require_ops_operator),
    db: Session | None = Depends(get_crawler_control_db),
) -> dict[str, Any]:
    database = _require_studio(db)
    try:
        provider, source_path = validate_provider_path(payload.provider, payload.source_path)
        source, source_bytes, source_sha256 = validate_source_text(
            payload.source_text, payload.source_sha256
        )
    except CrawlerStudioValidationError as exc:
        raise _validation_error(exc) from exc
    try:
        draft_id = database.execute(
            text(
                """
                INSERT INTO ops_crawler_studio_drafts (
                    environment, provider, source_path, title, created_by
                ) VALUES (:environment, :provider, :source_path, :title, CAST(:created_by AS uuid))
                RETURNING id::text
                """
            ),
            {
                "environment": current_environment(),
                "provider": provider,
                "source_path": source_path,
                "title": payload.title,
                "created_by": str(user.id),
            },
        ).scalar_one()
        database.execute(
            text(
                """
                INSERT INTO ops_crawler_studio_revisions (
                    draft_id, environment, revision, source_sha256, source_size_bytes,
                    source_text, change_summary, created_by
                ) VALUES (
                    CAST(:draft_id AS uuid), :environment, 1, :source_sha256,
                    :source_size_bytes, :source_text, :change_summary, CAST(:created_by AS uuid)
                )
                """
            ),
            {
                "draft_id": draft_id,
                "environment": current_environment(),
                "source_sha256": source_sha256,
                "source_size_bytes": len(source_bytes),
                "source_text": source,
                "change_summary": payload.change_summary,
                "created_by": str(user.id),
            },
        )
        updated = database.execute(
            text(
                "UPDATE ops_crawler_studio_drafts SET latest_revision = 1, status = 'draft' "
                "WHERE id = CAST(:draft_id AS uuid) AND environment = :environment "
                "AND latest_revision = 0"
            ),
            {"draft_id": draft_id, "environment": current_environment()},
        )
        if updated.rowcount != 1:
            raise _conflict()
        item = _draft_detail(database, UUID(str(draft_id)))
        assert item is not None
        append_audit(
            database,
            request,
            # The crawler-control DB is intentionally separate from the primary
            # users database, so its audit FK cannot safely reference that row.
            user_id=None,
            action="crawler_studio.create_draft",
            resource_type="crawler_studio_draft",
            resource_id=draft_id,
            after_data={
                **_audit_revision_data(item),
                "actor_user_id": str(user.id),
                "actor_role": ops_role_for_user(user),
            },
        )
        database.commit()
        return {"available": True, "item": item, "replayed": False}
    except HTTPException:
        database.rollback()
        raise
    except SQLAlchemyError as exc:
        database.rollback()
        raise _database_failure(exc) from exc


@router.post("/drafts/{draft_id}/revisions", status_code=status.HTTP_201_CREATED)
def append_studio_revision(
    draft_id: UUID,
    payload: AppendRevisionRequest,
    request: Request,
    user: models.User = Depends(require_ops_operator),
    db: Session | None = Depends(get_crawler_control_db),
) -> dict[str, Any]:
    database = _require_studio(db)
    try:
        source, source_bytes, source_sha256 = validate_source_text(
            payload.source_text, payload.source_sha256
        )
    except CrawlerStudioValidationError as exc:
        raise _validation_error(exc) from exc
    try:
        before = _draft_row(database, draft_id, lock=True)
        if before is None:
            raise HTTPException(status_code=404, detail="Crawler Studio draft not found")
        if int(before["latest_revision"]) != payload.expected_revision:
            raise _conflict()
        if before["status"] == "in_review":
            raise _conflict("Draft must leave review before a new revision is appended")
        next_revision = payload.expected_revision + 1
        database.execute(
            text(
                """
                INSERT INTO ops_crawler_studio_revisions (
                    draft_id, environment, revision, source_sha256, source_size_bytes,
                    source_text, change_summary, created_by
                ) VALUES (
                    CAST(:draft_id AS uuid), :environment, :revision, :source_sha256,
                    :source_size_bytes, :source_text, :change_summary, CAST(:created_by AS uuid)
                )
                """
            ),
            {
                "draft_id": str(draft_id),
                "environment": current_environment(),
                "revision": next_revision,
                "source_sha256": source_sha256,
                "source_size_bytes": len(source_bytes),
                "source_text": source,
                "change_summary": payload.change_summary,
                "created_by": str(user.id),
            },
        )
        updated = database.execute(
            text(
                "UPDATE ops_crawler_studio_drafts SET latest_revision = :next_revision, status = 'draft' "
                "WHERE id = CAST(:draft_id AS uuid) AND environment = :environment "
                "AND latest_revision = :expected_revision"
            ),
            {
                "draft_id": str(draft_id),
                "environment": current_environment(),
                "next_revision": next_revision,
                "expected_revision": payload.expected_revision,
            },
        )
        if updated.rowcount != 1:
            raise _conflict()
        item = _draft_detail(database, draft_id)
        assert item is not None
        append_audit(
            database,
            request,
            user_id=None,
            action="crawler_studio.append_revision",
            resource_type="crawler_studio_draft",
            resource_id=draft_id,
            before_data={
                "status": before["status"],
                "revision": before["latest_revision"],
            },
            after_data={
                **_audit_revision_data(item),
                "actor_user_id": str(user.id),
                "actor_role": ops_role_for_user(user),
            },
        )
        database.commit()
        return {"available": True, "item": item}
    except HTTPException:
        database.rollback()
        raise
    except SQLAlchemyError as exc:
        database.rollback()
        raise _database_failure(exc) from exc


@router.post("/drafts/{draft_id}/reviews", status_code=status.HTTP_201_CREATED)
def review_studio_draft(
    draft_id: UUID,
    payload: ReviewDraftRequest,
    request: Request,
    user: models.User = Depends(require_ops_operator),
    db: Session | None = Depends(get_crawler_control_db),
) -> dict[str, Any]:
    role = ops_role_for_user(user)
    if payload.decision == "approve" and role != "admin":
        raise HTTPException(status_code=403, detail="Ops admin access required for source review approval")
    if payload.decision == "approve":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "independent_source_approval_not_ready",
                "message": "Independent source approval evidence is not implemented",
            },
        )
    database = _require_studio(db)
    try:
        before = _draft_row(database, draft_id, lock=True)
        if before is None:
            raise HTTPException(status_code=404, detail="Crawler Studio draft not found")
        if int(before["latest_revision"]) != payload.expected_revision:
            raise _conflict()
        current_revision = mapped_one(
            database.execute(
                text(
                    "SELECT source_sha256 FROM ops_crawler_studio_revisions "
                    "WHERE draft_id = CAST(:draft_id AS uuid) AND environment = :environment "
                    "AND revision = :revision"
                ),
                {
                    "draft_id": str(draft_id),
                    "environment": current_environment(),
                    "revision": payload.expected_revision,
                },
            )
        )
        if (
            current_revision is None
            or current_revision["source_sha256"] != payload.expected_source_sha256
        ):
            raise _conflict("Crawler Studio source identity changed")
        current_status = str(before["status"])
        allowed = (
            (payload.decision == "submit" and current_status in {"draft", "changes_requested"})
            or (payload.decision in {"approve", "request_changes"} and current_status == "in_review")
            or (payload.decision == "archive" and current_status != "archived")
        )
        if not allowed:
            raise _conflict("Review decision is invalid for the current draft status")
        target_status = _REVIEW_TARGET_STATUS[payload.decision]
        database.execute(
            text(
                """
                INSERT INTO ops_crawler_studio_reviews (
                    draft_id, environment, revision, decision, comment, reviewed_by
                ) VALUES (
                    CAST(:draft_id AS uuid), :environment, :revision, :decision,
                    :comment, CAST(:reviewed_by AS uuid)
                )
                """
            ),
            {
                "draft_id": str(draft_id),
                "environment": current_environment(),
                "revision": payload.expected_revision,
                "decision": payload.decision,
                "comment": payload.comment,
                "reviewed_by": str(user.id),
            },
        )
        updated = database.execute(
            text(
                "UPDATE ops_crawler_studio_drafts SET status = :target_status "
                "WHERE id = CAST(:draft_id AS uuid) AND environment = :environment "
                "AND latest_revision = :expected_revision AND status = :current_status"
            ),
            {
                "draft_id": str(draft_id),
                "environment": current_environment(),
                "target_status": target_status,
                "expected_revision": payload.expected_revision,
                "current_status": current_status,
            },
        )
        if updated.rowcount != 1:
            raise _conflict()
        item = _draft_detail(database, draft_id)
        assert item is not None
        append_audit(
            database,
            request,
            user_id=None,
            action=f"crawler_studio.review.{payload.decision}",
            resource_type="crawler_studio_draft",
            resource_id=draft_id,
            before_data={"status": current_status, "revision": payload.expected_revision},
            after_data={
                "status": target_status,
                "revision": payload.expected_revision,
                "decision": payload.decision,
                "actor_user_id": str(user.id),
                "actor_role": role,
            },
        )
        database.commit()
        return {"available": True, "item": item}
    except HTTPException:
        database.rollback()
        raise
    except SQLAlchemyError as exc:
        database.rollback()
        raise _database_failure(exc) from exc
