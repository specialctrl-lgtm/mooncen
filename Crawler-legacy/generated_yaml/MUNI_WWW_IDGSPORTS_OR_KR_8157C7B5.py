from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup, Tag


PROVIDER = "MUNI_WWW_IDGSPORTS_OR_KR_8157C7B5"
PROVIDER_NAME = "인천광역시 동구체육회 프로그램예약"
BASE_URL = "https://www.idgsports.or.kr"
LIST_PATH = "/program/programInfoList.do"
DETAIL_PATH = "/main/program/programInfoDetail.do"
PROGRAM_DIV = "pingpong"
LIST_URL = f"{BASE_URL}{LIST_PATH}?{urlencode({'prgmdiv': PROGRAM_DIV})}"
DEFAULT_BRANCH = "인천광역시 동구체육회"
DEFAULT_ADDRESS = "인천광역시 동구"
DEFAULT_PHONE = "032-766-1998"

VENUE_ADDRESS_MAP = {
    "송림골꿈드림센터": "인천광역시 동구 새천년로 93",
    "동구문화체육센터": "인천광역시 동구 송림로110번길 5-8",
    "송림체육관": "인천광역시 동구 염전로 30",
}


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from DB.db_utils import get_db_cursor  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, extract_krw_amount, extract_material_fee_amount, setup_logger  # noqa: E402


logger = setup_logger("Crawler_IdgSports")


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
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


def stable_id(*parts: Any) -> str:
    seed = "|".join(normalize_space(part) for part in parts if normalize_space(part))
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def branch_code(branch: Any) -> str:
    return stable_id(PROVIDER, normalize_space(branch) or DEFAULT_BRANCH)[:12]


def list_url(page: int = 1) -> str:
    query = {"prgmdiv": PROGRAM_DIV}
    if page > 1:
        query["pgno"] = str(page)
    return f"{BASE_URL}{LIST_PATH}?{urlencode(query)}"


def detail_url(program_seq: str) -> str:
    return f"{BASE_URL}{DETAIL_PATH}?{urlencode({'prgm_seq': program_seq, 'prgmdiv': PROGRAM_DIV})}"


def fetch_soup(session: requests.Session, url: str, timeout: int) -> BeautifulSoup:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def normalize_date_text(value: Any) -> str:
    text = normalize_space(value)
    text = re.sub(
        r"(\d{4})[.](\d{1,2})[.](\d{1,2})",
        lambda m: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}",
        text,
    )
    text = re.sub(r"(\d{4})-(\d{1,2})-(\d{1,2})", lambda m: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}", text)
    text = re.sub(r"(\d{1,2})시\s*(\d{1,2})분", lambda m: f"{int(m.group(1)):02d}:{int(m.group(2)):02d}", text)
    text = re.sub(r"\s*[~∼]\s*", " ~ ", text)
    return normalize_space(text)


