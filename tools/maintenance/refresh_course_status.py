from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from psycopg2.extras import Json

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from DB.db_utils import get_db_cursor
from utils import clean_text, infer_course_status
from utils.outbound_http import SafeSession, harden_session, validate_outbound_url


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 5


def validate_public_source_url(value: str) -> str:
    try:
        destination = validate_outbound_url(value)
    except requests.RequestException as exc:
        raise ValueError("source URL must resolve to public HTTP(S)") from exc
    parsed = urlsplit(destination.url)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path or "/", parsed.query, ""))


def fetch_public_html(session: requests.Session, source_url: str, timeout: int) -> tuple[int, str, str]:
    current_url = validate_public_source_url(source_url)
    safe_session = harden_session(session)
    safe_session.max_redirects = min(safe_session.max_redirects, MAX_REDIRECTS)
    safe_session.max_response_bytes = min(safe_session.max_response_bytes, MAX_RESPONSE_BYTES)
    response = safe_session.get(current_url, timeout=timeout, allow_redirects=True)
    response.raise_for_status()
    encoding = response.encoding or "utf-8"
    return response.status_code, response.content.decode(encoding, errors="replace"), response.url


def fetch_targets(provider: str | None, only_open: bool, limit: int | None) -> list[dict]:
    provider_filter = "AND provider = %(provider)s" if provider else ""
    open_filter = "AND status IN ('OPEN', 'DEADLINE', 'WAITING')" if only_open else ""
    limit_sql = "LIMIT %(limit)s" if limit else ""
    params = {"provider": provider, "limit": limit}
    with get_db_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT id, provider, title, status, raw_url
            FROM courses
            WHERE is_active IS TRUE
              AND raw_url IS NOT NULL
              {provider_filter}
              {open_filter}
            ORDER BY provider, updated_at DESC NULLS LAST
            {limit_sql}
            """,
            params,
        )
        return [dict(row) for row in cursor.fetchall()]


def expire_old_update_requests() -> int:
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            UPDATE course_update_requests
            SET status = 'expired',
                updated_at = CURRENT_TIMESTAMP
            WHERE status = 'pending'
              AND expires_at <= CURRENT_TIMESTAMP
            """
        )
        return cursor.rowcount


def fetch_update_queue_targets(provider: str | None, limit: int | None) -> list[dict]:
    provider_filter = "AND c.provider = %(provider)s" if provider else ""
    limit_sql = "LIMIT %(limit)s" if limit else ""
    params = {"provider": provider, "limit": limit}
    with get_db_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT
                req.id AS update_request_id,
                c.id,
                c.provider,
                c.title,
                c.status,
                COALESCE(req.source_url, c.raw_url, c.application_url) AS raw_url
            FROM course_update_requests req
            JOIN courses c ON c.id = req.course_id
            WHERE req.status = 'pending'
              AND req.expires_at > CURRENT_TIMESTAMP
              AND COALESCE(req.source_url, c.raw_url, c.application_url) IS NOT NULL
              {provider_filter}
            ORDER BY req.requested_at DESC
            {limit_sql}
            """,
            params,
        )
        return [dict(row) for row in cursor.fetchall()]


def status_from_html(provider: str, title: str, html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    candidates = [title]

    selectors = [".lectStatNm", "p.label.lectStatNm", ".lecture_status", ".status"]
    if provider == "HOMEPLUS":
        selectors.extend([".btn_apply", ".btn_cart", ".btn_area a", ".newCon_btn_wrap a"])

    for selector in selectors:
        for elem in soup.select(selector):
            text = clean_text(elem.get_text(" ", strip=True))
            if text:
                candidates.append(text)

    action_texts = {"수강신청", "장바구니 담기", "접수마감", "신청마감", "수강신청 불가"}
    for elem in soup.select("button, a"):
        text = clean_text(elem.get_text(" ", strip=True))
        if text in action_texts:
            candidates.append(text)

    if provider == "EMART":
        body = clean_text(soup.get_text(" ", strip=True))
        body_markers = [
            marker
            for marker in ("접수마감", "신청마감", "모집마감", "접수종료", "폐강", "마감되었습니다", "대기접수", "접수예정")
            if marker in body
        ]
        candidates.extend(body_markers)

    return infer_course_status(*candidates)


def update_status(course_id: str, status: str) -> None:
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            UPDATE courses
            SET status = %(status)s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %(id)s
            """,
            {"id": course_id, "status": status},
        )


def mark_update_request(request_id: str, status: str, result: dict) -> None:
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            UPDATE course_update_requests
            SET status = %(status)s,
                last_checked_at = CURRENT_TIMESTAMP,
                check_result = %(result)s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %(id)s
            """,
            {"id": request_id, "status": status, "result": Json(result)},
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh course status from detail pages")
    parser.add_argument("--provider", default=None)
    parser.add_argument("--limit", type=int, default=100, help="Maximum courses to inspect. Use 0 for no limit.")
    parser.add_argument("--delay", type=float, default=0.15)
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--all-statuses", action="store_true", help="Inspect closed courses too.")
    parser.add_argument("--from-update-queue", action="store_true", help="Inspect active one-hour click update requests only.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    session = SafeSession(max_redirects=MAX_REDIRECTS, max_response_bytes=MAX_RESPONSE_BYTES)
    # Do not inherit proxy or .netrc credentials for user-influenced queue URLs.
    session.trust_env = False
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    if args.from_update_queue:
        expired = 0 if args.dry_run else expire_old_update_requests()
        targets = fetch_update_queue_targets(args.provider, limit=args.limit)
        print(f"expired_queue={expired}")
    else:
        targets = fetch_targets(args.provider, only_open=not args.all_statuses, limit=args.limit)
    print(f"targets={len(targets)} provider={args.provider or 'ALL'} queue={args.from_update_queue} dry_run={args.dry_run}")

    changed = 0
    failed = 0
    for row in targets:
        try:
            http_status, response_text, final_url = fetch_public_html(session, row["raw_url"], args.timeout)
            new_status = status_from_html(row["provider"], row["title"], response_text)
        except Exception as exc:
            print(f"FAILED [{row['provider']}] {row['title']} | {exc}")
            if args.from_update_queue and not args.dry_run:
                mark_update_request(
                    str(row["update_request_id"]),
                    "failed",
                    {"error": str(exc), "old_status": row["status"]},
                )
            failed += 1
            continue

        status_changed = new_status != row["status"]
        if new_status != row["status"]:
            print(f"{row['status']} -> {new_status} | [{row['provider']}] {row['title']}")
            if not args.dry_run:
                update_status(str(row["id"]), new_status)
            changed += 1

        if args.from_update_queue and not args.dry_run:
            mark_update_request(
                str(row["update_request_id"]),
                "checked",
                {
                    "old_status": row["status"],
                    "new_status": new_status,
                    "changed": status_changed,
                    "http_status": http_status,
                    "final_url": final_url,
                },
            )

        time.sleep(args.delay)

    print(f"changed={changed} failed={failed}")


if __name__ == "__main__":
    main()
