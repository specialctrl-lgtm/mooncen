from __future__ import annotations

import socket
from http.cookies import SimpleCookie
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request, Response
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.database import get_db
from backend.main import _cors_origin_regex, _cors_origins, app
from backend.privacy_notice import MEMBERSHIP_PRIVACY_NOTICE
from backend.routers import auth
from backend.routers.courses import _course_radius_clause, _public_http_url, _same_origin
from backend.routers.seo_pages import json_ld_script_data
from tools.maintenance.refresh_course_status import validate_public_source_url


@pytest.fixture(autouse=True)
def clear_security_state():
    app.dependency_overrides.clear()
    with auth._rate_limit_lock:
        auth._rate_limit_buckets.clear()
    yield
    app.dependency_overrides.clear()


def test_unhandled_exception_does_not_expose_detail_or_traceback():
    class BrokenDB:
        def query(self, *_args, **_kwargs):
            raise RuntimeError("sensitive-db-marker")

    def broken_db():
        yield BrokenDB()

    app.dependency_overrides[get_db] = broken_db
    response = TestClient(app, raise_server_exceptions=False).get(
        "/api/courses/00000000-0000-0000-0000-000000000000"
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"
    assert "trace" not in response.json()
    assert "sensitive-db-marker" not in response.text
    assert response.headers["X-Request-ID"] == response.json()["request_id"]


def test_untrusted_host_header_is_rejected_before_routing():
    response = TestClient(app, raise_server_exceptions=False).get(
        "/health",
        headers={"Host": "attacker.invalid"},
    )

    assert response.status_code == 400
    assert response.text == "Invalid host header"


def test_production_rejects_weak_auth_secret(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_SECRET", "change-me")
    with pytest.raises(RuntimeError, match="AUTH_SECRET"):
        auth.validate_auth_configuration()


def test_rate_limit_identity_cannot_be_rotated_with_fake_bearer_tokens():
    first = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"authorization", b"Bearer first")],
            "client": ("203.0.113.9", 1234),
        }
    )
    second = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"authorization", b"Bearer second")],
            "client": ("203.0.113.9", 4321),
        }
    )

    assert auth._request_identity(first) == "203.0.113.9"
    assert auth._request_identity(second) == "203.0.113.9"


