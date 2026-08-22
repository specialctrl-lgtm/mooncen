from __future__ import annotations

import hashlib
from pathlib import Path
import re
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from starlette.requests import Request

from backend.routers import crawler_studio as studio_router
from backend.services import crawler_studio


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "DB/crawler_control_migrations/20260812_003_crawler_studio.sql"
ALLOWLIST_DIGEST = "873172712aac8dd01e919864fece65b662f47adf0d4f9f0d404ce4bbebe350f4"


def test_reviewed_provider_path_snapshot_is_exact_and_points_to_files() -> None:
    mapping = crawler_studio.reviewed_provider_paths()
    rows = sorted((provider, next(iter(paths))) for provider, paths in mapping.items())

    assert len(rows) == 42
    assert all(len(paths) == 1 for paths in mapping.values())
    assert all((ROOT / source_path).is_file() for _, source_path in rows)
    assert mapping["ANYANG_LIFELONG_LEARNING"] == frozenset(
        {"Crawler/generated_yaml/ANYANG_LIFELONG_LEARNING.py"}
    )
    encoded = "\n".join("\t".join(row) for row in rows).encode("utf-8")
    assert hashlib.sha256(encoded).hexdigest() == ALLOWLIST_DIGEST


def test_source_validation_is_exact_utf8_sha256_and_bounded() -> None:
    source = "# crawler\nprint('???')\n"
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()

    assert crawler_studio.validate_source_text(source, digest) == (
        source,
        source.encode("utf-8"),
        digest,
    )
    with pytest.raises(crawler_studio.CrawlerStudioValidationError, match="SHA-256"):
        crawler_studio.validate_source_text(source, "0" * 64)
    with pytest.raises(crawler_studio.CrawlerStudioValidationError, match="without NUL"):
        crawler_studio.validate_source_text("bad\x00source", digest)
    with pytest.raises(crawler_studio.CrawlerStudioValidationError, match="size"):
        crawler_studio.validate_source_text(
            "x" * (crawler_studio.MAX_SOURCE_BYTES + 1),
            "0" * 64,
        )


def test_migration_enforces_database_revision_review_and_environment_fences() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "public.digest(bytea,text)" in sql
    assert "crawler studio reviewed provider/path snapshot differs" in sql
    assert ALLOWLIST_DIGEST in sql
    assert "FOR UPDATE" in sql
    assert "NEW.revision <> parent_latest_revision + 1" in sql
    assert "parent_status NOT IN ('draft', 'changes_requested', 'approved', 'archived')" in sql
    assert "UNIQUE (environment, source_path)" in sql
    assert "independent crawler source approval evidence is not implemented" in sql
    assert "crawler draft revision evidence is invalid" in sql
    assert "crawler drafts are append-only" in sql
    assert "crawler draft review transition is invalid" in sql
    assert "CREATE CONSTRAINT TRIGGER zz_ops_crawler_studio_revision_commit" in sql
    assert "CREATE CONSTRAINT TRIGGER zz_ops_crawler_studio_draft_commit" in sql
    assert "crawler draft has no attached revision" in sql
    assert "crawler revision was not attached to its draft" in sql
    assert "CREATE CONSTRAINT TRIGGER zz_ops_crawler_studio_review_commit" in sql
    assert "crawler review was not applied to its draft" in sql
    assert sql.count("DEFERRABLE INITIALLY DEFERRED") == 3
    assert "public.digest(convert_to(NEW.source_text, 'UTF8'), 'sha256')" in sql
    assert "ALTER TABLE ops_crawler_studio_revisions FORCE ROW LEVEL SECURITY" in sql
    assert "AS RESTRICTIVE FOR ALL" in sql
    assert "environment = current_crawler_api_environment()" in sql
    assert "GRANT UPDATE (status, latest_revision)" in sql
    assert "GRANT UPDATE (title" not in sql
    assert "CREATE OR REPLACE FUNCTION crawler_studio_contract_is_valid()" in sql
    assert "SECURITY INVOKER" in sql
    assert "SET search_path = pg_catalog, public" in sql
    assert "crawler studio live catalog contract differs" in sql
    assert "format_type(attribute.atttypid, attribute.atttypmod)" in sql
    assert "bool_and(attribute.attnotnull)" in sql
    assert "NOT constraint_row.convalidated" in sql
    assert "pg_get_constraintdef(constraint_row.oid)" in sql
    assert "constraint_row.condeferrable::text" in sql
    assert "constraint_row.condeferred::text" in sql
    assert "table_row.relforcerowsecurity" in sql
    assert "has_column_privilege('mooncen_crawler_api'" in sql
    assert "NEW.impacted_providers := expected_impacted_providers" in sql
    assert "array_agg(path.provider ORDER BY path.provider)" in sql
    assert "'impacted_providers'" in sql
    assert "'text[]'" in sql
    assert "procedure.prosrc" in sql
    assert "procedure.proconfig = ARRAY['search_path=pg_catalog, public']::TEXT[]" in sql
    assert "procedure.prosecdef" in sql
    assert "trigger_row.tgfoid" in sql
    assert "pg_get_expr(policy.polqual, policy.polrelid)" in sql
    assert "pg_get_expr(policy.polwithcheck, policy.polrelid)" in sql
    assert "table_acl_signature" in sql
    assert "column_acl_signature" in sql
    assert "relational_constraint_signature" in sql
    assert "check_constraint_signature" in sql
    assert "default_signature" in sql
    assert "index_signature" in sql
    assert "idx_ops_crawler_studio_drafts_environment" in sql
    assert "idx_ops_crawler_studio_revisions_draft" in sql
    assert "idx_ops_crawler_studio_reviews_draft" in sql
    assert "NOT owner_row.rolbypassrls" in sql
    assert "FROM pg_auth_members membership" in sql
    assert "CREATE TABLE IF NOT EXISTS ops_crawler_studio" not in sql
    assert "CREATE INDEX IF NOT EXISTS idx_ops_crawler_studio" not in sql
    assert "mooncen-crawler-studio-constraint-v1:sha256:" in sql
    assert "obj_description(constraint_row.oid, 'pg_constraint')" in sql
    check_contract = sql.split("IF check_constraint_signature <> ARRAY[", 1)[1].split(
        "]::TEXT[]", 1
    )[0]
    assert len(re.findall(r"[0-9a-f]{64}", check_contract)) == 16
    assert len(re.findall(r"ops_crawler_studio_[a-z_]+:chk_ops_crawler_studio_", check_contract)) == 16
    relational_contract = sql.split(
        "IF relational_constraint_signature <> ARRAY[", 1
    )[1].split("]::TEXT[]", 1)[0]
    assert relational_contract.count("FOREIGN KEY (") == 7
    assert "ON DELETE CASCADE" not in relational_contract


