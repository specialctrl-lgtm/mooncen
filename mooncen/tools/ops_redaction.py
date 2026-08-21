from __future__ import annotations

import os
import re
from typing import Any


MAX_OUTPUT_CHARS = 240_000
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(?<![A-Za-z0-9_.-])"
    r"([A-Za-z0-9_.-]*(?:password|passwd|secret|token|api[_-]?key|credential)[A-Za-z0-9_.-]*)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_SENSITIVE_QUERY = re.compile(
    r"(?i)([?&](?:token|access_token|api_key|key|secret|password)=)[^&#\s]+"
)
_BEARER_TOKEN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_DATABASE_URL_PASSWORD = re.compile(r"(?i)(postgres(?:ql)?(?:\+[a-z0-9_]+)?://[^\s:/@]+:)[^\s@/]+(@)")
_TELEGRAM_TOKEN_PATH = re.compile(r"(?i)(/bot)[0-9]{6,}:[A-Za-z0-9_-]{20,}(/)")
_AUTHORIZATION_HEADER = re.compile(r"(?im)\b(Authorization|Cookie|Set-Cookie)\s*:\s*[^\r\n]+")


def redact_text(value: Any, *, maximum: int = MAX_OUTPUT_CHARS) -> str:
    """Remove known credentials and credential-shaped values from operator output."""
    text = str(value or "")
    for name in (
        "AUTH_SECRET",
        "DB_PASSWORD",
        "DB_API_PASSWORD",
        "DB_CRAWLER_PASSWORD",
        "DB_AI_PASSWORD",
        "KAKAO_MAPS_REST_API_KEY",
        "GOOGLE_MAPS_API_KEY",
        "VITE_GOOGLE_MAPS_API_KEY",
        "MOONCEN_BOT_TOKEN",
        "GRAFANA_ADMIN_PASSWORD",
        "CLOUDFLARE_TUNNEL_TOKEN",
    ):
        secret = os.getenv(name, "")
        if secret and len(secret) >= 6:
            text = text.replace(secret, "<redacted>")
    text = _AUTHORIZATION_HEADER.sub(lambda match: f"{match.group(1)}: <redacted>", text)
    text = _BEARER_TOKEN.sub("Bearer <redacted>", text)
    text = _DATABASE_URL_PASSWORD.sub(r"\1<redacted>\2", text)
    text = _TELEGRAM_TOKEN_PATH.sub(r"\1<redacted>\2", text)
    text = _SENSITIVE_QUERY.sub(lambda match: f"{match.group(1)}<redacted>", text)

    def redact_assignment(match: re.Match[str]) -> str:
        raw_value = match.group(3)
        if raw_value.lower() in {"0", "false", "no", "none", "disabled", "off"}:
            return match.group(0)
        return f"{match.group(1)}{match.group(2)}<redacted>"

    text = _SENSITIVE_ASSIGNMENT.sub(redact_assignment, text)
    if len(text) > maximum:
        text = text[:maximum] + "\n<output truncated>"
    return text
