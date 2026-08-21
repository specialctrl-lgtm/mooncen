from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag


PROVIDER = "MUNI_WWW_YANGJU_GO_KR_1A2AECAC"
PROVIDER_NAME = "양주시평생학습센터"
BASE_URL = "https://www.yangju.go.kr"
LIST_URL = "https://www.yangju.go.kr/lll/selectEduLctreWebList.do?key=1838&lllKind=1"
DEFAULT_BRANCH = "양주시평생학습센터"
DEFAULT_ADDRESS = "경기도 양주시 부흥로 1533"


BRANCH_ADDRESSES = {
    "양주시평생학습관": "경기도 양주시 부흥로 1533",
    "양주시청": "경기도 양주시 부흥로 1533",
    "양주시청 미래교육과": "경기도 양주시 부흥로 1533",
    "옥정평생학습센터": "경기도 양주시 옥정동로7길 110",
    "옥정서부평생학습센터": "경기도 양주시 옥정서로 42",
    "덕계평생학습관": "경기도 양주시 평화로1475번길 39",
    "백석평생학습관": "경기도 양주시 백석읍 중앙로223번길 46",
    "율정평생학습센터": "경기도 양주시 옥정로 397-7",
    "옥정평생서부학습센터": "경기도 양주시 옥정서로 42",
    "광적평생학습센터": "경기도 양주시 광적면 가래비길 93",
    "은현평생학습센터": "경기도 양주시 은현면 은현로 66",
    "덕정평생학습센터": "경기도 양주시 화합로 1426",
}


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from DB.db_utils import get_db_cursor  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_instructor_name, clean_text, extract_material_fee_amount, setup_logger  # noqa: E402


logger = setup_logger("Crawler_YangjuLifelong")


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


