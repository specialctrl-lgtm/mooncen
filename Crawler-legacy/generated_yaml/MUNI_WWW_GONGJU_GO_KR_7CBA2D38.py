from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup, Tag


PROVIDER = "MUNI_WWW_GONGJU_GO_KR_7CBA2D38"
PROVIDER_NAME = "공주시 평생학습포털"
BASE_URL = "https://www.gongju.go.kr"
LIST_PATH = "/prog/nurimLeaEducate/E01/nurim/sub03_01/list.do"
LIST_URL = f"{BASE_URL}{LIST_PATH}"
DEFAULT_BRANCH = "공주시 평생학습포털"
DEFAULT_ADDRESS = "충청남도 공주시 봉황로 1"


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from DB.db_utils import get_db_cursor  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, setup_logger  # noqa: E402


logger = setup_logger("Crawler_GongjuNurim")


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "identity",
            "Referer": LIST_URL,
        }
    )
    return session


def normalize_space(value: Any) -> str:
    text = clean_text(value).replace("\xa0", " ")
    return clean_text(re.sub(r"\s+", " ", text))


def normalize_period(value: Any) -> str:
    text = normalize_space(value)
    text = re.sub(r"\s*~\s*", " ~ ", text)
    return text


def fetch_soup(session: requests.Session, url: str, timeout: int) -> BeautifulSoup:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def absolute_url(value: Any, base: str = LIST_URL) -> str:
    text = normalize_space(value)
    return urljoin(base, text) if text else ""


def extract_edu_no(url: str) -> str:
    match = re.search(r"[?&]eduNo=([^&#]+)", url)
    return match.group(1) if match else hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def li_pairs(container: Tag | None) -> dict[str, str]:
    pairs: dict[str, str] = {}
    if not container:
        return pairs
    for li in container.select("li"):
        key_node = li.select_one("b, strong, span")
        if not key_node:
            continue
        key = normalize_space(key_node.get_text(" ", strip=True))
        key_node.extract()
        value = normalize_space(li.get_text(" ", strip=True))
        if key:
            pairs[key] = value
    return pairs


