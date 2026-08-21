from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup, Tag


PROVIDER = "MUNI_WWW_WONJU_GO_KR_56B0C690"
PROVIDER_NAME = "원주시 통합예약 교육강좌"
BASE_URL = "https://yeyak.wonju.go.kr"
LIST_PATH = "/www/eduLectureAllWebList.do"
DETAIL_PATH = "/www/eduLectureWebView.do"
KEY = "74"
LIST_URL = f"{BASE_URL}{LIST_PATH}?{urlencode({'key': KEY})}"
DEFAULT_BRANCH = "원주시 통합예약"
DEFAULT_ADDRESS = "강원특별자치도 원주시"
DEFAULT_PHONE = ""


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from DB.db_utils import get_db_cursor  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, extract_krw_amount, extract_material_fee_amount, setup_logger  # noqa: E402


logger = setup_logger("Crawler_WonjuReservation")


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


def fetch_soup(session: requests.Session, url: str, timeout: int) -> BeautifulSoup:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def list_url(page: int = 1) -> str:
    query = {"key": KEY, "pageUnit": "8", "pageIndex": str(page), "searchCnd": "all"}
    return f"{BASE_URL}{LIST_PATH}?{urlencode(query)}"


def detail_url(prg_no: str, page: int = 1) -> str:
    query = {"key": KEY, "prgNo": prg_no, "pageUnit": "8", "pageIndex": str(page), "searchCnd": "all"}
    return f"{BASE_URL}{DETAIL_PATH}?{urlencode(query)}"


def normalize_date_text(value: Any) -> str:
    text = normalize_space(value)
    text = re.sub(
        r"(\d{4})[.](\d{1,2})[.](\d{1,2})",
        lambda m: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}",
        text,
    )
    text = re.sub(r"(\d{4})-(\d{1,2})-(\d{1,2})", lambda m: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}", text)
    text = re.sub(r"\s*[~∼]\s*", " ~ ", text)
    return normalize_space(text)


def normalize_fee(value: Any) -> str:
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
    if any(token in text for token in ["접수마감", "종료", "폐강", "취소"]):
        return "CLOSED"
    if any(token in text for token in ["대기자접수", "접수예정", "대기"]):
        return "SCHEDULED"
    if any(token in text for token in ["접수중", "추가모집", "운영중"]):
        return "OPEN"
    return text or "OPEN"


def extract_prg_no(href: Any) -> str:
    match = re.search(r"prgNo=(\d+)", normalize_space(href))
    return match.group(1) if match else ""


def info_items(card: Tag) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in card.select(".info_item"):
        key_node = item.select_one(".info_sub")
        key = normalize_space(key_node.get_text(" ", strip=True) if key_node else "")
        if not key:
            continue
        value = normalize_space(item.get_text(" ", strip=True))
        value = re.sub(rf"^{re.escape(key)}\s*", "", value).strip()
        values[key] = normalize_space(value)
    return values


