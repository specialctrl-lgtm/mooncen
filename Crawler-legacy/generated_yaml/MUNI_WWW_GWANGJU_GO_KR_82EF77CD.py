from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


PROVIDER = "MUNI_WWW_GWANGJU_GO_KR_82EF77CD"
PROVIDER_NAME = "광주광역시 통합예약 교육강좌"
BASE_URL = "https://www.gwangju.go.kr"
LIST_URL = f"{BASE_URL}/reserve/bookingList.do?pageId=reserve1&searchCate1=A"
API_URL = f"{BASE_URL}/reserve/getBookingList.do"
DEFAULT_BRANCH = "광주광역시청"
DEFAULT_ADDRESS = "광주광역시 서구 내방로 111"
DEFAULT_PHONE = "062-120"

INVALID_VENUES = {"", "광주", "신청한 장소", "신청한장소", "신청 장소", "온라인", "비대면"}
CITY_HALL_DEPTS = {"안전정책관", "관리운영과", "여성가족과", "기후대기정책과"}
KNOWN_BRANCH_ADDRESSES = {
    "광주광역시청": "광주광역시 서구 내방로 111",
    "농식품가공창업보육센터": "광주광역시 광산구 평동로 639-22",
    "광주광역시농업기술센터": "광주광역시 광산구 평동로 639-22",
    "광주김치타운": "광주광역시 남구 김치로 60",
    "광주시립민속박물관": "광주광역시 북구 서하로 48-25",
    "광주역사민속박물관": "광주광역시 북구 서하로 48-25",
    "광주문학관": "광주광역시 북구 각화대로 93",
    "덕남정수장": "광주광역시 남구 덕남길 820-8",
}

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from DB.db_utils import get_db_cursor  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, extract_material_fee_amount, setup_logger  # noqa: E402


logger = setup_logger("Crawler_GwangjuReservation82")


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
            ),
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "identity",
            "Referer": LIST_URL,
            "X-Requested-With": "XMLHttpRequest",
        }
    )
    return session


def normalize_space(value: Any) -> str:
    text = clean_text(value).replace("\xa0", " ")
    return clean_text(re.sub(r"\s+", " ", html.unescape(text)))


