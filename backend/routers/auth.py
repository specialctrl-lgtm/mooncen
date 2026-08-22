import base64
from collections import defaultdict, deque
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import threading
import time
from typing import Literal, Optional
from urllib.parse import urlsplit
from uuid import UUID

import requests
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from requests import HTTPError
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..privacy_notice import (
    MEMBERSHIP_ACCEPTANCE_TYPE,
    require_current_membership_consent,
    validate_acquisition_method,
)


router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)

TOKEN_TTL_SECONDS = 60 * 60
OAUTH_STATE_TTL_SECONDS = 10 * 60
OAUTH_PKCE_COOKIE_NAME = "mooncen_oauth_pkce"
_DEFAULT_AUTH_SECRET = "mooncen-dev-secret-change-me"
_WEAK_AUTH_SECRETS = {
    "",
    _DEFAULT_AUTH_SECRET,
    "change-me",
    "changeme",
    "secret",
    "your-secret-here",
}
_rate_limit_lock = threading.Lock()
_rate_limit_buckets: dict[tuple[str, str], deque[float]] = defaultdict(deque)
_password_hasher = PasswordHasher(time_cost=3, memory_cost=65_536, parallelism=4, hash_len=32, salt_len=16)
TOKEN_ISSUER = "mooncen"
TOKEN_AUDIENCE = "mooncen-web"
OPS_PASSWORD_VERSION_CLAIM = "ops_pwd_ver"


def _cookie_prefix() -> str:
    prefix = os.getenv("MOONCEN_AUTH_COOKIE_PREFIX", "mooncen").strip().lower()
    if (
        not 3 <= len(prefix) <= 32
        or not prefix[0].isalpha()
        or not prefix.replace("_", "").isalnum()
        or not prefix.isascii()
    ):
        raise RuntimeError("MOONCEN_AUTH_COOKIE_PREFIX is invalid")
    return prefix


_AUTH_COOKIE_PREFIX = _cookie_prefix()
ACCESS_COOKIE_NAME = f"{_AUTH_COOKIE_PREFIX}_access"
CSRF_COOKIE_NAME = f"{_AUTH_COOKIE_PREFIX}_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"
OPS_ACCOUNT_EMAIL = "opsadmin@ops.internal"


def _is_production() -> bool:
    return os.getenv("ENVIRONMENT", "dev").strip().lower() in {"prod", "production"}


def _auth_cookie_secure() -> bool:
    configured = os.getenv("MOONCEN_AUTH_COOKIE_SECURE", "").strip().lower()
    if not configured:
        return _is_production()
    if configured in {"1", "true", "yes", "on"}:
        return True
    if configured not in {"0", "false", "no", "off"}:
        raise RuntimeError("MOONCEN_AUTH_COOKIE_SECURE is invalid")
    local_ops_http = os.getenv("MOONCEN_LOCAL_LOOPBACK_OPS_HTTP", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    profile = os.getenv("MOONCEN_API_PROFILE", "combined").strip().lower()
    if _is_production() and not (profile == "ops" and local_ops_http):
        raise RuntimeError("production auth cookies must be Secure")
    return False


def validate_auth_configuration() -> None:
    """Fail closed when production would use a missing or guessable signing key."""
    secret = os.getenv("AUTH_SECRET", "").strip()
    if _is_production() and (secret.lower() in _WEAK_AUTH_SECRETS or len(secret) < 32):
        raise RuntimeError("AUTH_SECRET must be a unique secret of at least 32 characters in production")
    _auth_cookie_secure()


def _request_identity(request: Request) -> str:
    # Never let a caller create fresh buckets by rotating an arbitrary bearer
    # value. Nginx supplies the authenticated Cloudflare client address only
    # from its loopback tunnel, so the source address is the stable edge key.
    return request.client.host if request.client else "unknown"


def rate_limit(name: str, limit: int, window_seconds: int):
    """Small per-process guard; the edge should enforce a second distributed limit."""
    if limit < 1 or window_seconds < 1:
        raise ValueError("Invalid rate limit")

    def dependency(request: Request) -> None:
        if os.getenv("MOONCEN_DISABLE_RATE_LIMITS", "").strip().lower() in {"1", "true", "yes"}:
            if _is_production():
                raise RuntimeError("Rate limits cannot be disabled in production")
            return

        now = time.monotonic()
        cutoff = now - window_seconds
        key = (name, _request_identity(request))
        with _rate_limit_lock:
            bucket = _rate_limit_buckets[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after = max(1, int(bucket[0] + window_seconds - now) + 1)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many requests",
                    headers={"Retry-After": str(retry_after)},
                )
            bucket.append(now)

            # Keep the in-memory fallback bounded during scans with many source IPs.
            if len(_rate_limit_buckets) > 10_000:
                stale = [bucket_key for bucket_key, values in _rate_limit_buckets.items() if not values or values[-1] <= cutoff]
                for bucket_key in stale[:2_000]:
                    _rate_limit_buckets.pop(bucket_key, None)
                overflow = len(_rate_limit_buckets) - 10_000
                if overflow > 0:
                    for bucket_key in list(_rate_limit_buckets):
                        if bucket_key == key:
                            continue
                        _rate_limit_buckets.pop(bucket_key, None)
                        overflow -= 1
                        if overflow <= 0:
                            break

    dependency.__name__ = f"rate_limit_{name.replace('-', '_')}"
    return dependency


def _first_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return ""


def _oauth_error_detail(provider: str, response: requests.Response) -> str:
    try:
        error_data = response.json()
    except ValueError:
        error_data = {}

    error = error_data.get("error") if isinstance(error_data, dict) else None
    logger.warning("%s token exchange failed status=%s error=%s", provider, response.status_code, error or "unknown")
    return f"{provider} token exchange failed"


class SignupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=128)
    privacy_consent: bool = Field(strict=True)
    privacy_notice_version: str = Field(min_length=1, max_length=32)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str):
        normalized = value.strip().lower()
        if "@" not in normalized or "." not in normalized.rsplit("@", 1)[-1]:
            raise ValueError("Invalid email")
        return normalized

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str):
        normalized = value.strip()
        if not normalized:
            raise ValueError("Name is required")
        return normalized

    @field_validator("privacy_consent")
    @classmethod
    def validate_privacy_consent(cls, value: bool):
        if value is not True:
            raise ValueError("Current privacy consent is required for membership signup")
        return value

    @field_validator("privacy_notice_version")
    @classmethod
    def validate_privacy_notice_version(cls, value: str):
        require_current_membership_consent(True, value)
        return value


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str):
        normalized = value.strip().lower()
        if "@" not in normalized or "." not in normalized.rsplit("@", 1)[-1]:
            raise ValueError("Invalid email")
        return normalized


