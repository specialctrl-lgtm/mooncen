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


PROVIDER = "MUNI_WWW_MAPO_GO_KR_7852A077"
PROVIDER_NAME = "마포구 평생학습포털 강좌"
BASE_URL = "https://www.mapo.go.kr"
LIST_URL = f"{BASE_URL}/site/mll/edu/lecture_list"
DEFAULT_BRANCH = "마포구 평생학습포털"
DEFAULT_ADDRESS = "서울특별시 마포구 월드컵로 212"
DEFAULT_PHONE = "02-3153-8114"


ADDRESS_HINTS = {
    "마포구청": "서울특별시 마포구 월드컵로 212",
    "마포구 보건소": "서울특별시 마포구 월드컵로 212",
    "마포구보건소": "서울특별시 마포구 월드컵로 212",
    "마포구평생학습센터": "서울특별시 마포구 홍익로2길 16",
    "평생학습센터": "서울특별시 마포구 홍익로2길 16",
    "염리종합사회복지관": "서울특별시 마포구 대흥로24길 50",
}


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from DB.db_utils import get_db_cursor  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, setup_logger  # noqa: E402


logger = setup_logger("Crawler_MapoLifelong")


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
    if not text:
        return ""
    text = re.sub(
        r"\b(\d{2})[.](\d{1,2})[.](\d{1,2})\b",
        lambda m: f"20{int(m.group(1)):02d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}",
        text,
    )
    text = re.sub(
        r"\b(\d{4})[.](\d{1,2})[.](\d{1,2})\b",
        lambda m: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}",
        text,
    )
    text = re.sub(r"\s*~\s*", " ~ ", text)
    text = re.sub(r"(\d{1,2})\s*:\s*(\d{1,2})", lambda m: f"{int(m.group(1)):02d}:{int(m.group(2)):02d}", text)
    return normalize_space(text)


def fetch_soup(session: requests.Session, url: str, timeout: int) -> BeautifulSoup:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def list_url(page: int) -> str:
    query = {"cp": str(page), "pageSize": "9", "listType": "list"}
    return f"{LIST_URL}?{urlencode(query)}"


def stable_id(*parts: Any) -> str:
    seed = "|".join(normalize_space(part) for part in parts if normalize_space(part))
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def branch_code(branch: Any) -> str:
    return stable_id(PROVIDER, normalize_space(branch) or DEFAULT_BRANCH)[:12]


def normalize_status(value: Any) -> str:
    text = normalize_space(value)
    if any(token in text for token in ["접수중", "오늘마감"]):
        return "OPEN"
    if any(token in text for token in ["접수예정", "예정"]):
        return "SCHEDULED"
    if any(token in text for token in ["접수마감", "교육완료", "마감", "완료"]):
        return "CLOSED"
    if "교육중" in text:
        return "OPEN"
    return text