def test_oauth_state_is_signed_and_bound_to_provider_and_redirect(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_SECRET", "a-unique-production-secret-that-is-long-enough")
    redirect_uri = "https://mooncen.test/oauth/callback"
    monkeypatch.setenv("OAUTH_REDIRECT_URIS", redirect_uri)

    state = auth._make_oauth_state("google", redirect_uri)
    auth._verify_oauth_state(state, "google", redirect_uri, state)

    with pytest.raises(HTTPException, match="Invalid OAuth state"):
        auth._verify_oauth_state(state, "naver", redirect_uri)
    with pytest.raises(HTTPException, match="Invalid OAuth state"):
        auth._verify_oauth_state("google:legacy-random-state", "google", redirect_uri)


def test_google_oauth_state_issues_browser_bound_pkce_challenge(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_SECRET", "a-unique-production-secret-that-is-long-enough")
    redirect_uri = "https://mooncen.test/oauth/callback"
    monkeypatch.setenv("OAUTH_REDIRECT_URIS", redirect_uri)
    response = Response()

    result = auth.oauth_state(
        response,
        "google",
        redirect_uri,
        True,
        MEMBERSHIP_PRIVACY_NOTICE.version,
    )

    parsed = SimpleCookie()
    for key, value in response.raw_headers:
        if key == b"set-cookie":
            parsed.load(value.decode("latin-1"))
    verifier = parsed[auth.OAUTH_PKCE_COOKIE_NAME].value
    expected_challenge = auth._b64url(auth.hashlib.sha256(verifier.encode("ascii")).digest())
    assert result.code_challenge_method == "S256"
    assert result.code_challenge == expected_challenge
    assert parsed[auth.OAUTH_PKCE_COOKIE_NAME]["httponly"] is True
    assert parsed[auth.OAUTH_PKCE_COOKIE_NAME]["secure"] is True


def test_google_oauth_does_not_accept_legacy_access_token_only_payload():
    with pytest.raises(ValidationError):
        auth.GoogleOAuthRequest(access_token="attacker-controlled-token")


def test_json_ld_serialization_cannot_break_out_of_script_element():
    rendered = json_ld_script_data({"name": "</script><script>alert(1)</script>"})
    assert "</script>" not in rendered.lower()
    assert "<script>" not in rendered.lower()
    assert "\\u003c/script\\u003e" in rendered.lower()


def test_course_source_url_rejects_private_hosts_and_cross_origin_override():
    with pytest.raises(HTTPException, match="Private source_url"):
        _public_http_url("http://127.0.0.1/admin")

    canonical = _public_http_url("https://courses.example.com/course/1")
    same_origin = _public_http_url("https://courses.example.com/course/2")
    other_origin = _public_http_url("https://attacker.example/course/2")
    assert _same_origin(canonical, same_origin)
    assert not _same_origin(canonical, other_origin)


def test_refresh_worker_rejects_dns_resolving_to_private_address(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", 443))],
    )
    with pytest.raises(ValueError, match="public HTTP"):
        validate_public_source_url("https://courses.example.test/path")


def test_sensitive_routes_have_authz_dependencies():
    routes = {}
    for included in app.routes:
        if isinstance(included, APIRoute):
            candidates = [("", included)]
        elif hasattr(included, "original_router") and hasattr(included, "include_context"):
            candidates = [
                (included.include_context.prefix, route)
                for route in included.original_router.routes
                if isinstance(route, APIRoute)
            ]
        else:
            candidates = []
        for prefix, route in candidates:
            routes[(prefix + route.path, route.endpoint.__name__)] = {
                getattr(dependency.call, "__name__", "") for dependency in route.dependant.dependencies
            }
    assert "require_admin_user" in routes[("/api/courses/update-requests", "get_course_update_requests")]
    assert ("/api/courses/{course_id}/analyze", "analyze_course") not in routes
    assert "get_current_user" in routes[("/api/courses/{course_id}/update-request", "request_course_update")]


def test_course_query_and_uuid_parameters_are_bounded_before_database_use():
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/api/courses/?size=10000").status_code == 422
    assert client.get("/api/courses/?keyword=요").status_code == 422
    assert client.get("/api/courses/not-a-uuid").status_code == 422
    assert client.get("/api/courses/?branch_ids=not-a-uuid").status_code == 422
    too_many_providers = ",".join(f"P{index}" for index in range(51))
    assert client.get(f"/api/courses/?provider={too_many_providers}").status_code == 422
    assert client.get("/api/courses/?lat=37&lon=127").status_code == 422
    assert client.get("/api/courses/?lat=37&lon=127&radius_km=30.1").status_code == 422
    assert client.get("/api/branches/nearby?lat=91&lon=127").status_code == 422
    assert client.get("/api/branches/nearby?lat=37&lon=181").status_code == 422
    assert client.get("/api/branches/nearby?lat=37&lon=127&limit=2001").status_code == 422


def test_course_radius_filter_uses_bound_postgis_geography_parameters():
    clause = _course_radius_clause(37.5665, 126.978, 20)
    assert clause is not None
    compiled = clause.compile()

    assert "ST_DWithin" in str(compiled)
    assert compiled.params == {
        "course_radius_lat": 37.5665,
        "course_radius_lon": 126.978,
        "course_radius_m": 20_000,
    }
    assert _course_radius_clause(None, None, None) is None

    with pytest.raises(HTTPException) as exc_info:
        _course_radius_clause(37.5665, None, 10)
    assert exc_info.value.status_code == 422


def test_production_cors_uses_exact_https_allowlist(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv(
        "MOONCEN_CORS_ORIGINS",
        "https://mooncen.test,http://mooncen.test,https://admin.mooncen.test/",
    )
    assert _cors_origins() == ["https://mooncen.test", "https://admin.mooncen.test"]
    assert _cors_origin_regex() is None


def test_health_is_database_readiness_and_hides_failure_detail():
    class BrokenDB:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("database-host-secret")

    def broken_db():
        yield BrokenDB()

    app.dependency_overrides[get_db] = broken_db
    response = TestClient(app, raise_server_exceptions=False).get("/health")
    assert response.status_code == 503
    assert response.json() == {"detail": "Service not ready"}
    assert "database-host-secret" not in response.text


def test_health_checks_critical_api_relations_and_columns_not_only_connectivity():
    statements: list[str] = []

    class SchemaMismatchDB:
        def execute(self, statement, *_args, **_kwargs):
            rendered = str(statement)
            statements.append(rendered)
            if "FROM courses" in rendered:
                raise RuntimeError("missing-is-active-column")
            return SimpleNamespace()

    def mismatched_db():
        yield SchemaMismatchDB()

    app.dependency_overrides[get_db] = mismatched_db
    response = TestClient(app, raise_server_exceptions=False).get("/health")

    assert response.status_code == 503
    assert response.json() == {"detail": "Service not ready"}
    assert statements[0].endswith("FROM branches LIMIT 0")
    assert statements[1].endswith("FROM courses LIMIT 0")
    assert all(statement.strip() != "SELECT 1" for statement in statements)
    assert "missing-is-active-column" not in response.text


def test_admin_email_allowlist_cannot_be_claimed_by_unverified_password_signup(monkeypatch):
    monkeypatch.setenv("MOONCEN_ADMIN_EMAILS", "admin@mooncen.test")
    password_user = SimpleNamespace(
        id=uuid4(),
        email="admin@mooncen.test",
        provider="email",
        password_hash="hash",
    )
    with pytest.raises(HTTPException, match="Administrator access required"):
        auth.require_admin_user(password_user)

    oauth_user = SimpleNamespace(
        id=uuid4(),
        email="admin@mooncen.test",
        provider="google",
        password_hash=None,
        oauth_accounts=[
            SimpleNamespace(
                provider="google",
                provider_user_id="google-admin-id",
                email="admin@mooncen.test",
                email_verified=True,
            )
        ],
    )
    assert auth.require_admin_user(oauth_user) is oauth_user


def test_admin_requires_verified_oauth_email_or_provider_identity(monkeypatch):
    monkeypatch.setenv("MOONCEN_ADMIN_EMAILS", "admin@mooncen.test")
    monkeypatch.setenv("MOONCEN_ADMIN_PROVIDER_IDS", "naver:immutable-naver-id")
    monkeypatch.setenv("MOONCEN_ADMIN_USER_IDS", "internal-user-id-is-not-trusted")

    unverified = SimpleNamespace(
        id="internal-user-id-is-not-trusted",
        email="admin@mooncen.test",
        oauth_accounts=[
            SimpleNamespace(
                provider="naver",
                provider_user_id="ordinary-naver-id",
                email="admin@mooncen.test",
                email_verified=False,
            )
        ],
    )
    with pytest.raises(HTTPException, match="Administrator access required"):
        auth.require_admin_user(unverified)

    provider_allowlisted = SimpleNamespace(
        id=uuid4(),
        email="unverified-contact@example.test",
        oauth_accounts=[
            SimpleNamespace(
                provider="naver",
                provider_user_id="immutable-naver-id",
                email="unverified-contact@example.test",
                email_verified=False,
            )
        ],
    )
    assert auth.require_admin_user(provider_allowlisted) is provider_allowlisted


def test_browser_session_cookie_is_httponly_and_cookie_mutations_require_csrf(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_SECRET", "test-auth-secret-with-at-least-thirty-two-characters")
    user = SimpleNamespace(
        id=uuid4(),
        email="verified@example.test",
        name="Verified User",
        provider="email",
        auth_token_version=1,
    )
    response = Response()
    auth._set_auth_cookies(response, user)
    cookie_headers = [value.decode("latin-1") for key, value in response.raw_headers if key == b"set-cookie"]
    access_header = next(value for value in cookie_headers if value.startswith(f"{auth.ACCESS_COOKIE_NAME}="))
    csrf_header = next(value for value in cookie_headers if value.startswith(f"{auth.CSRF_COOKIE_NAME}="))
    assert "HttpOnly" in access_header and "Secure" in access_header and "SameSite=lax" in access_header
    assert "HttpOnly" not in csrf_header and "Secure" in csrf_header

    parsed = SimpleCookie()
    for value in cookie_headers:
        parsed.load(value)
    access_token = parsed[auth.ACCESS_COOKIE_NAME].value
    csrf_token = parsed[auth.CSRF_COOKIE_NAME].value

    class FakeQuery:
        def filter(self, *_args):
            return self

        def first(self):
            return user

    class FakeDB:
        def query(self, *_args):
            return FakeQuery()

    def make_request(header_token: str) -> Request:
        cookie = f"{auth.ACCESS_COOKIE_NAME}={access_token}; {auth.CSRF_COOKIE_NAME}={csrf_token}"
        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/users/me/course-marks/example",
                "headers": [
                    (b"cookie", cookie.encode("latin-1")),
                    (b"x-csrf-token", header_token.encode("ascii")),
                ],
                "client": ("127.0.0.1", 12345),
            }
        )

    assert auth.get_current_user(make_request(csrf_token), None, FakeDB()) is user
    with pytest.raises(HTTPException) as exc_info:
        auth.get_current_user(make_request("wrong-token"), None, FakeDB())
    assert exc_info.value.status_code == 403


def test_dedicated_ops_sessions_are_revoked_when_the_password_verifier_changes(
    monkeypatch,
):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv(
        "AUTH_SECRET",
        "test-auth-secret-with-at-least-thirty-two-characters",
    )
    monkeypatch.setenv("MOONCEN_OPS_LOGIN_ID", "opsadmin")

    def password_hash(password: str, salt: str) -> str:
        digest = auth.hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("ascii"),
            310_000,
        ).hex()
        return f"pbkdf2_sha256$310000${salt}${digest}"

    old_hash = password_hash("old-ops-password", "old_rotation_salt")
    new_hash = password_hash("new-ops-password", "new_rotation_salt")
    monkeypatch.setenv("MOONCEN_OPS_PASSWORD_HASH", old_hash)
    ops_user = SimpleNamespace(
        id=uuid4(),
        email=auth.OPS_ACCOUNT_EMAIL,
        name="opsadmin",
        provider="ops",
        auth_token_version=1,
    )

    class FakeQuery:
        def filter(self, *_args):
            return self

        def first(self):
            return ops_user

    class FakeDB:
        def query(self, *_args):
            return FakeQuery()

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/ops/session",
            "headers": [],
            "client": ("127.0.0.1", 12345),
        }
    )
    old_token = auth._make_token(ops_user)
    old_payload = auth._read_token(old_token)
    assert old_payload[auth.OPS_PASSWORD_VERSION_CLAIM] == auth._ops_password_version()
    assert auth.get_current_user(request, f"Bearer {old_token}", FakeDB()) is ops_user

    legacy_payload = dict(old_payload)
    legacy_payload.pop(auth.OPS_PASSWORD_VERSION_CLAIM)
    legacy_token = auth.jwt.encode(
        legacy_payload,
        auth._secret(),
        algorithm="HS256",
        headers={"kid": "v1"},
    )
    with pytest.raises(HTTPException) as legacy_error:
        auth.get_current_user(request, f"Bearer {legacy_token}", FakeDB())
    assert legacy_error.value.status_code == 401

    monkeypatch.setenv("MOONCEN_OPS_PASSWORD_HASH", new_hash)
    with pytest.raises(HTTPException) as rotated_error:
        auth.get_current_user(request, f"Bearer {old_token}", FakeDB())
    assert rotated_error.value.status_code == 401

    new_token = auth._make_token(ops_user)
    assert auth.get_current_user(request, f"Bearer {new_token}", FakeDB()) is ops_user

    ordinary_user = SimpleNamespace(
        id=uuid4(),
        email="ordinary@example.test",
        name="Ordinary User",
        provider="email",
        auth_token_version=1,
    )

    class OrdinaryQuery:
        def filter(self, *_args):
            return self

        def first(self):
            return ordinary_user

    class OrdinaryDB:
        def query(self, *_args):
            return OrdinaryQuery()

    ordinary_token = auth._make_token(ordinary_user)
    assert auth.OPS_PASSWORD_VERSION_CLAIM not in auth._read_token(ordinary_token)
    monkeypatch.setenv("MOONCEN_OPS_PASSWORD_HASH", old_hash)
    assert (
        auth.get_current_user(request, f"Bearer {ordinary_token}", OrdinaryDB())
        is ordinary_user
    )
