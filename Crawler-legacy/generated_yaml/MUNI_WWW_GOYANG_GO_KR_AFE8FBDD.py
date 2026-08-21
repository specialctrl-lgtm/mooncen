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


PROVIDER = "MUNI_WWW_GOYANG_GO_KR_AFE8FBDD"
PROVIDER_NAME = "고양시 통합예약 교육강좌"
BASE_URL = "https://www.goyang.go.kr"
LIST_PATH = "/resve/manage/BD_selectResveManageList.do"
DETAIL_PATH = "/resve/manage/BD_selectResveManage.do"
LIST_URL = f"{BASE_URL}{LIST_PATH}"
DEFAULT_BRANCH = "고양시 통합예약"
DEFAULT_ADDRESS = "경기도 고양시 덕양구 고양시청로 10"


VENUE_ADDRESS_MAP = {
    "고양시청": "경기도 고양시 덕양구 고양시청로 10",
    "덕양구청": "경기도 고양시 덕양구 화중로104번길 13",
    "일산동구청": "경기도 고양시 일산동구 중앙로 1256",
    "일산서구청": "경기도 고양시 일산서구 중앙로 1600",
    "고양종합운동장": "경기도 고양시 일산서구 중앙로 1601",
    "고양시 자전거 안전교육장": "경기도 고양시 일산서구 중앙로 1601",
}


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from DB.db_utils import get_db_cursor  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, setup_logger  # noqa: E402


logger = setup_logger("Crawler_GoyangReservation")


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


def normalize_period(value: Any) -> str:
    text = normalize_space(value)
    text = re.sub(r"(\d{4})[.](\d{1,2})[.](\d{1,2})", lambda m: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}", text)
    text = re.sub(r"\s*~\s*", " ~ ", text)
    return normalize_space(text)


def fetch_soup(session: requests.Session, url: str, timeout: int) -> BeautifulSoup:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def list_url(page: int = 1, gu_code: str = "", category_code: str = "") -> str:
    query = {
        "q_resveTopClCode": "CL_01",
        "q_resveClCode": category_code,
        "q_resveDtlCode": "",
        "q_guDeptCode": gu_code,
        "q_dongDeptCode": "ALL" if gu_code else "",
        "q_sortOrdr": "",
        "q_useReviewTabAt": "",
        "resveSn": "",
        "q_rowPerPage": "10",
        "q_currPage": str(page),
        "q_sortName": "",
        "q_sortOrder": "",
        "q_resveCl": category_code,
        "q_guDept": gu_code,
        "q_dongDept": "ALL" if gu_code else "",
        "q_reqstPdBgnde": "",
        "q_reqstPdEndde": "",
        "q_resveSttusCode": "",
        "q_resvePdBgnde": "",
        "q_resvePdEndde": "",
        "q_resveNm": "",
    }
    return f"{LIST_URL}?{urlencode(query)}"


def detail_url(resve_sn: str) -> str:
    return f"{BASE_URL}{DETAIL_PATH}?{urlencode({'q_resveTopClCode': 'CL_01', 'resveSn': resve_sn})}"


def extract_resve_sn(onclick: Any) -> str:
    match = re.search(r"opResveView\((\d+)", normalize_space(onclick))
    return match.group(1) if match else ""


def stable_id(*parts: Any) -> str:
    seed = "|".join(normalize_space(part) for part in parts if normalize_space(part))
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def branch_code(name: Any) -> str:
    return stable_id(PROVIDER, normalize_space(name) or DEFAULT_BRANCH)[:12]


def normalize_status(value: Any) -> str:
    text = normalize_space(value)
    if "접수중" in text:
        return "OPEN"
    if "접수예정" in text:
        return "SCHEDULED"
    if any(token in text for token in ["접수마감", "종료", "마감"]):
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


