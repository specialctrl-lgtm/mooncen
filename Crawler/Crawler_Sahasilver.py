from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urljoin, urlparse, urlsplit, urlunsplit

import requests
import yaml
from bs4 import BeautifulSoup

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from Crawler.Crawler_MunicipalYaml import MunicipalDbWriter
from DB.course_lifecycle import mark_stale_courses, utc_now
from DB.db_utils import get_db_cursor
from utils import clean_text, setup_logger
from utils.outbound_http import SafeSession


PROVIDER = "SAHASILVER_COURSE"
BASE_URL = "https://www.sahasilver.org"
LIST_URL = f"{BASE_URL}/05/02.php"
REPORT_DIR = Path(__file__).resolve().parents[1] / "logs" / "crawler_reports"

BRANCHES = {
    "사하": {
        "branch_code": "saha",
        "name": "사하사랑채노인복지관",
        "address": "부산광역시 사하구 사리로 35",
        "phone": "051.293.9544",
    },
    "신평": {
        "branch_code": "sinpyeong",
        "name": "신평사랑채노인복지관",
        "address": "부산광역시 사하구 다대로 130번길 34",
        "phone": "051.207.9544",
    },
}

logger = setup_logger(__name__, "logs/crawler_sahasilver.log")

MAX_LIMIT = 100_000
MAX_PAGES = 200
MAX_TIMEOUT_SECONDS = 120


def _validate_options(limit: Optional[int], timeout: int, max_pages: int) -> None:
    if limit is not None and not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    if not 1 <= timeout <= MAX_TIMEOUT_SECONDS:
        raise ValueError(f"timeout must be between 1 and {MAX_TIMEOUT_SECONDS}")
    if not 1 <= max_pages <= MAX_PAGES:
        raise ValueError(f"max_pages must be between 1 and {MAX_PAGES}")


def trusted_source_url(value: str) -> str:
    resolved = urljoin(BASE_URL, clean_text(value))
    parsed = urlsplit(resolved)
    expected = urlsplit(BASE_URL)
    if (
        parsed.scheme != "https"
        or parsed.hostname != expected.hostname
        or parsed.port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/05/02.php"
    ):
        return ""
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def session() -> requests.Session:
    s = SafeSession()
    s.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0 Safari/537.36",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    return s


def fetch_soup(s: requests.Session, url: str, timeout: int) -> BeautifulSoup:
    source_url = trusted_source_url(url)
    if not source_url:
        raise ValueError("refusing an untrusted Sahasilver source URL")
    for attempt in range(2):
        try:
            response = s.get(source_url, timeout=timeout, allow_redirects=False)
            if 300 <= response.status_code < 400:
                raise requests.TooManyRedirects("Sahasilver provider redirects are not allowed")
            response.raise_for_status()
            response.encoding = "utf-8"
            return BeautifulSoup(response.text, "lxml")
        except requests.RequestException:
            if attempt:
                raise
            time.sleep(0.2)
    raise AssertionError("unreachable")


def branch_info(label: str) -> Optional[dict[str, str]]:
    return BRANCHES.get(clean_text(label))


def status_from_node(node: BeautifulSoup) -> str:
    img = node.find("img")
    text = clean_text((img.get("alt") if img else "") or node.get_text(" ", strip=True))
    if any(token in text for token in ("종료", "마감")):
        return "CLOSED"
    if "대기" in text:
        return "WAITING"
    if any(token in text for token in ("신청", "접수", "진행")):
        return "OPEN"
    return text or "OPEN"


def split_schedule(value: str) -> tuple[str, str]:
    text = clean_text(value)
    match = re.search(
        r"(\d{4}[./-]\d{1,2}[./-]\d{1,2})\s*~\s*(\d{4}[./-]\d{1,2}[./-]\d{1,2})\s*(.*)",
        text,
    )
    if not match:
        return "", normalize_schedule(text)
    period = f"{match.group(1).replace('-', '.')} ~ {match.group(2).replace('-', '.')}"
    return period, normalize_schedule(match.group(3))


def normalize_schedule(value: str) -> str:
    text = clean_text(value)
    text = re.sub(r"(\d{1,2}:\d{2})\s*~\s*(\d{1,2}:\d{2})", r"\1-\2", text)
    return text


def normalize_apply_period(value: str) -> str:
    return clean_text(value).replace("-", ".")


def normalize_fee(value: str) -> str:
    fee = clean_text(value)
    if not fee:
        return ""
    if fee in {"0", "0원", "무료"}:
        return "무료"
    if "원" in fee:
        return fee
    if re.fullmatch(r"\d{1,3}(?:,\d{3})*|\d+", fee):
        return f"{fee}원"
    return fee


def build_description(row: dict[str, Any]) -> str:
    return clean_text(
        " ".join(
            str(part)
            for part in [
                row.get("title"),
                row.get("branch"),
                row.get("period"),
                row.get("schedule_raw"),
                row.get("apply_period"),
                row.get("instructor"),
                row.get("capacity"),
                row.get("fee"),
                row.get("room"),
            ]
            if clean_text(part)
        )
    )


