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


PROVIDER = "MUNI_HOME_PEN_GO_KR_92635850"
PROVIDER_NAME = "부산광역시교육청 통합예약포털 견학체험"
BASE_URL = "https://home.pen.go.kr"
LIST_PATH = "/yeyak/exprn/selectExprnList.do"
DETAIL_PATH = "/yeyak/exprn/selectExprnInfo.do"
LIST_URL = f"{BASE_URL}{LIST_PATH}?mi=14438"
DEFAULT_BRANCH = "부산광역시교육청 통합예약포털"
DEFAULT_ADDRESS = "부산광역시 부산진구 화지로 12"
DEFAULT_PHONE = "051-1396"


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from DB.db_utils import get_db_cursor  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, setup_logger  # noqa: E402


logger = setup_logger("Crawler_BusanEducationReservation")


STATIC_BRANCH_ADDRESSES = {
    "부산광역시교육청": "부산광역시 부산진구 화지로 12",
    "영양교육체험센터": "부산광역시 영도구 중리북로 31",
    "어린이창의교육관": "부산광역시 부산진구 성지곡로 15",
    "남부메이커교육체험센터": "부산광역시 남구 진남로 82-19",
    "놀이마루": "부산광역시 부산진구 전포대로209번길 26",
    "부산과학체험관": "부산광역시 동구 중앙대로260번길 11",
    "부산교육역사관": "부산광역시 부산진구 성지곡로 17",
    "부산수학문화관": "부산광역시 부산진구 가야대로 734",
    "학생교육문화회관": "부산광역시 부산진구 성지곡로 15",
    "학생안전체험관": "부산광역시 북구 산성로 87-22",
    "학생예술문화회관": "부산광역시 북구 낙동북로 737-1",
    "학생인성교육원": "부산광역시 금정구 북문로 178",
    "유아교육진흥원": "부산광역시 사하구 다대로529번길 11",
    "창의융합교육원": "부산광역시 연제구 토곡로 66",
}


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


def stable_id(*parts: Any) -> str:
    seed = "|".join(normalize_space(part) for part in parts if normalize_space(part))
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def branch_code(branch: Any) -> str:
    return stable_id(PROVIDER, normalize_space(branch) or DEFAULT_BRANCH)[:12]


def normalize_date_text(value: Any) -> str:
    text = normalize_space(value).replace("/", "-")
    text = re.sub(r"\s*~\s*", " ~ ", text)
    text = re.sub(r"\((월|화|수|목|금|토|일)\)", "", text)
    return normalize_space(text)


def normalize_status(value: Any) -> str:
    text = normalize_space(value)
    if any(token in text for token in ["접수중", "예약가능"]):
        return "OPEN"
    if any(token in text for token in ["예정", "대기접수"]):
        return "SCHEDULED"
    if any(token in text for token in ["마감", "종료", "예약불가", "END"]):
        return "CLOSED"
    return "OPEN" if not text else text


def infer_age_group(target: str, title: str) -> str:
    text = f"{target} {title}"
    if re.search(r"유치원|유아|초등|초\d|어린이|학생", text):
        return "KIDS"
    if re.search(r"중학교|고등학교|청소년|중학생|고등학생", text):
        return "TEEN"
    if re.search(r"학부모|교직원|일반|성인|회원전체", text):
        return "ADULT"
    return ""


def load_facility_addresses() -> dict[str, str]:
    path = ROOT / "config" / "facility_registry_crawl_targets.yaml"
    if not path.exists():
        return {}
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        logger.warning("Failed to load facility registry addresses: %s", exc)
        return {}
    rows = data.get("targets") if isinstance(data, dict) else data
    addresses: dict[str, str] = {}
    if not isinstance(rows, list):
        return addresses
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = normalize_space(row.get("name") or row.get("branch"))
        address = normalize_space(row.get("address"))
        if name and address and "부산" in address:
            addresses.setdefault(name, address)
            short = re.sub(r"^부산광역시립", "", name)
            if short != name:
                addresses.setdefault(short, address)
    return addresses


FACILITY_ADDRESSES = {**STATIC_BRANCH_ADDRESSES, **load_facility_addresses()}


def address_for(branch: str) -> str:
    branch = normalize_space(branch)
    if branch in FACILITY_ADDRESSES:
        return FACILITY_ADDRESSES[branch]
    for name, address in FACILITY_ADDRESSES.items():
        if branch in name or name in branch:
            return address
    return DEFAULT_ADDRESS