def _studio_contract_source() -> str:
    migration = MIGRATION.read_text(encoding="utf-8")
    matches = re.findall(
        r"CREATE OR REPLACE FUNCTION crawler_studio_contract_is_valid\(\)"
        r".*?AS \$crawler_studio_contract\$(.*?)\$crawler_studio_contract\$;",
        migration,
        re.DOTALL,
    )
    assert len(matches) == 1
    return matches[0].replace("\r\n", "\n").replace("\r", "\n").strip()


def test_runtime_verifier_source_identity_is_independent_and_exact() -> None:
    digest = hashlib.sha256(_studio_contract_source().encode("utf-8")).hexdigest()

    assert digest == studio_router._STUDIO_CONTRACT_SOURCE_SHA256
    installer = (ROOT / "tools/ensure_crawler_control_schema.py").read_text(
        encoding="utf-8"
    )
    assert "_studio_contract_source_sha256(" in installer
    assert "public.crawler_studio_contract_is_valid()" in installer
    assert 'raise SchemaInstallError("live Crawler Studio catalog contract has drifted")' in installer


def test_source_path_rejects_browser_canonicalization_and_traversal() -> None:
    with pytest.raises(crawler_studio.CrawlerStudioValidationError):
        crawler_studio.validate_source_path(r"Crawler\generated_yaml\ANYANG.py")
    with pytest.raises(crawler_studio.CrawlerStudioValidationError):
        crawler_studio.validate_source_path("Crawler/../secrets.py")


def test_studio_capabilities_never_claim_execution_or_release_approval() -> None:
    capabilities = crawler_studio.source_capabilities()

    assert capabilities["fixture_validation"]["available"] is False
    assert capabilities["source_execution"]["available"] is False
    assert capabilities["build"]["available"] is False
    assert capabilities["sign"]["available"] is False
    assert capabilities["source_approval"] == {
        "available": False,
        "reason": "independent_source_approval_evidence_not_implemented",
    }
    assert capabilities["independent_release_approval"] == {
        "available": False,
        "reason": "independent_operator_approval_evidence_not_implemented",
    }


