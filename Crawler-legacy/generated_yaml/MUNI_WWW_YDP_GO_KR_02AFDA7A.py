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


PROVIDER = "MUNI_WWW_YDP_GO_KR_02AFDA7A"
PROVIDER_NAME = "영등포구 통합예약 교육강좌"
BASE_URL = "https://www.ydp.go.kr"
LIST_PATH = "/reserve/selectTnEdcLctreListU.do"
LIST_URL = f"{BASE_URL}{LIST_PATH}?key=5062&"
DEFAULT_BRANCH = "영등포구 통합예약"
DEFAULT_ADDRESS = "서울특별시 영등포구 당산로 123"
DEFAULT_PHONE = "02-2670-3114"


ADDRESS_HINTS = {
    "YDP미래평생학습관": "서울특별시 영등포구 버드나루로 15",
    "영등포구청": "서울특별시 영등포구 당산로 123",
    "여의동 주민센터": "서울특별시 영등포구 국제금융로 124",
    "당산1동 주민센터": "서울특별시 영등포구 양산로23길 11",
    "당산2동 주민센터": "서울특별시 영등포구 당산로41가길 7",
    "신길4동 주민센터": "서울특별시 영등포구 신길로42길 1",
    "신길5동 주민센터": "서울특별시 영등포구 도림로 264",
    "신길6동 주민센터": "서울특별시 영등포구 대방천로 167",
    "신길7동 주민센터": "서울특별시 영등포구 여의대방로43길 10",
    "대림1동 주민센터": "서울특별시 영등포구 디지털로 441",
    "대림2동 주민센터": "서울특별시 영등포구 대림로23길 25",
    "대림3동 주민센터": "서울특별시 영등포구 대림로 197",
    "문래동 주민센터": "서울특별시 영등포구 문래로28길 15",
    "양평1동 주민센터": "서울특별시 영등포구 영등포로11길 12",
    "양평2동 주민센터": "서울특별시 영등포구 선유로47길 30",
    "영등포동 주민센터": "서울특별시 영등포구 영등포로53길 22",
    "도림동 주민센터": "서울특별시 영등포구 도영로7길 10",
}


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from DB.db_utils import get_db_cursor  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, setup_logger  # noqa: E402


logger = setup_logger("Crawler_YeongdeungpoEducation")


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


