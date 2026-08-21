from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup, Tag


PROVIDER = "MUNI_WWW_JUNGGU_ULSAN_KR_9703AC0F"
PROVIDER_NAME = "울산 중구 평생학습관 평생교육 강좌"
BASE_URL = "https://www.junggu.ulsan.kr"
LIST_PATH = "/edu/onRequest/selectProgram.do"
LIST_URL = f"{BASE_URL}{LIST_PATH}"
DEFAULT_BRANCH = "울산 중구 평생학습관"
DEFAULT_ADDRESS = "울산광역시 중구 중앙길 136"
DEFAULT_PHONE = "052-290-4760"


ADDRESS_HINTS = {
    "울산 중구 평생학습관": DEFAULT_ADDRESS,
    "중구 평생학습관": DEFAULT_ADDRESS,
    "산전평생학습센터": DEFAULT_ADDRESS,
    "중구문화대학": DEFAULT_ADDRESS,
    "배움의뜰": DEFAULT_ADDRESS,
}


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from DB.db_utils import get_db_cursor  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, extract_krw_amount, setup_logger  # noqa: E402


logger = setup_logger("Crawler_JungguUlsanLifelong")


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
    text = re.sub(r"\s*~\s*", " ~ ", text)
    return text


def fetch_soup(session: requests.Session, url: str, timeout: int, params: dict[str, Any] | None = None) -> BeautifulSoup:
    response = session.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def list_url(page: int) -> str:
    params = {"exec": "list", "currentPage": page, "pagePerCount": 15}
    return f"{LIST_URL}?{urlencode(params)}"


def detail_url(program_id: str, page: int = 1) -> str:
    params = {"exec": "view", "prgId": program_id, "currentPage": page, "pagePerCount": 15}
    return f"{LIST_URL}?{urlencode(params)}"


def stable_id(*parts: Any) -> str:
    seed = "|".join(normalize_space(part) for part in parts if normalize_space(part))
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def branch_code(branch: Any) -> str:
    return stable_id(PROVIDER, normalize_space(branch) or DEFAULT_BRANCH)[:12]


def normalize_status(*values: Any) -> str:
    text = normalize_space(" ".join(str(value or "") for value in values))
    if any(token in text for token in ["신청중", "교육중", "접수중"]):
        return "OPEN"
    if any(token in text for token in ["신청예정", "접수예정"]):
        return "SCHEDULED"
    if any(token in text for token in ["교육종료", "접수마감", "신청마감", "마감"]):
        return "CLOSED"
    return "OPEN" if not text else text


