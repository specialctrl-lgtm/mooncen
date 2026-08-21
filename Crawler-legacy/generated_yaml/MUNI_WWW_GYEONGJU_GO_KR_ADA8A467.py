from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag


PROVIDER = "MUNI_WWW_GYEONGJU_GO_KR_ADA8A467"
PROVIDER_NAME = "경주시 평생학습 강좌"
BASE_URL = "https://www.gyeongju.go.kr"
LIST_URL = f"{BASE_URL}/gjlll/main/lecture/index.do?menu_idx=126"
DEFAULT_BRANCH = "경주시평생학습가족관"
DEFAULT_ADDRESS = "경상북도 경주시 북성로 87"

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, setup_logger  # noqa: E402


logger = setup_logger("Crawler_GyeongjuLifelong")


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
    text = re.sub(r"\s*\(총\s*[^)]*\)", "", text)
    return normalize_space(text)


def fetch_soup(session: requests.Session, url: str, timeout: int) -> BeautifulSoup:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


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


def extract_title(link: Tag | None) -> str:
    if not link:
        return ""
    clone = BeautifulSoup(str(link), "html.parser")
    for code in clone.select(".bt_scls"):
        code.decompose()
    return normalize_space(clone.get_text(" ", strip=True))


def extract_lect_no(link: Tag | None) -> str:
    if not link:
        return ""
    onclick = link.get("onclick") or link.get("onClick") or ""
    match = re.search(r"viewLecture\(['\"]?(\d+)['\"]?\)", onclick)
    return match.group(1) if match else ""


def li_pairs(node: Tag | None) -> dict[str, str]:
    pairs: dict[str, str] = {}
    if not node:
        return pairs
    for li in node.select("li"):
        key_node = li.select_one(".col.tit")
        value_node = li.select_one(".col.cont")
        if not key_node or not value_node:
            continue
        for button in value_node.select("button"):
            button.decompose()
        key = normalize_space(key_node.get_text(" ", strip=True))
        value = normalize_space(value_node.get_text(" ", strip=True))
        if key:
            pairs[key] = value
    return pairs


def parse_list_row(tr: Tag) -> dict[str, Any] | None:
    cells = tr.select("td")
    if len(cells) < 4:
        return None
    link = cells[1].select_one("a.tit")
    lect_no = extract_lect_no(link)
    title = extract_title(link)
    if not lect_no or not title:
        return None
    info = li_pairs(cells[1])
    period_text = normalize_space(cells[2].get_text(" ", strip=True))
    reception_match = re.search(r"신청기간\s*(.+?)\s*교육기간", period_text)
    period_match = re.search(r"교육기간\s*(.+)$", period_text)
    weekdays = info.get("교육 요일", "")
    time = info.get("교육 시간", "")
    schedule_raw = normalize_space(" ".join(part for part in [weekdays, time] if part))
    status = normalize_space(cells[3].get_text(" ", strip=True))
    raw_url = f"{BASE_URL}/gjlll/main/lecture/view.do?lect_no={lect_no}&menu_idx=126"
    row = {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": lect_no,
        "course_id": "",
        "title": title,
        "branch": info.get("교육기관") or DEFAULT_BRANCH,
        "branch_code": hashlib.sha1((info.get("교육기관") or DEFAULT_BRANCH).encode("utf-8")).hexdigest()[:12],
        "address": DEFAULT_ADDRESS,
        "period": normalize_period(period_match.group(1) if period_match else ""),
        "schedule_raw": schedule_raw,
        "target": "",
        "fee": info.get("수강료", ""),
        "status": status,
        "description": "",
        "image_url": "",
        "raw_url": raw_url,
        "category": "",
        "collection_category": "평생학습",
        "domain_category": "평생학습",
        "operator_type": "지자체/공공기관",
        "reception_period": normalize_period(reception_match.group(1) if reception_match else ""),
    }
    row["course_id"] = stable_course_id(row)
    return row


def parse_list(soup: BeautifulSoup) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    table = soup.select_one("table.apply_list_tbl")
    if not table:
        return rows
    for tr in table.select("tbody tr"):
        row = parse_list_row(tr)
        if row:
            rows.append(row)
    return rows