def normalize_date_text(value: Any) -> str:
    text = normalize_space(value)
    text = re.sub(
        r"\b(\d{2})[.]\s*(\d{1,2})[.]\s*(\d{1,2})[.]?\b",
        lambda m: f"20{int(m.group(1)):02d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}",
        text,
    )
    text = re.sub(
        r"\b(\d{4})[.]\s*(\d{1,2})[.]\s*(\d{1,2})\b",
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


def list_url(page: int) -> str:
    query = {"key": "5062"}
    if page > 1:
        query["cpn"] = str(page)
    return f"{BASE_URL}{LIST_PATH}?{urlencode(query)}&"


def stable_id(*parts: Any) -> str:
    seed = "|".join(normalize_space(part) for part in parts if normalize_space(part))
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def branch_code(branch: Any) -> str:
    return stable_id(PROVIDER, normalize_space(branch) or DEFAULT_BRANCH)[:12]


def normalize_status(value: Any) -> str:
    text = normalize_space(value)
    if any(token in text for token in ["접수중", "대기접수", "교육중"]):
        return "OPEN"
    if "접수예정" in text:
        return "SCHEDULED"
    if any(token in text for token in ["접수마감", "마감", "종료"]):
        return "CLOSED"
    return text


def normalize_fee(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return ""
    if text in ["0원", "0 원"] or "무료" in text:
        return "무료"
    amount = re.search(r"[\d,]+\s*원", text)
    return normalize_space(amount.group(0)) if amount else text


def first_date_range(value: Any) -> str:
    text = normalize_date_text(value)
    match = re.search(r"\d{4}-\d{2}-\d{2}\s*~\s*\d{4}-\d{2}-\d{2}", text)
    return normalize_space(match.group(0)) if match else text


def extract_lctre_no(raw_url: str) -> str:
    query = parse_qs(urlparse(raw_url).query)
    return (query.get("lctreNo") or [""])[0]


def infer_branch(title: str, venue: str, category: str) -> str:
    text = f"{title} {venue} {category}"
    paren_matches = re.findall(r"\(([^()]*(?:주민센터|학습관|센터|도서관)[^()]*)\)", text)
    if paren_matches:
        return normalize_space(paren_matches[-1])
    for key in ADDRESS_HINTS:
        if key in text:
            return key
    if venue:
        return venue
    if "동주민센터" in category:
        return "영등포구 동주민센터"
    return DEFAULT_BRANCH


def address_for(branch: str) -> str:
    for key, address in ADDRESS_HINTS.items():
        if key in branch:
            return address
    return DEFAULT_ADDRESS


def infer_age_group(target: str, title: str) -> str:
    text = f"{target} {title}"
    if re.search(r"유아|초등|어린이|가족과함께|6~7세|아동", text):
        return "KIDS"
    if re.search(r"중학생|고등학생|청년|청소년", text):
        return "TEEN"
    if re.search(r"성인|어르신|일반", text):
        return "ADULT"
    return ""


def parse_capacity(value: Any) -> tuple[int | None, int | None]:
    nums = [int(num.replace(",", "")) for num in re.findall(r"\d[\d,]*", normalize_space(value))]
    if len(nums) >= 2:
        return nums[0], nums[1]
    return None, None


def parse_list_item(item: Tag, current_url: str) -> dict[str, Any] | None:
    title_link = item.select_one("a.lists[href]")
    title = normalize_space(title_link.get_text(" ", strip=True) if title_link else "")
    if not title:
        return None
    raw_url = urljoin(current_url, title_link.get("href"))
    lctre_no = extract_lctre_no(raw_url) or stable_id(raw_url, title)
    status = normalize_status(item.select_one("a.sk").get_text(" ", strip=True) if item.select_one("a.sk") else "")
    info = [normalize_space(li.get_text(" ", strip=True)) for li in item.select(".adds li")]
    venue = info[0] if info else ""
    target = info[1].lstrip("#") if len(info) > 1 else ""
    dates = [normalize_space(li.get_text(" ", strip=True)) for li in item.select(".days li")]
    reception_period = first_date_range(dates[0]) if dates else ""
    period = first_date_range(dates[1]) if len(dates) > 1 else ""
    methods = [normalize_space(span.get_text(" ", strip=True)) for span in item.select(".tops span.on1, .tops span.on2, .tops span.on3")]
    capacity_current, capacity_total = parse_capacity(item.select_one(".b-st").get_text(" ", strip=True) if item.select_one(".b-st") else "")
    branch = infer_branch(title, venue, target)
    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": lctre_no,
        "provider_course_id": lctre_no,
        "title": title,
        "branch": branch,
        "branch_code": branch_code(branch),
        "address": address_for(branch),
        "phone": DEFAULT_PHONE,
        "period": period,
        "schedule_raw": period,
        "target": target,
        "age_group": infer_age_group(target, title),
        "fee": "",
        "status": status,
        "description": "",
        "image_url": "",
        "raw_url": raw_url,
        "application_url": raw_url,
        "application_type": "ONLINE" if "인터넷" in methods else "",
        "application_method_raw": ", ".join(methods),
        "reservation_available": status == "OPEN",
        "category": target,
        "venue_name": venue,
        "venue_address": address_for(branch),
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "reception_period": reception_period,
        "collection_category": "공공예약",
        "domain_category": "공공예약",
        "source_group": "public_reservation",
        "operator_type": "지자체/공공기관",
        "collection_type": "static_html",
        "program_type": "OFFLINE",
        "raw_fields": {"list_parser": "ydp_education_card"},
    }


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


def description_from_pairs(pairs: dict[str, str]) -> str:
    parts = []
    for key in ["강의개요", "수강신청유의사항", "교재 및 참고자료", "강의계획서"]:
        value = normalize_space(pairs.get(key))
        if value:
            parts.append(f"{key}: {value}")
    return normalize_space(" ".join(parts))


def image_from_detail(soup: BeautifulSoup) -> str:
    for image in soup.select("img[src]"):
        src = normalize_space(image.get("src"))
        alt = normalize_space(image.get("alt"))
        if "atch" in src.lower() or "강의계획" in alt:
            return urljoin(BASE_URL, src)
    return ""


def is_expired_course(row: dict[str, Any]) -> bool:
    _, end_date = parse_date_range(row.get("period"))
    return bool(end_date and end_date < datetime.now().date())


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    soup = fetch_soup(session, row["raw_url"], timeout)
    pairs = table_pairs(soup)
    row["title"] = normalize_space(pairs.get("과정명")) or row["title"]
    venue = normalize_space(pairs.get("교육장소")) or row.get("venue_name", "")
    target = normalize_space(pairs.get("수강대상")) or row.get("target", "")
    row["venue_name"] = venue
    row["target"] = target.lstrip("#")
    row["branch"] = infer_branch(row["title"], venue, target)
    row["branch_code"] = branch_code(row["branch"])
    row["address"] = address_for(row["branch"])
    row["venue_address"] = row["address"]
    row["instructor"] = normalize_space(pairs.get("강사명"))
    row["application_method_raw"] = normalize_space(pairs.get("접수방식")) or row.get("application_method_raw", "")
    row["fee"] = normalize_fee(pairs.get("수강료")) or row.get("fee", "")
    row["material_fee"] = normalize_fee(pairs.get("재료비"))
    row["reception_period"] = first_date_range(pairs.get("접수기간")) or row.get("reception_period", "")
    row["period"] = first_date_range(pairs.get("교육기간")) or row.get("period", "")
    row["schedule_raw"] = normalize_space(f"{row['period']} {pairs.get('강의요일', '')}")
    row["phone"] = normalize_space(pairs.get("교육과정문의")) or row.get("phone", "")
    capacity_current, capacity_total = parse_capacity(pairs.get("정원"))
    row["capacity_current"] = row.get("capacity_current") or capacity_current
    row["capacity_total"] = capacity_total or row.get("capacity_total")
    row["description"] = description_from_pairs(pairs)
    row["image_url"] = image_from_detail(soup)
    row["age_group"] = infer_age_group(row["target"], row["title"])
    row["category"] = normalize_space(pairs.get("관심분야")).lstrip("#") or row.get("category", "")
    row["raw_fields"]["detail_pairs"] = pairs
    row["raw_fields"]["detail_parser"] = "ydp_education_detail"
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
        current_url = list_url(page)
        soup = fetch_soup(session, current_url, timeout)
        items = soup.select("li.typ1")
        if not items:
            break
        page_count = 0
        for item in items:
            row = parse_list_item(item, current_url)
            if not row or row["provider_course_id"] in seen:
                continue
            seen.add(row["provider_course_id"])
            if detail:
                try:
                    row = enrich_detail(session, row, timeout)
                except Exception as exc:
                    logger.warning("YDP detail failed %s: %s", row.get("raw_url"), exc)
            if not include_expired and is_expired_course(row):
                logger.info("Skipping expired YDP course: %s / %s", row.get("title"), row.get("period"))
                continue
            rows.append(row)
            page_count += 1
            if limit and len(rows) >= limit:
                return rows
        if page_count == 0:
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
        print(" | ".join([normalize_space(row.get(key)) for key in ["title", "branch", "address", "period", "target", "fee", "status"]]))


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
    parser = argparse.ArgumentParser(description="Yeongdeungpo education reservation crawler")
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

    started = datetime.now()
    rows = collect(
        limit=args.limit or args.per_target_limit,
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