def fetch_soup(session: requests.Session, url: str, timeout: int, params: dict[str, Any] | None = None) -> BeautifulSoup:
    response = session.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def list_params(page: int, page_size: int = 10) -> dict[str, Any]:
    return {
        "mi": "14438",
        "currPage": str(page),
        "pageIndex": str(page_size),
        "srchRsvSttus": ["REQST", "PREV"],
        "srchPeriodDiv": "rcept",
    }


def detail_url(exprn_seq: str, period_seq: str) -> str:
    params = {"mi": "14438", "exprnSeq": exprn_seq, "exprnPeriodSeq": period_seq, "srchRsvSttus": "REQST,PREV"}
    return f"{BASE_URL}{DETAIL_PATH}?{urlencode(params)}"


def text_lines(cell: Tag) -> list[str]:
    return [normalize_space(line) for line in cell.get_text("\n", strip=True).splitlines() if normalize_space(line)]


def parse_list_row(tr: Tag) -> dict[str, Any] | None:
    cells = tr.find_all("td", recursive=False)
    if len(cells) < 8:
        return None
    link = tr.select_one(".viewExprnInfo[data-id][data-period-id]")
    if not link:
        return None
    exprn_seq = normalize_space(link.get("data-id"))
    period_seq = normalize_space(link.get("data-period-id"))
    rs_sys_id = normalize_space(link.get("data-rssysid"))
    branch = normalize_space(cells[1].get_text(" ", strip=True)) or DEFAULT_BRANCH
    title = normalize_space(link.get_text(" ", strip=True))
    subtitle_lines = [line for line in text_lines(cells[2]) if line != title]
    subtitle = normalize_space(" ".join(subtitle_lines))
    period = normalize_date_text(" ".join(text_lines(cells[3])))
    reception_period = normalize_date_text(" ".join(text_lines(cells[4])))
    target = normalize_space(", ".join(text_lines(cells[5])))
    application_target = normalize_space(cells[6].get_text(" ", strip=True))
    status_raw = normalize_space(cells[7].get_text(" ", strip=True))
    raw_url = detail_url(exprn_seq, period_seq)
    provider_course_id = f"{exprn_seq}:{period_seq}"
    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": provider_course_id,
        "provider_course_id": provider_course_id,
        "title": normalize_space(" ".join(part for part in [title, subtitle] if part)),
        "branch": branch,
        "branch_code": branch_code(branch),
        "address": address_for(branch),
        "phone": DEFAULT_PHONE,
        "period": period,
        "schedule_raw": period,
        "target": target,
        "age_group": infer_age_group(target, title),
        "fee": "무료",
        "material_fee": None,
        "status": normalize_status(status_raw),
        "status_raw": status_raw,
        "description": "",
        "image_url": "",
        "raw_url": raw_url,
        "application_url": raw_url,
        "application_type": "ONLINE",
        "application_method_raw": application_target,
        "reservation_available": normalize_status(status_raw) == "OPEN",
        "category": "견학체험",
        "venue_name": branch,
        "venue_address": address_for(branch),
        "reception_period": reception_period,
        "collection_category": "공공예약",
        "domain_category": "교육체험",
        "source_group": "public_reservation",
        "operator_type": "교육청/공공기관",
        "collection_type": "static_html+detail_html",
        "program_type": "OFFLINE",
        "raw_fields": {
            "exprn_seq": exprn_seq,
            "exprn_period_seq": period_seq,
            "rs_sys_id": rs_sys_id,
            "list_parser": "busan_pen_exprn_table",
        },
    }


def parse_list(soup: BeautifulSoup) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for tr in soup.select("table tbody tr"):
        row = parse_list_row(tr)
        if row:
            rows.append(row)
    return rows