def split_periods(value: Any) -> tuple[str, str]:
    text = normalize_period(value)
    apply_match = re.search(r"신청\s*:\s*(\d{4}-\d{2}-\d{2}\s*~\s*\d{4}-\d{2}-\d{2})", text)
    edu_match = re.search(r"교육\s*:\s*(\d{4}-\d{2}-\d{2}\s*~\s*\d{4}-\d{2}-\d{2})", text)
    return (
        normalize_space(apply_match.group(1)) if apply_match else "",
        normalize_space(edu_match.group(1)) if edu_match else "",
    )


def first_date_range(value: Any) -> str:
    text = normalize_period(value)
    match = re.search(r"\d{4}-\d{2}-\d{2}\s*~\s*\d{4}-\d{2}-\d{2}", text)
    if match:
        return normalize_space(match.group(0))
    match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    return match.group(0) if match else text


def table_pairs(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for tr in soup.select("table.view-type tr, table tr"):
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


def description_from_detail(soup: BeautifulSoup) -> str:
    node = soup.select_one("#tab-cont1")
    if not node:
        return ""
    text = normalize_space(node.get_text(" ", strip=True))
    return re.sub(r"^상세정보\s*", "", text)


def image_from_detail(soup: BeautifulSoup) -> str:
    image = soup.select_one(".img-slider img[src], #tab-cont1 img[src], img[src*='resveImageFile']")
    return urljoin(BASE_URL, image.get("src")) if image and image.get("src") else ""


def address_for_venue(venue: str) -> str:
    text = normalize_space(venue)
    paren_match = re.search(r"\(([^)]*(?:경기도|고양시|덕양구|일산)[^)]*)\)", text)
    if paren_match:
        address = normalize_space(paren_match.group(1))
        if not address.startswith("경기도") and "고양시" in address:
            address = f"경기도 {address}"
        return address
    for key, address in VENUE_ADDRESS_MAP.items():
        if key in text:
            return address
    return DEFAULT_ADDRESS


def branch_for_venue(venue: str) -> str:
    text = normalize_space(venue)
    for key in VENUE_ADDRESS_MAP:
        if key in text:
            return key
    if "(" in text:
        return normalize_space(text.split("(", 1)[0])
    return text or DEFAULT_BRANCH


def infer_category(title: str, detail_category: str) -> str:
    text = normalize_space(f"{title} {detail_category}")
    if "정보화" in text or "AI" in text or "컴퓨터" in text or "엑셀" in text or "파워포인트" in text:
        return "정보화교육"
    if "보건" in text or "심폐소생술" in text or "응급처치" in text:
        return "보건소교육"
    if "체육" in text:
        return "생활체육"
    if "농업" in text:
        return "농업기술"
    if "환경" in text:
        return "환경교육"
    return "교육강좌"


def parse_list_item(anchor: Tag, page: int, gu_code: str, category_code: str) -> dict[str, Any] | None:
    resve_sn = extract_resve_sn(anchor.get("onclick"))
    if not resve_sn:
        return None
    title = normalize_space(anchor.select_one(".subject_tit").get_text(" ", strip=True) if anchor.select_one(".subject_tit") else "")
    venue = normalize_space(anchor.select_one(".list_type02 > span").get_text(" ", strip=True) if anchor.select_one(".list_type02 > span") else "")
    target = normalize_space(anchor.select_one(".list_type03").get_text(" ", strip=True) if anchor.select_one(".list_type03") else "")
    status = normalize_status(anchor.select_one(".list_type01 b").get_text(" ", strip=True) if anchor.select_one(".list_type01 b") else "")
    apply_period, period = split_periods(anchor.select_one(".list_type04").get_text(" ", strip=True) if anchor.select_one(".list_type04") else "")
    capacity = normalize_space(anchor.select_one(".list_type05").get_text(" ", strip=True) if anchor.select_one(".list_type05") else "")
    fee = normalize_fee(anchor.select_one(".list_type06").get_text(" ", strip=True) if anchor.select_one(".list_type06") else "")
    reception_type = normalize_space(anchor.select_one(".list_type07").get_text(" ", strip=True) if anchor.select_one(".list_type07") else "")
    if not title:
        return None
    branch = branch_for_venue(venue)
    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": resve_sn,
        "provider_course_id": resve_sn,
        "title": title,
        "branch": branch,
        "branch_code": branch_code(branch),
        "address": address_for_venue(venue),
        "period": period,
        "schedule_raw": period,
        "target": target,
        "fee": fee,
        "status": status,
        "description": "",
        "image_url": "",
        "raw_url": detail_url(resve_sn),
        "category": infer_category(title, ""),
        "venue_name": branch,
        "venue_address": address_for_venue(venue),
        "room": venue,
        "reception_period": apply_period,
        "reception_type": reception_type,
        "capacity_text": capacity,
        "raw_fields": {
            "parser": "goyang_reservation_list",
            "page": page,
            "gu_code": gu_code,
            "category_code": category_code,
        },
    }


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    soup = fetch_soup(session, row["raw_url"], timeout)
    title_node = soup.select_one("h3")
    if title_node:
        title = normalize_space(title_node.get_text(" ", strip=True))
        if title and title != "교육ㆍ강좌":
            row["title"] = title
    detail_category = normalize_space(soup.select_one(".h4-title").get_text(" ", strip=True) if soup.select_one(".h4-title") else "")
    pairs = table_pairs(soup)
    venue = normalize_space(pairs.get("장소")) or row.get("room", "")
    if venue:
        row["branch"] = branch_for_venue(venue)
        row["branch_code"] = branch_code(row["branch"])
        row["address"] = address_for_venue(venue)
        row["venue_name"] = row["branch"]
        row["venue_address"] = row["address"]
        row["room"] = venue
    row["target"] = normalize_space(pairs.get("이용대상")) or row.get("target")
    row["period"] = first_date_range(pairs.get("교육.강좌 일시")) if pairs.get("교육.강좌 일시") else row.get("period")
    row["schedule_raw"] = normalize_period(pairs.get("교육.강좌 일시")) or row.get("schedule_raw")
    row["fee"] = normalize_fee(pairs.get("이용료")) or row.get("fee")
    row["reception_period"] = normalize_period(pairs.get("신청기간")) or row.get("reception_period")
    row["reception_type"] = normalize_space(pairs.get("신청방법")) or row.get("reception_type")
    row["selection_method"] = normalize_space(pairs.get("선별방법"))
    row["manager"] = normalize_space(pairs.get("담당자"))
    row["capacity_text"] = normalize_space(pairs.get("모집정원")) or row.get("capacity_text")
    row["description"] = description_from_detail(soup)
    row["image_url"] = image_from_detail(soup)
    row["category"] = infer_category(row.get("title", ""), detail_category)
    row["raw_fields"]["detail_pairs"] = pairs
    row["raw_fields"]["detail_category"] = detail_category
    row["raw_fields"]["detail_parser"] = "goyang_reservation_detail"
    return row


