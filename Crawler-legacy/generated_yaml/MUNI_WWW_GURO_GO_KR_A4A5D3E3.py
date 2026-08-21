from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup, Tag


PROVIDER = "MUNI_WWW_GURO_GO_KR_A4A5D3E3"
PROVIDER_NAME = "구로구 통합예약 교육강좌"
BASE_URL = "https://www.guro.go.kr"
DEFAULT_ADDRESS = "서울특별시 구로구 가마산로 245"

TARGETS = [
    {
        "name": "구로구 정보화교육",
        "category": "정보화교육",
        "branch_code_prefix": "GURO_IT",
        "url": "https://www.guro.go.kr/yeyak/webEdcLctreList.do?key=3589&rep=1&searchLctreGroup=1&jachi=0&",
    },
    {
        "name": "구로구 자치회관",
        "category": "자치회관",
        "branch_code_prefix": "GURO_JACHI",
        "url": "https://www.guro.go.kr/yeyak/webEdcLctreList.do?key=3600&rep=1&searchLctreGroup=0&jachi=1&",
    },
]

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, setup_logger  # noqa: E402


logger = setup_logger("Crawler_GuroReservation")


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        }
    )
    return session


def normalize_space(value: Any) -> str:
    text = clean_text(value).replace("\xa0", " ")
    return clean_text(re.sub(r"\s+", " ", text))


def fetch_soup(session: requests.Session, url: str, timeout: int) -> BeautifulSoup:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def add_query(url: str, **params: Any) -> str:
    parsed = urlparse(url)
    query = dict(parse_qs(parsed.query, keep_blank_values=True))
    for key, value in params.items():
        if value is not None:
            query[key] = [str(value)]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def stable_id(*parts: Any, length: int = 16) -> str:
    text = "|".join(normalize_space(part) for part in parts)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:length]


def stable_course_id(row: dict[str, Any]) -> str:
    return hashlib.sha256(
        "|".join(
            [
                PROVIDER,
                normalize_space(row.get("external_id")),
                normalize_space(row.get("title")),
                normalize_space(row.get("period")),
                normalize_space(row.get("branch")),
            ]
        ).encode("utf-8")
    ).hexdigest()[:32]


def parse_lctre_key(url: str) -> str:
    return parse_qs(urlparse(url).query).get("searchLctreKey", [""])[0]


def parse_period_schedule(raw: str) -> tuple[str, str]:
    text = normalize_space(raw)
    match = re.search(r"(\d{4}[.]\d{1,2}[.]\d{1,2})\s*~\s*(\d{4}[.]\d{1,2}[.]\d{1,2})\s*(.*)", text)
    if not match:
        return text, ""
    start, end, rest = match.groups()
    period = f"{start.replace('.', '-')} ~ {end.replace('.', '-')}"
    return normalize_space(period), normalize_space(rest)


def parse_list_row(tr: Tag, target: dict[str, str]) -> dict[str, Any] | None:
    cells = tr.select("td")
    if len(cells) < 6:
        return None
    link = cells[1].select_one("a[href]")
    title = normalize_space(link.get_text(" ", strip=True) if link else cells[1].get_text(" ", strip=True))
    raw_url = urljoin(target["url"], link.get("href")) if link else ""
    if not title or not raw_url:
        return None
    period, schedule_raw = parse_period_schedule(cells[4].get_text(" ", strip=True))
    branch = normalize_space(cells[2].get_text(" ", strip=True)) or target["name"]
    row = {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": parse_lctre_key(raw_url),
        "course_id": "",
        "title": title,
        "branch": branch,
        "branch_code": f"{target['branch_code_prefix']}_{stable_id(branch)}",
        "address": DEFAULT_ADDRESS,
        "period": period,
        "schedule_raw": schedule_raw,
        "target": "",
        "fee": "",
        "status": normalize_space(cells[0].get_text(" ", strip=True)),
        "description": "",
        "image_url": "",
        "raw_url": raw_url,
        "category": target["category"],
        "collection_category": "평생학습",
        "domain_category": "평생학습",
        "operator_type": "지자체/공공기관",
        "reception_period": normalize_space(cells[3].get_text(" ", strip=True)),
        "capacity_text": normalize_space(cells[5].get_text(" ", strip=True)),
        "source_group": "lifelong_learning",
    }
    row["course_id"] = stable_course_id(row)
    return row


