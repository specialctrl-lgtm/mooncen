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


PROVIDER = "MUNI_EDUCITY_GEOCHANG_GO_KR_3187BF2A"
PROVIDER_NAME = "거창군평생학습센터"
BASE_URL = "https://educity.geochang.go.kr"
LIST_PATH = "/E0003/30020201.asp"
LIST_URL = f"{BASE_URL}{LIST_PATH}"
DEFAULT_BRANCH = "거창군평생학습센터"
DEFAULT_ADDRESS = "경상남도 거창군 거창읍 중앙로 103"


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from DB.db_utils import get_db_cursor  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, setup_logger  # noqa: E402


logger = setup_logger("Crawler_GeochangEducity")


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
            "Referer": LIST_URL,
        }
    )
    return session


def normalize_space(value: Any) -> str:
    text = clean_text(value).replace("\xa0", " ")
    return clean_text(re.sub(r"\s+", " ", text))


def normalize_period(value: Any) -> str:
    text = normalize_space(value)
    text = re.sub(
        r"(\d{4})[.](\d{1,2})[.](\d{1,2})",
        lambda m: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}",
        text,
    )
    text = re.sub(r"\s*~\s*", " ~ ", text)
    return normalize_space(text)


def fetch_soup(session: requests.Session, url: str, timeout: int) -> BeautifulSoup:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def absolute_url(value: Any, base: str = BASE_URL) -> str:
    text = normalize_space(value)
    return urljoin(base, text) if text else ""


def li_pairs(container: Tag) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for item in container.select("li"):
        key_node = item.select_one("b, span")
        if not key_node:
            continue
        key = normalize_space(key_node.get_text(" ", strip=True))
        value_node = item.select_one("span") if key_node.name == "b" else None
        if value_node and value_node is not key_node:
            value = normalize_space(value_node.get_text(" ", strip=True))
        else:
            key_node.extract()
            value = normalize_space(item.get_text(" ", strip=True))
        if key:
            pairs[key] = value
    return pairs


