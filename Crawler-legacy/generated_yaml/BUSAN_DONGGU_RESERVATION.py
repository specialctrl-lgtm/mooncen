from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib3
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup, Tag


PROVIDER = "BUSAN_DONGGU_RESERVATION"
PROVIDER_NAME = "부산 동구 통합예약"
BASE_URL = "https://www.bsdonggu.go.kr"
LIST_PATH = "/index.donggu"
LIST_MENU = "DOM_000000701001000000"
DETAIL_MENU = "DOM_000000701002000000"
HOME_URL = f"{BASE_URL}/reserve/index.donggu"
LIST_URL = f"{BASE_URL}{LIST_PATH}?menuCd={LIST_MENU}&dummy_Data_Gbn=LifeLong&search_Status=T&data_Title="
DEFAULT_BRANCH = "부산 동구 통합예약"
DEFAULT_ADDRESS = "부산광역시 동구 구청로 1"
DEFAULT_PHONE = "051-440-4082"


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from DB.db_utils import get_db_cursor  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, extract_krw_amount, extract_material_fee_amount, setup_logger  # noqa: E402


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = setup_logger("Crawler_BusanDongguReservation")


def make_session() -> requests.Session:
    session = requests.Session()
    session.verify = False
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


def stable_id(*parts: Any) -> str:
    seed = "|".join(normalize_space(part) for part in parts if normalize_space(part))
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def branch_code(branch: Any) -> str:
    return stable_id(PROVIDER, normalize_space(branch) or DEFAULT_BRANCH)[:12]


def normalize_date_text(value: Any) -> str:
    text = normalize_space(value)
    text = re.sub(
        r"\b(\d{4})(\d{2})(\d{2})\b",
        lambda m: f"{m.group(1)}-{m.group(2)}-{m.group(3)}",
        text,
    )
    text = re.sub(
        r"(\d{4})[./](\d{1,2})[./](\d{1,2})",
        lambda m: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}",
        text,
    )
    text = re.sub(r"(\d{4}-\d{2}-\d{2})\.\s*(\d{1,2}):(\d{2})", lambda m: f"{m.group(1)} {int(m.group(2)):02d}:{m.group(3)}", text)
    text = re.sub(r"(\d{1,2})\s*:\s*(\d{1,2})", lambda m: f"{int(m.group(1)):02d}:{int(m.group(2)):02d}", text)
    text = re.sub(r"\s*[~∼〜]\s*", " ~ ", text)
    return normalize_space(text)


