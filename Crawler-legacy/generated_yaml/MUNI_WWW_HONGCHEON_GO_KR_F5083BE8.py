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


PROVIDER = "MUNI_WWW_HONGCHEON_GO_KR_F5083BE8"
PROVIDER_NAME = "홍천군 평생학습관 일반교육"
BASE_URL = "https://www.hongcheon.go.kr"
LIST_PATH = "/edu/selectCourseWebList.do"
LIST_URL = f"{BASE_URL}{LIST_PATH}?key=1196&srcEdu=&srcCategory=&srcStatus=&srcTitle="
DEFAULT_BRANCH = "홍천군 평생학습관"
DEFAULT_ADDRESS = "강원특별자치도 홍천군 홍천읍 석화로 93"
DEFAULT_PHONE = "033-430-2583"


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from DB.db_utils import get_db_cursor  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, extract_krw_amount, extract_material_fee_amount, setup_logger  # noqa: E402


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = setup_logger("Crawler_HongcheonCourse")


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
        r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일(?:\s*(\d{1,2})시)?",
        lambda m: (
            f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            + (f" {int(m.group(4)):02d}:00" if m.group(4) else "")
        ),
        text,
    )
    text = re.sub(
        r"(\d{4})[./](\d{1,2})[./](\d{1,2})",
        lambda m: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}",
        text,
    )
    text = re.sub(r"(\d{1,2})\s*:\s*(\d{1,2})", lambda m: f"{int(m.group(1)):02d}:{int(m.group(2)):02d}", text)
    text = re.sub(r"\s*[~∼〜]\s*", " ~ ", text)
    return normalize_space(text)


