from __future__ import annotations

import re
from urllib.parse import parse_qsl, unquote_plus, urlencode, urlparse, urlunparse

from utils.url_security import safe_external_http_url


_PAGINATION_QUERY_KEYS = {
    "cpage",
    "currentpage",
    "currentpageno",
    "page",
    "pageindex",
    "pageno",
    "pagenum",
}


def canonical_source_endpoint(value: object) -> str:
    """Return a stable, safe collection entry point for lifecycle scoping.

    Pagination values are deliberately removed.  A configured catalogue's
    page 1 and page 2 are one collection endpoint, while category/menu query
    values remain part of the identity.
    """

    candidate = str(value or "").strip()
    if not candidate or len(candidate) > 4096 or any(
        ord(character) <= 32 or ord(character) == 127 for character in candidate
    ):
        return ""
    # Public course URL validation intentionally rejects secret-bearing query
    # parameters. Entry-point identity instead drops those values completely,
    # so token rotation neither leaks a credential nor creates a new scope.
    safe_url = safe_external_http_url(candidate)
    parsed = urlparse(safe_url or candidate)
    if parsed.scheme.lower() not in {"http", "https"} or parsed.username or parsed.password:
        return ""
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return ""
    try:
        port = parsed.port
    except ValueError:
        return ""
    default_port = (parsed.scheme == "http" and port == 80) or (
        parsed.scheme == "https" and port == 443
    )
    netloc = hostname if port is None or default_port else f"{hostname}:{port}"
    query: list[tuple[str, str]] = []
    for key, query_value in parse_qsl(parsed.query, keep_blank_values=True):
        normalized_key = re.sub(r"[^a-z0-9]", "", unquote_plus(key).lower())
        sensitive = normalized_key in {"auth", "sig", "session", "token"} or any(
            marker in normalized_key
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
        if key.casefold() in _PAGINATION_QUERY_KEYS or sensitive:
            continue
        query.append((key, query_value))
    query.sort(key=lambda item: (item[0].casefold(), item[0], item[1]))
    return urlunparse(
        (
            parsed.scheme.lower(),
            netloc,
            parsed.path or "/",
            "",
            urlencode(query, doseq=True),
            "",
        )
    )
