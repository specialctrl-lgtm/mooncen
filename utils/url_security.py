from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, unquote_plus, urlsplit, urlunsplit
from uuid import UUID


MAX_EXTERNAL_URL_LENGTH = 4096
COURSE_TEXT_LIMITS = {
    "title": 255,
    "title_raw": 255,
    "title_prefix_removed": 2_000,
    "instructor": 100,
    "target": 100,
    "category_raw": 100,
    "collection_category": 50,
    "domain_category": 50,
    "source_group": 50,
    "operator_type": 50,
    "service_group": 50,
    "collection_type": 50,
    "schedule_raw": 4_000,
    "apply_period_raw": 2_000,
    "venue_name": 150,
    "venue_address": 2_000,
    "application_type": 30,
    "application_method_raw": 2_000,
    "discovery_status": 50,
    "program_type": 50,
    "eligibility_raw": 4_000,
    "status": 50,
    "description": 20_000,
    "ai_category": 100,
    "ai_summary": 4_000,
}


def _sensitive_url_parameter(name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", unquote_plus(name).lower())
    return normalized in {"auth", "sig", "session", "token"} or any(
        marker in normalized
        for marker in (
            "authorization",
            "credential",
            "csrf",
            "password",
            "secret",
            "session",
            "signature",
            "token",
        )
    )


def safe_external_http_url(value: Any, *, max_length: int = MAX_EXTERNAL_URL_LENGTH) -> str:
    """Return a normalized HTTP(S) URL, or an empty string for unsafe input."""
    candidate = str(value or "").strip()
    if not candidate or len(candidate) > max_length:
        return ""
    if any(ord(character) <= 32 or ord(character) == 127 for character in candidate):
        return ""
    try:
        parsed = urlsplit(candidate)
        _ = parsed.port
    except (TypeError, ValueError):
        return ""
    if parsed.scheme.lower() not in {"http", "https"}:
        return ""
    if not parsed.hostname or parsed.username or parsed.password:
        return ""
    if re.search(r";j?sessionid=", parsed.path, re.IGNORECASE):
        return ""
    if any(
        _sensitive_url_parameter(key)
        for key, _value in [
            *parse_qsl(parsed.query, keep_blank_values=True),
            *parse_qsl(parsed.fragment, keep_blank_values=True),
        ]
    ):
        return ""
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path, parsed.query, parsed.fragment))


def sanitize_course_external_urls(course: dict[str, Any]) -> None:
    """Apply a final URL guard immediately before a crawler database write."""
    raw_value = course.get("raw_url")
    if raw_value:
        safe_raw_url = safe_external_http_url(raw_value)
        if not safe_raw_url:
            raise ValueError("course raw_url must be a valid HTTP(S) URL")
        course["raw_url"] = safe_raw_url

    for field in ("application_url", "image_url"):
        value = course.get(field)
        if value:
            course[field] = safe_external_http_url(value) or None


def sanitize_course_payload(course: dict[str, Any]) -> None:
    """Bound crawler text and URLs before a course reaches PostgreSQL."""
    sanitize_course_external_urls(course)
    for field, maximum in COURSE_TEXT_LIMITS.items():
        value = course.get(field)
        if isinstance(value, str):
            course[field] = value.strip()[:maximum]

    for identity_field, maximum in (("provider", 50), ("provider_course_id", 100)):
        value = str(course.get(identity_field) or "").strip()
        if not value or len(value) > maximum:
            raise ValueError(f"course {identity_field} is missing or too long")
        course[identity_field] = value

    if not str(course.get("title") or "").strip():
        raise ValueError("course title is required")

    for field in ("schedule_days", "target_tags"):
        value = course.get(field)
        if isinstance(value, (list, tuple)):
            course[field] = [str(item).strip()[:100] for item in value[:64] if str(item).strip()]


def safe_course_reference(value: Any) -> str:
    """Allow a public HTTP(S) course URL or the server's UUID-based internal reference."""
    candidate = str(value or "").strip()
    safe_url = safe_external_http_url(candidate)
    if safe_url:
        return safe_url
    if candidate.startswith("course:"):
        try:
            identifier = UUID(candidate.removeprefix("course:"))
            if identifier.version in {1, 2, 3, 4, 5} and str(identifier) == candidate.removeprefix("course:").lower():
                return f"course:{identifier}"
        except ValueError:
            pass
    return ""