def first_amount_fee(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return ""
    if "무료" in text:
        return "무료"
    amount = extract_krw_amount(text)
    if amount is not None:
        return f"{amount:,}원"
    return text


def normalize_status(value: Any) -> str:
    text = normalize_space(value)
    if any(token in text for token in ["접수마감", "마감", "종료", "폐강", "취소"]):
        return "CLOSED"
    if any(token in text for token in ["접수대기", "예정"]):
        return "SCHEDULED"
    if any(token in text for token in ["접수중", "신청", "예약"]):
        return "OPEN"
    return text or "OPEN"


def extract_program_seq(value: Any) -> str:
    match = re.search(r"prgm_seq=(\d+)", normalize_space(value))
    return match.group(1) if match else ""


def extract_info(card: Tag) -> dict[str, str]:
    info: dict[str, str] = {}
    for item in card.select(".lec_info li"):
        text = normalize_space(item.get_text(" ", strip=True))
        if ":" not in text:
            continue
        key, value = text.split(":", 1)
        info[normalize_space(key)] = normalize_space(value)
    return info


def parse_capacity(value: Any) -> tuple[int | None, int | None, int | None]:
    text = normalize_space(value)
    match = re.search(r"(\d+)\s*명(?:\s*\(대기\s*(\d+)\s*명\))?", text)
    if match:
        return None, int(match.group(1)), int(match.group(2)) if match.group(2) else None
    nums = [int(num) for num in re.findall(r"\d+", text)]
    if nums:
        return None, nums[0], nums[1] if len(nums) > 1 else None
    return None, None, None


def detail_pairs(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for dl in soup.select("dl"):
        cells = dl.find_all(["dt", "dd"], recursive=False)
        if len(cells) < 2:
            continue
        key = normalize_space(cells[0].get_text(" ", strip=True))
        value = normalize_space(cells[1].get_text(" ", strip=True))
        if key:
            pairs[key] = value
    return pairs


def detail_description(soup: BeautifulSoup) -> str:
    node = soup.select_one(".edu_view")
    if not node:
        return ""
    text = normalize_space(node.get_text(" ", strip=True))
    return text


def image_url_from(node: Tag | BeautifulSoup) -> str:
    image = node.select_one(".img img[src], img[src*='/images/program/']")
    return urljoin(BASE_URL, image.get("src")) if image and image.get("src") else ""


def branch_from_room(room: str) -> str:
    text = normalize_space(room)
    for venue in VENUE_ADDRESS_MAP:
        if venue in text:
            return venue
    return DEFAULT_BRANCH


def address_from_room(room: str) -> str:
    text = normalize_space(room)
    for venue, address in VENUE_ADDRESS_MAP.items():
        if venue in text:
            return address
    return DEFAULT_ADDRESS


def parse_list_card(card: Tag, page: int) -> dict[str, Any] | None:
    onclick_node = card.select_one("[onclick*='programInfoDetail.do']")
    program_seq = extract_program_seq(onclick_node.get("onclick") if onclick_node else "")
    title = normalize_space(card.select_one(".tit").get_text(" ", strip=True) if card.select_one(".tit") else "")
    if not program_seq or not title:
        return None
    info = extract_info(card)
    status_text = normalize_space(card.select_one(".tag_state").get_text(" ", strip=True) if card.select_one(".tag_state") else "")
    category = normalize_space(card.select_one(".spot").get_text(" ", strip=True) if card.select_one(".spot") else "").strip("[]")
    time_tag = normalize_space(card.select_one(".tag_cate").get_text(" ", strip=True) if card.select_one(".tag_cate") else "")
    period = normalize_date_text(info.get("교육기간"))
    apply_period = normalize_date_text(info.get("신청기간"))
    fee = first_amount_fee(info.get("수 강 료"))
    current, total, waitlist = parse_capacity(info.get("정원"))
    raw_url = detail_url(program_seq)
    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": program_seq,
        "provider_course_id": program_seq,
        "title": title,
        "branch": DEFAULT_BRANCH,
        "branch_code": branch_code(DEFAULT_BRANCH),
        "address": DEFAULT_ADDRESS,
        "phone": DEFAULT_PHONE,
        "period": period,
        "schedule_raw": period,
        "target": normalize_space(info.get("대상")),
        "age_group": "ADULT" if "성인" in normalize_space(info.get("대상")) else "",
        "category_raw": category or PROGRAM_DIV,
        "fee": fee,
        "material_fee": "",
        "material_note": "",
        "status": normalize_status(status_text),
        "raw_url": raw_url,
        "application_url": raw_url,
        "application_type": "ONLINE",
        "description": normalize_space(card.get_text(" ", strip=True)),
        "image_url": image_url_from(card),
        "instructor": "",
        "capacity_current": current,
        "capacity_total": total,
        "waitlist_total": waitlist,
        "apply_period": apply_period,
        "time_tag": time_tag,
        "collection_category": "체육/스포츠",
        "domain_category": "체육/스포츠",
        "source_group": "sports_facility",
        "operator_type": "지자체/공공기관",
        "collection_type": "static_html+detail_html",
        "program_type": "OFFLINE",
        "raw_fields": {"page": page, "parser": "idgsports_program_card"},
    }


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    try:
        soup = fetch_soup(session, str(row["raw_url"]), timeout)
    except requests.RequestException as exc:
        logger.warning("%s detail failed: %s", row.get("raw_url"), exc)
        return row
    pairs = detail_pairs(soup)
    row = dict(row)
    weekday = normalize_space(pairs.get("교육 요일"))
    time_text = normalize_space(pairs.get("교육 시간"))
    period = normalize_date_text(pairs.get("교육기간") or row.get("period"))
    schedule_parts = [period, " ".join(part for part in [weekday, time_text] if part)]
    room = normalize_space(pairs.get("강의실"))
    row["period"] = period
    row["schedule_raw"] = normalize_space(" ".join(part for part in schedule_parts if part))
    row["target"] = normalize_space(pairs.get("교육 대상") or row.get("target"))
    row["age_group"] = "ADULT" if "성인" in row["target"] else row.get("age_group", "")
    row["fee"] = first_amount_fee(pairs.get("수강료") or row.get("fee"))
    row["material_note"] = normalize_space(pairs.get("재료비"))
    row["material_fee"] = extract_material_fee_amount(row["material_note"])
    row["instructor"] = normalize_space(pairs.get("강사명"))
    row["phone"] = normalize_space(pairs.get("문의처") or row.get("phone"))
    row["room"] = room
    row["venue_name"] = room
    row["branch"] = branch_from_room(room)
    row["branch_code"] = branch_code(row["branch"])
    row["address"] = address_from_room(room)
    description = detail_description(soup)
    if description:
        row["description"] = description
    image_url = image_url_from(soup)
    if image_url:
        row["image_url"] = image_url
    row.setdefault("raw_fields", {})["detail_pairs"] = pairs
    return row


def is_expired(row: dict[str, Any]) -> bool:
    _start, end = parse_date_range(row.get("period"))
    return bool(end and end < date.today())


def collect(limit: int | None = None, max_pages: int = 5, timeout: int = 20, include_expired: bool = False) -> list[dict[str, Any]]:
    session = make_session()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        soup = fetch_soup(session, list_url(page), timeout)
        cards = soup.select("ul.eduList > li")
        logger.info("%s page=%s cards=%s", PROVIDER, page, len(cards))
        if not cards:
            break
        page_added = 0
        for card in cards:
            row = parse_list_card(card, page)
            if not row:
                continue
            key = normalize_space(row.get("provider_course_id"))
            if key in seen:
                continue
            seen.add(key)
            row = enrich_detail(session, row, timeout)
            if not include_expired and is_expired(row):
                continue
            rows.append(row)
            page_added += 1
            if limit and len(rows) >= limit:
                return rows
        if page_added == 0 and page > 1:
            break
    return rows


def quality_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fields = ["title", "branch", "address", "period", "schedule_raw", "target", "fee", "status", "description", "raw_url", "image_url"]
    counts = {field: sum(1 for row in rows if normalize_space(row.get(field))) for field in fields}
    score = round((sum(counts.values()) / (len(fields) * max(1, len(rows)))) * 100, 1)
    grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D"
    return {
        "provider": PROVIDER,
        "collected": len(rows),
        "score": score,
        "grade": grade,
        "field_counts": counts,
        "sample_titles": [row.get("title") for row in rows[:5]],
    }


def save_branch_with_address(row: dict[str, Any]) -> str:
    branch = {
        "provider": PROVIDER,
        "branch_code": (normalize_space(row.get("branch_code")) or branch_code(row.get("branch")))[:50],
        "name": (normalize_space(row.get("branch")) or DEFAULT_BRANCH)[:100],
        "address": normalize_space(row.get("address") or DEFAULT_ADDRESS),
        "phone": normalize_space(row.get("phone") or DEFAULT_PHONE),
        "website_url": LIST_URL,
        "address_source": "crawler_known_venue",
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
        if crawler.save_course(course):
            saved += 1
    logger.info("%s saved %s/%s rows.", PROVIDER, saved, len(rows))
    return saved


def main() -> int:
    parser = argparse.ArgumentParser(description=f"{PROVIDER_NAME} crawler")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--include-expired", action="store_true")
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--mark-stale", action="store_true")
    parser.add_argument("--per-target-limit", type=int)
    parser.add_argument("--max-depth", type=int)
    parser.add_argument("--detail-limit", type=int)
    parser.add_argument("--no-detail", action="store_true")
    args = parser.parse_args()

    rows = collect(
        limit=args.limit or args.per_target_limit,
        max_pages=args.max_pages,
        timeout=args.timeout,
        include_expired=args.include_expired,
    )
    saved = save_rows(rows) if args.save_db else 0
    report = quality_report(rows)
    report["saved"] = saved
    print(json.dumps(report, ensure_ascii=False, indent=2))
    for row in rows[: min(10, len(rows))]:
        print(
            "SAMPLE\t{title}\t{branch}\t{period}\t{schedule}\t{target}\t{fee}\t{status}".format(
                title=row.get("title", ""),
                branch=row.get("branch", ""),
                period=row.get("period", ""),
                schedule=row.get("schedule_raw", ""),
                target=row.get("target", ""),
                fee=row.get("fee", ""),
                status=row.get("status", ""),
            )
        )
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