def detail_pairs(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    headings = ["운영기관", "운영기간", "접수기간", "신청대상", "체험대상", "예약지역"]
    text = (soup.select_one("#contents") or soup.select_one("body") or soup).get_text("\n", strip=True)
    lines = [normalize_space(line) for line in text.splitlines() if normalize_space(line)]
    for idx, line in enumerate(lines):
        if line in headings and idx + 1 < len(lines):
            values: list[str] = []
            for next_line in lines[idx + 1 :]:
                if next_line in headings or next_line in {"이용안내", "체험안내", "유의사항", "목록"}:
                    break
                values.append(next_line)
            pairs[line] = normalize_space(" ".join(values))
    return pairs


def description_from_detail(soup: BeautifulSoup) -> str:
    body = soup.select_one("#contents") or soup.select_one("body")
    if not body:
        return ""
    text = body.get_text("\n", strip=True)
    lines = [normalize_space(line) for line in text.splitlines() if normalize_space(line)]
    sections = []
    for label in ["이용안내 콘텐츠", "체험안내 콘텐츠", "유의사항 콘텐츠"]:
        if label in lines:
            idx = lines.index(label)
            chunk = []
            for line in lines[idx + 1 :]:
                if line in {"체험안내 콘텐츠", "유의사항 콘텐츠", "전체메뉴", "목록"}:
                    break
                chunk.append(line)
            if chunk:
                sections.append(f"{label}: " + " ".join(chunk))
    if sections:
        return normalize_space(" ".join(sections))[:2500]
    return ""


def extract_capacity(soup: BeautifulSoup) -> tuple[int | None, int | None, int | None]:
    text = soup.get_text(" ", strip=True)
    match = re.search(r"\[(\d+)\s*/\s*(\d+)\]\s*\(대기\s*(\d+)\s*/\s*(\d+)\)", text)
    if match:
        return int(match.group(1)), int(match.group(2)), int(match.group(4))
    return None, None, None


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    soup = fetch_soup(session, row["raw_url"], timeout)
    pairs = detail_pairs(soup)
    current, total, wait_total = extract_capacity(soup)
    branch = normalize_space(pairs.get("운영기관")) or row["branch"]
    target = normalize_space(pairs.get("체험대상")) or row["target"]
    period = normalize_date_text(pairs.get("운영기간")) or row["period"]
    row.update(
        {
            "branch": branch,
            "branch_code": branch_code(branch),
            "address": address_for(branch),
            "venue_name": branch,
            "venue_address": address_for(branch),
            "period": period,
            "schedule_raw": period,
            "reception_period": normalize_date_text(pairs.get("접수기간")) or row["reception_period"],
            "target": target,
            "age_group": infer_age_group(target, row["title"]),
            "application_method_raw": normalize_space(pairs.get("신청대상")) or row["application_method_raw"],
            "description": description_from_detail(soup),
            "capacity_current": current,
            "capacity_total": total,
            "waitlist_total": wait_total,
        }
    )
    row["raw_fields"]["detail_pairs"] = pairs
    row["raw_fields"]["detail_parser"] = "busan_pen_exprn_detail"
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
        soup = fetch_soup(session, f"{BASE_URL}{LIST_PATH}", timeout, params=list_params(page))
        page_rows = parse_list(soup)
        if not page_rows:
            break
        for row in page_rows:
            key = row["provider_course_id"]
            if key in seen:
                continue
            seen.add(key)
            if detail:
                try:
                    row = enrich_detail(session, row, timeout)
                except Exception as exc:
                    logger.warning("Busan PEN detail failed %s: %s", row.get("raw_url"), exc)
            if not include_expired and is_expired_course(row):
                logger.info("Skipping expired Busan PEN course: %s / %s", row.get("title"), row.get("period"))
                continue
            rows.append(row)
            if limit and len(rows) >= limit:
                return rows
    return rows


def quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fields = ["title", "branch", "raw_url", "address", "period", "schedule_raw", "target", "fee", "status", "description", "image_url"]
    counts = {field: sum(1 for row in rows if clean_text(row.get(field))) for field in fields}
    score = round(sum(counts.values()) / (len(rows) * len(fields)) * 100, 1) if rows else 0.0
    branch_count = len({row.get("branch") for row in rows if row.get("branch")})
    return {"rows": len(rows), "score": score, "branches": branch_count, "field_counts": counts}


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
                    normalize_space(row.get("target")),
                    normalize_space(row.get("status")),
                ]
            )
        )


def save_branch_with_address(row: dict[str, Any]) -> str:
    branch = {
        "provider": PROVIDER,
        "branch_code": (normalize_space(row.get("branch_code")) or branch_code(row.get("branch")))[:50],
        "name": (normalize_space(row.get("branch")) or DEFAULT_BRANCH)[:100],
        "address": normalize_space(row.get("address")) or DEFAULT_ADDRESS,
        "phone": normalize_space(row.get("phone")) or DEFAULT_PHONE,
        "website_url": LIST_URL,
        "address_source": "facility_registry_or_static",
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
    parser = argparse.ArgumentParser(description="Busan PEN integrated experience reservation crawler")
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