def normalize_fee(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return ""
    if "무료" in text or text == "0원" or text == "0 원":
        return "무료"
    amount = extract_krw_amount(text)
    if amount is not None:
        return f"{amount:,}원"
    return text


def normalize_status(value: Any, period: str) -> str:
    text = normalize_space(value)
    if "접수중" in text or "접수 중" in text:
        return "OPEN"
    if "접수예정" in text or "예정" in text:
        return "SCHEDULED"
    if any(token in text for token in ["접수마감", "마감", "폐강", "종료"]):
        return "CLOSED"
    _start, end = parse_date_range(period)
    if end and end < date.today():
        return "CLOSED"
    return "OPEN"


def list_url(page: int) -> str:
    query = {
        "key": "1196",
        "pageUnit": "10",
        "srcStatus": "",
        "srcYear": "",
        "srcQuarter": "",
        "srcTitle": "",
        "srcCategory": "",
        "srcEdu": "",
        "pageIndex": str(page),
    }
    return f"{BASE_URL}{LIST_PATH}?{urlencode(query)}"


def fetch_soup(session: requests.Session, url: str, timeout: int) -> BeautifulSoup:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def table_pairs(scope: Tag | BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for tr in scope.select("table tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if not cells:
            continue
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


def detail_course_id(url: str) -> str:
    parsed = urlparse(url)
    return normalize_space(parse_qs(parsed.query).get("course", [""])[0])


def parse_capacity(value: Any) -> tuple[int | None, int | None, int | None]:
    text = normalize_space(value)
    match = re.search(r"(\d[\d,]*)\s*/\s*(\d[\d,]*)", text)
    if match:
        return int(match.group(1).replace(",", "")), int(match.group(2).replace(",", "")), None
    match = re.search(r"(\d[\d,]*)명\s*접수\s*/\s*총\s*(\d[\d,]*)명", text)
    if match:
        return int(match.group(1).replace(",", "")), int(match.group(2).replace(",", "")), None
    return None, None, None


def infer_age_group(target: str, title: str) -> str:
    text = f"{target} {title}"
    if re.search(r"성인|직장인|주민|군민|노인|어르신|학부모", text):
        return "ADULT"
    if re.search(r"청소년|중학생|고등학생|초중고", text):
        return "TEEN"
    if re.search(r"유아|초등|어린이|아동|영유아", text):
        return "KIDS"
    return ""


def branch_address(branch: str) -> str:
    branch = normalize_space(branch)
    if not branch:
        return DEFAULT_ADDRESS
    if "홍천" in branch or branch.endswith(("관", "원", "센터", "도서관", "박물관", "미술관")):
        return f"강원특별자치도 홍천군 {branch}"
    return DEFAULT_ADDRESS


def parse_list_row(tr: Tag, current_url: str) -> dict[str, Any] | None:
    cells = tr.find_all("td", recursive=False)
    if len(cells) < 7:
        return None
    link = tr.select_one("td.subject a[href*='courseWebView.do']")
    if not link:
        return None
    raw_url = urljoin(current_url, link.get("href", ""))
    course_id = detail_course_id(raw_url)
    if not course_id:
        return None
    category = normalize_space(cells[1].get_text(" ", strip=True))
    title = normalize_space(link.get_text(" ", strip=True))
    target = normalize_space(cells[3].get_text(" ", strip=True))
    period = normalize_date_text(cells[4].get_text(" ", strip=True))
    capacity = normalize_space(cells[5].get_text(" ", strip=True))
    status = normalize_space(cells[6].get_text(" ", strip=True))
    current, total, wait_total = parse_capacity(capacity)
    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": course_id,
        "provider_course_id": course_id,
        "title": title,
        "branch": DEFAULT_BRANCH,
        "branch_code": branch_code(DEFAULT_BRANCH),
        "address": DEFAULT_ADDRESS,
        "phone": DEFAULT_PHONE,
        "period": period,
        "schedule_raw": period,
        "target": target,
        "age_group": infer_age_group(target, title),
        "category_raw": category,
        "fee": "",
        "material_fee": None,
        "material_note": "",
        "status": normalize_status(status, period),
        "raw_url": raw_url,
        "application_url": raw_url,
        "application_type": "ONLINE" if "온라인" in status else "",
        "description": "",
        "image_url": "",
        "instructor": "",
        "capacity_current": current,
        "capacity_total": total,
        "waitlist_total": wait_total,
    }


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    try:
        soup = fetch_soup(session, normalize_space(row.get("raw_url")), timeout=timeout)
    except Exception as exc:
        logger.warning("Detail fetch failed %s: %s", row.get("external_id"), exc)
        return row
    pairs = table_pairs(soup)
    row["title"] = normalize_space(pairs.get("강좌명") or row.get("title"))
    row["category_raw"] = normalize_space(pairs.get("분야") or row.get("category_raw"))
    row["target"] = normalize_space(pairs.get("교육대상") or row.get("target"))
    venue = normalize_space(pairs.get("교육장소"))
    if venue:
        row["branch"] = venue[:100]
        row["branch_code"] = branch_code(venue)
        row["address"] = branch_address(venue)
    capacity_current, capacity_total, wait_total = parse_capacity(pairs.get("모집인원"))
    row["capacity_current"] = capacity_current or row.get("capacity_current")
    row["capacity_total"] = capacity_total or row.get("capacity_total")
    row["waitlist_total"] = wait_total or row.get("waitlist_total")
    row["apply_period"] = normalize_date_text(pairs.get("접수기간"))
    row["period"] = normalize_date_text(pairs.get("교육기간") or row.get("period"))
    edu_time = normalize_date_text(pairs.get("교육시간"))
    row["schedule_raw"] = normalize_space(" ".join(part for part in [row.get("period"), edu_time] if part))
    row["instructor"] = normalize_space(pairs.get("강사명"))
    row["fee"] = normalize_fee(pairs.get("수강료"))
    material_text = normalize_space(pairs.get("재료비"))
    row["material_note"] = material_text
    row["material_fee"] = extract_krw_amount(material_text) or extract_material_fee_amount(material_text)
    row["description"] = normalize_space(pairs.get("교육내용"))
    row["phone"] = normalize_space(pairs.get("문의전화") or row.get("phone"))
    row["status"] = normalize_status(row.get("status"), row.get("period", ""))
    row["age_group"] = infer_age_group(row.get("target", ""), row.get("title", ""))
    return row


def is_expired(row: dict[str, Any]) -> bool:
    _start, end = parse_date_range(row.get("period"))
    return bool(end and end < date.today())


def collect(
    limit: int | None = None,
    max_pages: int = 20,
    timeout: int = 25,
    include_expired: bool = False,
    detail: bool = True,
) -> list[dict[str, Any]]:
    session = make_session()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        current_url = list_url(page)
        soup = fetch_soup(session, current_url, timeout=timeout)
        trs = soup.select("table tbody tr")
        logger.info("%s page %s rows=%s", PROVIDER, page, len(trs))
        if not trs:
            break
        page_added = 0
        for tr in trs:
            row = parse_list_row(tr, current_url)
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
            page_added += 1
            if limit and len(rows) >= limit:
                return rows
        if page_added == 0 and page > 3:
            break
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
        "instructor",
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
    parser.add_argument("--max-pages", type=int, default=20)
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