def course_id_from_href(href: str, fallback: str) -> str:
    query = parse_qs(urlparse(href).query)
    uid = clean_text((query.get("uid") or [""])[0])
    if uid:
        return uid
    # List row numbers repeat on every page, so they are not safe identities.
    _ = fallback
    return hashlib.sha1(href.encode("utf-8")).hexdigest()[:20]


def detail_pairs(soup: BeautifulSoup) -> dict[str, str]:
    lines = [clean_text(line) for line in soup.get_text("\n", strip=True).splitlines() if clean_text(line)]
    labels = {"수업일시", "모집인원", "장소", "신청기간", "이용료", "첨부파일"}
    pairs: dict[str, str] = {}
    for index, line in enumerate(lines):
        if line in labels and index + 1 < len(lines):
            pairs[line] = lines[index + 1]
    return pairs


def parse_detail(s: requests.Session, url: str, timeout: int) -> dict[str, Any]:
    soup = fetch_soup(s, url, timeout)
    pairs = detail_pairs(soup)
    period, schedule = split_schedule(pairs.get("수업일시", ""))
    return {
        "period": period,
        "schedule_raw": schedule,
        "room": pairs.get("장소", ""),
        "apply_period": normalize_apply_period(pairs.get("신청기간", "")),
        "fee": normalize_fee(pairs.get("이용료", "")),
        "target": "어르신",
        "description": "",
    }


def parse_list_page(soup: BeautifulSoup, page_url: str) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    for tr in soup.select("table tr"):
        cells = tr.find_all("td")
        if len(cells) < 6:
            continue
        title_cell = tr.select_one(".board-list-title")
        link = title_cell.find("a", href=True) if title_cell else None
        if not link:
            continue
        title_lines = [clean_text(line) for line in link.get_text("\n", strip=True).splitlines() if clean_text(line)]
        title = title_lines[0] if title_lines else ""
        schedule_line = next((line.replace("수업일시 :", "").strip() for line in title_lines if line.startswith("수업일시")), "")
        apply_line = next((line.replace("신청기간 :", "").strip() for line in title_lines if line.startswith("신청기간")), "")
        period, schedule = split_schedule(schedule_line)
        branch_label = clean_text(cells[1].get_text(" ", strip=True))
        branch = branch_info(branch_label)
        if not branch:
            continue
        raw_url = trusted_source_url(urljoin(page_url, link["href"]))
        if not raw_url:
            continue
        capacity = clean_text(cells[4].get_text(" ", strip=True))
        rows.append(
            {
                "provider": PROVIDER,
                "provider_course_id": course_id_from_href(link["href"], cells[0].get_text(" ", strip=True)),
                "title": title,
                "branch": branch["name"],
                "branch_code": branch["branch_code"],
                "address": branch["address"],
                "phone": branch["phone"],
                "category": "노인복지관",
                "target": "어르신",
                "raw_url": raw_url,
                "period": period,
                "schedule_raw": schedule,
                "apply_period": normalize_apply_period(apply_line),
                "instructor": clean_text(cells[3].get_text(" ", strip=True)),
                "capacity": capacity,
                "status": status_from_node(cells[5]),
                "fee": "",
                "room": "",
                "description": "",
            }
        )

    links: list[str] = []
    for a in soup.find_all("a", href=True):
        text = clean_text(a.get_text(" ", strip=True))
        if re.fullmatch(r"\d+|다음|>", text):
            link = trusted_source_url(urljoin(page_url, a["href"]))
            if link:
                links.append(link)
    return rows, list(dict.fromkeys(links))