def is_expired_course(row: dict[str, Any]) -> bool:
    _, end_date = parse_date_range(row.get("period"))
    return bool(end_date and end_date < datetime.now().date())


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
        soup = fetch_soup(session, list_url(page=page), timeout)
        anchors = [a for a in soup.select("a[onclick*='opResveView']") if a.select_one(".subject_tit")]
        if not anchors:
            break
        expired_on_page = 0
        for anchor in anchors:
            row = parse_list_item(anchor, page, "", "")
            if not row or row["provider_course_id"] in seen:
                continue
            seen.add(row["provider_course_id"])
            if not include_expired and is_expired_course(row):
                expired_on_page += 1
                logger.info("Skipping expired Goyang course: %s / %s", row.get("title"), row.get("period"))
                continue
            if detail:
                try:
                    row = enrich_detail(session, row, timeout)
                except Exception as exc:
                    logger.warning("Goyang detail failed %s: %s", row.get("raw_url"), exc)
            if not include_expired and is_expired_course(row):
                logger.info("Skipping expired Goyang course: %s / %s", row.get("title"), row.get("period"))
                continue
            rows.append(row)
            if limit and len(rows) >= limit:
                return rows
        if not include_expired and expired_on_page == len(anchors):
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
        "phone": "",
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
    parser = argparse.ArgumentParser(description="Goyang reservation education crawler")
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
