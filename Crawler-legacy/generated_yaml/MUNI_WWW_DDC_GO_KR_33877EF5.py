from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag


PROVIDER = "MUNI_WWW_DDC_GO_KR_33877EF5"
PROVIDER_NAME = "동두천미디어센터"
BASE_URL = "https://www.ddc.go.kr"
LIST_URL = f"{BASE_URL}/media/selectBbsNttList.do?bbsNo=201&key=2136"
DEFAULT_BRANCH = "동두천미디어센터"
DEFAULT_ADDRESS = "경기도 동두천시 동두천로 314"

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, setup_logger  # noqa: E402


logger = setup_logger("Crawler_DongducheonMedia")


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
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def detail_id(url: str) -> str:
    parsed = urlparse(url)
    return parse_qs(parsed.query).get("nttNo", [""])[0]


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


def table_pairs(table: Tag | None) -> dict[str, str]:
    pairs: dict[str, str] = {}
    if not table:
        return pairs
    for tr in table.select("tr"):
        cells = tr.select("th,td")
        if len(cells) < 2:
            continue
        key = normalize_space(cells[0].get_text(" ", strip=True))
        value = normalize_space(cells[1].get_text(" ", strip=True))
        if key:
            pairs[key] = value
    return pairs


def parse_list_item(item: Tag) -> dict[str, Any] | None:
    link = item.select_one("a.post_box[href]")
    if not link:
        return None
    raw_url = urljoin(LIST_URL, link.get("href"))
    title = normalize_space(item.select_one(".subject").get_text(" ", strip=True) if item.select_one(".subject") else "")
    status = normalize_space(item.select_one(".magam_text").get_text(" ", strip=True) if item.select_one(".magam_text") else "")
    image = item.select_one("img.post_img")
    image_url = urljoin(LIST_URL, image.get("src")) if image and image.get("src") else ""
    if image and image.get("alt"):
        title = normalize_space(re.sub(r"\s*이미지\s*$", "", image.get("alt"))) or title
    row = {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": detail_id(raw_url),
        "course_id": "",
        "title": title,
        "branch": DEFAULT_BRANCH,
        "branch_code": "DONGDUCHEON_MEDIA_CENTER",
        "address": DEFAULT_ADDRESS,
        "period": "",
        "schedule_raw": "",
        "target": "시민",
        "fee": "",
        "status": status,
        "description": "",
        "image_url": image_url,
        "raw_url": raw_url,
        "category": "미디어교육",
        "collection_category": "평생학습",
        "domain_category": "평생학습",
        "operator_type": "지자체/공공기관",
    }
    row["course_id"] = stable_course_id(row)
    return row


def parse_list(soup: BeautifulSoup) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in soup.select("ul.guide_item_list li.guide_item"):
        row = parse_list_item(item)
        if row:
            rows.append(row)
    return rows


def title_from_caption(caption: str) -> str:
    text = normalize_space(caption)
    if " - " in text:
        return normalize_space(text.split(" - ", 1)[0])
    return text


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    soup = fetch_soup(session, row["raw_url"], timeout)
    table = soup.select_one("table.bbs_default.view")
    pairs = table_pairs(table)
    caption = normalize_space(table.find("caption").get_text(" ", strip=True) if table and table.find("caption") else "")
    if caption:
        row["title"] = title_from_caption(caption)
    row["reception_period"] = normalize_period(pairs.get("모집기간"))
    row["capacity_text"] = normalize_space(pairs.get("모집인원"))
    row["fee"] = normalize_fee(pairs.get("수강료"))
    row["period"] = normalize_period(pairs.get("교육기간"))
    row["schedule_raw"] = normalize_space(pairs.get("교육시간"))
    place = normalize_space(pairs.get("교육장소"))
    if place:
        row["branch"] = f"{DEFAULT_BRANCH} {place}"
        row["branch_code"] = hashlib.sha1(row["branch"].encode("utf-8")).hexdigest()[:12]
    row["description"] = description_text(soup)
    image = soup.select_one("#contents img[src*='/DATA/bbs/201/'], table + img, img[src*='/DATA/bbs/201/']")
    if image and image.get("src"):
        row["image_url"] = urljoin(row["raw_url"], image.get("src"))
    row["course_id"] = stable_course_id(row)
    return row


def normalize_fee(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return ""
    if text in {"0", "무료"}:
        return "무료"
    if "원" not in text:
        return f"{text}원"
    return text


def description_text(soup: BeautifulSoup) -> str:
    contents = soup.select_one("#contents")
    if not contents:
        return ""
    for node in contents.select("nav, .path, .btn, .pagination, .table.type2.bbs_default.view"):
        node.decompose()
    text = normalize_space(contents.get_text(" ", strip=True))
    text = re.sub(r"^.*?교육신청\s*", "", text)
    return text[:2000]


def is_expired_course(row: dict[str, Any]) -> bool:
    parsed = parse_date_range(row.get("period"))
    end_date = parsed[1] if parsed else None
    if not end_date:
        return False
    end_day = end_date.date() if hasattr(end_date, "date") else end_date
    return end_day < datetime.now().date()


def collect(
    limit: int | None = None,
    max_pages: int = 3,
    timeout: int = 20,
    include_expired: bool = False,
) -> list[dict[str, Any]]:
    session = make_session()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        url = f"{LIST_URL}&pageUnit=8&pageIndex={page}" if page > 1 else LIST_URL
        soup = fetch_soup(session, url, timeout)
        page_rows = parse_list(soup)
        if not page_rows:
            break
        expired_on_page = 0
        for row in page_rows:
            key = row.get("external_id") or row.get("raw_url")
            if key in seen:
                continue
            seen.add(key)
            row = enrich_detail(session, row, timeout)
            if not include_expired and is_expired_course(row):
                expired_on_page += 1
                logger.info("Skipping expired Dongducheon media course: %s / %s", row.get("title"), row.get("period"))
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


def save_rows(rows: list[dict[str, Any]]) -> int:
    crawler = YamlSourceCrawler.__new__(YamlSourceCrawler)
    crawler.provider = PROVIDER
    crawler.target_parser = TargetParser()
    crawler.schedule_parser = ScheduleParser()
    saved = 0
    branch_ids: dict[str, str] = {}
    for row in rows:
        branch_code = normalize_space(row.get("branch_code")) or hashlib.sha1(normalize_space(row.get("branch")).encode("utf-8")).hexdigest()[:12]
        branch_name = normalize_space(row.get("branch")) or DEFAULT_BRANCH
        if branch_code not in branch_ids:
            branch_ids[branch_code] = crawler.save_branch(branch_code, branch_name)
        course = crawler.normalize_course(row, branch_ids[branch_code])
        crawler.save_course(course)
        saved += 1
    logger.info("%s saved %s/%s rows.", PROVIDER, saved, len(rows))
    return saved


def main() -> int:
    parser = argparse.ArgumentParser(description="Dongducheon media education crawler")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-pages", type=int, default=3)
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
