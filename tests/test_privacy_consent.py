from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException, Response
from pydantic import ValidationError
import pytest

from backend import models
from backend.privacy_notice import MEMBERSHIP_PRIVACY_NOTICE
from backend.readiness import PUBLIC_API_READINESS_QUERIES
from backend.routers import auth


EXPECTED_NOTICE_HASH = "4c01b656b92713aa35bf24149a12ce45e3e17f856d54beb950da4a69ad6e9000"


def _persisted_notice(**overrides):
    notice = MEMBERSHIP_PRIVACY_NOTICE
    values = {
        "version": notice.version,
        "notice_type": notice.notice_type,
        "legal_basis": notice.legal_basis,
        "notice_hash": notice.notice_hash,
        "effective_date": notice.effective_date,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _FakeQuery:
    def __init__(self, value):
        self.value = value

    def filter(self, *_args):
        return self

    def first(self):
        return self.value


class _FakeDB:
    def __init__(self, *, account=None, user=None, notice=None, fail_acceptance=False):
        self.account = account
        self.user = user
        self.notice = _persisted_notice() if notice is None else notice
        self.fail_acceptance = fail_acceptance
        self.added = []
        self.queries = []
        self.commit_calls = 0
        self.rollback_calls = 0
        self.flush_calls = 0
        self.refresh_calls = 0

    def query(self, model):
        self.queries.append(model)
        if model is models.OAuthAccount:
            return _FakeQuery(self.account)
        if model is models.User:
            return _FakeQuery(self.user)
        if model is models.PrivacyNoticeVersion:
            return _FakeQuery(self.notice)
        raise AssertionError(f"Unexpected query model: {model}")

    def add(self, value):
        if self.fail_acceptance and isinstance(value, models.UserPrivacyAcceptance):
            raise RuntimeError("acceptance-write-failed")
        self.added.append(value)

    def flush(self):
        self.flush_calls += 1
        for value in self.added:
            if isinstance(value, models.User) and value.id is None:
                value.id = uuid4()
                value.auth_token_version = 1

    def commit(self):
        self.commit_calls += 1

    def rollback(self):
        self.rollback_calls += 1

    def refresh(self, _value):
        self.refresh_calls += 1


def _signup_payload(**overrides):
    values = {
        "name": "Privacy User",
        "email": "privacy@example.test",
        "password": "correct-horse-battery-staple",
        "privacy_consent": True,
        "privacy_notice_version": MEMBERSHIP_PRIVACY_NOTICE.version,
    }
    values.update(overrides)
    return values


def test_authoritative_membership_notice_is_validated_and_hashed_canonically():
    notice = MEMBERSHIP_PRIVACY_NOTICE

    assert notice.version == "2026-08-10"
    assert notice.notice_type == "membership"
    assert notice.legal_basis == "consent"
    assert notice.notice_hash == EXPECTED_NOTICE_HASH
    assert notice.effective_date == date(2026, 8, 10)
    assert notice.notice_json["items"]


def test_email_signup_schema_requires_true_current_privacy_consent():
    assert auth.SignupRequest(**_signup_payload()).privacy_consent is True

    missing = _signup_payload()
    missing.pop("privacy_consent")
    with pytest.raises(ValidationError):
        auth.SignupRequest(**missing)
    with pytest.raises(ValidationError):
        auth.SignupRequest(**_signup_payload(privacy_consent=False))
    with pytest.raises(ValidationError):
        auth.SignupRequest(**_signup_payload(privacy_consent=1))
    with pytest.raises(ValidationError):
        auth.SignupRequest(**_signup_payload(privacy_notice_version="2026-08-09"))


def test_oauth_state_binds_and_returns_current_notice_version(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("AUTH_SECRET", "privacy-test-secret-that-is-long-enough")
    redirect_uri = "http://127.0.0.1:5173/"

    state = auth._make_oauth_state(
        "google",
        redirect_uri,
        MEMBERSHIP_PRIVACY_NOTICE.version,
    )

    assert (
        auth._verify_oauth_state(state, "google", redirect_uri, state)
        == MEMBERSHIP_PRIVACY_NOTICE.version
    )
    legacy_state = auth._make_oauth_state("google", redirect_uri)
    assert auth._verify_oauth_state(legacy_state, "google", redirect_uri, legacy_state) is None


def test_oauth_state_endpoint_rejects_missing_or_stale_consent(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("AUTH_SECRET", "privacy-test-secret-that-is-long-enough")
    redirect_uri = "http://127.0.0.1:5173/"

    with pytest.raises(HTTPException) as false_consent:
        auth.oauth_state(
            Response(),
            "google",
            redirect_uri,
            False,
            MEMBERSHIP_PRIVACY_NOTICE.version,
        )
    assert false_consent.value.status_code == 422

    with pytest.raises(HTTPException) as stale_notice:
        auth.oauth_state(Response(), "naver", redirect_uri, True, "2026-08-09")
    assert stale_notice.value.status_code == 422


def test_existing_oauth_account_accepts_legacy_state_without_duplicate_acceptance():
    user = SimpleNamespace(id=uuid4(), name="Existing User")
    db = _FakeDB(account=SimpleNamespace(user=user), notice=None)

    result = auth._oauth_user(
        db,
        "google",
        "existing-provider-id",
        "existing@example.test",
        "Existing User",
        email_verified=True,
    )

    assert result is user
    assert db.added == []
    assert models.PrivacyNoticeVersion not in db.queries
    assert db.commit_calls == 0
    assert db.rollback_calls == 0


def test_legacy_oauth_state_cannot_create_a_new_account_and_rolls_back():
    db = _FakeDB(account=None)

    with pytest.raises(HTTPException) as exc_info:
        auth._oauth_user(
            db,
            "google",
            "new-provider-id",
            "new@example.test",
            "New User",
            email_verified=True,
        )

    assert exc_info.value.status_code == 422
    assert db.added == []
    assert db.commit_calls == 0
    assert db.rollback_calls == 1


@pytest.mark.parametrize("provider", ["google", "naver"])
def test_new_oauth_account_and_acceptance_are_committed_once(provider):
    db = _FakeDB(account=None, user=None)

    user = auth._oauth_user(
        db,
        provider,
        f"{provider}-provider-id",
        f"{provider}@example.test",
        "New User",
        email_verified=True,
        privacy_notice_version=MEMBERSHIP_PRIVACY_NOTICE.version,
    )

    oauth_account = next(value for value in db.added if isinstance(value, models.OAuthAccount))
    acceptance = next(value for value in db.added if isinstance(value, models.UserPrivacyAcceptance))
    assert oauth_account.user_id == user.id
    assert acceptance.user_id == user.id
    assert acceptance.notice_version == MEMBERSHIP_PRIVACY_NOTICE.version
    assert acceptance.acceptance_type == "consent_granted"
    assert acceptance.acquisition_method == f"{provider}_signup"
    assert db.flush_calls == 1
    assert db.commit_calls == 1
    assert db.rollback_calls == 0


def test_new_oauth_account_fails_closed_when_persisted_notice_differs():
    db = _FakeDB(account=None, notice=_persisted_notice(notice_hash="0" * 64))

    with pytest.raises(HTTPException) as exc_info:
        auth._oauth_user(
            db,
            "google",
            "new-provider-id",
            "new@example.test",
            "New User",
            email_verified=True,
            privacy_notice_version=MEMBERSHIP_PRIVACY_NOTICE.version,
        )

    assert exc_info.value.status_code == 503
    assert db.added == []
    assert db.rollback_calls == 1


def test_email_signup_commits_user_and_acceptance_atomically(monkeypatch):
    db = _FakeDB(user=None)
    monkeypatch.setattr(auth, "_hash_password", lambda _value: "hashed-password")
    monkeypatch.setattr(auth, "_set_auth_cookies", lambda _response, user: user)
    payload = auth.SignupRequest(**_signup_payload())

    user = auth.signup(payload, Response(), db)

    acceptance = next(value for value in db.added if isinstance(value, models.UserPrivacyAcceptance))
    assert acceptance.user_id == user.id
    assert acceptance.notice_version == MEMBERSHIP_PRIVACY_NOTICE.version
    assert acceptance.acquisition_method == "email_signup"
    assert db.flush_calls == 1
    assert db.commit_calls == 1
    assert db.rollback_calls == 0


def test_email_signup_rolls_back_if_acceptance_cannot_be_added(monkeypatch):
    db = _FakeDB(user=None, fail_acceptance=True)
    monkeypatch.setattr(auth, "_hash_password", lambda _value: "hashed-password")
    payload = auth.SignupRequest(**_signup_payload())

    with pytest.raises(RuntimeError, match="acceptance-write-failed"):
        auth.signup(payload, Response(), db)

    assert db.commit_calls == 0
    assert db.rollback_calls == 1


def test_privacy_tables_and_columns_are_part_of_readiness_contract():
    readiness = "\n".join(PUBLIC_API_READINESS_QUERIES)

    assert "FROM privacy_notice_versions LIMIT 0" in readiness
    assert "notice_hash" in readiness
    assert "notice_json" in readiness
    assert "FROM user_privacy_acceptances LIMIT 0" in readiness
    assert "acquisition_method" in readiness


def test_recent_course_and_branch_columns_are_part_of_readiness_contract():
    readiness = "\n".join(PUBLIC_API_READINESS_QUERIES)

    assert "source_endpoint" in readiness
    assert "geocode_status" in readiness
    assert "geocode_reason_code" in readiness
    assert "geocode_attempt_count" in readiness
    assert "geocode_candidates" in readiness
    assert "geocode_next_retry_at" in readiness
    assert "geocode_last_error" in readiness
    assert "geocode_last_attempt_at" in readiness