def html_to_text(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return ""
    soup = BeautifulSoup(text, "html.parser")
    return normalize_space(soup.get_text(" ", strip=True))


def stable_id(*parts: Any) -> str:
    seed = "|".join(normalize_space(part) for part in parts if normalize_space(part))
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def branch_code(branch: Any) -> str:
    return stable_id(PROVIDER, normalize_space(branch) or DEFAULT_BRANCH)[:12]


def normalize_date(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return ""
    text = re.sub(
        r"(\d{4})[./](\d{1,2})[./](\d{1,2})",
        lambda m: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}",
        text,
    )
    return text


def normalize_status(item: dict[str, Any]) -> str:
    raw = normalize_space(item.get("status"))
    text = normalize_space(item.get("statusNm") or item.get("stateNm") or item.get("bookingStateNm"))
    merged = f"{raw} {text}"
    if raw in {"S", "R", "A"} or any(token in merged for token in ["접수중", "접수대기", "신청가능"]):
        return "OPEN"
    if raw in {"W", "P"} or any(token in merged for token in ["예정", "대기"]):
        return "SCHEDULED"
    if raw in {"E", "F", "C"} or any(token in merged for token in ["마감", "종료", "취소"]):
        return "CLOSED"
    return "OPEN" if not merged.strip() else merged.strip()


def normalize_fee(item: dict[str, Any]) -> str:
    price_type = normalize_space(item.get("eduPriceType"))
    price = normalize_space(item.get("eduPrice") or item.get("price"))
    if price_type == "P":
        return price if price else "유료"
    return "무료"


def normalize_period(start: Any, end: Any) -> str:
    start_text = normalize_date(start)
    end_text = normalize_date(end)
    if start_text and end_text:
        return f"{start_text} ~ {end_text}"
    return start_text or end_text


def normalize_schedule(item: dict[str, Any]) -> str:
    period = normalize_period(item.get("startEduDate"), item.get("endEduDate"))
    start_time = normalize_space(item.get("startEduTime"))
    end_time = normalize_space(item.get("endEduTime"))
    time_text = f"{start_time}~{end_time}" if start_time and end_time else start_time or end_time
    return normalize_space(" ".join(part for part in [period, time_text] if part))


def normalize_branch(item: dict[str, Any]) -> tuple[str, str, str]:
    venue = normalize_space(item.get("eduAddress"))
    dept = normalize_space(item.get("manageDeptName"))
    venue_no_paren = normalize_space(re.sub(r"\([^)]*\)", "", venue))

    if venue_no_paren in INVALID_VENUES:
        branch = DEFAULT_BRANCH if dept in CITY_HALL_DEPTS or not dept else dept
    elif "농식품가공창업보육센터" in venue_no_paren:
        branch = "농식품가공창업보육센터"
    elif "농업기술센터" in venue_no_paren:
        branch = "광주광역시농업기술센터"
    elif "김치타운" in venue_no_paren:
        branch = "광주김치타운"
    elif "시립민속박물관" in venue_no_paren or "역사민속박물관" in venue_no_paren:
        branch = "광주역사민속박물관"
    elif "문학관" in venue_no_paren:
        branch = "광주문학관"
    elif "덕남" in venue_no_paren and "정수" in venue_no_paren:
        branch = "덕남정수장"
    elif "광주광역시청" in venue_no_paren or "시청" in venue_no_paren:
        branch = DEFAULT_BRANCH
    else:
        branch = re.sub(r"\s*(?:교육장|교육관|강의실|회의실|체험실|실습실|[0-9]+층.*)$", "", venue_no_paren).strip()
        branch = branch or dept or DEFAULT_BRANCH

    address = KNOWN_BRANCH_ADDRESSES.get(branch, "")
    if not address and re.search(r"(광주광역시|동구|서구|남구|북구|광산구)\s+.+(?:로|길)\s*\d+", venue):
        address = venue if venue.startswith("광주광역시") else f"광주광역시 {venue}"
    if not address:
        address = DEFAULT_ADDRESS if branch == DEFAULT_BRANCH or dept in CITY_HALL_DEPTS else ""
    return branch[:100], venue, address


def normalize_target(item: dict[str, Any]) -> str:
    target = normalize_space(item.get("eduTarget") or item.get("eduTargetNm") or item.get("targetNm"))
    if target:
        return target
    description = html_to_text(item.get("contents"))
    match = re.search(r"(?:모집대상|교육대상|대상)\s*[:：]\s*([^○\n\r]+)", description)
    if match:
        return normalize_space(match.group(1))[:200]
    return ""


def infer_age_group(target: str, title: str, description: str) -> str:
    text = f"{target} {title} {description}"
    if any(token in text for token in ["유아", "어린이", "아동", "초등"]):
        return "KIDS"
    if any(token in text for token in ["청소년", "중학생", "고등학생"]):
        return "TEEN"
    if any(token in text for token in ["성인", "시민", "학부모", "직장인"]):
        return "ADULT"
    return ""


def item_to_row(item: dict[str, Any], page_id: str, search_cate1: str) -> dict[str, Any] | None:
    booking_code = normalize_space(item.get("bookingCode"))
    title = normalize_space(item.get("eduNm"))
    if not booking_code or not title or title == "부하테스트":
        return None

    branch, venue_name, venue_address = normalize_branch(item)
    description = html_to_text(item.get("contents"))
    material_note = html_to_text(item.get("notes"))
    target = normalize_target(item)
    raw_url = urljoin(
        BASE_URL,
        f"/reserve/bookingView.do?{urlencode({'pageId': page_id, 'searchCate1': search_cate1, 'bookingCode': booking_code})}",
    )
    period = normalize_period(item.get("startEduDate"), item.get("endEduDate"))
    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": booking_code,
        "provider_course_id": booking_code,
        "title": title,
        "branch": branch,
        "branch_code": branch_code(branch),
        "address": venue_address,
        "phone": normalize_space(item.get("information")) or DEFAULT_PHONE,
        "period": period,
        "reception_period": normalize_period(item.get("startPeriodDate"), item.get("endPeriodDate")),
        "schedule_raw": normalize_schedule(item),
        "target": target,
        "age_group": infer_age_group(target, title, description),
        "category_raw": normalize_space(item.get("cateNm") or item.get("cateCode") or search_cate1),
        "fee": normalize_fee(item),
        "material_fee": extract_material_fee_amount(material_note or description),
        "material_note": material_note,
        "status": normalize_status(item),
        "raw_url": raw_url,
        "application_url": raw_url,
        "application_type": "ONLINE_RESERVATION",
        "reservation_available": normalize_status(item) == "OPEN",
        "description": description[:4000],
        "instructor": normalize_space(item.get("charge")),
        "capacity_total": item.get("limit"),
        "capacity_current": item.get("applicantCnt"),
        "waitlist_total": item.get("standbyLimit"),
        "room": venue_name,
        "venue_name": venue_name,
        "venue_address": venue_address,
        "image_url": urljoin(BASE_URL, normalize_space(item.get("fileMediaThumbUrl"))),
        "collection_category": "교육·체험",
        "domain_category": "공공예약",
        "source_group": "public_reservation",
        "operator_type": "지자체/공공기관",
        "collection_type": "json_api",
        "raw_fields": {
            "parser": "gwangju_booking_api",
            "area": normalize_space(item.get("areaNm")),
            "area_code": normalize_space(item.get("areaCode")),
            "manage_dept": normalize_space(item.get("manageDept")),
            "manage_dept_name": normalize_space(item.get("manageDeptName")),
            "cate_code": normalize_space(item.get("cateCode")),
            "edu_address": normalize_space(item.get("eduAddress")),
            "status": normalize_space(item.get("status")),
        },
    }