def normalize_fee(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return ""
    if text in {"없음", "무료", "0원"} or "무료" in text:
        return "무료"
    amount = extract_krw_amount(text)
    if amount is not None:
        return f"{amount:,}원"
    return text


def normalize_status(value: Any, apply_period: str, period: str) -> str:
    text = normalize_space(value)
    if any(token in text for token in ["접수중", "접수 중", "대기자"]):
        return "OPEN"
    if any(token in text for token in ["접수대기", "대기"]):
        return "SCHEDULED"
    if any(token in text for token in ["기간마감", "접수마감", "마감", "종료"]):
        return "CLOSED"
    _apply_start, apply_end = parse_date_range(apply_period)
    _course_start, course_end = parse_date_range(period)
    today = date.today()
    if course_end and course_end < today:
        return "CLOSED"
    if apply_end and apply_end < today:
        return "CLOSED"
    return "OPEN"


def list_url(page: int) -> str:
    query = {
        "menuCd": LIST_MENU,
        "edu_Start_Date_From": "",
        "edu_Start_Date_To": "",
        "accept_Start_Date_From": "",
        "accept_Start_Date_To": "",
        "data_Title": "",
        "search_Status": "T",
        "page_no": str(page),
        "gubun_l": "",
    }
    return f"{BASE_URL}{LIST_PATH}?{urlencode(query)}"


def detail_url(data_sid: str, page: int = 1) -> str:
    query = {
        "menuCd": DETAIL_MENU,
        "edu_Start_Date_From": "",
        "edu_Start_Date_To": "",
        "accept_Start_Date_From": "",
        "accept_Start_Date_To": "",
        "data_Title": "",
        "search_Status": "T",
        "page_no": str(page),
        "data_Sid": data_sid,
    }
    return f"{BASE_URL}{LIST_PATH}?{urlencode(query)}"


def fetch_soup(session: requests.Session, url: str, timeout: int) -> BeautifulSoup:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def extract_data_sid(url: str) -> str:
    parsed = urlparse(url)
    return normalize_space(parse_qs(parsed.query).get("data_Sid", [""])[0])


def text_without_child(parent: Tag, child: Tag) -> str:
    clone = BeautifulSoup(str(parent), "html.parser")
    found = clone.find(child.name, class_=child.get("class"))
    if found:
        found.extract()
    return normalize_space(clone.get_text(" ", strip=True))


def list_pairs(dl: Tag) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for li in dl.select("dd li"):
        label = li.select_one(".name")
        if not label:
            continue
        key = normalize_space(label.get_text(" ", strip=True))
        label.extract()
        pairs[key] = normalize_space(li.get_text(" ", strip=True))
    return pairs


def table_pairs(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for tr in soup.select("table tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        i = 0
        while i < len(cells):
            if cells[i].name != "th":
                i += 1
                continue
            key = normalize_space(cells[i].get_text(" ", strip=True))
            value = ""
            if i + 1 < len(cells) and cells[i + 1].name == "td":
                td = cells[i + 1]
                textarea = td.select_one("textarea")
                value = normalize_space(textarea.get_text("\n", strip=True) if textarea else td.get_text(" ", strip=True))
                i += 2
            else:
                i += 1
            if key:
                pairs[key] = value
    return pairs


def parse_capacity(value: Any) -> tuple[int | None, int | None, int | None]:
    text = normalize_space(value)
    match = re.search(r"신청자\s*\((\d[\d,]*)\s*/\s*(\d[\d,]*)\)", text)
    if not match:
        match = re.search(r"(\d[\d,]*)\s*/\s*(\d[\d,]*)", text)
    current = int(match.group(1).replace(",", "")) if match else None
    total = int(match.group(2).replace(",", "")) if match else None
    wait_match = re.search(r"대기자\s*\((\d[\d,]*)\s*/\s*(\d[\d,]*)\)", text)
    wait_total = int(wait_match.group(2).replace(",", "")) if wait_match else None
    return current, total, wait_total


def infer_age_group(target: str, title: str) -> str:
    text = f"{target} {title}"
    if re.search(r"성인|시니어|직장인|19세|만19", text):
        return "ADULT"
    if re.search(r"중학생|고등학생|청소년", text):
        return "TEEN"
    if re.search(r"유아|초등|어린이|아동|[0-9]{2}년생|만[0-9]+세", text):
        return "KIDS"
    return ""


def branch_from_venue(venue: str, category: str) -> str:
    venue = normalize_space(venue)
    if venue:
        return venue[:100]
    category = normalize_space(category)
    if category:
        return f"부산 동구 {category}"
    return DEFAULT_BRANCH


def address_from_detail(venue: str, detail_address: str) -> str:
    detail_address = normalize_space(detail_address)
    if detail_address:
        return detail_address
    venue = normalize_space(venue)
    if venue and venue not in {"온라인", "비대면"}:
        return f"부산광역시 동구 {venue}"
    return DEFAULT_ADDRESS


def extract_description_value(description: str, labels: list[str]) -> str:
    text = normalize_space(description)
    for label in labels:
        match = re.search(rf"(?:○|◾|ㆍ|❍|[-])?\s*{re.escape(label)}\s*[:：]\s*([^○◾ㆍ❍☎\n\r]+)", text)
        if match:
            return normalize_space(match.group(1))
    return ""


def parse_list_row(dl: Tag, page: int) -> dict[str, Any] | None:
    title_link = dl.select_one("dt > a[href*='data_Sid=']")
    if not title_link:
        return None
    raw_url = urljoin(BASE_URL, title_link.get("href", ""))
    data_sid = extract_data_sid(raw_url)
    if not data_sid:
        return None
    status = normalize_space(dl.select_one(".mark").get_text(" ", strip=True) if dl.select_one(".mark") else "")
    title = normalize_space(title_link.get_text(" ", strip=True))
    pairs = list_pairs(dl)
    period = normalize_date_text(pairs.get("교육기간"))
    apply_period = normalize_date_text(pairs.get("접수기간"))
    target = normalize_space(pairs.get("교육대상"))
    venue = normalize_space(pairs.get("교육장소"))
    fee_raw = normalize_space(pairs.get("기타경비"))
    current, total, wait_total = parse_capacity(pairs.get("신청/모집"))
    branch = branch_from_venue(venue, "")

    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": data_sid,
        "provider_course_id": data_sid,
        "title": title,
        "branch": branch,
        "branch_code": branch_code(branch),
        "address": address_from_detail(venue, ""),
        "phone": DEFAULT_PHONE,
        "period": period,
        "schedule_raw": period,
        "target": target,
        "age_group": infer_age_group(target, title),
        "category_raw": "",
        "fee": normalize_fee(fee_raw),
        "material_fee": extract_material_fee_amount(fee_raw),
        "material_note": fee_raw if any(token in fee_raw for token in ["재료비", "교재비"]) else "",
        "status": normalize_status(status, apply_period, period),
        "raw_url": detail_url(data_sid, page),
        "application_url": detail_url(data_sid, page),
        "application_type": "ONLINE",
        "description": "\n".join([title, *[f"{k}: {v}" for k, v in pairs.items() if v]]),
        "image_url": "",
        "instructor": "",
        "capacity_current": current,
        "capacity_total": total,
        "waitlist_total": wait_total,
        "apply_period": apply_period,
        "room": venue,
        "venue_name": venue,
    }


def parse_home_table_row(tr: Tag) -> dict[str, Any] | None:
    cells = tr.find_all("td", recursive=False)
    if len(cells) < 4:
        return None
    title_link = cells[0].select_one("a[href*='data_Sid=']")
    if not title_link:
        return None
    href = title_link.get("href", "")
    raw_url = urljoin(BASE_URL, href)
    data_sid = extract_data_sid(raw_url)
    if not data_sid:
        return None
    title = normalize_space(title_link.get_text(" ", strip=True))
    dates = [normalize_date_text(node.get_text(" ", strip=True)) for node in cells[0].select("i.date")]
    apply_period = dates[0] if len(dates) >= 1 else ""
    period = dates[1] if len(dates) >= 2 else ""
    target = normalize_space(cells[1].get_text(" ", strip=True))
    capacity = normalize_space(cells[2].get_text(" ", strip=True))
    status = normalize_space(cells[3].get_text(" ", strip=True))
    current, total, wait_total = parse_capacity(capacity)

    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": data_sid,
        "provider_course_id": data_sid,
        "title": title,
        "branch": DEFAULT_BRANCH,
        "branch_code": branch_code(DEFAULT_BRANCH),
        "address": DEFAULT_ADDRESS,
        "phone": DEFAULT_PHONE,
        "period": period,
        "schedule_raw": period,
        "target": target,
        "age_group": infer_age_group(target, title),
        "category_raw": "",
        "fee": "",
        "material_fee": None,
        "material_note": "",
        "status": normalize_status(status, apply_period, period),
        "raw_url": raw_url,
        "application_url": raw_url,
        "application_type": "ONLINE",
        "description": "\n".join(part for part in [title, f"교육대상: {target}", f"교육기간: {period}", f"접수기간: {apply_period}"] if part),
        "image_url": "",
        "instructor": "",
        "capacity_current": current,
        "capacity_total": total,
        "waitlist_total": wait_total,
        "apply_period": apply_period,
        "room": "",
        "venue_name": "",
    }


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    data_sid = normalize_space(row.get("external_id"))
    if not data_sid:
        return row
    try:
        soup = fetch_soup(session, normalize_space(row.get("raw_url")), timeout=timeout)
    except Exception as exc:
        logger.warning("Detail fetch failed %s: %s", data_sid, exc)
        return row
    pairs = table_pairs(soup)
    title = normalize_space(pairs.get("강좌명"))
    if title:
        row["title"] = title
    category = normalize_space(pairs.get("강좌구분"))
    row["category_raw"] = category or row.get("category_raw")
    description = normalize_space(pairs.get("강좌내용"))
    if description:
        row["description"] = description
    start_date = normalize_date_text(pairs.get("교육시작일"))
    end_date = normalize_date_text(pairs.get("교육종료일"))
    if start_date and end_date:
        row["period"] = f"{start_date} ~ {end_date}"
    edu_time = normalize_date_text(pairs.get("교육시간"))
    row["schedule_raw"] = normalize_space(" ".join(part for part in [row.get("period"), edu_time] if part))
    row["target"] = normalize_space(pairs.get("교육대상") or row.get("target"))
    apply_start = normalize_date_text(pairs.get("접수시작일"))
    apply_end = normalize_date_text(pairs.get("접수종료일"))
    if apply_start and apply_end:
        row["apply_period"] = f"{apply_start} ~ {apply_end}"
    venue = normalize_space(pairs.get("교육장소") or row.get("venue_name"))
    if not venue and description:
        venue = extract_description_value(description, ["교육장소", "장소"])
    detail_address = normalize_space(pairs.get("교육장소주소"))
    branch = branch_from_venue(venue, category)
    row["branch"] = branch
    row["branch_code"] = branch_code(branch)
    row["address"] = address_from_detail(venue, detail_address)
    row["phone"] = normalize_space(pairs.get("교육문의전화") or row.get("phone"))
    row["instructor"] = normalize_space(pairs.get("강사명"))
    fee_raw = normalize_space(pairs.get("기타경비") or row.get("fee"))
    if not fee_raw and description:
        fee_raw = extract_description_value(description, ["수강료", "재료비", "교재비"])
    row["fee"] = normalize_fee(fee_raw)
    row["material_fee"] = extract_material_fee_amount(fee_raw, row.get("description"))
    row["material_note"] = fee_raw if any(token in fee_raw for token in ["재료비", "교재비"]) else normalize_space(row.get("material_note"))
    current, total, wait_total = parse_capacity(
        f"신청자 ({pairs.get('신청가능인원', '')}) / 대기자 ({pairs.get('대기가능인원', '')})"
    )
    row["capacity_current"] = current or row.get("capacity_current")
    row["capacity_total"] = total or row.get("capacity_total")
    row["waitlist_total"] = wait_total or row.get("waitlist_total")
    row["status"] = normalize_status(row.get("status"), row.get("apply_period", ""), row.get("period", ""))
    row["age_group"] = infer_age_group(row.get("target", ""), row.get("title", ""))
    return row


def is_expired(row: dict[str, Any]) -> bool:
    _start, end = parse_date_range(row.get("period"))
    return bool(end and end < date.today())


def collect(
    limit: int | None = None,
    max_pages: int = 30,
    timeout: int = 25,
    include_expired: bool = False,
    detail: bool = True,
) -> list[dict[str, Any]]:
    session = make_session()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    home_soup = fetch_soup(session, HOME_URL, timeout=timeout)
    home_rows = home_soup.select("table.table tr")
    logger.info("%s home table rows=%s", PROVIDER, len(home_rows))
    for tr in home_rows:
        row = parse_home_table_row(tr)
        if not row:
            continue
        key = normalize_space(row.get("provider_course_id"))
        if key in seen:
            continue
        seen.add(key)
        if detail:
            row = enrich_detail(session, row, timeout=timeout)
        if not include_expired and is_expired(row):
            continue
        rows.append(row)
        if limit and len(rows) >= limit:
            return rows

    for page in range(1, max_pages + 1):
        current_url = list_url(page)
        soup = fetch_soup(session, current_url, timeout=timeout)
        cards = soup.select(".bbs_ltype2 > dl")
        logger.info("%s page %s cards=%s", PROVIDER, page, len(cards))
        if not cards:
            break
        page_found = 0
        for card in cards:
            row = parse_list_row(card, page)
            if not row:
                continue
            key = normalize_space(row.get("provider_course_id"))
            if key in seen:
                continue
            seen.add(key)
            if detail:
                row = enrich_detail(session, row, timeout=timeout)
            if not include_expired and is_expired(row):
                continue
            rows.append(row)
            page_found += 1
            if limit and len(rows) >= limit:
                return rows
        if len(cards) == 0 or (page > 3 and page_found == 0 and not include_expired):
            continue
    return rows


def quality_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fields = [
        "title",
        "branch",
        "address",
        "period",
        "schedule_raw",
        "target",
        "fee",
        "status",
        "description",
        "raw_url",
    ]
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
        "address_source": "crawler_detail_or_fallback",
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
    parser.add_argument("--max-pages", type=int, default=30)
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--include-expired", action="store_true")
    parser.add_argument("--no-detail", action="store_true")
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--mark-stale", action="store_true")
    parser.add_argument("--per-target-limit", type=int)
    parser.add_argument("--max-depth", type=int)
    parser.add_argument("--detail-limit", type=int)
    args = parser.parse_args()

    rows = collect(
        limit=args.limit or args.per_target_limit,
        max_pages=args.max_pages,
        timeout=args.timeout,
        include_expired=args.include_expired,
        detail=not args.no_detail,
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