def normalize_fee(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return ""
    if "무료" in text:
        return "무료"
    amount = re.search(r"[\d,]+\s*원", text)
    return normalize_space(amount.group(0)) if amount else text


def split_list_periods(value: Any) -> tuple[str, str]:
    text = normalize_date_text(value)
    ranges = re.findall(r"\d{4}-\d{2}-\d{2}\s*~\s*\d{4}-\d{2}-\d{2}", text)
    reception_period = normalize_space(ranges[0]) if len(ranges) >= 1 else ""
    period = normalize_space(ranges[1]) if len(ranges) >= 2 else ""
    return reception_period, period


def first_date_range(value: Any) -> str:
    text = normalize_date_text(value)
    match = re.search(r"\d{4}-\d{2}-\d{2}\s*~\s*\d{4}-\d{2}-\d{2}", text)
    return normalize_space(match.group(0)) if match else text


def parse_capacity(value: Any) -> tuple[int | None, int | None, int | None]:
    nums = [int(num.replace(",", "")) for num in re.findall(r"\d[\d,]*", normalize_space(value))]
    if len(nums) >= 3:
        return nums[2], nums[0], nums[1]
    if len(nums) >= 1:
        return None, nums[0], None
    return None, None, None


def extract_lt_seq(raw_url: str) -> str:
    query = parse_qs(urlparse(raw_url).query)
    return (query.get("ltSeq") or [""])[0]


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


def description_from_detail(soup: BeautifulSoup, pairs: dict[str, str]) -> str:
    node = soup.select_one(".lesson-intro-cont")
    text = normalize_space(node.get_text(" ", strip=True)) if node else ""
    if text:
        return text
    parts = []
    for key in ["강좌분야", "교육대상", "교육장소", "강사명", "교육기간", "교육요일", "접수방법", "선정방법"]:
        value = normalize_space(pairs.get(key))
        if value:
            parts.append(f"{key}: {value}")
    return normalize_space(" ".join(parts))


def image_from_detail(soup: BeautifulSoup) -> str:
    for img in soup.select(".lesson-intro-cont img[src], .contents img[src]"):
        src = normalize_space(img.get("src"))
        if src and not src.startswith("/design/theme/"):
            return urljoin(BASE_URL, src)
    return ""


def infer_age_group(target: str, title: str) -> str:
    text = f"{target} {title}"
    if re.search(r"영유아|유아|어린이|아동|초등|개월|손주|부모", text):
        return "KIDS"
    if re.search(r"청소년|중학생|고등학생", text):
        return "TEEN"
    if re.search(r"성인|일반|어르신|임산부|주민|퇴근길", text):
        return "ADULT"
    return ""


def normalize_branch(category: str, venue: str) -> str:
    venue_text = normalize_space(venue)
    category_text = normalize_space(category)
    if "마포구청" in venue_text:
        return "마포구청"
    if "보건소" in venue_text or category_text == "보건교육":
        return "마포구 보건소"
    if "평생학습센터" in venue_text or category_text == "평생학습센터":
        return "마포구평생학습센터"
    if "복지관" in venue_text:
        return re.sub(r"\([^)]*\).*", "", venue_text).strip()
    return category_text or DEFAULT_BRANCH


def address_for(branch: str, venue: str) -> str:
    text = f"{branch} {venue}"
    for key, address in ADDRESS_HINTS.items():
        if key in text:
            return address
    if "마포구" in venue:
        return f"서울특별시 {normalize_space(venue)}"
    return DEFAULT_ADDRESS


def parse_list_row(row: Tag, current_url: str) -> dict[str, Any] | None:
    cells = row.find_all("td", recursive=False)
    if len(cells) < 9:
        return None
    link = cells[1].select_one("a[href]")
    title = normalize_space(link.get_text(" ", strip=True) if link else cells[1].get_text(" ", strip=True))
    if not title:
        return None
    raw_url = urljoin(current_url, link.get("href") if link else "")
    lt_seq = extract_lt_seq(raw_url) or stable_id(raw_url, title)
    reception_period, period = split_list_periods(cells[2].get_text(" ", strip=True))
    category = normalize_space(cells[3].get_text(" ", strip=True))
    venue = normalize_space(cells[4].get_text(" ", strip=True))
    branch = normalize_branch(category, venue)
    capacity_current, capacity_total, waitlist_total = parse_capacity(cells[7].get_text(" ", strip=True))
    status = normalize_status(cells[8].get_text(" ", strip=True))
    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": lt_seq,
        "provider_course_id": lt_seq,
        "title": title,
        "branch": branch,
        "branch_code": branch_code(branch),
        "address": address_for(branch, venue),
        "phone": DEFAULT_PHONE,
        "period": period,
        "schedule_raw": period,
        "target": "",
        "age_group": infer_age_group("", title),
        "fee": normalize_fee(cells[5].get_text(" ", strip=True)),
        "status": status,
        "description": "",
        "image_url": "",
        "raw_url": raw_url,
        "application_url": raw_url,
        "application_type": "ONLINE",
        "application_method_raw": normalize_space(cells[9].get_text(" ", strip=True)) if len(cells) > 9 else "",
        "reservation_available": status == "OPEN",
        "category": category,
        "venue_name": venue,
        "venue_address": address_for(branch, venue),
        "instructor": "",
        "capacity_total": capacity_total,
        "capacity_current": capacity_current,
        "waitlist_total": waitlist_total,
        "reception_period": reception_period,
        "collection_category": "평생학습",
        "domain_category": "평생학습",
        "source_group": "lifelong_learning",
        "operator_type": "지자체/공공기관",
        "collection_type": "static_html",
        "program_type": "OFFLINE",
        "raw_fields": {
            "list_parser": "mapo_lifelong_table",
            "list_category": category,
        },
    }


def is_expired_course(row: dict[str, Any]) -> bool:
    _, end_date = parse_date_range(row.get("period"))
    return bool(end_date and end_date < datetime.now().date())


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    soup = fetch_soup(session, row["raw_url"], timeout)
    pairs = table_pairs(soup)
    row["category"] = normalize_space(pairs.get("강좌분야")) or row.get("category", "")
    row["target"] = normalize_space(pairs.get("교육대상")) or row.get("target", "")
    row["venue_name"] = normalize_space(pairs.get("교육장소")) or row.get("venue_name", "")
    row["instructor"] = normalize_space(pairs.get("강사명")) or row.get("instructor", "")
    row["reception_period"] = normalize_date_text(pairs.get("접수기간")) or row.get("reception_period", "")
    row["period"] = first_date_range(pairs.get("교육기간")) or row.get("period", "")
    day = normalize_space(pairs.get("교육요일"))
    time_match = re.search(r"\d{2}\s*:\s*\d{2}\s*~\s*\d{2}\s*:\s*\d{2}", normalize_date_text(pairs.get("교육기간")))
    time_text = normalize_space(time_match.group(0)) if time_match else ""
    row["schedule_raw"] = normalize_space(f"{row['period']} {day} {time_text}")
    row["fee"] = normalize_fee(pairs.get("수강료")) or row.get("fee", "")
    row["phone"] = normalize_space(pairs.get("문의처")) or row.get("phone", "")
    row["description"] = description_from_detail(soup, pairs)
    row["image_url"] = image_from_detail(soup)
    row["branch"] = normalize_branch(row.get("category", ""), row.get("venue_name", ""))
    row["branch_code"] = branch_code(row["branch"])
    row["address"] = address_for(row["branch"], row.get("venue_name", ""))
    row["venue_address"] = row["address"]
    row["age_group"] = infer_age_group(row.get("target", ""), row["title"])
    row["raw_fields"]["detail_pairs"] = pairs
    row["raw_fields"]["detail_parser"] = "mapo_lifelong_detail"
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
        tables = soup.select("table")
        table = tables[-1] if tables else None
        if not table:
            break
        page_count = 0
        for tr in table.select("tbody tr"):
            row = parse_list_row(tr, current_url)
            if not row or row["provider_course_id"] in seen:
                continue
            seen.add(row["provider_course_id"])
            if detail:
                try:
                    row = enrich_detail(session, row, timeout)
                except Exception as exc:
                    logger.warning("Mapo detail failed %s: %s", row.get("raw_url"), exc)
            if not include_expired and is_expired_course(row):
                logger.info("Skipping expired Mapo course: %s / %s", row.get("title"), row.get("period"))
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
        "address_source": "crawler_detail_or_static",
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
    parser = argparse.ArgumentParser(description="Mapo lifelong course crawler")
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