def is_expired(row: dict[str, Any]) -> bool:
    try:
        _start, end = parse_date_range(row.get("period") or row.get("schedule_raw") or "")
    except Exception:  # noqa: BLE001
        return False
    if not end:
        return False
    end_day = end.date() if hasattr(end, "date") else end
    return end_day < date.today()


def collect(
    limit: int | None = None,
    max_pages: int = 60,
    timeout: int = 25,
    include_expired: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    session = make_session()
    parsed = urlparse(LIST_URL)
    query = parse_qs(parsed.query)
    page_id = (query.get("pageId") or ["reserve1"])[0]
    search_cate1 = (query.get("searchCate1") or ["A"])[0]
    search_cate2 = (query.get("searchCate2") or [""])[0]

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    total_count = 0
    page_count = 0
    pages_read = 0

    for page in range(1, max_pages + 1):
        payload = {
            "pageId": page_id,
            "movePage": str(page),
            "searchCate1": search_cate1,
            "searchCate2": search_cate2,
            "searchEduTargetChk": "",
            "searchEduPriceTypeChk": "",
            "bookingCode": "0",
            "userId": "",
            "searchQuery": "",
            "searchDept": "",
            "searchState": "",
            "searchPeriod": "R",
            "searchStartDate": "",
            "searchEndDate": "",
            "searchEduType": "",
            "searchEduTarget": "",
            "searchEduPriceType": "",
        }
        response = session.post(API_URL, data=payload, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        if normalize_space(data.get("error")) != "N":
            break
        data_map = data.get("dataMap") or {}
        items = data_map.get("list") or []
        pages_read = page
        total_count = int(float(data_map.get("totalCnt") or total_count or 0))
        page_count = int(float(data_map.get("pageCnt") or page_count or 0))
        if not items:
            break
        for item in items:
            row = item_to_row(item, page_id, search_cate1)
            if not row or row["provider_course_id"] in seen:
                continue
            seen.add(row["provider_course_id"])
            if not include_expired and is_expired(row):
                continue
            rows.append(row)
            if limit is not None and len(rows) >= limit:
                return rows, {"pages": pages_read, "total_count": total_count, "page_count": page_count}
        if page_count and page >= page_count:
            break
    return rows, {"pages": pages_read, "total_count": total_count, "page_count": page_count}


def quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fields = ["title", "branch", "address", "period", "schedule_raw", "target", "fee", "status", "description", "raw_url"]
    counts = {field: sum(1 for row in rows if normalize_space(row.get(field))) for field in fields}
    score = round(sum(counts.values()) / (len(rows) * len(fields)) * 100, 1) if rows else 0.0
    grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D"
    return {"rows": len(rows), "score": score, "grade": grade, "field_counts": counts}


def print_quality(rows: list[dict[str, Any]], meta: dict[str, Any]) -> None:
    report = quality(rows)
    report["meta"] = meta
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("\nSAMPLE")
    for row in rows[:10]:
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
        "address": normalize_space(row.get("address") or DEFAULT_ADDRESS),
        "phone": normalize_space(row.get("phone") or DEFAULT_PHONE),
        "website_url": LIST_URL,
        "address_source": "crawler_api",
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


def save_rows(rows: list[dict[str, Any]], mark_stale: bool = False) -> int:
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
    if mark_stale and saved > 0:
        from DB.course_lifecycle import mark_stale_courses, utc_now

        mark_stale_courses(PROVIDER, utc_now())
    logger.info("%s saved %s/%s rows.", PROVIDER, saved, len(rows))
    return saved


def main() -> int:
    parser = argparse.ArgumentParser(description=f"{PROVIDER_NAME} crawler")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--per-target-limit", type=int)
    parser.add_argument("--max-pages", type=int, default=60)
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--include-expired", action="store_true")
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--mark-stale", action="store_true")
    parser.add_argument("--max-depth", type=int)
    parser.add_argument("--detail-limit", type=int)
    args = parser.parse_args()

    limit = args.limit if args.limit is not None else args.per_target_limit
    started = datetime.now()
    rows, meta = collect(limit=limit, max_pages=args.max_pages, timeout=args.timeout, include_expired=args.include_expired)
    print_quality(rows, meta)
    saved = save_rows(rows, mark_stale=args.mark_stale) if args.save_db else 0
    elapsed = (datetime.now() - started).total_seconds()
    logger.info("%s completed collected=%s saved=%s elapsed=%.1fs", PROVIDER, len(rows), saved, elapsed)
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
