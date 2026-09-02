from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from fastapi import HTTPException
from selenium.webdriver.chrome.options import Options
from starlette.responses import Response

from Crawler.selenium_driver import _browser_service_environment, build_chrome_driver, configure_driver_timeouts
from DB import db_utils
from DB.connection_settings import database_connect_options, database_sslmode
from backend import schemas
from backend.routers import auth
from backend.routers.user_courses import FavoriteCourseItem, _course_url
from tools import (
    apply_staging_batch,
    course_data_quality_report,
    discover_application_urls,
    generate_culture_facility_api_targets,
)
from tools.maintenance import audit_emart_branch_locations, kakao_geocode_branches
from utils.url_security import (
    safe_course_reference,
    safe_external_http_url,
    sanitize_course_external_urls,
    sanitize_course_payload,
)


ROOT = Path(__file__).resolve().parents[1]


def test_oauth_http_redirects_are_only_allowed_for_development_loopback(monkeypatch):
    monkeypatch.setenv("OAUTH_REDIRECT_URIS", "http://mooncen.test/callback,http://127.0.0.1:5173/callback")
    monkeypatch.setenv("ENVIRONMENT", "development")

    assert auth._validate_redirect_uri("http://127.0.0.1:5173/callback") == "http://127.0.0.1:5173/callback"
    assert auth._validate_redirect_uri("http://127.0.0.2:5173/callback") == "http://127.0.0.2:5173/callback"
    with pytest.raises(HTTPException, match="must use HTTPS"):
        auth._validate_redirect_uri("http://mooncen.test/callback")

    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(HTTPException, match="must use HTTPS"):
        auth._validate_redirect_uri("http://127.0.0.1:5173/callback")


def test_naver_email_verification_is_derived_from_provider_claim():
    assert auth._provider_reports_verified_email(None) is False
    assert auth._provider_reports_verified_email(False) is False
    assert auth._provider_reports_verified_email("false") is False
    assert auth._provider_reports_verified_email(True) is True
    assert auth._provider_reports_verified_email("verified") is True


def test_naver_oauth_does_not_promote_missing_verification_claim(monkeypatch):
    redirect_uri = "https://mooncen.test/oauth/callback"
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-long-enough-for-signed-state")
    monkeypatch.setenv("OAUTH_REDIRECT_URIS", redirect_uri)
    monkeypatch.setenv("NAVER_OAUTH_CLIENT_ID", "naver-client")
    monkeypatch.setenv("NAVER_OAUTH_CLIENT_SECRET", "naver-secret")
    state = auth._make_oauth_state("naver", redirect_uri)

    class ProviderResponse:
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    monkeypatch.setattr(
        auth.requests,
        "post",
        lambda *_args, **_kwargs: ProviderResponse({"access_token": "provider-access-token"}),
    )
    monkeypatch.setattr(
        auth.requests,
        "get",
        lambda *_args, **_kwargs: ProviderResponse(
            {
                "resultcode": "00",
                "response": {
                    "id": "immutable-naver-user-id",
                    "email": "user@example.test",
                    "name": "User",
                },
            }
        ),
    )
    captured: dict[str, object] = {}

    def fake_oauth_user(_db, provider, provider_user_id, email, name, *, email_verified):
        captured.update(
            provider=provider,
            provider_user_id=provider_user_id,
            email=email,
            name=name,
            email_verified=email_verified,
        )
        return object()

    monkeypatch.setattr(auth, "_oauth_user", fake_oauth_user)
    monkeypatch.setattr(auth, "_set_auth_cookies", lambda _response, user: user)

    result = auth.naver_oauth(
        auth.NaverOAuthRequest(code="authorization-code", state=state, redirect_uri=redirect_uri),
        SimpleNamespace(cookies={"mooncen_oauth_state": state}),
        Response(),
        object(),
    )
    assert result is not None
    assert captured["provider_user_id"] == "immutable-naver-user-id"
    assert captured["email_verified"] is False


def test_remote_production_database_defaults_to_full_certificate_verification(monkeypatch):
    monkeypatch.delenv("DB_SSLMODE", raising=False)
    assert database_sslmode("db.example.test", "production") == "verify-full"
    assert database_sslmode("db.example.test", "staging") == "verify-full"
    assert database_sslmode("127.0.0.1", "production") == "prefer"
    assert database_sslmode("db.example.test", "ci") == "prefer"

    for unsafe_mode in ("disable", "allow", "prefer", "require", "verify-ca"):
        monkeypatch.setenv("DB_SSLMODE", unsafe_mode)
        with pytest.raises(RuntimeError, match="verify-full"):
            database_sslmode("db.example.test", "production")
        with pytest.raises(RuntimeError, match="verify-full"):
            database_sslmode("db.example.test", "staging")
    monkeypatch.setenv("DB_SSLMODE", "verify-full")
    assert database_sslmode("db.example.test", "production") == "verify-full"