def test_router_exposes_storage_review_only_and_main_includes_it() -> None:
    paths = {
        (route.path, tuple(sorted(route.methods or ())))
        for route in studio_router.router.routes
        if isinstance(route, APIRoute)
    }
    assert ("/api/ops/crawler-studio/capabilities", ("GET",)) in paths
    assert ("/api/ops/crawler-studio/providers", ("GET",)) in paths
    assert ("/api/ops/crawler-studio/drafts", ("POST",)) in paths
    assert ("/api/ops/crawler-studio/drafts/{draft_id}/revisions", ("POST",)) in paths
    assert (
        "/api/ops/crawler-studio/drafts/{draft_id}/revisions/{revision}",
        ("GET",),
    ) in paths
    assert ("/api/ops/crawler-studio/drafts/{draft_id}/reviews", ("POST",)) in paths
    assert all(
        forbidden not in route.path
        for route in studio_router.router.routes
        for forbidden in ("/execute", "/validate", "/build", "/sign", "/deploy")
    )
    main_source = (ROOT / "backend/main.py").read_text(encoding="utf-8")
    assert "crawler_studio," in main_source
    assert "app.include_router(crawler_studio.router)" in main_source
    router_source = (ROOT / "backend/routers/crawler_studio.py").read_text(encoding="utf-8")
    assert "subprocess" not in router_source
    assert "ops_jobs" not in router_source
    assert router_source.count("user_id=None") == 3
    assert '"actor_user_id": str(user.id)' in router_source
    revision_list_sql = router_source.split(
        '@router.get("/drafts/{draft_id}/revisions")', 1
    )[1].split('@router.get("/drafts/{draft_id}/revisions/{revision}")', 1)[0]
    assert "source_text" not in revision_list_sql


class _MappingRows:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def first(self) -> dict[str, object] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, object]]:
        return self._rows


class _Result:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> _MappingRows:
        return _MappingRows(self._rows)


class _SchemaResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def all(self) -> list[object]:
        assert isinstance(self.value, list)
        return self.value

    def scalar(self) -> object:
        return self.value

    def first(self) -> object:
        return self.value


class _SchemaDatabase:
    def __init__(
        self,
        *,
        source: str,
        contract_valid: bool,
        identity_valid: bool = True,
    ) -> None:
        self.source = source
        self.contract_valid = contract_valid
        self.identity_valid = identity_valid
        self.rolled_back = 0
        self.statements: list[str] = []

    def execute(self, statement: object, _parameters: object = None) -> _SchemaResult:
        sql = str(statement)
        self.statements.append(sql)
        if "unnest(CAST(:required AS text[]))" in sql:
            rows = [(name, True) for name in [*studio_router._STUDIO_TABLES, "marker"]]
            return _SchemaResult(rows)
        if "FROM ops_crawler_control_database_marker" in sql:
            return _SchemaResult(True)
        if "SELECT current_crawler_api_environment()" in sql:
            return _SchemaResult("development")
        if "procedure.proname = 'crawler_studio_contract_is_valid'" in sql:
            return _SchemaResult((self.source, self.identity_valid))
        if "SELECT public.crawler_studio_contract_is_valid()" in sql:
            return _SchemaResult(self.contract_valid)
        raise AssertionError(f"unexpected schema query: {sql}")

    def rollback(self) -> None:
        self.rolled_back += 1


def test_runtime_studio_schema_rejects_live_contract_body_or_catalog_drift(
    monkeypatch,
) -> None:
    source = _studio_contract_source()
    monkeypatch.setattr(studio_router, "current_environment", lambda: "development")

    valid = _SchemaDatabase(source=source, contract_valid=True)
    assert studio_router._studio_schema_available(valid) is True

    replaced = _SchemaDatabase(source="BEGIN RETURN TRUE; END;", contract_valid=True)
    assert studio_router._studio_schema_available(replaced) is False

    catalog_drift = _SchemaDatabase(source=source, contract_valid=False)
    assert studio_router._studio_schema_available(catalog_drift) is False

    unsafe_identity = _SchemaDatabase(
        source=source, contract_valid=True, identity_valid=False
    )
    assert studio_router._studio_schema_available(unsafe_identity) is False
    assert sum(
        "SELECT public.crawler_studio_contract_is_valid()" in statement
        and "FROM pg_proc" not in statement
        for statement in unsafe_identity.statements
    ) == 0
    assert (
        valid.rolled_back
        == replaced.rolled_back
        == catalog_drift.rolled_back
        == unsafe_identity.rolled_back
        == 1
    )


class _ConflictDatabase:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.rolled_back = False

    def execute(self, statement: object, _parameters: object = None) -> _Result:
        sql = str(statement)
        self.statements.append(sql)
        if "FROM ops_crawler_studio_drafts" in sql:
            return _Result(
                [
                    {
                        "id": str(uuid4()),
                        "environment": "development",
                        "provider": "EMART",
                        "source_path": "Crawler/Crawler_Emart.py",
                        "title": "Emart source",
                        "status": "draft",
                        "latest_revision": 2,
                        "created_by": str(uuid4()),
                        "created_at": None,
                        "updated_at": None,
                    }
                ]
            )
        raise AssertionError(f"optimistic conflict performed an unexpected query: {sql}")

    def rollback(self) -> None:
        self.rolled_back = True