def table_pairs(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for tr in soup.select("table.basic_tbl01 tr"):
        key = normalize_space(tr.select_one("th").get_text(" ", strip=True) if tr.select_one("th") else "")
        value = normalize_space(tr.select_one("td").get_text(" ", strip=True) if tr.select_one("td") else "")
        if key:
            pairs[key] = value
    return pairs


def split_organ(value: str) -> tuple[str, str]:
    text = normalize_space(value)
    phone_match = re.search(r"([0-9]{2,3}-[0-9]{3,4}-[0-9]{4})", text)
    phone = phone_match.group(1) if phone_match else ""
    name = re.sub(r"\(.*?\)", "", text).strip()
    return normalize_space(name) or DEFAULT_BRANCH, phone


def normalize_fee(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return ""
    if "무료" in text or text == "0":
        return "무료"
    amount = re.search(r"[\d,]+\s*원?", text)
    if amount:
        cleaned = amount.group(0).strip()
        return cleaned if "원" in cleaned else f"{cleaned}원"
    return text


def extract_lc(url: str) -> str:
    match = re.search(r"[?&]lc=([^&#]+)", url)
    return match.group(1) if match else ""


def parse_list_card(card: Tag) -> dict[str, Any] | None:
    link = card.select_one("a[href*='30020203.asp'][href*='lc=']")
    if not link:
        return None
    href = link.get("href", "")
    external_id = extract_lc(href)
    title_node = card.select_one(".lec_title")
    status_node = title_node.select_one("span") if title_node else None
    status = normalize_space(status_node.get_text(" ", strip=True) if status_node else "")
    title = normalize_space(title_node.get_text(" ", strip=True) if title_node else link.get_text(" ", strip=True))
    if status and title.startswith(status):
        title = normalize_space(title[len(status) :])
    if not title or not external_id:
        return None

    pairs = li_pairs(card.select_one(".lec_left_wrap") or card)
    image = card.select_one(".lec_left_img img")
    target_capacity = normalize_space(pairs.get("대상"))
    target = target_capacity.split("/", 1)[0].strip() if "/" in target_capacity else target_capacity
    capacity = target_capacity.split("/", 1)[1].strip() if "/" in target_capacity else ""
    raw_url = absolute_url(href, LIST_URL)
    row = {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": external_id,
        "provider_course_id": external_id,
        "title": title,
        "branch": DEFAULT_BRANCH,
        "branch_code": branch_code(DEFAULT_BRANCH),
        "address": DEFAULT_ADDRESS,
        "phone": normalize_space(pairs.get("문의")),
        "period": normalize_period(pairs.get("교육")),
        "schedule_raw": normalize_space(pairs.get("교육")),
        "target": target,
        "capacity_text": capacity,
        "fee": "",
        "status": status,
        "reception_period": normalize_period(pairs.get("접수")),
        "venue_name": normalize_space(pairs.get("장소")),
        "room": normalize_space(pairs.get("장소")),
        "raw_url": raw_url,
        "application_url": raw_url,
        "image_url": absolute_url(image.get("src")) if image else "",
        "description": "",
        "category": "평생학습강좌",
        "collection_category": "평생학습",
        "domain_category": "평생학습",
        "source_group": "municipal_lifelong_learning",
        "operator_type": "지자체/공공기관",
        "program_type": "강좌",
    }
    row["course_id"] = stable_course_id(row)
    return row


def parse_detail_summary(soup: BeautifulSoup) -> dict[str, str]:
    summary = soup.select_one(".sub0202_view_wrap .con01 .txt")
    return li_pairs(summary) if summary else {}


def description_from_detail(pairs: dict[str, str]) -> str:
    parts: list[str] = []
    for key in ["학습 목표", "학습 계획", "개인준비물"]:
        value = normalize_space(pairs.get(key))
        if value and value != ".":
            parts.append(f"{key}: {value}")
    return normalize_space(" ".join(parts))


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    soup = fetch_soup(session, row["raw_url"], timeout)
    title_node = soup.select_one(".sub0202_view_wrap .tit em")
    if title_node:
        row["title"] = normalize_space(title_node.get_text(" ", strip=True)) or row.get("title")
    detail_pairs = parse_detail_summary(soup)
    table = table_pairs(soup)

    branch, phone = split_organ(detail_pairs.get("기관", ""))
    row["branch"] = branch or row.get("branch")
    row["branch_code"] = branch_code(row["branch"])
    row["phone"] = phone or row.get("phone")
    row["reception_period"] = normalize_period(detail_pairs.get("접수") or row.get("reception_period"))

    schedule = normalize_space(detail_pairs.get("일정"))
    if schedule:
        date_part = schedule.split("/", 1)[0].strip()
        row["period"] = normalize_period(date_part)
        row["schedule_raw"] = schedule
    target_capacity = normalize_space(detail_pairs.get("대상"))
    if target_capacity:
        row["target"] = target_capacity.split("/", 1)[0].strip()
        if "/" in target_capacity:
            row["capacity_text"] = target_capacity.split("/", 1)[1].strip()
    row["venue_name"] = normalize_space(detail_pairs.get("장소") or row.get("venue_name"))
    row["room"] = row["venue_name"]
    row["instructor"] = normalize_space(table.get("강 사 명"))
    row["fee"] = normalize_fee(table.get("수 강 료") or row.get("fee"))
    row["capacity_text"] = normalize_space(table.get("교육정원") or row.get("capacity_text"))
    row["application_method"] = normalize_space(table.get("접수방법"))
    row["material_note"] = normalize_space(table.get("개인준비물"))
    row["description"] = description_from_detail(table)
    image = soup.select_one(".sub0202_view_wrap .img_wrap img")
    if image:
        row["image_url"] = absolute_url(image.get("src")) or row.get("image_url")
    row["raw_fields"] = {"detail_summary": detail_pairs, "detail_table": table, "parser": "geochang_educity_card_detail"}
    row["course_id"] = stable_course_id(row)
    return row


def parse_list(soup: BeautifulSoup) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for card in soup.select(".listover"):
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
        url = LIST_URL if page == 1 else f"{LIST_URL}?{urlencode({'page': page, 'lc': '', 'search_date': ''})}"
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
                logger.warning("Geochang detail failed %s: %s", row.get("raw_url"), exc)
            if not include_expired and is_expired_course(row):
                expired_on_page += 1
                logger.info("Skipping expired Geochang course: %s / %s", row.get("title"), row.get("period"))
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
        "address_source": "crawler_default",
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
    parser = argparse.ArgumentParser(description="Geochang educity lifelong course crawler")
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
