#!/usr/bin/env python3
"""Wait for a loopback MoonCen HTTP health endpoint."""

from __future__ import annotations

import argparse
import time
import urllib.error
import urllib.parse
import urllib.request


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Never turn a reviewed loopback probe into a second HTTP request."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        del req, fp, code, msg, headers, newurl
        return None


def _ready_response(response: object, expected_url: str) -> bool:
    if response.status != 200 or response.geturl() != expected_url:  # type: ignore[attr-defined]
        return False
    body = response.read(1024 * 1024 + 1)  # type: ignore[attr-defined]
    if len(body) > 1024 * 1024:
        return False
    path = urllib.parse.urlsplit(expected_url).path
    content_type = response.headers.get_content_type()  # type: ignore[attr-defined]
    if path.endswith("/health"):
        return content_type == "application/json" and body == b'{"status":"ready"}'
    return content_type == "text/html" and bool(body)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()
    parsed = urllib.parse.urlsplit(args.url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        parser.error("url must be a loopback HTTP endpoint")
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _RejectRedirects(),
    )
    deadline = time.monotonic() + max(1, args.timeout)
    while time.monotonic() < deadline:
        try:
            request = urllib.request.Request(args.url, headers={"User-Agent": "mooncen-an2p-readiness"})
            with opener.open(request, timeout=3) as response:
                if _ready_response(response, args.url):
                    print(f"ready: {args.url}")
                    return 0
        except (OSError, TimeoutError, urllib.error.URLError):
            pass
        time.sleep(1)
    print(f"readiness timed out: {args.url}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