class _ReviewDigestConflictDatabase:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.rolled_back = False

    def execute(self, statement: object, _parameters: object = None) -> _Result:
        sql = str(statement)
        self.statements.append(sql)
        if "FROM ops_crawler_studio_drafts" in sql:
            return _Result(
                [
                    {
                        "id": str(uuid4()),
                        "environment": "development",
                        "provider": "EMART",
                        "source_path": "Crawler/Crawler_Emart.py",
                        "title": "Emart source",
                        "status": "draft",
                        "latest_revision": 1,
                        "created_by": str(uuid4()),
                        "created_at": None,
                        "updated_at": None,
                    }
                ]
            )
        if "SELECT source_sha256 FROM ops_crawler_studio_revisions" in sql:
            return _Result([{"source_sha256": "1" * 64}])
        raise AssertionError(f"digest conflict performed an unexpected query: {sql}")

    def rollback(self) -> None:
        self.rolled_back = True


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/ops/crawler-studio/drafts/test/revisions",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("test", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )


def test_append_revision_optimistic_conflict_has_no_write(monkeypatch) -> None:
    source = "# reviewed source\n"
    database = _ConflictDatabase()
    monkeypatch.setattr(studio_router, "_require_studio", lambda _db: database)
    payload = studio_router.AppendRevisionRequest(
        expected_revision=1,
        source_text=source,
        source_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
        change_summary="reviewed change",
    )

    with pytest.raises(HTTPException) as rejected:
        studio_router.append_studio_revision(
            uuid4(), payload, _request(), SimpleNamespace(id=uuid4()), database
        )

    assert rejected.value.status_code == 409
    assert rejected.value.detail["code"] == "crawler_studio_revision_conflict"
    assert database.rolled_back is True
    assert all(
        not statement.lstrip().startswith(("INSERT", "UPDATE"))
        for statement in database.statements
    )


def test_review_requires_the_exact_stored_source_digest(monkeypatch) -> None:
    database = _ReviewDigestConflictDatabase()
    monkeypatch.setattr(studio_router, "_require_studio", lambda db: db)
    monkeypatch.setattr(studio_router, "ops_role_for_user", lambda _user: "operator")
    payload = studio_router.ReviewDraftRequest(
        expected_revision=1,
        expected_source_sha256="0" * 64,
        decision="submit",
        comment="review the exact source",
    )

    with pytest.raises(HTTPException) as rejected:
        studio_router.review_studio_draft(
            uuid4(), payload, _request(), SimpleNamespace(id=uuid4()), database
        )

    assert rejected.value.status_code == 409
    assert database.rolled_back is True
    assert all(
        not statement.lstrip().startswith(("INSERT", "UPDATE"))
        for statement in database.statements
    )


def test_source_approval_is_closed_before_any_database_access(monkeypatch) -> None:
    monkeypatch.setattr(studio_router, "ops_role_for_user", lambda _user: "admin")
    monkeypatch.setattr(
        studio_router,
        "_require_studio",
        lambda _db: pytest.fail("approval gate accessed the database"),
    )
    payload = studio_router.ReviewDraftRequest(
        expected_revision=1,
        expected_source_sha256="0" * 64,
        decision="approve",
        comment="independent evidence is required",
    )

    with pytest.raises(HTTPException) as rejected:
        studio_router.review_studio_draft(
            uuid4(), payload, _request(), SimpleNamespace(id=uuid4()), None
        )

    assert rejected.value.status_code == 409
    assert rejected.value.detail["code"] == "independent_source_approval_not_ready"


def test_roles_and_installer_keep_studio_acl_after_convergence() -> None:
    roles = (ROOT / "DB/roles.sql").read_text(encoding="utf-8")
    roles_body = (ROOT / "DB/roles_body.sql").read_text(encoding="utf-8")
    installer = (ROOT / "tools/ensure_crawler_control_schema.py").read_text(encoding="utf-8")
    builder = (ROOT / "tools/build_crawler_control_release.py").read_text(encoding="utf-8")

    for source in (roles, roles_body):
        assert "GRANT SELECT ON ops_crawler_studio_provider_paths" in source
        assert "GRANT SELECT, INSERT ON ops_crawler_studio_drafts," in source
        assert "GRANT UPDATE (status, latest_revision)" in source
        assert "GRANT EXECUTE ON FUNCTION crawler_studio_contract_is_valid()" in source
        assert "ops_crawler_studio_drafts TO mooncen_api" not in source
    assert "STUDIO_MIGRATION_VERSION" in installer
    assert "studio_recorded == studio_checksum" in installer
    assert "cursor.execute(studio_migration)" in installer
    assert '"DB/crawler_control_migrations/20260812_003_crawler_studio.sql"' in builder