def detail_pairs(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for tr in soup.select("table tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if len(cells) < 2:
            continue
        key = normalize_space(cells[0].get_text(" ", strip=True))
        value = normalize_space(cells[1].get_text(" ", strip=True))
        if key:
            pairs[key] = value
    return pairs


def parse_capacity(value: Any) -> tuple[int | None, int | None, int | None]:
    text = normalize_space(value)
    total_match = re.search(r"모집인원\s*:\s*(\d+)", text)
    current_match = re.search(r"신청인원\s*:\s*(\d+)", text)
    wait_total_match = re.search(r"대기모집인원\s*:\s*(\d+)", text)
    return (
        int(current_match.group(1)) if current_match else None,
        int(total_match.group(1)) if total_match else None,
        int(wait_total_match.group(1)) if wait_total_match else None,
    )


def infer_age_group(target: str) -> str:
    text = normalize_space(target)
    if "영유아" in text or "아동" in text or "초등" in text:
        return "KIDS"
    if "청소년" in text:
        return "TEEN"
    if "성인" in text or "어르신" in text:
        return "ADULT"
    return ""


def image_url_from(node: Tag | BeautifulSoup) -> str:
    image = node.select_one("img[src*='/DATA/lectu/']")
    src = image.get("src") if image else ""
    return urljoin(BASE_URL, src) if src else ""


def parse_list_card(card: Tag, page: int) -> dict[str, Any] | None:
    anchor = card.select_one("a.thumbnail_anchor[href*='prgNo=']")
    prg_no = extract_prg_no(anchor.get("href") if anchor else "")
    title = normalize_space(card.select_one(".thumbnail_sub").get_text(" ", strip=True) if card.select_one(".thumbnail_sub") else "")
    if not prg_no or not title:
        return None
    info = info_items(card)
    status_text = normalize_space(card.select_one(".stat").get_text(" ", strip=True) if card.select_one(".stat") else "")
    place = normalize_space(card.select_one(".place").get_text(" ", strip=True) if card.select_one(".place") else "")
    fee = normalize_fee(card.select_one(".price").get_text(" ", strip=True) if card.select_one(".price") else "")
    period = normalize_date_text(info.get("운영"))
    target = normalize_space(info.get("대상"))
    raw_url = detail_url(prg_no, page)
    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": prg_no,
        "provider_course_id": prg_no,
        "title": title,
        "branch": place or DEFAULT_BRANCH,
        "branch_code": branch_code(place or DEFAULT_BRANCH),
        "address": DEFAULT_ADDRESS,
        "phone": DEFAULT_PHONE,
        "period": period,
        "schedule_raw": period,
        "target": target,
        "age_group": infer_age_group(target),
        "category_raw": "",
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
        "capacity_current": None,
        "capacity_total": None,
        "waitlist_total": None,
        "apply_period": normalize_date_text(info.get("접수")),
        "venue_name": normalize_space(info.get("장소")),
        "room": normalize_space(info.get("장소")),
        "collection_category": "교육·체험",
        "domain_category": "평생학습",
        "source_group": "lifelong_learning",
        "operator_type": "지자체/공공기관",
        "collection_type": "static_html+detail_html",
        "program_type": "OFFLINE",
        "raw_fields": {"page": page, "parser": "wonju_edu_card_detail"},
    }


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    try:
        soup = fetch_soup(session, str(row["raw_url"]), timeout)
    except requests.RequestException as exc:
        logger.warning("%s detail failed: %s", row.get("raw_url"), exc)
        return row
    pairs = detail_pairs(soup)
    row = dict(row)
    branch = normalize_space(pairs.get("운영기관") or row.get("branch") or DEFAULT_BRANCH)
    address = normalize_space(pairs.get("주소") or row.get("address") or DEFAULT_ADDRESS)
    period = normalize_date_text(pairs.get("운영기간") or row.get("period"))
    weekday = normalize_space(pairs.get("운영요일"))
    time_text = normalize_space(pairs.get("운영시간"))
    target = normalize_space(pairs.get("대상") or row.get("target"))
    fee = normalize_fee(pairs.get("이용요금") or row.get("fee"))
    material_parts = []
    if normalize_fee(pairs.get("교재비")) not in {"", "무료", "0원"}:
        material_parts.append(f"교재비 {pairs.get('교재비')}")
    if normalize_fee(pairs.get("재료비")) not in {"", "무료", "0원"}:
        material_parts.append(f"재료비 {pairs.get('재료비')}")
    current, total, waitlist = parse_capacity(pairs.get("모집/신청"))
    _wait_current, _wait_total, wait_total_from_wait_row = parse_capacity(pairs.get("대기모집인원"))
    row.update(
        {
            "branch": branch,
            "branch_code": branch_code(branch),
            "address": address,
            "period": period,
            "schedule_raw": normalize_space(" ".join(part for part in [period, weekday, time_text] if part)),
            "target": target,
            "age_group": infer_age_group(target),
            "category_raw": normalize_space(pairs.get("카테고리")),
            "fee": fee,
            "material_note": normalize_space(" / ".join(material_parts)),
            "material_fee": extract_material_fee_amount(" ".join(material_parts)),
            "phone": normalize_space(pairs.get("문의전화") or row.get("phone")),
            "capacity_current": current,
            "capacity_total": total,
            "waitlist_total": waitlist or wait_total_from_wait_row,
            "apply_period": normalize_date_text(pairs.get("접수기간") or row.get("apply_period")),
            "venue_name": normalize_space(pairs.get("장소") or row.get("venue_name")),
            "room": normalize_space(pairs.get("장소") or row.get("room")),
            "description": normalize_space(" ".join(f"{key}: {value}" for key, value in pairs.items() if value)),
        }
    )
    image_url = image_url_from(soup)
    if image_url:
        row["image_url"] = image_url
    row.setdefault("raw_fields", {})["detail_pairs"] = pairs
    return row


def is_expired(row: dict[str, Any]) -> bool:
    _start, end = parse_date_range(row.get("period"))
    return bool(end and end < date.today())


def collect(limit: int | None = None, max_pages: int = 10, timeout: int = 20, include_expired: bool = False) -> list[dict[str, Any]]:
    session = make_session()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        soup = fetch_soup(session, list_url(page), timeout)
        cards = soup.select("li.thumbnail_item")
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
        if page_added == 0 and page > 3:
            break
    return rows


def quality_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fields = ["title", "branch", "address", "period", "schedule_raw", "target", "fee", "status", "description", "raw_url", "image_url"]
    counts = {field: sum(1 for row in rows if normalize_space(row.get(field))) for field in fields}
    score = round((sum(counts.values()) / (len(fields) * max(1, len(rows)))) * 100, 1)
    grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D"
    return {"provider": PROVIDER, "collected": len(rows), "score": score, "grade": grade, "field_counts": counts, "sample_titles": [row.get("title") for row in rows[:5]]}


def save_branch_with_address(row: dict[str, Any]) -> str:
    branch = {
        "provider": PROVIDER,
        "branch_code": (normalize_space(row.get("branch_code")) or branch_code(row.get("branch")))[:50],
        "name": (normalize_space(row.get("branch")) or DEFAULT_BRANCH)[:100],
        "address": normalize_space(row.get("address") or DEFAULT_ADDRESS),
        "phone": normalize_space(row.get("phone") or DEFAULT_PHONE),
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
        if crawler.save_course(course):
            saved += 1
    logger.info("%s saved %s/%s rows.", PROVIDER, saved, len(rows))
    return saved


def main() -> int:
    parser = argparse.ArgumentParser(description=f"{PROVIDER_NAME} crawler")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--include-expired", action="store_true")
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--mark-stale", action="store_true")
    parser.add_argument("--per-target-limit", type=int)
    parser.add_argument("--max-depth", type=int)
    parser.add_argument("--detail-limit", type=int)
    parser.add_argument("--no-detail", action="store_true")
    args = parser.parse_args()

    rows = collect(limit=args.limit or args.per_target_limit, max_pages=args.max_pages, timeout=args.timeout, include_expired=args.include_expired)
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
