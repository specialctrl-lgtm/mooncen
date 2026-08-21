from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag


PROVIDER = "MUNI_WWW_DANGJIN_GO_KR_3C378AA6"
PROVIDER_NAME = "당진시청 시민정보화교육"
BASE_URL = "https://www.dangjin.go.kr"
LIST_URL = f"{BASE_URL}/prog/reprsntInfrmEdu/kor/sub05_07_01/list.do"
DEFAULT_BRANCH = "당진시청 시민정보화교육"
DEFAULT_ADDRESS = "충청남도 당진시 시청1로 1"

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, setup_logger  # noqa: E402


logger = setup_logger("Crawler_DangjinInfoEdu")


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
        r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일",
        lambda m: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}",
        text,
    )
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


def parse_schedule_seq(url: str) -> str:
    parsed = urlparse(url)
    return parse_qs(parsed.query).get("schedule_seq", [""])[0]


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


def parse_basic_table(table: Tag | None) -> dict[str, str]:
    data: dict[str, str] = {}
    if not table:
        return data
    for tr in table.select("tr"):
        cells = tr.select("th,td")
        idx = 0
        while idx < len(cells):
            key = normalize_space(cells[idx].get_text(" ", strip=True))
            value = normalize_space(cells[idx + 1].get_text(" ", strip=True)) if idx + 1 < len(cells) else ""
            if key and value:
                data[key] = value
            idx += 2
    return data


def parse_list_row(tr: Tag) -> dict[str, Any] | None:
    cells = tr.select("td")
    if len(cells) < 6:
        return None
    link = cells[1].select_one("a[href]")
    title = normalize_space(link.get_text(" ", strip=True) if link else cells[1].get_text(" ", strip=True))
    raw_url = urljoin(LIST_URL, link.get("href")) if link else ""
    if not title or not raw_url:
        return None

    period = normalize_period(cells[2].get_text(" ", strip=True))
    schedule_raw = normalize_space(cells[3].get_text(" ", strip=True))
    reception = normalize_period(cells[4].get_text(" ", strip=True))
    capacity = normalize_space(cells[5].get_text(" ", strip=True))
    status = normalize_space(cells[6].get_text(" ", strip=True)) if len(cells) > 6 else ""
    row = {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": parse_schedule_seq(raw_url),
        "course_id": "",
        "title": title,
        "branch": DEFAULT_BRANCH,
        "branch_code": "DANGJIN_CITY_HALL",
        "address": DEFAULT_ADDRESS,
        "period": period,
        "schedule_raw": schedule_raw,
        "target": "당진시민",
        "fee": "",
        "status": status,
        "description": "",
        "image_url": "",
        "raw_url": raw_url,
        "category": "정보화교육",
        "collection_category": "평생학습",
        "domain_category": "평생학습",
        "operator_type": "지자체/공공기관",
        "reception_period": reception,
        "capacity_text": capacity,
    }
    row["course_id"] = stable_course_id(row)
    return row


def parse_list(soup: BeautifulSoup) -> list[dict[str, Any]]:
    table = soup.select_one("table.tbl_basic.center")
    if not table:
        return []
    rows = []
    for tr in table.select("tbody tr"):
        row = parse_list_row(tr)
        if row:
            rows.append(row)
    return rows


def first_heading_text(soup: BeautifulSoup) -> str:
    for selector in ["#content h3", "#content h4", ".contents h3", ".substance h3"]:
        node = soup.select_one(selector)
        text = normalize_space(node.get_text(" ", strip=True) if node else "")
        if text and "시민정보화교육" not in text:
            return text
    return ""


def extract_material_note(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return ""
    parts = re.split(r"(?=◆|※|<)", text)
    return normalize_space(" ".join(part for part in parts if re.search(r"준\s*비\s*물|재료|교재|준비", part)))


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    if not row.get("raw_url"):
        return row

    soup = fetch_soup(session, row["raw_url"], timeout)
    title = first_heading_text(soup)
    if title:
        row["title"] = title

    data = parse_basic_table(soup.select_one("table.basic_table"))
    row["target"] = data.get("교육대상") or row.get("target") or "당진시민"
    row["period"] = normalize_period(data.get("교육기간") or row.get("period"))
    row["schedule_raw"] = normalize_space(data.get("교육시간") or row.get("schedule_raw"))
    row["branch"] = normalize_space(data.get("교육장소") or row.get("branch") or DEFAULT_BRANCH)
    row["address"] = DEFAULT_ADDRESS
    row["fee"] = normalize_space(data.get("교육비") or row.get("fee"))
    row["description"] = normalize_space(data.get("기타") or row.get("description"))
    row["phone"] = normalize_space(data.get("전화문의"))
    row["capacity_text"] = normalize_space(data.get("교육인원") or row.get("capacity_text"))
    row["material_note"] = extract_material_note(row.get("description"))
    row["course_id"] = stable_course_id(row)
    return row


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
        url = LIST_URL if page == 1 else f"{LIST_URL}?pageIndex={page}"
        soup = fetch_soup(session, url, timeout)
        page_rows = parse_list(soup)
        if not page_rows:
            break
        expired_on_page = 0
        for row in page_rows:
            key = normalize_space(row.get("external_id")) or row["raw_url"]
            if key in seen:
                continue
            seen.add(key)
            row = enrich_detail(session, row, timeout)
            if not include_expired and is_expired_course(row):
                expired_on_page += 1
                logger.info("Skipping expired Dangjin course: %s / %s", row.get("title"), row.get("period"))
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
    parser = argparse.ArgumentParser(description="Dangjin citizen information education crawler")
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
    started = datetime.now(timezone.utc)
    rows = collect(
        limit=effective_limit,
        max_pages=args.max_pages,
        timeout=args.timeout,
        include_expired=args.include_expired,
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