def test_all_database_clients_receive_connection_and_query_timeouts(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DB_HOST", "db.example.test")
    monkeypatch.setenv("DB_CONNECT_TIMEOUT", "7")
    monkeypatch.setenv("DB_STATEMENT_TIMEOUT_MS", "12000")
    monkeypatch.setenv("DB_LOCK_TIMEOUT_MS", "2500")
    monkeypatch.setenv("DB_RUNTIME_USER", "mooncen_test_worker")
    monkeypatch.setenv("DB_RUNTIME_PASSWORD", "test-worker-password")
    monkeypatch.delenv("DB_SSLMODE", raising=False)

    expected = database_connect_options("db.example.test", "test-client")
    assert expected["connect_timeout"] == 7
    assert expected["sslmode"] == "verify-full"
    assert expected["options"].startswith("-c search_path=pg_catalog,public ")
    assert "statement_timeout=12000" in expected["options"]
    assert "lock_timeout=2500" in expected["options"]

    crawler_config = db_utils.get_db_config()
    assert crawler_config["connect_timeout"] == 7
    assert crawler_config["sslmode"] == "verify-full"
    assert crawler_config["options"].startswith("-c search_path=pg_catalog,public ")
    assert "statement_timeout=12000" in crawler_config["options"]
    assert "lock_timeout=2500" in crawler_config["options"]

    monkeypatch.setenv("PRIMARY_DB_HOST", "db.example.test")
    applier_config = apply_staging_batch.db_config("PRIMARY", "mooncen")
    assert applier_config["connect_timeout"] == 7
    assert applier_config["sslmode"] == "verify-full"
    assert "statement_timeout=12000" in applier_config["options"]

    captured: list[dict] = []
    monkeypatch.setattr(
        course_data_quality_report.psycopg2,
        "connect",
        lambda **kwargs: captured.append(kwargs) or SimpleNamespace(),
    )
    course_data_quality_report.connect()
    audit_emart_branch_locations.db_connect()
    assert len(captured) == 2
    for config in captured:
        assert config["connect_timeout"] == 7
        assert config["sslmode"] == "verify-full"
        assert "statement_timeout=12000" in config["options"]
        assert "lock_timeout=2500" in config["options"]


def test_external_url_guard_rejects_active_content_and_credentials():
    assert safe_external_http_url("https://courses.example.test/course/1")
    assert safe_external_http_url("javascript:alert(1)") == ""
    assert safe_external_http_url("https://user:password@example.test/private") == ""
    assert safe_external_http_url("https://example.test/path\nInjected: value") == ""

    course = {
        "raw_url": "https://courses.example.test/course/1",
        "application_url": "javascript:alert(1)",
        "image_url": "data:image/svg+xml,<svg/>",
    }
    sanitize_course_external_urls(course)
    assert course["application_url"] is None
    assert course["image_url"] is None

    with pytest.raises(ValueError, match="raw_url"):
        sanitize_course_external_urls({"raw_url": "file:///etc/passwd"})

    internal_reference = "course:123e4567-e89b-42d3-a456-426614174000"
    assert safe_course_reference(internal_reference) == internal_reference
    assert safe_course_reference("course:not-a-uuid") == ""
    with pytest.raises(HTTPException, match="course_url"):
        _course_url(None, course_url="javascript:alert(1)")
    item = FavoriteCourseItem(id=1, course_url="javascript:alert(1)")
    assert item.course_url == ""


def test_api_schemas_fail_closed_for_unsafe_external_urls():
    branch = schemas.BranchBase(
        id="branch-1",
        name="Example",
        provider="EXAMPLE",
        website_url="javascript:alert(1)",
        favicon_url="data:image/svg+xml,<svg/>",
    )
    course = schemas.CourseBase(
        id="course-1",
        provider="EXAMPLE",
        title="Example course",
        raw_url="javascript:alert(1)",
        application_url="https://user:password@example.test/private",
        image_url="https://images.example.test/course.png",
    )
    assert branch.website_url is None
    assert branch.favicon_url is None
    assert course.raw_url is None
    assert course.application_url is None
    assert course.image_url == "https://images.example.test/course.png"


def test_crawler_course_payloads_are_bounded_before_database_writes():
    course = {
        "provider": "EXAMPLE",
        "provider_course_id": "course-1",
        "title": "T" * 300,
        "description": "D" * 25_000,
        "raw_url": "https://example.test/course/1",
        "target_tags": ["x" * 200] * 100,
    }

    sanitize_course_payload(course)

    assert len(course["title"]) == 255
    assert len(course["description"]) == 20_000
    assert len(course["target_tags"]) == 64
    assert all(len(tag) == 100 for tag in course["target_tags"])


def test_map_request_failures_never_expose_api_keys(monkeypatch):
    kakao_api_key = "a-sensitive-kakao-rest-key-that-must-not-leak"
    audit_api_key = "a-second-sensitive-kakao-key-that-must-not-leak"

    def fail_kakao_request(url, *, headers, params, timeout):
        response = requests.Response()
        response.status_code = 403
        response.url = url
        raise requests.HTTPError(
            f"403 Authorization={headers['Authorization']}",
            response=response,
        )

    monkeypatch.setattr(kakao_geocode_branches.requests, "get", fail_kakao_request)

    with pytest.raises(RuntimeError) as geocode_error:
        kakao_geocode_branches.geocode_branch(kakao_api_key, "EMART", "Example", None, 3)

    def fail_audit_request(url, *, headers, params, timeout):
        response = requests.Response()
        response.status_code = 403
        response.url = url
        raise requests.HTTPError(
            f"403 Authorization={headers['Authorization']}",
            response=response,
        )

    monkeypatch.setattr(audit_emart_branch_locations.requests, "get", fail_audit_request)

    with pytest.raises(RuntimeError) as audit_error:
        audit_emart_branch_locations.fetch_candidates(audit_api_key, "Example", 3, 75)

    assert kakao_api_key not in str(geocode_error.value)
    assert audit_api_key not in str(audit_error.value)
    assert "status=403" in str(geocode_error.value)
    assert "status=403" in str(audit_error.value)


def test_search_and_culture_api_failures_never_expose_service_keys(monkeypatch):
    google_key = "google-custom-search-key-that-must-not-leak"
    culture_key = "culture-service-key-that-must-not-leak"

    class FailingGoogleSession:
        @staticmethod
        def get(url, *, params, timeout):
            response = requests.Response()
            response.status_code = 429
            response.url = f"{url}?key={params['key']}"
            raise requests.HTTPError(f"429 for url: {response.url}", response=response)

    class FailingSession:
        @staticmethod
        def get(url, *, params, timeout):
            response = requests.Response()
            response.status_code = 500
            response.url = f"{url}?serviceKey={params['serviceKey']}"
            raise requests.HTTPError(f"500 for url: {response.url}", response=response)

    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", google_key)
    monkeypatch.setenv("GOOGLE_SEARCH_CX", "search-engine-id")
    with pytest.raises(RuntimeError) as google_error:
        discover_application_urls.google_cse_candidates("course", 5, FailingGoogleSession())
    with pytest.raises(RuntimeError) as culture_error:
        generate_culture_facility_api_targets.fetch_page(FailingSession(), "https://api.example", culture_key, 1, 10)

    assert google_key not in str(google_error.value)
    assert culture_key not in str(culture_error.value)
    assert "status=429" in str(google_error.value)
    assert "status=500" in str(culture_error.value)


def test_selenium_driver_has_bounded_page_and_script_timeouts(monkeypatch):
    calls: list[tuple[str, int]] = []
    driver = SimpleNamespace(
        set_page_load_timeout=lambda value: calls.append(("page", value)),
        set_script_timeout=lambda value: calls.append(("script", value)),
    )
    monkeypatch.setenv("SELENIUM_PAGE_LOAD_TIMEOUT_SECONDS", "41")
    monkeypatch.setenv("SELENIUM_SCRIPT_TIMEOUT_SECONDS", "29")

    assert configure_driver_timeouts(driver) is driver
    assert calls == [("page", 41), ("script", 29)]


@pytest.mark.parametrize(
    "argument",
    (
        "--allow-running-insecure-content",
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-namespace-sandbox",
        "--disable-seccomp-filter-sandbox",
        "--disable-gpu-sandbox",
        "--disable-site-isolation-trials",
        "--disable-web-security",
        "--ignore-certificate-errors",
        "--no-zygote",
        "--single-process",
    ),
)
def test_selenium_driver_rejects_sandbox_disabling_arguments(argument):
    options = Options()
    options.add_argument(argument)

    with pytest.raises(RuntimeError, match="sandbox disabling argument is forbidden"):
        build_chrome_driver(options)


def test_selenium_browser_process_environment_excludes_crawler_secrets(monkeypatch):
    monkeypatch.setenv("HOME", "/tmp")
    monkeypatch.delenv("TMPDIR", raising=False)
    monkeypatch.setenv("DB_CRAWLER_PASSWORD", "do-not-inherit")
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "do-not-inherit")
    monkeypatch.setenv("KAKAO_MAPS_REST_API_KEY", "do-not-inherit")
    monkeypatch.setattr("Crawler.selenium_driver.os.name", "posix")

    environment = _browser_service_environment()

    assert environment["HOME"] == "/tmp"
    assert environment["TMPDIR"] == "/tmp"
    assert "DB_CRAWLER_PASSWORD" not in environment
    assert "GOOGLE_MAPS_API_KEY" not in environment
    assert "KAKAO_MAPS_REST_API_KEY" not in environment


