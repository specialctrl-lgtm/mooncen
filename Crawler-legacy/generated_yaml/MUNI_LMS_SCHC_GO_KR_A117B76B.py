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


PROVIDER = "MUNI_LMS_SCHC_GO_KR_A117B76B"
PROVIDER_NAME = "순천시평생교육포털 통합강좌"
BASE_URL = "https://lms.schc.go.kr"
LIST_PATH = "/lms/class_01.do"
LIST_URL = f"{BASE_URL}{LIST_PATH}"
DEFAULT_BRANCH = "순천시평생교육포털"
DEFAULT_ADDRESS = "전라남도 순천시"


BRANCH_ADDRESS_MAP = {
    "인생이모작지원센터": "전라남도 순천시 서문로 7-2",
    "순천시여성문화회관": "전라남도 순천시 장명로 30",
    "여성문화회관": "전라남도 순천시 장명로 30",
    "신대도서관": "전라남도 순천시 해룡면 매안로 162",
    "청소년수련관": "전라남도 순천시 금곡길 20",
    "청소년문화의집": "전라남도 순천시 금곡길 20",
    "평생학습관": "전라남도 순천시 중앙로 232",
    "선비문화체험관": "전라남도 순천시 낙안면 충민길 30",
    "별량 별빛나루": "전라남도 순천시 별량면 별량길 58",
}


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from DB.db_utils import get_db_cursor  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, setup_logger  # noqa: E402


logger = setup_logger("Crawler_SuncheonLifelong")


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
        r"\b(\d{2})-(\d{1,2})-(\d{1,2})\b",
        lambda m: f"20{int(m.group(1)):02d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}",
        text,
    )
    text = re.sub(
        r"\b(\d{4})[.](\d{1,2})[.](\d{1,2})\b",
        lambda m: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}",
        text,
    )
    text = re.sub(r"\s*~\s*", " ~ ", text)
    return normalize_space(text)


def fetch_soup(session: requests.Session, url: str, timeout: int, page: int | None = None) -> BeautifulSoup:
    if page and page > 1:
        response = session.post(
            url,
            data={"nowPage": str(page), "mode": "", "iEduLgrpCd": "1", "type": "list"},
            timeout=timeout,
        )
    else:
        response = session.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def stable_id(*parts: Any) -> str:
    seed = "|".join(normalize_space(part) for part in parts if normalize_space(part))
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def branch_code(branch: Any) -> str:
    return stable_id(PROVIDER, normalize_space(branch) or DEFAULT_BRANCH)[:12]


def detail_url(edu_group: str, class_idx: str) -> str:
    query = {"mode": "view", "iEduLgrpCd": edu_group or "1", "iClassIdx": class_idx}
    return f"{LIST_URL}?{urlencode(query)}"


def normalize_status(value: Any) -> str:
    text = normalize_space(value)
    if "접수중" in text or "대기자접수중" in text:
        return "OPEN"
    if "접수준비" in text or "예정" in text:
        return "SCHEDULED"
    if any(token in text for token in ["정원마감", "대기자마감", "접수마감", "마감", "완료"]):
        return "CLOSED"
    return text


def infer_age_group(target: str, title: str = "") -> str:
    text = f"{target} {title}"
    if re.search(r"유아|어린이|초등|아동", text):
        return "KIDS"
    if re.search(r"청소년|중학생|고등학생", text):
        return "TEEN"
    if re.search(r"성인|중장년|신중년|어르신|46세|20세", text):
        return "ADULT"
    return ""


