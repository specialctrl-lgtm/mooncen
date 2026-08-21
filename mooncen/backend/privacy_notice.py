from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any


MEMBERSHIP_NOTICE_PATH = Path(__file__).resolve().parents[1] / "config" / "privacy_membership_notice.json"
MEMBERSHIP_NOTICE_TYPE = "membership"
MEMBERSHIP_NOTICE_LEGAL_BASIS = "consent"
MEMBERSHIP_ACCEPTANCE_TYPE = "consent_granted"
MEMBERSHIP_ACQUISITION_METHODS = frozenset(
    {"email_signup", "google_signup", "naver_signup"}
)

_REQUIRED_TEXT_FIELDS = (
    "version",
    "effective_date",
    "title",
    "purpose",
    "retention",
    "refusal",
    "consent_label",
)


@dataclass(frozen=True)
class MembershipPrivacyNotice:
    version: str
    notice_type: str
    legal_basis: str
    notice_hash: str
    notice_json: dict[str, Any]
    effective_date: date


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _load_membership_privacy_notice(path: Path = MEMBERSHIP_NOTICE_PATH) -> MembershipPrivacyNotice:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Membership privacy notice configuration is invalid") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("Membership privacy notice configuration must be an object")
    for field in _REQUIRED_TEXT_FIELDS:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError(f"Membership privacy notice field is invalid: {field}")
    if len(payload["version"]) > 32:
        raise RuntimeError("Membership privacy notice version is too long")
    items = payload.get("items")
    if (
        not isinstance(items, list)
        or not items
        or any(not isinstance(item, str) or not item.strip() for item in items)
    ):
        raise RuntimeError("Membership privacy notice items are invalid")

    try:
        effective_date = date.fromisoformat(payload["effective_date"])
    except ValueError as exc:
        raise RuntimeError("Membership privacy notice effective_date is invalid") from exc

    canonical = _canonical_json(payload)
    return MembershipPrivacyNotice(
        version=payload["version"],
        notice_type=MEMBERSHIP_NOTICE_TYPE,
        legal_basis=MEMBERSHIP_NOTICE_LEGAL_BASIS,
        notice_hash=hashlib.sha256(canonical).hexdigest(),
        notice_json=payload,
        effective_date=effective_date,
    )


MEMBERSHIP_PRIVACY_NOTICE = _load_membership_privacy_notice()


def require_current_membership_consent(
    privacy_consent: object,
    privacy_notice_version: object,
) -> MembershipPrivacyNotice:
    notice = MEMBERSHIP_PRIVACY_NOTICE
    if privacy_consent is not True or privacy_notice_version != notice.version:
        raise ValueError("Current privacy consent is required for membership signup")
    return notice


def validate_acquisition_method(value: str) -> str:
    if value not in MEMBERSHIP_ACQUISITION_METHODS:
        raise ValueError("Invalid privacy consent acquisition method")
    return value