def detail_title(soup: BeautifulSoup) -> str:
    node = soup.select_one(".view_tit_box .col.tit")
    return extract_title(node) if node else ""


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    soup = fetch_soup(session, row["raw_url"], timeout)
    title = detail_title(soup)
    if title:
        row["title"] = title

    intro = li_pairs(soup.select_one("#clsdtl-intro"))
    detail = li_pairs(soup.select_one("#clsdtl-lecturer"))
    review = li_pairs(soup.select_one("#clsdtl-review"))
    guide = li_pairs(soup.select_one("#clsdtl-guide"))

    row["branch"] = detail.get("교육기관") or intro.get("교육기관") or row.get("branch") or DEFAULT_BRANCH
    row["branch_code"] = hashlib.sha1(normalize_space(row["branch"]).encode("utf-8")).hexdigest()[:12]
    row["category"] = detail.get("강좌분류") or row.get("category")
    row["period"] = normalize_period(detail.get("교육 기간") or row.get("period"))
    weekdays = detail.get("교육 요일", "")
    time = detail.get("교육 시간", "")
    row["schedule_raw"] = normalize_space(" ".join(part for part in [weekdays, time] if part)) or row.get("schedule_raw")
    row["fee"] = detail.get("수강료") or row.get("fee")
    row["material_fee"] = detail.get("재료비", "")
    row["target"] = detail.get("교육대상") or row.get("target")
    row["address"] = extract_address(detail.get("교육장소")) or row.get("address") or DEFAULT_ADDRESS
    row["instructor"] = detail.get("강사", "")
    row["phone"] = detail.get("문의전화", "")
    row["reception_period"] = normalize_period(intro.get("신청 기간 (인터넷접수)") or row.get("reception_period"))

    description_parts = []
    for key in ["강의목표", "강좌개요", "강의교재", "강좌안내"]:
        if review.get(key):
            description_parts.append(f"{key}: {review[key]}")
    for key in ["기타 안내", "유의사항"]:
        if guide.get(key):
            description_parts.append(f"{key}: {guide[key]}")
    row["description"] = normalize_space(" ".join(description_parts))
    row["material_note"] = normalize_space(" ".join(part for part in [row.get("material_fee"), review.get("강의교재")] if part))
    row["course_id"] = stable_course_id(row)
    return row


def extract_address(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return ""
    text = re.sub(r"\s*지도보기\s*$", "", text)
    match = re.search(r"(.+?(?:로|길)\s*\d+(?:-\d+)?)", text)
    return normalize_space(match.group(1) if match else text)


def is_expired_course(row: dict[str, Any]) -> bool:
    parsed = parse_date_range(row.get("period"))
    end_date = parsed[1] if parsed else None
    if not end_date:
        return False
    end_day = end_date.date() if hasattr(end_date, "date") else end_date
    return end_day < datetime.now().date()


def collect(
    limit: int | None = None,
    max_pages: int = 1,
    timeout: int = 20,
    include_expired: bool = False,
) -> list[dict[str, Any]]:
    session = make_session()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        # The site uses POST for pagination. Page 1 is enough for manual 10-row validation
        # and avoids broad crawling before a provider-specific paging contract is needed.
        if page > 1:
            break
        soup = fetch_soup(session, LIST_URL, timeout)
        page_rows = parse_list(soup)
        if not page_rows:
            break
        for row in page_rows:
            key = normalize_space(row.get("external_id")) or row["raw_url"]
            if key in seen:
                continue
            seen.add(key)
            row = enrich_detail(session, row, timeout)
            if not include_expired and is_expired_course(row):
                logger.info("Skipping expired Gyeongju course: %s / %s", row.get("title"), row.get("period"))
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
    parser = argparse.ArgumentParser(description="Gyeongju lifelong learning lecture crawler")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-pages", type=int, default=1)
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
    rows = collect(
        limit=effective_limit,
        max_pages=args.max_pages,
        timeout=args.timeout,
        include_expired=args.include_expired,
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