class OpsLoginRequest(BaseModel):
    login_id: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=256)

    @field_validator("login_id")
    @classmethod
    def validate_login_id(cls, value: str):
        normalized = value.strip()
        if not normalized or not normalized.isascii() or not normalized.replace("-", "").replace("_", "").isalnum():
            raise ValueError("Invalid Ops login id")
        return normalized


class AuthUser(BaseModel):
    id: str
    email: str
    name: str
    provider: str


class AuthResponse(BaseModel):
    user: AuthUser


class NaverOAuthRequest(BaseModel):
    code: str = Field(min_length=1, max_length=2048)
    state: str = Field(min_length=16, max_length=2048)
    redirect_uri: str = Field(min_length=8, max_length=2048)


class GoogleOAuthRequest(BaseModel):
    code: str = Field(min_length=1, max_length=2048)
    state: str = Field(min_length=16, max_length=2048)
    redirect_uri: str = Field(min_length=8, max_length=2048)


class OAuthStateResponse(BaseModel):
    state: str
    expires_in: int = OAUTH_STATE_TTL_SECONDS
    code_challenge: Optional[str] = None
    code_challenge_method: Optional[Literal["S256"]] = None


def _secret() -> bytes:
    validate_auth_configuration()
    return os.getenv("AUTH_SECRET", _DEFAULT_AUTH_SECRET).encode("utf-8")


def _hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def _verify_password(password: str, stored: Optional[str]) -> bool:
    if not stored:
        return False
    if stored.startswith("$argon2"):
        try:
            return _password_hasher.verify(stored, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
    try:
        algorithm, rounds, salt, expected = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), int(rounds))
        return hmac.compare_digest(digest.hex(), expected)
    except Exception:
        return False


def _password_needs_rehash(stored: Optional[str]) -> bool:
    if not stored or not stored.startswith("$argon2"):
        return True
    try:
        return _password_hasher.check_needs_rehash(stored)
    except (VerificationError, InvalidHashError):
        return True


def _dedicated_ops_user(user: models.User) -> bool:
    return (
        str(getattr(user, "provider", "") or "").strip().lower() == "ops"
        and hmac.compare_digest(
            str(getattr(user, "email", "") or "").strip().lower(),
            OPS_ACCOUNT_EMAIL,
        )
    )


def _ops_login_configuration() -> tuple[str, str]:
    login_id = os.getenv("MOONCEN_OPS_LOGIN_ID", "").strip()
    password_hash = os.getenv("MOONCEN_OPS_PASSWORD_HASH", "").strip()
    valid_hash = False
    try:
        algorithm, rounds_text, salt, digest = password_hash.split("$", 3)
        rounds = int(rounds_text)
        valid_hash = (
            algorithm == "pbkdf2_sha256"
            and 310_000 <= rounds <= 2_000_000
            and 16 <= len(salt) <= 128
            and len(digest) == 64
            and all(character in "0123456789abcdef" for character in digest)
        )
    except (TypeError, ValueError):
        valid_hash = False
    if not hmac.compare_digest(login_id, "opsadmin") or not valid_hash:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ops password login is not configured",
        )
    return login_id, password_hash


def _ops_password_version() -> str:
    """Bind dedicated Ops sessions to the currently configured verifier."""

    _login_id, password_hash = _ops_login_configuration()
    return hashlib.sha256(
        f"mooncen-ops-password-version:{password_hash}".encode("utf-8")
    ).hexdigest()