def test_selenium_driver_uses_unique_temporary_profile_and_cleans_it(monkeypatch, tmp_path):
    chrome_binary = tmp_path / "chrome"
    chromedriver = tmp_path / "chromedriver"
    for executable in (chrome_binary, chromedriver):
        executable.write_text("", encoding="utf-8")
        executable.chmod(0o700)

    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    class Driver:
        def __init__(self):
            self.quit_calls = 0

        def quit(self):
            self.quit_calls += 1

        def set_page_load_timeout(self, _value):
            return None

        def set_script_timeout(self, _value):
            return None

    driver = Driver()
    options_seen = None

    def chrome(*, service, options):
        nonlocal options_seen
        options_seen = options
        return driver

    monkeypatch.setenv("CHROME_BINARY", str(chrome_binary))
    monkeypatch.setenv("CHROMEDRIVER", str(chromedriver))
    monkeypatch.setattr(
        "Crawler.selenium_driver._required_root_executable",
        lambda value, _label: value,
    )
    monkeypatch.setattr("Crawler.selenium_driver.tempfile.mkdtemp", lambda **_kwargs: str(profile_dir))
    monkeypatch.setattr("Crawler.selenium_driver.webdriver.Chrome", chrome)

    built = build_chrome_driver(Options())

    assert built is driver
    assert options_seen is not None
    assert f"--user-data-dir={profile_dir}" in options_seen.arguments
    built.quit()
    assert driver.quit_calls == 1
    assert not profile_dir.exists()