def table_pairs(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for tr in soup.select("table tr"):
        cells = [cell for cell in tr.find_all(["th", "td"], recursive=False)]
        i = 0
        while i < len(cells):
            if cells[i].name == "th":
                key = normalize_space(cells[i].get_text(" ", strip=True))
                value = ""
                if i + 1 < len(cells) and cells[i + 1].name == "td":
                    value = normalize_space(cells[i + 1].get_text(" ", strip=True))
                    i += 2
                else:
                    i += 1
                if key:
                    pairs[key] = value
            else:
                i += 1
    return pairs


def normalize_fee(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return ""
    if "무료" in text or text == "0":
        return text
    amount = re.search(r"[\d,]+\s*원", text)
    return normalize_space(amount.group(0)) if amount else text


def split_venue(value: Any) -> tuple[str, str]:
    text = normalize_space(value)
    if not text:
        return "", ""
    if "(" in text and ")" in text:
        name = normalize_space(text.split("(", 1)[0])
        address = normalize_space(text.split("(", 1)[1].rsplit(")", 1)[0])
        if any(token in address for token in ["로", "길", "읍", "면", "시", "군"]):
            return name or text, address
        return text, ""
    address = text if any(token in text for token in ["로", "길", "읍", "면"]) else ""
    name = text
    return name or text, address


def parse_list_card(card: Tag) -> dict[str, Any] | None:
    link = card.select_one("a[href*='view.do'][href*='eduNo=']")
    title_node = card.select_one(".tit strong, .tit")
    if not link or not title_node:
        return None
    raw_url = absolute_url(link.get("href"))
    edu_no = extract_edu_no(raw_url)
    title = normalize_space(title_node.get_text(" ", strip=True))
    if not title:
        return None

    pairs = li_pairs(card.select_one("ul.info"))
    status = normalize_space(card.select_one(".state_btn b").get_text(" ", strip=True) if card.select_one(".state_btn b") else "")
    reception_type = normalize_space(card.select_one(".state_btn .typeC").get_text(" ", strip=True) if card.select_one(".state_btn .typeC") else "")
    venue_name, venue_address = split_venue(pairs.get("교육장소"))
    branch = venue_name or normalize_space(pairs.get("교육기관")) or DEFAULT_BRANCH
    row = {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": edu_no,
        "provider_course_id": edu_no,
        "title": title,
        "branch": branch,
        "branch_code": branch_code(branch),
        "address": venue_address or DEFAULT_ADDRESS,
        "period": normalize_period(pairs.get("교육기간")),
        "schedule_raw": normalize_space(pairs.get("교육시간")),
        "target": "",
        "fee": "",
        "status": status,
        "reception_type": reception_type,
        "reception_period": normalize_period(pairs.get("접수기간")),
        "capacity_text": normalize_space(pairs.get("신청/정원")),
        "venue_name": venue_name,
        "venue_address": venue_address,
        "raw_url": raw_url,
        "application_url": raw_url,
        "description": "",
        "image_url": "",
        "category": "",
        "collection_category": "평생학습",
        "domain_category": "평생학습",
        "source_group": "municipal_lifelong_learning",
        "operator_type": "지자체/공공기관",
        "program_type": "강좌",
        "raw_fields": {"list_pairs": pairs, "parser": "gongju_nurim_courses"},
    }
    row["course_id"] = stable_course_id(row)
    return row


def description_from_pairs(pairs: dict[str, str]) -> str:
    parts: list[str] = []
    for key in ["교육내용", "강사소개", "폐강조건인원", "홈페이지"]:
        value = normalize_space(pairs.get(key))
        if value:
            parts.append(f"{key}: {value}")
    return normalize_space(" ".join(parts))


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    soup = fetch_soup(session, row["raw_url"], timeout)
    pairs = table_pairs(soup)
    if pairs.get("강좌명"):
        row["title"] = normalize_space(pairs["강좌명"])
    venue_name, venue_address = split_venue(pairs.get("교육장소") or row.get("venue_name"))
    row["branch"] = venue_name or normalize_space(pairs.get("교육기관")) or row.get("branch")
    row["branch_code"] = branch_code(row["branch"])
    row["address"] = venue_address or row.get("address") or DEFAULT_ADDRESS
    row["venue_name"] = venue_name or row.get("venue_name")
    row["venue_address"] = venue_address or row.get("venue_address")
    row["period"] = normalize_period(pairs.get("교육기간") or row.get("period"))
    row["reception_period"] = normalize_period(pairs.get("접수기간") or row.get("reception_period"))
    row["schedule_raw"] = normalize_space(pairs.get("교육시간") or row.get("schedule_raw"))
    row["target"] = normalize_space(pairs.get("교육대상") or row.get("target"))
    row["fee"] = normalize_fee(pairs.get("수강료") or row.get("fee"))
    row["category"] = normalize_space(pairs.get("분야") or pairs.get("강좌구분") or row.get("category"))
    row["instructor"] = normalize_space(pairs.get("강사명"))
    row["phone"] = normalize_space(pairs.get("문의전화"))
    row["capacity_text"] = normalize_space(pairs.get("정원") or row.get("capacity_text"))
    row["description"] = description_from_pairs(pairs)
    row["raw_fields"] = {"detail_pairs": pairs, "parser": "gongju_nurim_courses"}
    row["course_id"] = stable_course_id(row)
    return row


def parse_list(soup: BeautifulSoup) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for card in soup.select(".courses_wrap .list"):
        row = parse_list_card(card)
        if row:
            rows.append(row)
    return rows


def stable_course_id(row: dict[str, Any]) -> str:
    key = "|".join(
        [
            PROVIDER,
            normalize_space(row.get("external_id")),
            normalize_space(row.get("title")),
            normalize_space(row.get("period")),
        ]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def branch_code(name: Any) -> str:
    text = normalize_space(name) or DEFAULT_BRANCH
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def is_expired_course(row: dict[str, Any]) -> bool:
    parsed = parse_date_range(row.get("period"))
    end_date = parsed[1] if parsed else None
    if not end_date:
        return False
    end_day = end_date.date() if hasattr(end_date, "date") else end_date
    return end_day < datetime.now().date()


def collect(
    limit: int | None = None,
    max_pages: int = 5,
    timeout: int = 20,
    include_expired: bool = False,
) -> list[dict[str, Any]]:
    session = make_session()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        url = LIST_URL if page == 1 else f"{LIST_URL}?{urlencode({'pageIndex': page})}"
        soup = fetch_soup(session, url, timeout)
        page_rows = parse_list(soup)
        if not page_rows:
            break
        expired_on_page = 0
        for row in page_rows:
            key = normalize_space(row.get("external_id"))
            if key in seen:
                continue
            seen.add(key)
            try:
                row = enrich_detail(session, row, timeout)
            except Exception as exc:
                logger.warning("Gongju detail failed %s: %s", row.get("raw_url"), exc)
            if not include_expired and is_expired_course(row):
                expired_on_page += 1
                logger.info("Skipping expired Gongju course: %s / %s", row.get("title"), row.get("period"))
                continue
            rows.append(row)
            if limit and len(rows) >= limit:
                return rows
        if not include_expired and expired_on_page == len(page_rows):
            break
    return rows


def quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fields = ["title", "branch", "raw_url", "address", "period", "schedule_raw", "target", "fee", "status", "description", "image_url"]
    counts = {field: sum(1 for row in rows if clean_text(row.get(field))) for field in fields}
    score = round(sum(counts.values()) / (len(rows) * len(fields)) * 100, 1) if rows else 0.0
    return {"rows": len(rows), "score": score, "field_counts": counts}


def print_quality(rows: list[dict[str, Any]]) -> None:
    print(json.dumps(quality(rows), ensure_ascii=False, indent=2))
    print("\nSAMPLE")
    for row in rows[:5]:
        print(
            " | ".join(
                [
                    normalize_space(row.get("title")),
                    normalize_space(row.get("branch")),
                    normalize_space(row.get("address")),
                    normalize_space(row.get("period")),
                    normalize_space(row.get("schedule_raw")),
                    normalize_space(row.get("target")),
                    normalize_space(row.get("fee")),
                    normalize_space(row.get("status")),
                ]
            )
        )


def save_branch_with_address(row: dict[str, Any]) -> str:
    branch = {
        "provider": PROVIDER,
        "branch_code": (normalize_space(row.get("branch_code")) or branch_code(row.get("branch")))[:50],
        "name": (normalize_space(row.get("branch")) or DEFAULT_BRANCH)[:100],
        "address": normalize_space(row.get("address")),
        "phone": normalize_space(row.get("phone")),
        "website_url": LIST_URL,
        "address_source": "crawler_detail",
    }
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO branches (provider, branch_code, name, address, phone, website_url, address_source)
            VALUES (%(provider)s, %(branch_code)s, %(name)s, %(address)s, %(phone)s, %(website_url)s, %(address_source)s)
            ON CONFLICT (provider, branch_code)
            DO UPDATE SET
                name = EXCLUDED.name,
                address = COALESCE(NULLIF(EXCLUDED.address, ''), branches.address),
                phone = COALESCE(NULLIF(EXCLUDED.phone, ''), branches.phone),
                website_url = EXCLUDED.website_url,
                address_source = COALESCE(NULLIF(EXCLUDED.address_source, ''), branches.address_source),
                updated_at = CURRENT_TIMESTAMP
            RETURNING id
            """,
            branch,
        )
        return str(cursor.fetchone()["id"])


def save_rows(rows: list[dict[str, Any]]) -> int:
    crawler = YamlSourceCrawler.__new__(YamlSourceCrawler)
    crawler.provider = PROVIDER
    crawler.target_parser = TargetParser()
    crawler.schedule_parser = ScheduleParser()
    saved = 0
    branch_ids: dict[str, str] = {}
    for row in rows:
        code = normalize_space(row.get("branch_code")) or branch_code(row.get("branch"))
        if code not in branch_ids:
            branch_ids[code] = save_branch_with_address(row)
        course = crawler.normalize_course(row, branch_ids[code])
        crawler.save_course(course)
        saved += 1
    logger.info("%s saved %s/%s rows.", PROVIDER, saved, len(rows))
    return saved


def main() -> int:
    parser = argparse.ArgumentParser(description="Gongju Nurim lifelong course crawler")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--include-expired", action="store_true")
    parser.add_argument("--mark-stale", action="store_true")
    parser.add_argument("--per-target-limit", type=int)
    parser.add_argument("--max-depth", type=int)
    parser.add_argument("--detail-limit", type=int)
    args = parser.parse_args()

    effective_limit = args.limit or args.per_target_limit
    started = datetime.now()
    rows = collect(limit=effective_limit, max_pages=args.max_pages, timeout=args.timeout, include_expired=args.include_expired)
    saved = save_rows(rows) if args.save_db else 0
    print_quality(rows)
    logger.info(
        "%s completed collected=%s saved=%s elapsed=%.1fs",
        PROVIDER,
        len(rows),
        saved,
        (datetime.now() - started).total_seconds(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