def normalize_fee(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return ""
    if text in {"0", "0원", "무료"}:
        return "무료"
    amount = re.search(r"[\d,]+\s*원", text)
    return normalize_space(amount.group(0)) if amount else text


def first_date_range(value: Any) -> str:
    text = normalize_date_text(value)
    match = re.search(r"\d{4}-\d{2}-\d{2}\s*~\s*\d{4}-\d{2}-\d{2}", text)
    return normalize_space(match.group(0)) if match else text


def normalize_schedule(value: Any) -> str:
    text = normalize_space(value)
    text = re.sub(r"\(\s*([월화수목금토일])\s*\)", r"\1", text)
    text = re.sub(r"\(\s*([월화수목금토일,\s]+)\s*\)", r"\1", text)
    text = re.sub(r"\s*~\s*", " ~ ", text)
    return normalize_space(text)


def infer_branch(title: str, category: str = "", agency: str = "") -> str:
    text = normalize_space(" ".join([title, category, agency]))
    match = re.search(r"^\[[^\]]*?\s+([^)\]]*?(?:평생학습센터|센터|마을학교))\)", text)
    if match:
        return normalize_space(match.group(1))
    match = re.search(r"\[([^\]]*?(?:평생학습센터|센터|마을학교))[)\]]", text)
    if match:
        return normalize_space(match.group(1))
    for name in ADDRESS_HINTS:
        if name in text:
            return name
    return DEFAULT_BRANCH


def address_for(branch: str) -> str:
    for key, address in ADDRESS_HINTS.items():
        if key in branch:
            return address
    return DEFAULT_ADDRESS


def infer_age_group(target: str, title: str) -> str:
    text = f"{target} {title}"
    if re.search(r"영아|유아|\d+\s*[~-]\s*\d+\s*세|초등|어린이|아동", text):
        return "KIDS"
    if re.search(r"청소년|중등|고등|중학생|고등학생", text):
        return "TEEN"
    if re.search(r"성인|주민|구민|일반", text):
        return "ADULT"
    return ""


def parse_capacity(value: Any) -> tuple[int | None, int | None]:
    nums = [int(num.replace(",", "")) for num in re.findall(r"\d[\d,]*", normalize_space(value))]
    if len(nums) >= 2:
        return nums[0], nums[1]
    if len(nums) == 1:
        return nums[0], None
    return None, None


def label_pairs(scope: Tag | BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for dl in scope.select("dl"):
        dt = dl.find("dt")
        dd = dl.find("dd")
        if not dt or not dd:
            continue
        key = normalize_space(dt.get_text(" ", strip=True))
        value = normalize_space(dd.get_text(" ", strip=True))
        if key:
            pairs[key] = value
    return pairs


def table_pairs(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for tr in soup.select("table.table_view tr, table tr"):
        th = tr.find("th")
        td = tr.find("td")
        if not th or not td:
            continue
        key = normalize_space(th.get_text(" ", strip=True))
        value = normalize_space(td.get_text(" ", strip=True))
        if key:
            pairs[key] = value
    return pairs


def extract_image_url(soup: BeautifulSoup) -> str:
    for img in soup.select("#con_body img[src], .table_view img[src]"):
        src = normalize_space(img.get("src"))
        if src and not src.startswith("data:"):
            return src if src.startswith("http") else f"{BASE_URL}{src}"
    return ""


def parse_list_item(item: Tag, page: int) -> dict[str, Any] | None:
    link = item.select_one("a[onclick*='fn_view']")
    if not link:
        return None
    onclick = link.get("onclick") or ""
    id_match = re.search(r"fn_view\('([^']+)'\)", onclick)
    if not id_match:
        return None
    program_id = id_match.group(1)
    pairs = label_pairs(item)
    title = normalize_space(link.get_text(" ", strip=True))
    status = normalize_space(item.select_one(".label").get_text(" ", strip=True) if item.select_one(".label") else "")
    category = normalize_space(pairs.get("분류"))
    period = first_date_range(pairs.get("교육기간"))
    reception_period = first_date_range(pairs.get("접수기간"))
    fee = normalize_fee(pairs.get("수강료"))
    material_fee = normalize_fee(pairs.get("재료비"))
    capacity_current, waitlist_current = parse_capacity(pairs.get("현재 신청/ 대기인원") or pairs.get("현재 신청/대기인원"))
    branch = infer_branch(title, category)
    raw_url = detail_url(program_id, page)
    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": program_id,
        "provider_course_id": program_id,
        "title": title,
        "branch": branch,
        "branch_code": branch_code(branch),
        "address": address_for(branch),
        "phone": DEFAULT_PHONE,
        "period": period,
        "schedule_raw": period,
        "target": "",
        "age_group": infer_age_group("", title),
        "fee": fee,
        "material_fee": extract_krw_amount(material_fee),
        "material_note": material_fee,
        "status": normalize_status(status),
        "status_raw": status,
        "description": "",
        "image_url": "",
        "raw_url": raw_url,
        "application_url": raw_url,
        "application_type": "ONLINE",
        "reservation_available": normalize_status(status) == "OPEN",
        "category": category,
        "venue_name": branch,
        "venue_address": address_for(branch),
        "capacity_current": capacity_current,
        "waitlist_current": waitlist_current,
        "reception_period": reception_period,
        "collection_category": "평생학습",
        "domain_category": "평생학습",
        "source_group": "lifelong_learning",
        "operator_type": "지자체/공공기관",
        "collection_type": "static_html",
        "program_type": "OFFLINE",
        "raw_fields": {"list_pairs": pairs, "list_parser": "junggu_ulsan_lifelong_card"},
    }


def parse_list(soup: BeautifulSoup, page: int) -> list[dict[str, Any]]:
    return [row for item in soup.select(".register_list > ul > li") if (row := parse_list_item(item, page))]


def detail_description(soup: BeautifulSoup, pairs: dict[str, str]) -> str:
    description = normalize_space(pairs.get("강좌소개"))
    if description:
        return description
    body = soup.select_one("#con_body")
    if not body:
        return ""
    for node in body.select("form#searchForm, .buttonFieldCenter, script, style"):
        node.decompose()
    return normalize_space(body.get_text(" ", strip=True))[:2000]


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    soup = fetch_soup(session, row["raw_url"], timeout)
    pairs = table_pairs(soup)
    title = normalize_space(pairs.get("강좌명"))
    if title:
        row["title"] = title
    category = normalize_space(pairs.get("일반/특강")) or row.get("category", "")
    agency = normalize_space(pairs.get("강좌기관"))
    branch = infer_branch(row["title"], category, agency)
    target = normalize_space(pairs.get("교육대상")) or "주민"
    period = first_date_range(pairs.get("교육기간")) or row.get("period", "")
    schedule_time = normalize_schedule(pairs.get("교육시간"))
    schedule_raw = normalize_space(" ".join(part for part in [period, schedule_time] if part))
    capacity_total, waitlist_total = parse_capacity(pairs.get("모집정원"))
    capacity_current, waitlist_current = parse_capacity(pairs.get("현재 신청/대기인원"))
    fee = normalize_fee(pairs.get("수강료")) or row.get("fee", "")
    material_note = normalize_fee(pairs.get("재료(교재)비")) or row.get("material_note", "")
    row.update(
        {
            "branch": branch,
            "branch_code": branch_code(branch),
            "address": address_for(branch),
            "venue_name": branch,
            "venue_address": address_for(branch),
            "category": category,
            "target": target,
            "age_group": infer_age_group(target, row["title"]),
            "reception_period": normalize_date_text(pairs.get("접수기간")) or row.get("reception_period", ""),
            "period": period,
            "schedule_raw": schedule_raw or row.get("schedule_raw", ""),
            "instructor": normalize_space(pairs.get("강사명")),
            "teacher": normalize_space(pairs.get("강사명")),
            "fee": fee,
            "material_fee": extract_krw_amount(material_note),
            "material_note": material_note,
            "capacity_total": capacity_total,
            "capacity_current": capacity_current if capacity_current is not None else row.get("capacity_current"),
            "waitlist_total": waitlist_total,
            "waitlist_current": waitlist_current if waitlist_current is not None else row.get("waitlist_current"),
            "description": detail_description(soup, pairs),
            "image_url": extract_image_url(soup),
        }
    )
    row["raw_fields"]["detail_pairs"] = pairs
    row["raw_fields"]["detail_parser"] = "junggu_ulsan_lifelong_detail"
    return row


def is_expired_course(row: dict[str, Any]) -> bool:
    _, end_date = parse_date_range(row.get("period"))
    if end_date is None:
        return False
    return end_date < datetime.now().date()


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
        soup = fetch_soup(session, list_url(page), timeout)
        page_rows = parse_list(soup, page)
        if not page_rows:
            break
        added = 0
        for row in page_rows:
            key = row["provider_course_id"]
            if key in seen:
                continue
            seen.add(key)
            if detail:
                try:
                    row = enrich_detail(session, row, timeout)
                except Exception as exc:
                    logger.warning("Junggu Ulsan detail failed %s: %s", row.get("raw_url"), exc)
            if not include_expired and is_expired_course(row):
                logger.info("Skipping expired Junggu Ulsan course: %s / %s", row.get("title"), row.get("period"))
                continue
            rows.append(row)
            added += 1
            if limit and len(rows) >= limit:
                return rows
        if added == 0 and not include_expired:
            continue
    return rows


def quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fields = [
        "title",
        "branch",
        "raw_url",
        "address",
        "period",
        "schedule_raw",
        "target",
        "fee",
        "status",
        "description",
        "image_url",
    ]
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


def save_branch_with_address(row: dict[str, Any]) -> str:
    branch = {
        "provider": PROVIDER,
        "branch_code": (normalize_space(row.get("branch_code")) or branch_code(row.get("branch")))[:50],
        "name": (normalize_space(row.get("branch")) or DEFAULT_BRANCH)[:100],
        "address": normalize_space(row.get("address")),
        "phone": normalize_space(row.get("phone")) or DEFAULT_PHONE,
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
    parser = argparse.ArgumentParser(description="Ulsan Junggu lifelong learning crawler")
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
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