def parse_list(soup: BeautifulSoup, target: dict[str, str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    table = soup.select_one("table.p-table")
    if not table:
        return rows
    for tr in table.select("tbody tr"):
        row = parse_list_row(tr, target)
        if row:
            rows.append(row)
    return rows


def parse_table_pairs(table: Tag | None) -> dict[str, str]:
    pairs: dict[str, str] = {}
    if not table:
        return pairs
    for tr in table.select("tr"):
        cells = tr.select("th,td")
        idx = 0
        while idx < len(cells):
            key = normalize_space(cells[idx].get_text(" ", strip=True))
            value = normalize_space(cells[idx + 1].get_text(" ", strip=True)) if idx + 1 < len(cells) else ""
            if key and value:
                pairs[key] = value
            idx += 2
    return pairs


def clean_fee(value: str) -> str:
    text = normalize_space(value)
    match = re.search(r"([0-9,]+\s*원)", text)
    return normalize_space(match.group(1) if match else text)


def parse_branch_address(value: str, fallback_branch: str) -> tuple[str, str]:
    text = normalize_space(value)
    match = re.search(r"(.+?)\s+((?:0\d{4}|1\d{4}|서울특별시|서울시).*)", text)
    if match:
        return normalize_space(match.group(1)), normalize_space(match.group(2))
    return fallback_branch, DEFAULT_ADDRESS


def build_description(pairs: dict[str, str]) -> str:
    useful = []
    for key in ["강좌영역", "수강신청방법", "주최", "문의"]:
        if pairs.get(key):
            useful.append(f"{key}: {pairs[key]}")
    return normalize_space(" / ".join(useful))


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    soup = fetch_soup(session, row["raw_url"], timeout)
    pairs = parse_table_pairs(soup.select_one("table.p-table.block"))
    row["target"] = pairs.get("수강대상") or row.get("target") or "구로구민"
    row["period"] = normalize_space(pairs.get("교육기간") or row.get("period"))
    row["schedule_raw"] = normalize_space(pairs.get("강의시간") or row.get("schedule_raw"))
    branch, address = parse_branch_address(pairs.get("강의장소") or "", row.get("branch") or "")
    row["branch"] = branch or row.get("branch")
    row["address"] = address or row.get("address") or DEFAULT_ADDRESS
    row["branch_code"] = f"GURO_{stable_id(row.get('branch'))}"
    row["fee"] = clean_fee(pairs.get("수강료") or row.get("fee") or "")
    row["status"] = pairs.get("강좌상태") or row.get("status")
    row["description"] = build_description(pairs)
    row["phone"] = normalize_space(pairs.get("문의"))
    row["capacity_text"] = normalize_space(pairs.get("정원") or row.get("capacity_text"))
    row["reception_period"] = normalize_space(pairs.get("신청기간") or row.get("reception_period"))
    row["course_id"] = stable_course_id(row)
    return row


def max_page_index(soup: BeautifulSoup) -> int:
    max_page = 1
    for a in soup.select('a[href*="pageIndex="]'):
        href = a.get("href") or ""
        page = parse_qs(urlparse(urljoin(BASE_URL, href)).query).get("pageIndex", [""])[0]
        if page.isdigit():
            max_page = max(max_page, int(page))
    return max_page


def is_expired_course(row: dict[str, Any]) -> bool:
    parsed = parse_date_range(row.get("period"))
    end_date = parsed[1] if parsed else None
    if not end_date:
        return False
    end_day = end_date.date() if hasattr(end_date, "date") else end_date
    return end_day < datetime.now().date()


def collect(
    limit: int | None = None,
    max_pages: int = 10,
    timeout: int = 20,
    include_expired: bool = False,
) -> list[dict[str, Any]]:
    session = make_session()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for target in TARGETS:
        first_soup = fetch_soup(session, target["url"], timeout)
        page_limit = min(max_pages, max_page_index(first_soup))
        for page in range(1, page_limit + 1):
            soup = first_soup if page == 1 else fetch_soup(session, add_query(target["url"], pageIndex=page, pageUnit=10), timeout)
            page_rows = parse_list(soup, target)
            if not page_rows:
                break
            for row in page_rows:
                key = normalize_space(row.get("external_id")) or row["raw_url"]
                if key in seen:
                    continue
                seen.add(key)
                row = enrich_detail(session, row, timeout)
                if not include_expired and is_expired_course(row):
                    logger.info("Skipping expired Guro course: %s / %s", row.get("title"), row.get("period"))
                    continue
                rows.append(row)
                if limit and len(rows) >= limit:
                    return rows
    return rows


def quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fields = ["title", "branch", "raw_url", "address", "period", "schedule_raw", "target", "fee", "status", "description", "image_url"]
    counts = {field: sum(1 for row in rows if clean_text(row.get(field))) for field in fields}
    score = round(sum(counts.values()) / (len(rows) * len(fields)) * 100, 1) if rows else 0.0
    return {"rows": len(rows), "score": score, "field_counts": counts}


def print_quality(rows: list[dict[str, Any]]) -> None:
    print(json.dumps(quality(rows), ensure_ascii=False, indent=2))
    print("\nSAMPLE")
    for row in rows[:8]:
        print(
            " | ".join(
                [
                    normalize_space(row.get("title")),
                    normalize_space(row.get("branch")),
                    normalize_space(row.get("period")),
                    normalize_space(row.get("schedule_raw")),
                    normalize_space(row.get("target")),
                    normalize_space(row.get("fee")),
                    normalize_space(row.get("status")),
                ]
            )
        )


def save_rows(rows: list[dict[str, Any]]) -> int:
    crawler = YamlSourceCrawler.__new__(YamlSourceCrawler)
    crawler.provider = PROVIDER
    crawler.target_parser = TargetParser()
    crawler.schedule_parser = ScheduleParser()
    saved = 0
    branch_ids: dict[str, str] = {}
    for row in rows:
        branch_code = normalize_space(row.get("branch_code")) or f"GURO_{stable_id(row.get('branch'))}"
        branch_name = normalize_space(row.get("branch")) or PROVIDER_NAME
        if branch_code not in branch_ids:
            branch_ids[branch_code] = crawler.save_branch(branch_code, branch_name)
        course = crawler.normalize_course(row, branch_ids[branch_code])
        crawler.save_course(course)
        saved += 1
    logger.info("%s saved %s/%s rows.", PROVIDER, saved, len(rows))
    return saved


def main() -> int:
    parser = argparse.ArgumentParser(description="Guro integrated reservation education crawler")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--include-expired", action="store_true")
    parser.add_argument("--mark-stale", action="store_true")
    parser.add_argument("--per-target-limit", type=int)
    parser.add_argument("--max-depth", type=int)
    parser.add_argument("--detail-limit", type=int)
    args = parser.parse_args()

    rows = collect(
        limit=args.limit or args.per_target_limit,
        max_pages=args.max_pages,
        timeout=args.timeout,
        include_expired=args.include_expired,
    )
    saved = save_rows(rows) if args.save_db else 0
    print_quality(rows)
    logger.info("%s completed collected=%s saved=%s", PROVIDER, len(rows), saved)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
