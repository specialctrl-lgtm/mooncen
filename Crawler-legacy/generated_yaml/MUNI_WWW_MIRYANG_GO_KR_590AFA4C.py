from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag


PROVIDER = "MUNI_WWW_MIRYANG_GO_KR_590AFA4C"
PROVIDER_NAME = "밀양시평생학습포털 진행중 강좌"
BASE_URL = "https://miryang.go.kr"
LIST_URL = "https://miryang.go.kr/edu/nmprogram/curriculum/default.php?st=e"
DEFAULT_BRANCH = "밀양시 미래교육과"
DEFAULT_ADDRESS = "경상남도 밀양시 중앙로 265"
DEFAULT_PHONE = "055-359-6005"


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from DB.db_utils import get_db_cursor  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, extract_krw_amount, setup_logger  # noqa: E402


logger = setup_logger("Crawler_MiryangLifelongCurrent")


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


def normalize_date_range(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return ""
    text = re.sub(
        r"\b(\d{4})[.](\d{1,2})[.](\d{1,2})\b",
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


def page_url(page: int) -> str:
    parsed = urlparse(LIST_URL)
    query = parse_qs(parsed.query)
    query["page"] = [str(page)]
    return parsed._replace(query=urlencode(query, doseq=True)).geturl()


def stable_id(*parts: Any) -> str:
    seed = "|".join(normalize_space(part) for part in parts if normalize_space(part))
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def branch_code(name: Any) -> str:
    return stable_id(PROVIDER, normalize_space(name) or DEFAULT_BRANCH)[:12]


def absolute_url(base: str, href: Any) -> str:
    text = normalize_space(href)
    return urljoin(base, text) if text else ""


def split_list_periods(value: Any) -> tuple[str, str]:
    text = normalize_space(value)
    apply_match = re.search(r"접수기간\s*(.*?)(?=\s*교육기간|$)", text)
    course_match = re.search(r"교육기간\s*(.*)$", text)
    return (
        normalize_date_range(apply_match.group(1)) if apply_match else "",
        normalize_date_range(course_match.group(1)) if course_match else "",
    )


def parse_capacity(value: Any) -> tuple[int | None, int | None, int | None]:
    text = normalize_space(value)
    match = re.search(r"정원\s*[:：]?\s*(\d+)\s*\(\s*\+?\s*(\d+)\s*\)\s*명?\s*/?\s*신청\s*(\d+)", text)
    if match:
        return int(match.group(3)), int(match.group(1)), int(match.group(2))
    match = re.search(r"정원\s*[:：]?\s*(\d+)", text)
    return (None, int(match.group(1)), None) if match else (None, None, None)


def normalize_status(value: Any) -> str:
    text = normalize_space(value)
    if any(token in text for token in ["신청가능", "접수중", "진행중"]):
        return "OPEN"
    if any(token in text for token in ["신청완료", "접수완료", "마감"]):
        return "CLOSED"
    if any(token in text for token in ["예정", "대기"]):
        return "SCHEDULED"
    return text


def normalize_fee(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return ""
    if "Warning" in text or "number_format" in text:
        return ""
    if "무료" in text:
        return "무료"
    amount = re.search(r"[\d,]+\s*원", text)
    return normalize_space(amount.group(0)) if amount else text


def table_pairs(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for tr in soup.select("table tr"):
        cells = [cell for cell in tr.find_all(["th", "td"], recursive=False)]
        i = 0
        while i < len(cells):
            if cells[i].name != "th":
                i += 1
                continue
            key = normalize_space(cells[i].get_text(" ", strip=True))
            value = ""
            if i + 1 < len(cells) and cells[i + 1].name == "td":
                value = normalize_space(cells[i + 1].get_text(" ", strip=True))
                i += 2
            else:
                i += 1
            if key:
                pairs[key] = value
    return pairs


def detail_description(pairs: dict[str, str]) -> str:
    parts = []
    for key in ["교육과정", "수강안내", "기타사항"]:
        value = normalize_space(pairs.get(key))
        if value:
            parts.append(f"{key}: {value}")
    return normalize_space(" ".join(parts))


def parse_list_row(row: Tag, current_url: str) -> dict[str, Any] | None:
    cells = row.find_all("td", recursive=False)
    if len(cells) < 5:
        return None
    link = row.select_one('a[href*="mod=o"]')
    title = normalize_space(link.get_text(" ", strip=True) if link else "")
    if not title:
        return None
    raw_url = absolute_url(current_url, link.get("href") if link else "")
    idx_match = re.search(r"[?&]idx=(\d+)", raw_url)
    external_id = idx_match.group(1) if idx_match else stable_id(raw_url, title)
    info = normalize_space(cells[2].get_text(" ", strip=True))
    branch_match = re.search(r"기관\s*[:：]\s*(.*?)(?=\s*정원\s*[:：]|$)", info)
    capacity_current, capacity_total, waitlist_total = parse_capacity(info)
    apply_period, period = split_list_periods(cells[3].get_text(" ", strip=True))
    branch = normalize_space(branch_match.group(1)) if branch_match else DEFAULT_BRANCH
    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": external_id,
        "provider_course_id": external_id,
        "title": title,
        "branch": branch or DEFAULT_BRANCH,
        "branch_code": branch_code(branch or DEFAULT_BRANCH),
        "address": DEFAULT_ADDRESS,
        "period": period,
        "schedule_raw": period,
        "target": "",
        "fee": "",
        "status": normalize_status(cells[4].get_text(" ", strip=True)),
        "description": "",
        "image_url": "",
        "raw_url": raw_url,
        "category": normalize_space(cells[1].get_text(" ", strip=True)),
        "venue_name": "",
        "venue_address": "",
        "room": "",
        "reception_period": apply_period,
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "waitlist_total": waitlist_total,
        "raw_fields": {
            "parser": "miryang_lifelong_current_list",
            "list_cells": [normalize_space(cell.get_text(" ", strip=True)) for cell in cells],
        },
    }


def is_expired_course(row: dict[str, Any]) -> bool:
    _, end_date = parse_date_range(row.get("period"))
    return bool(end_date and end_date < datetime.now().date())


def extract_labeled_from_description(text: str, label: str) -> str:
    normalized_label = r"\s*".join(re.escape(ch) for ch in label)
    pattern = rf"{normalized_label}\s*[:：]\s*(.*?)(?=\s*○\s*[가-힣A-Za-z ]+\s*[:：]|$)"
    match = re.search(pattern, text)
    return normalize_space(match.group(1)) if match else ""


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    soup = fetch_soup(session, row["raw_url"], timeout)
    pairs = table_pairs(soup)
    title_node = next(
        (node for node in soup.select("h2") if normalize_space(node.get_text(" ", strip=True)).startswith("강좌명")),
        None,
    )
    if title_node:
        title = re.sub(r"^\s*강좌명\s*[:：]\s*", "", normalize_space(title_node.get_text(" ", strip=True)))
        row["title"] = title or row["title"]
    branch = normalize_space(pairs.get("기관")) or row["branch"]
    row["branch"] = branch or DEFAULT_BRANCH
    row["branch_code"] = branch_code(row["branch"])
    row["address"] = DEFAULT_ADDRESS
    row["reception_period"] = normalize_date_range(pairs.get("접수기간")) or row["reception_period"]
    row["period"] = normalize_date_range(pairs.get("교육기간")) or row["period"]
    row["schedule_raw"] = normalize_space(pairs.get("교육시간")) or row["schedule_raw"]
    row["fee"] = normalize_fee(pairs.get("수강료")) or row["fee"]
    row["target"] = normalize_space(pairs.get("모집대상")) or row["target"]
    row["description"] = detail_description(pairs)
    description = normalize_space(pairs.get("교육과정"))
    venue = extract_labeled_from_description(description, "교육장소")
    if venue:
        row["venue_name"] = venue
        row["room"] = venue
    target_from_desc = extract_labeled_from_description(description, "교육대상")
    if target_from_desc:
        row["target"] = target_from_desc
    fee_from_desc = extract_labeled_from_description(description, "수강료")
    if fee_from_desc and not row["fee"]:
        row["fee"] = normalize_fee(fee_from_desc)
    schedule_from_desc = extract_labeled_from_description(description, "교육기간")
    if schedule_from_desc and not row["schedule_raw"]:
        row["schedule_raw"] = schedule_from_desc
    phone_match = re.search(r"0\d{1,2}-\d{3,4}-\d{4}", description)
    row["phone"] = phone_match.group(0) if phone_match else DEFAULT_PHONE
    row["material_fee"] = extract_krw_amount(pairs.get("준비물"))
    row["raw_fields"]["detail_pairs"] = pairs
    row["raw_fields"]["detail_parser"] = "miryang_lifelong_current_detail"
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
        table = soup.select_one("table.basic_edu")
        if not table:
            break
        page_rows = 0
        for tr in table.select("tbody tr"):
            row = parse_list_row(tr, current_url)
            if not row or row["provider_course_id"] in seen:
                continue
            seen.add(row["provider_course_id"])
            if not include_expired and is_expired_course(row):
                logger.info("Skipping expired Miryang course: %s / %s", row.get("title"), row.get("period"))
                continue
            if detail:
                try:
                    row = enrich_detail(session, row, timeout)
                except Exception as exc:
                    logger.warning("Miryang detail failed %s: %s", row.get("raw_url"), exc)
            if not include_expired and is_expired_course(row):
                logger.info("Skipping expired Miryang course: %s / %s", row.get("title"), row.get("period"))
                continue
            rows.append(row)
            page_rows += 1
            if limit and len(rows) >= limit:
                return rows
        if page_rows == 0 or len(table.select("tbody tr")) == 0:
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
        "address_source": "crawler_static",
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
    parser = argparse.ArgumentParser(description="Miryang lifelong current course crawler")
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
    started = datetime.now()
    rows = collect(
        limit=effective_limit,
        max_pages=args.max_pages,
        timeout=args.timeout,
        include_expired=args.include_expired,
        detail=not args.no_detail,
    )
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