def test_selenium_driver_preserves_explicit_profile(monkeypatch, tmp_path):
    chrome_binary = tmp_path / "chrome"
    chromedriver = tmp_path / "chromedriver"
    for executable in (chrome_binary, chromedriver):
        executable.write_text("", encoding="utf-8")
        executable.chmod(0o700)

    driver = SimpleNamespace(
        quit=lambda: None,
        set_page_load_timeout=lambda _value: None,
        set_script_timeout=lambda _value: None,
    )
    monkeypatch.setenv("CHROME_BINARY", str(chrome_binary))
    monkeypatch.setenv("CHROMEDRIVER", str(chromedriver))
    monkeypatch.setattr(
        "Crawler.selenium_driver._required_root_executable",
        lambda value, _label: value,
    )
    monkeypatch.setattr(
        "Crawler.selenium_driver.tempfile.mkdtemp",
        lambda **_kwargs: pytest.fail("explicit profiles must not allocate a temporary profile"),
    )
    monkeypatch.setattr("Crawler.selenium_driver.webdriver.Chrome", lambda **_kwargs: driver)
    options = Options()
    options.add_argument("--user-data-dir=/tmp/explicit-profile")

    assert build_chrome_driver(options) is driver
    assert options.arguments.count("--user-data-dir=/tmp/explicit-profile") == 1