class SahasilverDbWriter(MunicipalDbWriter):
    def save_branch(self, branch_code: str, name: str) -> Optional[str]:
        branch = next((value for value in BRANCHES.values() if value["branch_code"] == branch_code), BRANCHES["신평"])
        data = {
            "provider": self.provider,
            "branch_code": branch["branch_code"],
            "name": branch["name"],
            "address": branch["address"],
            "phone": branch["phone"],
            "website_url": BASE_URL,
            "address_source": "crawler",
        }
        with get_db_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO branches (provider, branch_code, name, address, phone, website_url, address_source)
                VALUES (%(provider)s, %(branch_code)s, %(name)s, %(address)s, %(phone)s, %(website_url)s, %(address_source)s)
                ON CONFLICT (provider, branch_code)
                DO UPDATE SET
                    name = EXCLUDED.name,
                    address = EXCLUDED.address,
                    phone = EXCLUDED.phone,
                    website_url = EXCLUDED.website_url,
                    address_source = EXCLUDED.address_source,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING id
                """,
                data,
            )
            return str(cursor.fetchone()["id"])


def collect(limit: Optional[int], timeout: int, max_pages: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    _validate_options(limit, timeout, max_pages)
    rows: list[dict[str, Any]] = []
    queue = [LIST_URL]
    visited: set[str] = set()
    detail_pages = 0
    list_errors = 0
    detail_errors = 0
    invalid_rows = 0
    duplicate_rows = 0
    seen_course_ids: set[str] = set()
    pagination_detected = False
    with session() as s:
        while queue and len(visited) < max_pages and (limit is None or len(rows) < limit):
            url = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)
            try:
                soup = fetch_soup(s, url, timeout)
            except Exception as exc:
                list_errors += 1
                logger.warning("Sahasilver list fetch failed: %s", exc)
                continue
            page_rows, links = parse_list_page(soup, url)
            pagination_detected = pagination_detected or bool(links)
            for row in page_rows:
                if limit is not None and len(rows) >= limit:
                    break
                try:
                    detail = parse_detail(s, row["raw_url"], timeout)
                    detail_pages += 1
                    for key, value in detail.items():
                        if value and not row.get(key):
                            row[key] = value
                except Exception as exc:
                    detail_errors += 1
                    logger.warning("Sahasilver detail fetch failed: %s", exc)
                if not row.get("description"):
                    row["description"] = build_description(row)
                if not clean_text(row.get("title")) or not clean_text(row.get("provider_course_id")):
                    invalid_rows += 1
                    continue
                identity = clean_text(row.get("provider_course_id"))
                if identity in seen_course_ids:
                    duplicate_rows += 1
                    continue
                seen_course_ids.add(identity)
                rows.append(row)
            for link in links:
                if link not in visited and link not in queue:
                    queue.append(link)
    capped = bool(queue and len(visited) >= max_pages)
    complete = bool(
        limit is None
        and not capped
        and not list_errors
        and not detail_errors
        and not invalid_rows
    )
    return rows, {
        "pages": len(visited),
        "detail_pages": detail_pages,
        "pagination_detected": pagination_detected,
        "list_errors": list_errors,
        "detail_errors": detail_errors,
        "invalid_rows": invalid_rows,
        "duplicate_rows": duplicate_rows,
        "capped": capped,
        "complete": complete,
    }


def score_fields(rows: list[dict[str, Any]]) -> dict[str, int]:
    keys = ["title", "branch", "address", "period", "schedule_raw", "target", "fee", "status", "description", "image_url"]
    return {key: sum(1 for row in rows if row.get(key)) for key in keys}


def write_report(rows: list[dict[str, Any]], meta: dict[str, Any], saved: int, error: str = "") -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "provider": PROVIDER,
        "success": bool(rows) and not error,
        "collected": len(rows),
        "saved": saved,
        "error": error,
        "meta": meta,
        "fields": score_fields(rows),
        "samples": rows[:3],
        "notes": ["description/image_url are not exposed on the current course pages."],
    }
    path = REPORT_DIR / f"sahasilver_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
    return path


def print_quality(rows: list[dict[str, Any]], meta: dict[str, Any], saved: int, report_path: Path) -> None:
    fields = score_fields(rows)
    headers = ["provider", "rows", "saved", "pages", "detail", "pg", "title", "period", "schedule", "target", "fee", "desc", "image"]
    values = [
        PROVIDER,
        str(len(rows)),
        str(saved),
        str(meta.get("pages", 0)),
        str(meta.get("detail_pages", 0)),
        "Y" if meta.get("pagination_detected") else "N",
        str(fields["title"]),
        str(fields["period"]),
        str(fields["schedule_raw"]),
        str(fields["target"]),
        str(fields["fee"]),
        str(fields["description"]),
        str(fields["image_url"]),
    ]
    widths = [max(len(h), len(v)) for h, v in zip(headers, values)]
    def fmt(row: list[str]) -> str:
        return "| " + " | ".join(cell.ljust(width) for cell, width in zip(row, widths)) + " |"
    print(fmt(headers))
    print("| " + " | ".join("-" * width for width in widths) + " |")
    print(fmt(values))
    print(f"report={report_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Crawler for Sahasilver course applications")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--mark-stale", action="store_true")
    args = parser.parse_args()
    if args.mark_stale and not args.save_db:
        parser.error("--mark-stale requires --save-db")
    if args.mark_stale and args.limit is not None:
        parser.error("--mark-stale cannot be used with --limit")

    saved = 0
    error = ""
    crawl_started_at = utc_now()
    try:
        rows, meta = collect(args.limit, args.timeout, args.max_pages)
        if args.save_db and rows:
            writer = SahasilverDbWriter(PROVIDER)
            saved = writer.save_rows(rows)
            if args.mark_stale and saved == len(rows) and meta.get("complete"):
                mark_stale_courses(PROVIDER, crawl_started_at)
            elif args.mark_stale:
                error = "stale cleanup refused because the Sahasilver crawl was partial"
                logger.error(error)
    except Exception as exc:
        rows = []
        meta = {}
        error = f"{type(exc).__name__}: {exc}"
        logger.exception("Sahasilver crawler failed")
    report_path = write_report(rows, meta, saved, error)
    print_quality(rows, meta, saved, report_path)
    return 0 if rows and not error else 1


if __name__ == "__main__":
    raise SystemExit(main())
