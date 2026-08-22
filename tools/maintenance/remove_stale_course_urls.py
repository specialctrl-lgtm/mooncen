from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
from psycopg2.extras import Json
from requests.exceptions import RequestException

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from DB.db_utils import get_db_cursor
from utils import clean_text


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0 Safari/537.36"
GONE_HTTP_STATUSES = {404, 410, 451}
TRANSIENT_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}

STRONG_GONE_PATTERNS = tuple(
    re.compile(pattern, re.I)
    for pattern in (
        r"404\s*(?:not\s*found|error)?",
        r"page\s*not\s*found",
        r"not\s*found",
        r"페이지를\s*찾을\s*수\s*없",
        r"요청하신\s*페이지.*(?:없|찾)",
        r"존재하지\s*않는?\s*(?:페이지|게시물|게시글|강좌|교육|프로그램|콘텐츠|내용)",
        r"(?:페이지|게시물|게시글|강좌|교육|프로그램|콘텐츠|내용|정보|자료).*존재하지\s*않",
        r"삭제되었(?:거나|습니다)?",
        r"잘못된\s*접근",
        r"유효하지\s*않",
    )
)

CONTEXT_GONE_PATTERNS = tuple(
    re.compile(pattern, re.I)
    for pattern in (
        r"게시(?:물|글)이?\s*(?:존재하지\s*않|없)",
        r"해당\s*(?:강좌|교육|프로그램|강의|게시물|게시글|콘텐츠|정보|자료)\s*(?:가|은|는)?\s*(?:존재하지\s*않|없)",
        r"(?:등록|조회)된\s*(?:강좌|교육|프로그램|강의|게시물|게시글|데이터|정보|내용|자료)\s*(?:가|은|는)?\s*(?:없)",
        r"no\s*(?:data|record|content|article|post)\s*found",
    )
)


@dataclass
class UrlVerdict:
    url: str
    state: str
    removable: bool
    reason: str
    http_status: int | None = None
    final_url: str = ""
    title_present: bool = False
    evidence: str = ""
    error: str = ""


def compact_text(value: Any) -> str:
    return re.sub(r"\s+", "", clean_text(value)).lower()


def visible_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "lxml")
    for node in soup.select("script, style, noscript"):
        node.extract()
    main = (
        soup.select_one("main")
        or soup.select_one("#contents")
        or soup.select_one("#content")
        or soup.select_one(".contents")
        or soup.select_one(".content")
        or soup.body
        or soup
    )
    return clean_text(main.get_text(" ", strip=True))


def title_is_present(title: str, text: str) -> bool:
    title_compact = compact_text(title)
    if len(title_compact) < 4:
        return False
    text_compact = compact_text(text)
    if title_compact in text_compact:
        return True
    stop_words = {
        "개강",
        "휴강",
        "중도",
        "강좌",
        "특강",
        "토요일",
        "일요일",
        "월요일",
        "화요일",
        "수요일",
        "목요일",
        "금요일",
    }
    title_words = []
    for part in re.split(r"[\s\[\]()/|,:·ㅣ]+", clean_text(title)):
        word = compact_text(part)
        if len(word) < 2 or word in stop_words or re.fullmatch(r"\d+(?:주|회|차)?", word):
            continue
        title_words.append(word)
    if len(title_words) >= 2:
        hits = sum(1 for word in title_words[:8] if word in text_compact)
        return hits >= min(3, len(title_words))
    return False


def first_match(patterns: tuple[re.Pattern[str], ...], text: str) -> str:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return clean_text(match.group(0))
    return ""


def classify_response(url: str, title: str, response: requests.Response) -> UrlVerdict:
    status = int(response.status_code)
    final_url = response.url or url
    if status in GONE_HTTP_STATUSES:
        return UrlVerdict(url=url, state="gone", removable=True, reason=f"http_{status}", http_status=status, final_url=final_url)
    if status in TRANSIENT_HTTP_STATUSES or status in {401, 403}:
        return UrlVerdict(url=url, state="unknown", removable=False, reason=f"http_{status}", http_status=status, final_url=final_url)
    if status >= 400:
        return UrlVerdict(url=url, state="unknown", removable=False, reason=f"http_{status}", http_status=status, final_url=final_url)

    content_type = response.headers.get("content-type", "").lower()
    if content_type and "html" not in content_type and "text" not in content_type:
        return UrlVerdict(url=url, state="present", removable=False, reason="non_html_content", http_status=status, final_url=final_url)

    if not response.encoding or response.encoding.lower() == "iso-8859-1":
        response.encoding = response.apparent_encoding
    text = visible_text(response.text)
    title_present = title_is_present(title, text)
    if title_present:
        return UrlVerdict(
            url=url,
            state="present",
            removable=False,
            reason="title_present",
            http_status=status,
            final_url=final_url,
            title_present=True,
        )

    strong_evidence = first_match(STRONG_GONE_PATTERNS, text)
    if strong_evidence:
        return UrlVerdict(
            url=url,
            state="gone",
            removable=True,
            reason="gone_message",
            http_status=status,
            final_url=final_url,
            title_present=title_present,
            evidence=strong_evidence,
        )

    contextual_evidence = first_match(CONTEXT_GONE_PATTERNS, text)
    if contextual_evidence and not title_present:
        return UrlVerdict(
            url=url,
            state="gone",
            removable=True,
            reason="missing_content_message",
            http_status=status,
            final_url=final_url,
            title_present=title_present,
            evidence=contextual_evidence,
        )

    if len(compact_text(text)) < 20 and not title_present:
        return UrlVerdict(
            url=url,
            state="unknown",
            removable=False,
            reason="empty_html",
            http_status=status,
            final_url=final_url,
            title_present=title_present,
            evidence=text[:160],
        )

    return UrlVerdict(
        url=url,
        state="present" if title_present else "unknown",
        removable=False,
        reason="title_present" if title_present else "no_gone_evidence",
        http_status=status,
        final_url=final_url,
        title_present=title_present,
    )