def test_database_migration_persists_oauth_verification_and_url_guards():
    migration = (ROOT / "DB/migrations/20260710_010_oauth_identity_and_url_guards.sql").read_text(encoding="utf-8")
    assert "email_verified BOOLEAN NOT NULL DEFAULT FALSE" in migration
    assert "chk_course_url_shape" in migration
    assert "chk_branch_website_url_shape" in migration
    assert "image_url" in migration
    assert "https?://" in migration
    for column in ("raw_url", "application_url", "image_url", "website_url"):
        assert migration.count(f"length({column}) > 4096") == 1

    user_url_migration = (ROOT / "DB/migrations/20260710_011_user_course_url_guards.sql").read_text(
        encoding="utf-8"
    )
    assert "chk_user_favorite_course_url_shape" in user_url_migration
    assert "chk_course_alert_url_shape" in user_url_migration
    assert user_url_migration.count("length(invalid.course_url) > 4096") == 1
    assert user_url_migration.count("length(course_url) > 4096") == 4

    auxiliary_migration = (ROOT / "DB/migrations/20260710_012_auxiliary_url_guards.sql").read_text(
        encoding="utf-8"
    )
    assert "chk_course_update_source_url_shape" in auxiliary_migration
    assert "chk_course_quality_url_shape" in auxiliary_migration
    assert auxiliary_migration.count("length(source_url) > 4096") == 1
    assert auxiliary_migration.count("length(url) > 4096") == 1

    oauth_identity_migration = (ROOT / "DB/migrations/20260710_013_oauth_identity_immutable.sql").read_text(
        encoding="utf-8"
    )
    roles = (ROOT / "DB/roles.sql").read_text(encoding="utf-8")
    assert "trg_protect_oauth_identity" in oauth_identity_migration
    assert "OAuth identity fields are immutable" in oauth_identity_migration
    assert "GRANT INSERT, DELETE ON oauth_accounts TO mooncen_api" in roles


def test_user_course_url_migration_deduplicates_before_canonicalization():
    migration = (
        ROOT / "DB/migrations/20260710_011_user_course_url_guards.sql"
    ).read_text(encoding="utf-8")

    canonical_cleanup = "canonical.course_url = 'course:' || invalid.course_id::text"
    duplicate_ranking = "PARTITION BY user_id, course_id"
    deterministic_order = "ORDER BY created_at ASC NULLS LAST, id ASC"
    canonical_update = "SET course_url = 'course:' || course_id::text"

    assert canonical_cleanup in migration
    assert duplicate_ranking in migration
    assert deterministic_order in migration
    assert (
        migration.index(canonical_cleanup)
        < migration.index(duplicate_ranking)
        < migration.index(canonical_update)
    )


def test_versioned_migration_does_not_commit_runner_owned_transaction():
    migration = (
        ROOT / "DB/migrations/20260712_001_disable_false_positive_ansan_target.sql"
    ).read_text(encoding="utf-8")
    transaction_statements = {
        line.strip().upper()
        for line in migration.splitlines()
        if line.strip() and not line.lstrip().startswith("--")
    }

    assert "BEGIN;" not in transaction_statements
    assert "COMMIT;" not in transaction_statements


def test_location_api_does_not_return_internal_database_errors():
    source = (ROOT / "backend" / "routers" / "locations.py").read_text(encoding="utf-8")

    assert "detail=str(e)" not in source
    assert "traceback.print_exc()" not in source
    assert 'detail="Branch metadata is temporarily unavailable"' in source
    assert 'detail="Nearby branch search is temporarily unavailable"' in source


def test_db_status_quotes_discovered_table_identifiers():
    source = (ROOT / "DB" / "db_status.py").read_text(encoding="utf-8")

    assert 'sql.Identifier(table["table_name"])' in source
    assert 'execute(f"SELECT COUNT(*)' not in source


def test_api_enforces_trusted_hosts_and_safe_direct_run_defaults():
    source = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")

    assert "TrustedHostMiddleware" in source
    assert 'os.getenv("MOONCEN_TRUSTED_HOSTS", "")' in source
    assert 'host=os.getenv("API_HOST", "127.0.0.1")' in source
    assert "reload=True" not in source


def test_favicon_proxy_encodes_the_entire_source_url():
    from backend.provider_metadata import favicon_url_for

    favicon = favicon_url_for("https://example.test/course?key=value&size=large")
    assert "domain_url=https%3A%2F%2Fexample.test%2Fcourse%3Fkey%3Dvalue%26size%3Dlarge" in favicon
    assert favicon.count("&") == 1


def test_production_worker_db_config_never_falls_back_to_the_owner(monkeypatch):
    from DB.db_utils import get_db_config

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("DB_USE_MIGRATOR", raising=False)
    monkeypatch.delenv("DB_RUNTIME_USER", raising=False)
    monkeypatch.delenv("DB_RUNTIME_PASSWORD", raising=False)
    monkeypatch.delenv("DB_CRAWLER_USER", raising=False)
    monkeypatch.delenv("DB_CRAWLER_PASSWORD", raising=False)
    monkeypatch.setenv("DB_USER", "mooncen_owner")
    monkeypatch.setenv("DB_PASSWORD", "owner-password-must-not-be-used")

    with pytest.raises(RuntimeError, match="explicit DB_RUNTIME_USER"):
        get_db_config()