def normalize_fee(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return ""
    if "무료" in text:
        return "무료"
    amount = re.search(r"[\d,]+\s*원", text)
    return normalize_space(amount.group(0)) if amount else text


def split_title_teacher(value: Any) -> tuple[str, str]:
    text = normalize_space(value)
    match = re.match(r"(.+?)\s*\(([^()]*)\)\s*$", text)
    if match:
        return normalize_space(match.group(1)), normalize_space(match.group(2))
    return text, ""


def extract_go_view(row: Tag) -> tuple[str, str]:
    link = row.select_one('a[href*="goView"]')
    href = link.get("href", "") if link else ""
    match = re.search(r"goView\('([^']+)'\s*,\s*'([^']+)'\)", href)
    return (match.group(1), match.group(2)) if match else ("1", stable_id(href, row.get_text(" ", strip=True)))


def clean_schedule(value: Any) -> str:
    text = normalize_date_text(value)
    text = re.sub(r"\s*/\s*", " / ", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    return normalize_space(text)


def period_from_schedule(schedule: str) -> str:
    match = re.search(r"(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})", schedule)
    if match:
        return f"{match.group(1)} ~ {match.group(2)}"
    return ""


def first_date_range(value: Any) -> str:
    text = normalize_date_text(value)
    match = re.search(r"(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})", text)
    if match:
        return f"{match.group(1)} ~ {match.group(2)}"
    return text


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


def extract_address(text: str) -> str:
    normalized = normalize_space(text)
    matches = re.findall(r"(?:전라남도\s*)?순천시\s+[가-힣A-Za-z0-9·\-]+(?:로|길)\s*\d+(?:-\d+)?", normalized)
    if matches:
        address = normalize_space(matches[-1])
        return address if address.startswith("전라남도") else f"전라남도 {address}"
    return ""


def branch_address(branch: str, venue: str, description: str) -> str:
    address = extract_address(description) or extract_address(venue)
    if address:
        return address
    for key, mapped in BRANCH_ADDRESS_MAP.items():
        if key in branch or key in venue:
            return mapped
    return DEFAULT_ADDRESS


def parse_list_row(row: Tag, current_page: int) -> dict[str, Any] | None:
    cells = row.find_all("td", recursive=False)
    if len(cells) < 9:
        return None
    edu_group, class_idx = extract_go_view(row)
    title, teacher = split_title_teacher(cells[2].get_text(" ", strip=True))
    if not title:
        return None
    branch = normalize_space(cells[1].get_text(" ", strip=True)) or DEFAULT_BRANCH
    schedule_raw = clean_schedule(cells[6].get_text(" ", strip=True))
    period = period_from_schedule(schedule_raw)
    reception_period = normalize_date_text(cells[5].get_text(" ", strip=True))
    status_text = normalize_space(cells[7].get_text(" ", strip=True))
    status_text = f"{status_text} {cells[8].get_text(' ', strip=True)}"
    raw_url = detail_url(edu_group, class_idx)
    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": class_idx,
        "provider_course_id": class_idx,
        "title": title,
        "branch": branch,
        "branch_code": branch_code(branch),
        "address": branch_address(branch, "", ""),
        "phone": "",
        "period": period,
        "schedule_raw": schedule_raw,
        "target": "",
        "age_group": "",
        "fee": normalize_fee(cells[4].get_text(" ", strip=True)),
        "status": normalize_status(status_text),
        "description": "",
        "image_url": "",
        "raw_url": raw_url,
        "application_url": raw_url,
        "application_type": "ONLINE",
        "application_method_raw": "순천시평생교육포털 수강신청",
        "reservation_available": normalize_status(status_text) == "OPEN",
        "category": "",
        "venue_name": branch,
        "venue_address": branch_address(branch, "", ""),
        "instructor": teacher,
        "capacity_total": int(re.search(r"\d+", normalize_space(cells[3].get_text(" ", strip=True))).group(0))
        if re.search(r"\d+", normalize_space(cells[3].get_text(" ", strip=True)))
        else None,
        "reception_period": reception_period,
        "collection_category": "평생학습",
        "domain_category": "평생학습",
        "source_group": "lifelong_learning",
        "operator_type": "지자체/공공기관",
        "collection_type": "static_html",
        "program_type": "OFFLINE",
        "raw_fields": {
            "list_page": current_page,
            "edu_group": edu_group,
            "list_parser": "suncheon_lifelong_table",
        },
    }


def is_expired_course(row: dict[str, Any]) -> bool:
    _, end_date = parse_date_range(row.get("period"))
    return bool(end_date and end_date < datetime.now().date())


def description_text(pairs: dict[str, str]) -> str:
    parts = []
    for key in ["교육내용", "강의명", "강사", "교육대상", "교육기관", "교육장소", "교육일정", "강의일수", "수료기준"]:
        value = normalize_space(pairs.get(key))
        if value and value not in ["강의계획서(첨부파일)", "첨부파일없음"]:
            parts.append(f"{key}: {value}")
    return normalize_space(" ".join(parts))


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    soup = fetch_soup(session, row["raw_url"], timeout)
    pairs = table_pairs(soup)
    h2 = soup.select_one("h2")
    if h2:
        row["title"] = normalize_space(h2.get_text(" ", strip=True)) or row["title"]
    row["instructor"] = normalize_space(pairs.get("강사")) or row.get("instructor", "")
    row["target"] = normalize_space(pairs.get("교육대상")) or row.get("target", "")
    row["fee"] = normalize_fee(pairs.get("수강료")) or row.get("fee", "")
    row["reception_period"] = normalize_date_text(pairs.get("접수기간")) or row.get("reception_period", "")
    row["period"] = first_date_range(pairs.get("교육기간")) or row.get("period", "")
    row["branch"] = normalize_space(pairs.get("교육기관")) or row["branch"]
    row["venue_name"] = normalize_space(pairs.get("교육장소")) or row.get("venue_name", "")
    row["schedule_raw"] = normalize_space(pairs.get("교육일정")) or row.get("schedule_raw", "")
    row["phone"] = normalize_space(pairs.get("문의전화")) or row.get("phone", "")
    description = description_text(pairs)
    row["description"] = description
    row["address"] = branch_address(row["branch"], row.get("venue_name", ""), description)
    row["venue_address"] = row["address"]
    row["branch_code"] = branch_code(row["branch"])
    row["age_group"] = infer_age_group(row.get("target", ""), row["title"])
    row["raw_fields"]["detail_pairs"] = pairs
    row["raw_fields"]["detail_parser"] = "suncheon_lifelong_detail_tables"
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
        soup = fetch_soup(session, LIST_URL, timeout, page=page)
        table = soup.select_one("table.w100, table")
        if not table:
            break
        page_count = 0
        for tr in table.select("tbody tr"):
            row = parse_list_row(tr, page)
            if not row or row["provider_course_id"] in seen:
                continue
            seen.add(row["provider_course_id"])
            if detail:
                try:
                    row = enrich_detail(session, row, timeout)
                except Exception as exc:
                    logger.warning("Suncheon detail failed %s: %s", row.get("raw_url"), exc)
            if not include_expired and is_expired_course(row):
                logger.info("Skipping expired Suncheon course: %s / %s", row.get("title"), row.get("period"))
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
    parser = argparse.ArgumentParser(description="Suncheon lifelong course crawler")
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