def normalize_yymmdd_range(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return ""

    def repl(match: re.Match[str]) -> str:
        year = int(match.group(1))
        return f"{2000 + year:04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"

    text = re.sub(r"\b(\d{2})[.](\d{1,2})[.](\d{1,2})\b", repl, text)
    text = re.sub(r"\b(\d{4})[.](\d{1,2})[.](\d{1,2})\b", lambda m: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}", text)
    text = re.sub(r"\s*~\s*", " ~ ", text)
    return normalize_space(text)


def fetch_soup(session: requests.Session, url: str, timeout: int) -> BeautifulSoup:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def page_url(page: int) -> str:
    parsed = urlparse(LIST_URL)
    query = parse_qs(parsed.query)
    query["pageIndex"] = [str(page)]
    query.setdefault("pageUnit", ["20"])
    return parsed._replace(query=urlencode(query, doseq=True)).geturl()


def absolute_url(base: str, href: Any) -> str:
    text = normalize_space(href)
    return urljoin(base, text) if text else ""


def stable_id(*parts: Any) -> str:
    seed = "|".join(normalize_space(part) for part in parts if normalize_space(part))
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def branch_code(name: Any) -> str:
    return stable_id(PROVIDER, normalize_space(name) or DEFAULT_BRANCH)[:12]


def split_period_cell(value: Any) -> tuple[str, str]:
    text = normalize_space(value)
    apply_period = ""
    course_period = ""
    apply_match = re.search(r"접수\s*:\s*(.*?)(?=\s*교육\s*:|$)", text)
    course_match = re.search(r"교육\s*:\s*(.+)$", text)
    if apply_match:
        apply_period = normalize_yymmdd_range(apply_match.group(1))
    if course_match:
        course_period = normalize_yymmdd_range(course_match.group(1))
    return apply_period, course_period


def parse_capacity(value: Any) -> tuple[int | None, int | None, int | None]:
    text = normalize_space(value)
    capacity_current = None
    capacity_total = None
    waitlist_total = None
    match = re.search(r"정원\s*:\s*(\d+)\s*/\s*(\d+)", text)
    if match:
        capacity_current = int(match.group(1))
        capacity_total = int(match.group(2))
    wait_match = re.search(r"대기\s*:\s*\(?\s*\d+\s*/\s*(\d+)", text)
    if wait_match:
        waitlist_total = int(wait_match.group(1))
    return capacity_current, capacity_total, waitlist_total


def normalize_status(value: Any) -> str:
    text = normalize_space(value)
    if any(token in text for token in ["접수중", "모집중"]):
        return "OPEN"
    if any(token in text for token in ["접수대기", "접수예정"]):
        return "SCHEDULED"
    if any(token in text for token in ["교육종료", "접수마감", "마감", "종료"]):
        return "CLOSED"
    return text


def normalize_fee(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return ""
    if "무료" in text:
        return "무료"
    amount = re.search(r"[\d,]+\s*원", text)
    return normalize_space(amount.group(0)) if amount else text


def detail_pairs(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for item in soup.select(".education_request li.clearfix"):
        key_node = item.select_one("em")
        value_node = item.select_one("p")
        key = normalize_space(key_node.get_text(" ", strip=True) if key_node else "")
        value = normalize_space(value_node.get_text(" ", strip=True) if value_node else "")
        if key:
            pairs[key] = value
    return pairs


def address_for(branch: Any, venue: Any = "") -> str:
    text = normalize_space(f"{branch} {venue}")
    for name, address in BRANCH_ADDRESSES.items():
        if name in text:
            return address
    return DEFAULT_ADDRESS


def parse_list_row(row: Tag, current_url: str) -> dict[str, Any] | None:
    cells = row.find_all("td", recursive=False)
    if len(cells) < 8:
        return None
    link = row.select_one('a[href*="eduLctreWebView"]')
    title = normalize_space(link.get_text(" ", strip=True) if link else cells[1].get_text(" ", strip=True))
    if not title:
        return None
    raw_url = absolute_url(current_url, link.get("href") if link else "")
    apply_period, period = split_period_cell(cells[3].get_text(" ", strip=True))
    capacity_current, capacity_total, waitlist_total = parse_capacity(cells[5].get_text(" ", strip=True))
    status_text = normalize_space(cells[7].get_text(" ", strip=True))
    edu_no_match = re.search(r"eduLctreNo=(\d+)", raw_url)
    external_id = edu_no_match.group(1) if edu_no_match else stable_id(raw_url, title)
    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": external_id,
        "provider_course_id": external_id,
        "title": title,
        "branch": DEFAULT_BRANCH,
        "branch_code": branch_code(DEFAULT_BRANCH),
        "address": DEFAULT_ADDRESS,
        "period": period,
        "schedule_raw": normalize_space(cells[4].get_text(" ", strip=True)),
        "target": "",
        "fee": normalize_fee(cells[6].get_text(" ", strip=True)),
        "status": normalize_status(status_text),
        "description": "",
        "image_url": "",
        "raw_url": raw_url,
        "category": "",
        "venue_name": "",
        "venue_address": "",
        "room": "",
        "reception_period": apply_period,
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "waitlist_total": waitlist_total,
        "application_method_raw": normalize_space(cells[2].get_text(" ", strip=True)),
        "raw_fields": {
            "parser": "yangju_lifelong_list",
            "list_cells": [normalize_space(cell.get_text(" ", strip=True)) for cell in cells],
        },
    }


def is_expired_course(row: dict[str, Any]) -> bool:
    _, end_date = parse_date_range(row.get("period"))
    return bool(end_date and end_date < datetime.now().date())


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    soup = fetch_soup(session, row["raw_url"], timeout)
    scope = soup.select_one(".education_request") or soup
    title_node = scope.select_one(".titlebox .title")
    status_node = scope.select_one(".titlebox .state")
    pairs = detail_pairs(soup)
    if title_node:
        row["title"] = normalize_space(title_node.get_text(" ", strip=True)) or row["title"]
    row["status"] = normalize_status(status_node.get_text(" ", strip=True) if status_node else row.get("status"))
    institution = normalize_space(pairs.get("교육기관")) or row["branch"]
    venue = normalize_space(pairs.get("교육장소"))
    room = normalize_space(pairs.get("강의실")) or venue
    row["branch"] = institution if venue in {"", "기타", "온라인"} else venue
    row["branch_code"] = branch_code(row["branch"])
    row["address"] = address_for(row["branch"], venue)
    row["venue_name"] = venue or row["branch"]
    row["venue_address"] = row["address"]
    row["room"] = room
    row["category"] = normalize_space(pairs.get("분류")) or row["category"]
    row["target"] = normalize_space(pairs.get("수강대상")) or row["target"]
    row["fee"] = normalize_fee(pairs.get("수강료")) or row["fee"]
    material_note = normalize_space(pairs.get("교재 및 참고자료") or pairs.get("재료비"))
    row["material_note"] = material_note
    row["material_fee"] = extract_material_fee_amount(material_note)
    row["period"] = normalize_yymmdd_range(pairs.get("교육기간")) or row["period"]
    row["reception_period"] = normalize_yymmdd_range(pairs.get("접수기간")) or row["reception_period"]
    weekday = normalize_space(pairs.get("교육요일"))
    if weekday and weekday not in row["schedule_raw"]:
        row["schedule_raw"] = normalize_space(f"{weekday} {row['schedule_raw']}")
    current, total, wait_total = parse_capacity(pairs.get("모집인원"))
    row["capacity_current"] = current if current is not None else row.get("capacity_current")
    row["capacity_total"] = total if total is not None else row.get("capacity_total")
    row["waitlist_total"] = wait_total if wait_total is not None else row.get("waitlist_total")
    row["instructor"] = clean_instructor_name(pairs.get("강사명"))
    row["phone"] = normalize_space(pairs.get("전화번호"))
    row["application_method_raw"] = normalize_space(pairs.get("접수방식") or pairs.get("모집방법")) or row["application_method_raw"]
    apply_link = scope.select_one('a[href*="selectEduApplcntAgreView"]')
    row["application_url"] = absolute_url(row["raw_url"], apply_link.get("href")) if apply_link and normalize_status(row["status"]) in {"OPEN", "SCHEDULED"} else ""
    description_parts = []
    for key in ["강의개요", "유의사항", "강의계획서"]:
        if normalize_space(pairs.get(key)):
            description_parts.append(f"{key}: {pairs[key]}")
    row["description"] = normalize_space(" ".join(description_parts))
    image = scope.select_one("img[src]")
    row["image_url"] = absolute_url(row["raw_url"], image.get("src")) if image and image.get("src") else ""
    row["provider_course_id"] = row["external_id"]
    row["raw_fields"]["detail_pairs"] = pairs
    row["raw_fields"]["detail_parser"] = "yangju_lifelong_detail"
    return row


def collect(
    limit: int | None = None,
    max_pages: int = 5,
    timeout: int = 20,
    include_expired: bool = False,
    detail: bool = True,
) -> list[dict[str, Any]]:
    session = make_session()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        current_url = page_url(page)
        soup = fetch_soup(session, current_url, timeout)
        table = soup.select_one("table.list_table")
        if not table:
            break
        page_rows = 0
        expired_on_page = 0
        for tr in table.select("tbody tr"):
            row = parse_list_row(tr, current_url)
            if not row or row["provider_course_id"] in seen:
                continue
            seen.add(row["provider_course_id"])
            if not include_expired and is_expired_course(row):
                expired_on_page += 1
                logger.info("Skipping expired Yangju course: %s / %s", row.get("title"), row.get("period"))
                continue
            if detail:
                try:
                    row = enrich_detail(session, row, timeout)
                except Exception as exc:
                    logger.warning("Yangju detail failed %s: %s", row.get("raw_url"), exc)
            if not include_expired and is_expired_course(row):
                expired_on_page += 1
                logger.info("Skipping expired Yangju course: %s / %s", row.get("title"), row.get("period"))
                continue
            rows.append(row)
            page_rows += 1
            if limit and len(rows) >= limit:
                return rows
        if not include_expired and expired_on_page == len(table.select("tbody tr")):
            break
        if page_rows == 0 and not include_expired:
            break
    return rows


def quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fields = ["title", "branch", "address", "period", "schedule_raw", "target", "fee", "status", "description", "image_url"]
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
    parser = argparse.ArgumentParser(description="Yangju lifelong learning crawler")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--include-expired", action="store_true")
    parser.add_argument("--no-detail", action="store_true")
    parser.add_argument("--mark-stale", action="store_true")
    parser.add_argument("--per-target-limit", type=int)
    parser.add_argument("--max-depth", type=int)
    parser.add_argument("--detail-limit", type=int)
    args = parser.parse_args()

    effective_limit = args.limit or args.per_target_limit
    started = datetime.now(timezone.utc)
    rows = collect(
        limit=effective_limit,
        max_pages=args.max_pages,
        timeout=args.timeout,
        include_expired=args.include_expired,
        detail=not args.no_detail,
    )
    saved = save_rows(rows) if args.save_db else 0
    if args.save_db and args.mark_stale and (not effective_limit or not rows):
        from DB.course_lifecycle import mark_stale_courses

        stale_count = mark_stale_courses(PROVIDER, started)
        logger.info("%s marked stale rows=%s", PROVIDER, stale_count)
    print_quality(rows)
    logger.info(
        "%s completed collected=%s saved=%s elapsed=%.1fs",
        PROVIDER,
        len(rows),
        saved,
        (datetime.now(timezone.utc) - started).total_seconds(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