def _get_or_create_dedicated_ops_user(db: Session, login_id: str) -> models.User:
    user = db.query(models.User).filter(models.User.email == OPS_ACCOUNT_EMAIL).first()
    if user:
        if not _dedicated_ops_user(user):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Reserved Ops account identity is already in use",
            )
        return user

    user = models.User(
        email=OPS_ACCOUNT_EMAIL,
        name=login_id,
        password_hash=None,
        provider="ops",
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        user = db.query(models.User).filter(models.User.email == OPS_ACCOUNT_EMAIL).first()
        if not user or not _dedicated_ops_user(user):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Reserved Ops account identity is already in use",
            ) from None
        return user
    db.refresh(user)
    return user


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64url(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _allowed_redirect_uris() -> set[str]:
    configured = {
        item.strip()
        for item in os.getenv("OAUTH_REDIRECT_URIS", "").split(",")
        if item.strip()
    }
    single = _first_env("OAUTH_REDIRECT_URI", "VITE_OAUTH_REDIRECT_URI")
    if single:
        configured.add(single.strip())
    site_url = _first_env("VITE_SITE_URL", "SITE_URL").rstrip("/")
    if site_url:
        configured.add(f"{site_url}/")
    return configured


def _validate_redirect_uri(value: str) -> str:
    redirect_uri = (value or "").strip()
    try:
        parsed = urlsplit(redirect_uri)
        _ = parsed.port
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid OAuth redirect_uri") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="Invalid OAuth redirect_uri")
    if parsed.fragment:
        raise HTTPException(status_code=400, detail="Invalid OAuth redirect_uri")

    hostname = parsed.hostname.rstrip(".").lower()
    try:
        is_loopback = ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        is_loopback = hostname == "localhost"
    if parsed.scheme == "http" and (_is_production() or not is_loopback):
        raise HTTPException(status_code=400, detail="OAuth redirect_uri must use HTTPS")

    allowed = _allowed_redirect_uris()
    if redirect_uri in allowed:
        return redirect_uri

    is_local_dev = is_loopback and parsed.scheme == "http"
    if not _is_production() and is_local_dev:
        return redirect_uri
    raise HTTPException(status_code=400, detail="OAuth redirect_uri is not allowed")


def _make_oauth_state(
    provider: str,
    redirect_uri: str,
    privacy_notice_version: Optional[str] = None,
) -> str:
    payload = {
        "provider": provider,
        "redirect": hashlib.sha256(redirect_uri.encode("utf-8")).hexdigest(),
        "nonce": secrets.token_urlsafe(24),
        "exp": int(time.time()) + OAUTH_STATE_TTL_SECONDS,
    }
    if privacy_notice_version is not None:
        notice = require_current_membership_consent(True, privacy_notice_version)
        payload["privacy_consent"] = True
        payload["privacy_notice_version"] = notice.version
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(_secret(), f"oauth-state:{body}".encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64url(signature)}"


def _verify_oauth_state(
    state_value: Optional[str],
    provider: str,
    redirect_uri: str,
    expected_state: Optional[str] = None,
) -> Optional[str]:
    value = (state_value or "").strip()
    try:
        body, signature = value.split(".", 1)
        expected = hmac.new(_secret(), f"oauth-state:{body}".encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64url(expected), signature):
            raise ValueError("bad signature")
        payload = json.loads(_unb64url(body))
        if payload.get("provider") != provider:
            raise ValueError("wrong provider")
        if not hmac.compare_digest(
            str(payload.get("redirect", "")),
            hashlib.sha256(redirect_uri.encode("utf-8")).hexdigest(),
        ):
            raise ValueError("wrong redirect")
        if int(payload.get("exp", 0)) < int(time.time()):
            raise ValueError("expired")
        if not payload.get("nonce"):
            raise ValueError("missing nonce")
        if _is_production() and (
            not expected_state or not hmac.compare_digest(value, expected_state)
        ):
            raise ValueError("state was not bound to this browser")
        privacy_notice_version = payload.get("privacy_notice_version")
        if privacy_notice_version is None:
            return None
        if payload.get("privacy_consent") is not True or not isinstance(privacy_notice_version, str):
            raise ValueError("invalid privacy consent")
        if not privacy_notice_version or len(privacy_notice_version) > 32:
            raise ValueError("invalid privacy notice version")
        return privacy_notice_version
    except Exception:
        legacy_default = "0" if _is_production() else "1"
        allow_legacy = os.getenv("OAUTH_ALLOW_LEGACY_STATE", legacy_default).strip().lower() in {"1", "true", "yes"}
        if not _is_production() and allow_legacy and value.startswith(f"{provider}:") and len(value) >= 16:
            return None
        raise HTTPException(status_code=400, detail="Invalid OAuth state") from None


def _make_token(user: models.User) -> str:
    now = int(time.time())
    payload = {
        "sub": str(user.id),
        "iat": now,
        "nbf": now,
        "exp": now + TOKEN_TTL_SECONDS,
        "iss": TOKEN_ISSUER,
        "aud": TOKEN_AUDIENCE,
        "jti": secrets.token_urlsafe(24),
        "ver": int(user.auth_token_version or 1),
    }
    if _dedicated_ops_user(user):
        payload[OPS_PASSWORD_VERSION_CLAIM] = _ops_password_version()
    return jwt.encode(
        payload,
        _secret(),
        algorithm="HS256",
        headers={"kid": os.getenv("AUTH_KEY_ID", "v1")},
    )


def _read_token(token: str) -> dict:
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "HS256" or header.get("kid") != os.getenv("AUTH_KEY_ID", "v1"):
            raise ValueError("unexpected token header")
        payload = jwt.decode(
            token,
            _secret(),
            algorithms=["HS256"],
            audience=TOKEN_AUDIENCE,
            issuer=TOKEN_ISSUER,
            leeway=5,
            options={"require": ["sub", "iat", "nbf", "exp", "iss", "aud", "jti", "ver"]},
        )
        subject = str(payload.get("sub") or "")
        if not subject or len(subject) > 64:
            raise ValueError("missing subject")
        UUID(subject)
        if int(payload.get("ver", 0)) < 1:
            raise ValueError("invalid token version")
        return payload
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


def _set_auth_cookies(response: Response, user: models.User) -> AuthResponse:
    ops_profile = os.getenv("MOONCEN_API_PROFILE", "combined").strip().lower() == "ops"
    response.set_cookie(
        ACCESS_COOKIE_NAME,
        _make_token(user),
        max_age=TOKEN_TTL_SECONDS,
        httponly=True,
        secure=_auth_cookie_secure(),
        samesite="strict" if ops_profile else "lax",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE_NAME,
        secrets.token_urlsafe(32),
        max_age=TOKEN_TTL_SECONDS,
        httponly=False,
        secure=_auth_cookie_secure(),
        samesite="strict" if ops_profile else "lax",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return AuthResponse(user=_serialize_user(user))


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")
    response.headers["Cache-Control"] = "no-store"


def _serialize_user(user: models.User) -> AuthUser:
    return AuthUser(id=str(user.id), email=user.email, name=user.name, provider=user.provider)


def _oauth_provider_label(provider: str) -> str:
    provider_labels = {
        "naver": "\ub124\uc774\ubc84 \uc0ac\uc6a9\uc790",
        "google": "Google \uc0ac\uc6a9\uc790",
    }
    return provider_labels.get(provider, "\uc0ac\uc6a9\uc790")


def _is_placeholder_oauth_name(provider: str, value: Optional[str]) -> bool:
    text = (value or "").strip().lower()
    if not text:
        return True
    placeholders = {
        provider.lower(),
        f"{provider.lower()} user",
        f"{provider.lower()} \uc0ac\uc6a9\uc790",
        "user",
        "\uc0ac\uc6a9\uc790",
        "\ub124\uc774\ubc84",
        "\ub124\uc774\ubc84 \uc0ac\uc6a9\uc790",
        "naver",
        "naver user",
        "google",
        "google user",
        "google \uc0ac\uc6a9\uc790",
    }
    return text in {placeholder.lower() for placeholder in placeholders}


def _clean_oauth_name(provider: str, *values: Optional[str]) -> str:
    for value in values:
        candidate = (value or "").strip()
        if not candidate:
            continue
        if "@" in candidate:
            candidate = candidate.split("@", 1)[0].strip()
        if _is_placeholder_oauth_name(provider, candidate):
            continue
        return candidate[:100]

    return _oauth_provider_label(provider)


def _verified_oauth_email(value: Optional[str], provider: str) -> str:
    email = (value or "").strip().lower()
    if len(email) > 255 or "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        raise HTTPException(status_code=400, detail=f"{provider.title()} account did not provide a valid verified email")
    return email


def _provider_reports_verified_email(value: object) -> bool:
    return value is True or str(value or "").strip().lower() in {
        "1",
        "true",
        "verified",
        "y",
        "yes",
    }


def _membership_privacy_acceptance_values(
    db: Session,
    privacy_notice_version: Optional[str],
    acquisition_method: str,
) -> tuple[str, str]:
    try:
        notice = require_current_membership_consent(
            privacy_notice_version is not None,
            privacy_notice_version,
        )
        method = validate_acquisition_method(acquisition_method)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Current privacy consent is required for membership signup",
        ) from exc

    persisted_notice = (
        db.query(models.PrivacyNoticeVersion)
        .filter(models.PrivacyNoticeVersion.version == notice.version)
        .first()
    )
    if (
        persisted_notice is None
        or persisted_notice.notice_type != notice.notice_type
        or persisted_notice.legal_basis != notice.legal_basis
        or persisted_notice.notice_hash != notice.notice_hash
        or persisted_notice.effective_date != notice.effective_date
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Privacy notice is not configured",
        )

    return notice.version, method


def _add_membership_privacy_acceptance(
    db: Session,
    user_id: UUID,
    notice_version: str,
    acquisition_method: str,
) -> None:
    db.add(
        models.UserPrivacyAcceptance(
            user_id=user_id,
            notice_version=notice_version,
            acceptance_type=MEMBERSHIP_ACCEPTANCE_TYPE,
            acquisition_method=acquisition_method,
        )
    )


def _oauth_user(
    db: Session,
    provider: str,
    provider_user_id: str,
    email: str,
    name: str,
    *,
    email_verified: bool,
    privacy_notice_version: Optional[str] = None,
) -> models.User:
    account = (
        db.query(models.OAuthAccount)
        .filter(
            models.OAuthAccount.provider == provider,
            models.OAuthAccount.provider_user_id == provider_user_id,
        )
        .first()
    )
    if account:
        if name and not _is_placeholder_oauth_name(provider, name) and (
            _is_placeholder_oauth_name(provider, account.user.name) or account.user.name != name
        ):
            account.user.name = name
            db.commit()
            db.refresh(account.user)
        return account.user

    try:
        accepted_notice_version, acquisition_method = _membership_privacy_acceptance_values(
            db,
            privacy_notice_version,
            f"{provider}_signup",
        )

        # Never silently attach OAuth to a password account by email. Without a
        # verified-email enrollment flow this enables account pre-hijacking.
        existing_user = db.query(models.User).filter(models.User.email == email).first()
        if existing_user:
            raise HTTPException(
                status_code=409,
                detail="An account with this email already exists; sign in to that account before linking OAuth",
            )

        user = models.User(
            email=email,
            name=name,
            password_hash=None,
            provider=provider,
        )
        db.add(user)
        db.flush()
        db.add(
            models.OAuthAccount(
                user_id=user.id,
                provider=provider,
                provider_user_id=provider_user_id,
                email=email,
                email_verified=email_verified,
            )
        )
        _add_membership_privacy_acceptance(
            db,
            user.id,
            accepted_notice_version,
            acquisition_method,
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        account = (
            db.query(models.OAuthAccount)
            .filter(
                models.OAuthAccount.provider == provider,
                models.OAuthAccount.provider_user_id == provider_user_id,
            )
            .first()
        )
        if account:
            return account.user
        raise HTTPException(status_code=409, detail="OAuth account already exists") from None
    except Exception:
        db.rollback()
        raise
    db.refresh(user)
    return user


def get_current_user(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> models.User:
    using_cookie = False
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1]
    else:
        token = request.cookies.get(ACCESS_COOKIE_NAME, "")
        using_cookie = bool(token)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    if using_cookie and request.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
        csrf_cookie = request.cookies.get(CSRF_COOKIE_NAME, "")
        csrf_header = request.headers.get(CSRF_HEADER_NAME, "")
        if not csrf_cookie or not csrf_header or not hmac.compare_digest(csrf_cookie, csrf_header):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")
    payload = _read_token(token)
    user = db.query(models.User).filter(models.User.id == payload["sub"]).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if int(user.auth_token_version or 1) != int(payload.get("ver", 0)):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has been revoked")
    if _dedicated_ops_user(user):
        observed_ops_version = payload.get(OPS_PASSWORD_VERSION_CLAIM)
        expected_ops_version = _ops_password_version()
        if not isinstance(observed_ops_version, str) or not hmac.compare_digest(
            observed_ops_version,
            expected_ops_version,
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has been revoked",
            )
    return user


def require_admin_user(user: models.User = Depends(get_current_user)) -> models.User:
    if not _user_matches_ops_identity(
        user,
        email_env_names=("MOONCEN_ADMIN_EMAILS", "MOONCEN_OPS_ADMIN_EMAILS"),
        provider_env_names=("MOONCEN_ADMIN_PROVIDER_IDS", "MOONCEN_OPS_ADMIN_PROVIDER_IDS"),
        user_id_env_names=("MOONCEN_ADMIN_USER_IDS", "MOONCEN_OPS_ADMIN_USER_IDS"),
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator access required")
    return user


def _configured_identity_values(*env_names: str) -> set[str]:
    values: set[str] = set()
    for env_name in env_names:
        values.update(
            value.strip().lower()
            for value in os.getenv(env_name, "").split(",")
            if value.strip()
        )
    return values


def _configured_provider_identities(*env_names: str) -> set[str]:
    identities: set[str] = set()
    for env_name in env_names:
        for raw_value in os.getenv(env_name, "").split(","):
            provider, separator, provider_user_id = raw_value.strip().partition(":")
            if separator and provider.strip() and provider_user_id.strip():
                identities.add(f"{provider.strip().lower()}:{provider_user_id.strip()}")
    return identities


def _configured_user_ids(*env_names: str) -> set[str]:
    user_ids: set[str] = set()
    for env_name in env_names:
        for raw_value in os.getenv(env_name, "").split(","):
            value = raw_value.strip()
            if not value:
                continue
            try:
                user_ids.add(str(UUID(value)))
            except ValueError:
                logger.warning("Ignoring invalid user UUID in %s", env_name)
    return user_ids


def _user_matches_ops_identity(
    user: models.User,
    *,
    email_env_names: tuple[str, ...],
    provider_env_names: tuple[str, ...],
    user_id_env_names: tuple[str, ...] = (),
) -> bool:
    allowed_emails = _configured_identity_values(*email_env_names)
    allowed_provider_ids = _configured_provider_identities(*provider_env_names)
    allowed_user_ids = _configured_user_ids(*user_id_env_names)
    accounts = getattr(user, "oauth_accounts", ()) or ()
    user_email = str(getattr(user, "email", "") or "").strip().lower()
    verified_oauth_email = any(
        bool(getattr(account, "email_verified", False))
        and str(getattr(account, "email", "") or "").strip().lower() == user_email
        and user_email in allowed_emails
        for account in accounts
    )
    provider_id_allowed = any(
        f"{str(getattr(account, 'provider', '') or '').strip().lower()}:{str(getattr(account, 'provider_user_id', '') or '').strip()}"
        in allowed_provider_ids
        for account in accounts
    )
    user_id_allowed = str(getattr(user, "id", "") or "") in allowed_user_ids
    return verified_oauth_email or provider_id_allowed or user_id_allowed


def ops_role_for_user(user: models.User) -> Literal["viewer", "operator", "admin"] | None:
    """Resolve the Ops role without trusting mutable user/profile fields.

    The existing verified OAuth administrator allowlist remains fully
    compatible.  Viewer and operator identities use the same immutable
    provider-ID or verified-email contract through dedicated environment
    variables.
    """

    if _dedicated_ops_user(user):
        return "admin"

    if os.getenv("MOONCEN_OPS_SINGLE_ACCOUNT_ONLY", "true").strip().lower() in {"1", "true", "yes"}:
        return None

    role_sources = (
        (
            "admin",
            ("MOONCEN_ADMIN_EMAILS", "MOONCEN_OPS_ADMIN_EMAILS"),
            ("MOONCEN_ADMIN_PROVIDER_IDS", "MOONCEN_OPS_ADMIN_PROVIDER_IDS"),
            ("MOONCEN_ADMIN_USER_IDS", "MOONCEN_OPS_ADMIN_USER_IDS"),
        ),
        (
            "operator",
            ("MOONCEN_OPS_OPERATOR_EMAILS",),
            ("MOONCEN_OPS_OPERATOR_PROVIDER_IDS",),
            ("MOONCEN_OPS_OPERATOR_USER_IDS",),
        ),
        (
            "viewer",
            ("MOONCEN_OPS_VIEWER_EMAILS",),
            ("MOONCEN_OPS_VIEWER_PROVIDER_IDS",),
            ("MOONCEN_OPS_VIEWER_USER_IDS",),
        ),
    )
    for role, email_names, provider_names, user_id_names in role_sources:
        if _user_matches_ops_identity(
            user,
            email_env_names=email_names,
            provider_env_names=provider_names,
            user_id_env_names=user_id_names,
        ):
            return role
    return None


def _require_ops_role(
    minimum_role: Literal["viewer", "operator", "admin"],
    user: models.User,
) -> models.User:
    role = ops_role_for_user(user)
    ranks = {"viewer": 1, "operator": 2, "admin": 3}
    if role is None or ranks[role] < ranks[minimum_role]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Ops {minimum_role} access required",
        )
    return user


def require_ops_viewer(user: models.User = Depends(get_current_user)) -> models.User:
    return _require_ops_role("viewer", user)


def require_ops_operator(user: models.User = Depends(get_current_user)) -> models.User:
    return _require_ops_role("operator", user)


def require_ops_admin(user: models.User = Depends(get_current_user)) -> models.User:
    return _require_ops_role("admin", user)


@router.post(
    "/signup",
    response_model=AuthResponse,
    dependencies=[Depends(rate_limit("auth-signup", 5, 3600))],
)
def signup(payload: SignupRequest, response: Response, db: Session = Depends(get_db)):
    email = payload.email.lower()
    existing = db.query(models.User).filter(models.User.email == email).first()
    if existing:
        raise HTTPException(status_code=409, detail="Email already exists")

    try:
        accepted_notice_version, acquisition_method = _membership_privacy_acceptance_values(
            db,
            payload.privacy_notice_version,
            "email_signup",
        )
        user = models.User(
            email=email,
            name=payload.name.strip(),
            password_hash=_hash_password(payload.password),
            provider="email",
        )
        db.add(user)
        db.flush()
        _add_membership_privacy_acceptance(
            db,
            user.id,
            accepted_notice_version,
            acquisition_method,
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email already exists") from None
    except Exception:
        db.rollback()
        raise
    db.refresh(user)
    return _set_auth_cookies(response, user)


def ops_login(payload: OpsLoginRequest, response: Response, db: Session = Depends(get_db)):
    configured_id, password_hash = _ops_login_configuration()
    valid_id = hmac.compare_digest(payload.login_id, configured_id)
    valid_password = _verify_password(payload.password, password_hash)
    if not valid_id or not valid_password:
        raise HTTPException(status_code=401, detail="Invalid Ops id or password")

    user = _get_or_create_dedicated_ops_user(db, configured_id)
    return _set_auth_cookies(response, user)


# The public-only an2p development API must not expose the privileged Ops
# credential endpoint.  Combined mode preserves the existing production app
# contract; the standalone Ops profile mounts the narrow ops_auth router.
if os.getenv("MOONCEN_API_PROFILE", "combined").strip().lower() != "public":
    router.add_api_route(
        "/ops/login",
        ops_login,
        methods=["POST"],
        response_model=AuthResponse,
        dependencies=[Depends(rate_limit("ops-auth-login", 5, 60))],
    )


@router.post(
    "/login",
    response_model=AuthResponse,
    dependencies=[Depends(rate_limit("auth-login", 10, 60))],
)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == payload.email.lower()).first()
    if not user or not _verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if _password_needs_rehash(user.password_hash):
        user.password_hash = _hash_password(payload.password)
        db.commit()
        db.refresh(user)
    return _set_auth_cookies(response, user)


@router.post("/logout", dependencies=[Depends(rate_limit("auth-logout", 20, 60))])
def logout(response: Response, _user: models.User = Depends(get_current_user)):
    _clear_auth_cookies(response)
    return {"ok": True}


@router.post("/logout-all", dependencies=[Depends(rate_limit("auth-logout-all", 10, 60))])
def logout_all(response: Response, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    user.auth_token_version = int(user.auth_token_version or 1) + 1
    db.commit()
    _clear_auth_cookies(response)
    return {"ok": True}


@router.get("/oauth/config", dependencies=[Depends(rate_limit("oauth-config", 60, 60))])
def oauth_config():
    google_client_id = _first_env("GOOGLE_OAUTH_CLIENT_ID", "VITE_GOOGLE_OAUTH_CLIENT_ID")
    naver_client_id = _first_env("NAVER_OAUTH_CLIENT_ID", "VITE_NAVER_OAUTH_CLIENT_ID")
    return {
        "google_client_id": google_client_id,
        "google_client_secret_configured": bool(google_client_id and os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")),
        "naver_client_id": naver_client_id,
        "naver_client_secret_configured": bool(naver_client_id and os.getenv("NAVER_OAUTH_CLIENT_SECRET")),
    }


@router.get(
    "/oauth/state",
    response_model=OAuthStateResponse,
    dependencies=[Depends(rate_limit("oauth-state", 20, 60))],
)
def oauth_state(
    response: Response,
    provider: Literal["google", "naver"] = Query(...),
    redirect_uri: str = Query(..., min_length=8, max_length=2048),
    privacy_consent: bool = Query(...),
    privacy_notice_version: str = Query(..., min_length=1, max_length=32),
):
    validated_redirect = _validate_redirect_uri(redirect_uri)
    try:
        notice = require_current_membership_consent(
            privacy_consent,
            privacy_notice_version,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Current privacy consent is required for membership signup",
        ) from exc
    response.headers["Cache-Control"] = "no-store"
    state_value = _make_oauth_state(provider, validated_redirect, notice.version)
    response.set_cookie(
        "mooncen_oauth_state",
        state_value,
        max_age=OAUTH_STATE_TTL_SECONDS,
        httponly=True,
        secure=_is_production(),
        samesite="lax",
        path="/",
    )
    if provider == "google":
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = _b64url(hashlib.sha256(code_verifier.encode("ascii")).digest())
        response.set_cookie(
            OAUTH_PKCE_COOKIE_NAME,
            code_verifier,
            max_age=OAUTH_STATE_TTL_SECONDS,
            httponly=True,
            secure=_is_production(),
            samesite="lax",
            path="/",
        )
        return OAuthStateResponse(
            state=state_value,
            code_challenge=code_challenge,
            code_challenge_method="S256",
        )
    response.delete_cookie(OAUTH_PKCE_COOKIE_NAME, path="/")
    return OAuthStateResponse(state=state_value)


@router.post(
    "/oauth/naver",
    response_model=AuthResponse,
    dependencies=[Depends(rate_limit("oauth-naver", 10, 60))],
)
def naver_oauth(
    payload: NaverOAuthRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    client_id = _first_env("NAVER_OAUTH_CLIENT_ID", "VITE_NAVER_OAUTH_CLIENT_ID")
    client_secret = os.getenv("NAVER_OAUTH_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise HTTPException(status_code=500, detail="Naver OAuth is not configured")

    redirect_uri = _validate_redirect_uri(payload.redirect_uri)
    privacy_notice_version = _verify_oauth_state(
        payload.state,
        "naver",
        redirect_uri,
        request.cookies.get("mooncen_oauth_state"),
    )
    response.delete_cookie("mooncen_oauth_state", path="/")

    try:
        token_response = requests.post(
            "https://nid.naver.com/oauth2.0/token",
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": client_secret,
                "code": payload.code,
                "state": payload.state,
                "redirect_uri": redirect_uri,
            },
            timeout=10,
        )
        token_response.raise_for_status()
        token_data = token_response.json()
    except HTTPError as exc:
        provider_response = exc.response
        detail = _oauth_error_detail("Naver", provider_response) if provider_response is not None else "Naver token exchange failed"
        raise HTTPException(status_code=400, detail=detail) from exc
    except Exception as exc:
        logger.warning("Naver token exchange failed", exc_info=True)
        raise HTTPException(status_code=400, detail="Naver token exchange failed") from exc

    access_token = token_data.get("access_token")
    if not access_token:
        logger.warning("Naver token exchange returned no access token")
        raise HTTPException(status_code=400, detail="Naver token exchange failed")

    try:
        profile_response = requests.get(
            "https://openapi.naver.com/v1/nid/me",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        profile_response.raise_for_status()
        profile_data = profile_response.json()
    except Exception as exc:
        logger.warning("Naver profile request failed", exc_info=True)
        raise HTTPException(status_code=400, detail="Naver profile request failed") from exc

    if str(profile_data.get("resultcode") or "00") != "00":
        raise HTTPException(status_code=400, detail="Naver profile response was not successful")
    profile = profile_data.get("response") or {}
    provider_user_id = str(profile.get("id") or "").strip()
    if not provider_user_id:
        raise HTTPException(status_code=400, detail="Naver profile did not include user id")

    email = _verified_oauth_email(profile.get("email"), "naver")
    name = _clean_oauth_name(
        "naver",
        profile.get("name"),
        profile.get("nickname"),
        profile.get("email"),
    )
    email_verified = _provider_reports_verified_email(profile.get("email_verified"))
    if privacy_notice_version is None:
        user = _oauth_user(db, "naver", provider_user_id, email, name, email_verified=email_verified)
    else:
        user = _oauth_user(
            db,
            "naver",
            provider_user_id,
            email,
            name,
            email_verified=email_verified,
            privacy_notice_version=privacy_notice_version,
        )
    return _set_auth_cookies(response, user)


def _validate_google_access_token(access_token: str, client_id: str) -> dict:
    try:
        response = requests.get(
            "https://oauth2.googleapis.com/tokeninfo",
            params={"access_token": access_token},
            timeout=10,
        )
        response.raise_for_status()
        token_info = response.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Google token validation failed") from exc

    audience = str(token_info.get("aud") or token_info.get("issued_to") or "")
    if not audience or not hmac.compare_digest(audience, client_id):
        raise HTTPException(status_code=400, detail="Google token was not issued for this application")
    return token_info


@router.post(
    "/oauth/google",
    response_model=AuthResponse,
    dependencies=[Depends(rate_limit("oauth-google", 10, 60))],
)
def google_oauth(
    payload: GoogleOAuthRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    client_id = _first_env("GOOGLE_OAUTH_CLIENT_ID", "VITE_GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise HTTPException(status_code=500, detail="Google OAuth is not configured")
    redirect_uri = _validate_redirect_uri(payload.redirect_uri)
    privacy_notice_version = _verify_oauth_state(
        payload.state,
        "google",
        redirect_uri,
        request.cookies.get("mooncen_oauth_state"),
    )
    code_verifier = request.cookies.get(OAUTH_PKCE_COOKIE_NAME, "")
    if not 43 <= len(code_verifier) <= 128:
        raise HTTPException(status_code=400, detail="Google OAuth PKCE verifier is missing")
    response.delete_cookie("mooncen_oauth_state", path="/")
    response.delete_cookie(OAUTH_PKCE_COOKIE_NAME, path="/")

    try:
        token_response = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": client_secret,
                "code": payload.code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            },
            timeout=10,
        )
        token_response.raise_for_status()
        token_data = token_response.json()
    except HTTPError as exc:
        provider_response = exc.response
        detail = _oauth_error_detail("Google", provider_response) if provider_response is not None else "Google token exchange failed"
        raise HTTPException(status_code=400, detail=detail) from exc
    except Exception as exc:
        logger.warning("Google token exchange failed", exc_info=True)
        raise HTTPException(status_code=400, detail="Google token exchange failed") from exc

    access_token = token_data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="Google token exchange failed")

    token_info = _validate_google_access_token(access_token, client_id)

    try:
        profile_response = requests.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        profile_response.raise_for_status()
        profile = profile_response.json()
    except Exception as exc:
        logger.warning("Google profile request failed", exc_info=True)
        raise HTTPException(status_code=400, detail="Google profile request failed") from exc

    provider_user_id = str(profile.get("sub") or "").strip()
    if not provider_user_id:
        raise HTTPException(status_code=400, detail="Google profile did not include user id")
    token_subject = str(token_info.get("sub") or token_info.get("user_id") or "").strip()
    if token_subject and not hmac.compare_digest(token_subject, provider_user_id):
        raise HTTPException(status_code=400, detail="Google token subject did not match profile")

    email = _verified_oauth_email(profile.get("email"), "google")
    email_verified = profile.get("email_verified") is True or str(token_info.get("email_verified", "")).lower() == "true"
    token_email = str(token_info.get("email") or "").strip().lower()
    if token_email and email and not hmac.compare_digest(token_email, email):
        raise HTTPException(status_code=400, detail="Google token email did not match profile")
    if not email_verified:
        raise HTTPException(status_code=400, detail="Google account email is not verified")
    name = _clean_oauth_name(
        "google",
        profile.get("name"),
        profile.get("given_name"),
        profile.get("email"),
    )
    if privacy_notice_version is None:
        user = _oauth_user(
            db,
            "google",
            provider_user_id,
            email,
            name,
            email_verified=email_verified,
        )
    else:
        user = _oauth_user(
            db,
            "google",
            provider_user_id,
            email,
            name,
            email_verified=email_verified,
            privacy_notice_version=privacy_notice_version,
        )
    return _set_auth_cookies(response, user)


@router.get("/me", response_model=AuthUser, dependencies=[Depends(rate_limit("auth-me", 120, 60))])
def me(response: Response, user: models.User = Depends(get_current_user)):
    _set_auth_cookies(response, user)
    return _serialize_user(user)


@router.delete("/me", dependencies=[Depends(rate_limit("auth-delete", 3, 3600))])
def delete_me(
    response: Response,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    db.query(models.CourseAlert).filter(models.CourseAlert.user_id == user.id).delete(synchronize_session=False)
    db.query(models.UserFavoriteCourse).filter(models.UserFavoriteCourse.user_id == user.id).delete(synchronize_session=False)
    db.query(models.UserCourseNotificationSetting).filter(
        models.UserCourseNotificationSetting.user_id == user.id
    ).delete(synchronize_session=False)
    db.query(models.UserCourseMark).filter(models.UserCourseMark.user_id == user.id).delete(synchronize_session=False)
    db.query(models.OAuthAccount).filter(models.OAuthAccount.user_id == user.id).delete(synchronize_session=False)
    db.delete(user)
    db.commit()
    _clear_auth_cookies(response)
    return {"ok": True}