def fetch_url(session: requests.Session, url: str, title: str, timeout: int) -> UrlVerdict:
    try:
        response = session.get(url, timeout=timeout, allow_redirects=True)
    except RequestException as exc:
        return UrlVerdict(url=url, state="error", removable=False, reason="request_error", error=str(exc))
    return classify_response(url, title, response)


def load_targets(args: argparse.Namespace) -> list[dict[str, Any]]:
    provider_filter = ""
    params: dict[str, Any] = {"limit": args.limit if args.limit > 0 else None}
    if args.provider:
        provider_filter = "AND provider = ANY(%(providers)s)"
        params["providers"] = args.provider
    status_filter = ""
    if args.status:
        status_filter = "AND status = ANY(%(statuses)s)"
        params["statuses"] = args.status
    limit_sql = "LIMIT %(limit)s" if args.limit > 0 else ""
    with get_db_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT id, provider, title, status, raw_url, application_url, updated_at
            FROM courses
            WHERE is_active IS TRUE
              AND COALESCE(NULLIF(raw_url, ''), NULLIF(application_url, '')) IS NOT NULL
              {provider_filter}
              {status_filter}
            ORDER BY updated_at ASC NULLS FIRST, provider, title
            {limit_sql}
            """,
            params,
        )
        return [dict(row) for row in cursor.fetchall()]


def deactivate_courses(rows: list[dict[str, Any]], verdicts: dict[str, UrlVerdict]) -> int:
    changed = 0
    with get_db_cursor() as cursor:
        for row in rows:
            url = clean_text(row.get("raw_url")) or clean_text(row.get("application_url"))
            verdict = verdicts[url]
            cursor.execute(
                """
                UPDATE courses
                SET is_active = FALSE,
                    status = 'CLOSED',
                    removed_at = COALESCE(removed_at, CURRENT_TIMESTAMP),
                    raw_fields = COALESCE(raw_fields, '{}'::jsonb) || %(raw_fields)s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %(id)s
                  AND is_active IS TRUE
                """,
                {
                    "id": row["id"],
                    "raw_fields": Json(
                        {
                            "stale_url_check": {
                                **asdict(verdict),
                                "checked_at": datetime.now(timezone.utc).isoformat(),
                            }
                        }
                    ),
                },
            )
            changed += cursor.rowcount
    return changed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deactivate active courses whose collected detail URL is clearly gone.")
    parser.add_argument("--provider", action="append", help="Provider to inspect. Can be repeated.")
    parser.add_argument("--status", action="append", help="Course status to inspect. Can be repeated.")
    parser.add_argument("--limit", type=int, default=100, help="Maximum active courses to inspect. Use 0 for no limit.")
    parser.add_argument("--timeout", type=int, default=12)
    parser.add_argument("--delay", type=float, default=0.1)
    parser.add_argument("--apply", action="store_true", help="Deactivate removable courses. Default is dry-run.")
    parser.add_argument("--max-remove", type=int, default=100, help="Safety cap for --apply unless --force is used.")
    parser.add_argument("--force", action="store_true", help="Allow --apply to exceed --max-remove.")
    parser.add_argument("--only-removable", action="store_true", help="Print only removable rows plus the final summary.")
    return parser.parse_args()


def run(args: argparse.Namespace) -> int:
    rows = load_targets(args)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"})

    verdict_cache: dict[str, UrlVerdict] = {}
    removable_rows: list[dict[str, Any]] = []
    counters: dict[str, int] = {}
    for index, row in enumerate(rows, start=1):
        url = clean_text(row.get("raw_url")) or clean_text(row.get("application_url"))
        if url not in verdict_cache:
            verdict_cache[url] = fetch_url(session, url, clean_text(row.get("title")), args.timeout)
            time.sleep(max(0.0, args.delay))
        verdict = verdict_cache[url]
        counters[verdict.state] = counters.get(verdict.state, 0) + 1
        if verdict.removable:
            removable_rows.append(row)
        if verdict.removable or not args.only_removable:
            print(
                json.dumps(
                    {
                        "index": index,
                        "provider": row.get("provider"),
                        "title": row.get("title"),
                        "status": row.get("status"),
                        "removable": verdict.removable,
                        **asdict(verdict),
                    },
                    ensure_ascii=False,
                    default=str,
                )
            )

    changed = 0
    if args.apply and removable_rows:
        if not args.force and len(removable_rows) > args.max_remove:
            print(
                json.dumps(
                    {
                        "error": "remove_cap_exceeded",
                        "removable": len(removable_rows),
                        "max_remove": args.max_remove,
                        "hint": "review dry-run output, then rerun with a higher --max-remove or --force",
                    },
                    ensure_ascii=False,
                )
            )
            return 2
        changed = deactivate_courses(removable_rows, verdict_cache)

    print(
        json.dumps(
            {
                "processed": len(rows),
                "unique_urls": len(verdict_cache),
                "removable": len(removable_rows),
                "deactivated": changed,
                "dry_run": not args.apply,
                "states": counters,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
