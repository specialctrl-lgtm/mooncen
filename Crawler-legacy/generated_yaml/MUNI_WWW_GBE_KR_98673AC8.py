from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib3
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup, Tag


PROVIDER = "MUNI_WWW_GBE_KR_98673AC8"
PROVIDER_NAME = "경상북도울진교육지원청 프로그램 신청"
BASE_URL = "https://www.gbe.kr"
LIST_PATH = "/uj/eq/view/selectEqList.do"
MI = "22841"
SYS_ID = "uj"
LIST_URL = f"{BASE_URL}{LIST_PATH}?mi={MI}"
DEFAULT_BRANCH = "울진과학발명교육센터"
DEFAULT_ADDRESS = "경상북도 울진군"
DEFAULT_PHONE = ""


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from DB.db_utils import get_db_cursor  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, extract_material_fee_amount, setup_logger  # noqa: E402


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = setup_logger("Crawler_GbeUljinEq")


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
        r"(\d{4})[./](\d{1,2})[./](\d{1,2})",
        lambda m: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}",
        text,
    )
    text = re.sub(r"(\d{1,2})\s*:\s*(\d{1,2})", lambda m: f"{int(m.group(1)):02d}:{int(m.group(2)):02d}", text)
    text = re.sub(r"\s*[~∼]\s*", " ~ ", text)
    return normalize_space(text)


def normalize_status(value: Any) -> str:
    text = normalize_space(value)
    if any(token in text for token in ["마감", "종료", "완료", "취소"]):
        return "CLOSED"
    if any(token in text for token in ["예정", "대기"]):
        return "SCHEDULED"
    if any(token in text for token in ["신청", "접수", "예약"]):
        return "OPEN"
    return text or "OPEN"


def split_category_title(value: Any) -> tuple[str, str]:
    text = normalize_space(value)
    match = re.match(r"^\[([^\]]+)\]\s*(.+)$", text)
    if match:
        return normalize_space(match.group(1)), normalize_space(match.group(2))
    return "", text


def extract_labeled_range(text: str, label: str) -> str:
    pattern = (
        rf"{re.escape(label)}\s*:?\s*"
        r"(\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:\s+\d{1,2}:\d{2})?"
        r"\s*~\s*"
        r"\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:\s+\d{1,2}:\d{2})?"
        r"(?:\s*\([^)]*\))?)"
    )
    match = re.search(pattern, normalize_date_text(text))
    return normalize_space(match.group(1)) if match else ""


def parse_period_and_schedule(text: str) -> tuple[str, str]:
    raw = normalize_date_text(text)
    value = extract_labeled_range(raw, "강좌기간") or extract_labeled_range(raw, "교육기간")
    if not value:
        return "", ""
    date_match = re.search(r"\d{4}-\d{2}-\d{2}\s*~\s*\d{4}-\d{2}-\d{2}", value)
    period = normalize_space(date_match.group(0)) if date_match else value
    time_match = re.search(r"\(([^)]*\d{1,2}:\d{2}[^)]*)\)", value)
    schedule = normalize_space(f"{period} {time_match.group(1)}") if time_match else period
    return period, schedule


def parse_reception_period(text: str) -> str:
    return extract_labeled_range(text, "접수기간") or extract_labeled_range(text, "신청기간")


def parse_capacity(value: Any) -> tuple[int | None, int | None, int | None]:
    text = normalize_space(value)
    apply_match = re.search(r"신청\s*:\s*(\d+)\s*명\s*/\s*(\d+)\s*명", text)
    wait_match = re.search(r"대기\s*:\s*(\d+)\s*명\s*/\s*(\d+)\s*명", text)
    current = int(apply_match.group(1)) if apply_match else None
    total = int(apply_match.group(2)) if apply_match else None
    wait_total = int(wait_match.group(2)) if wait_match else None
    return current, total, wait_total


def infer_target(category: str, title: str, detail_target: str = "") -> str:
    text = f"{category} {title} {detail_target}"
    if "학부모" in text:
        return "학부모"
    if "교직원" in text or "교원" in text or "관리자" in text or "지도강사" in text:
        return "교원"
    if "지역주민" in text:
        return "성인"
    if "학생" in text or "초급" in text or "정규교육과정" in text or "특별교육과정" in text or "방학" in text:
        return "학생"
    return ""


def infer_age_group(target: str) -> str:
    if target in {"교원", "학부모", "성인"}:
        return "ADULT"
    if target == "학생":
        return "KIDS"
    return ""


def detail_url(eq_sn: str) -> str:
    return f"{BASE_URL}/uj/eq/view/selectEqInfo.do?{urlencode({'mi': MI, 'eqSn': eq_sn})}"


def fetch_soup(
    session: requests.Session,
    url: str,
    timeout: int,
    method: str = "GET",
    data: dict[str, str] | None = None,
) -> BeautifulSoup:
    if method.upper() == "POST":
        response = session.post(url, data=data or {}, timeout=timeout)
    else:
        response = session.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def list_payload(page: int) -> dict[str, str]:
    return {
        "currPage": str(page),
        "maxSn": "20",
        "pageIndex": "20",
        "sysId": SYS_ID,
        "mi": MI,
        "minSn": "0",
    }


def parse_detail_table(soup: BeautifulSoup) -> dict[str, str]:
    values: dict[str, str] = {}
    for tr in soup.select("table tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if len(cells) < 2:
            continue
        label = normalize_space(cells[0].get_text(" ", strip=True))
        value = normalize_space(cells[1].get_text(" ", strip=True))
        if label and value:
            values[label] = value
    return values


def fetch_detail(session: requests.Session, eq_sn: str, timeout: int) -> dict[str, str]:
    try:
        soup = fetch_soup(session, detail_url(eq_sn), timeout)
    except requests.RequestException as exc:
        logger.warning("%s detail fetch failed: %s", eq_sn, exc)
        return {}
    return parse_detail_table(soup)


def build_description(
    category: str,
    reception_period: str,
    schedule_raw: str,
    capacity_text: str,
    method: str,
    detail: dict[str, str],
) -> str:
    detail_description = detail.get("강좌내용", "")
    parts = [
        f"분류: {category}" if category else "",
        f"접수기간: {reception_period}" if reception_period else "",
        f"교육기간: {schedule_raw}" if schedule_raw else "",
        f"모집: {capacity_text}" if capacity_text else "",
        f"접수방식: {method}" if method else "",
        detail_description,
    ]
    return normalize_space(" ".join(part for part in parts if part))


def parse_row(tr: Tag, session: requests.Session, timeout: int, fetch_details: bool = True) -> dict[str, Any] | None:
    cells = tr.find_all("td", recursive=False)
    if len(cells) < 5:
        return None
    title_cell = cells[0]
    title_node = title_cell.select_one("p")
    title_text = normalize_space(title_node.get_text(" ", strip=True) if title_node else title_cell.get_text(" ", strip=True))
    category, title = split_category_title(title_text)
    if not title:
        return None

    full_text = normalize_space(title_cell.get_text(" ", strip=True))
    period, schedule_raw = parse_period_and_schedule(full_text)
    reception_period = parse_reception_period(full_text)
    capacity_text = normalize_space(cells[1].get_text(" ", strip=True))
    capacity_current, capacity_total, waitlist_total = parse_capacity(capacity_text)
    method = normalize_space(cells[2].get_text(" ", strip=True))
    button = cells[4].select_one("button[data-id]")
    eq_sn = normalize_space(button.get("data-id")) if button else stable_id(title, period)
    status_text = normalize_space(button.get_text(" ", strip=True) if button else cells[4].get_text(" ", strip=True))
    detail = fetch_detail(session, eq_sn, timeout) if fetch_details else {}

    detail_period, detail_schedule = parse_period_and_schedule(
        f"교육기간: {detail.get('교육기간', '')}" if detail.get("교육기간") else ""
    )
    period = detail_period or period
    schedule_raw = detail_schedule or schedule_raw
    reception_period = (
        parse_reception_period(f"신청기간: {detail.get('신청기간', '')}") if detail.get("신청기간") else reception_period
    )
    target = infer_target(category, title, detail.get("참가대상자구분", ""))
    description = build_description(category, reception_period, schedule_raw, capacity_text, method, detail)

    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": eq_sn,
        "provider_course_id": eq_sn,
        "title": title,
        "branch": DEFAULT_BRANCH,
        "branch_code": branch_code(DEFAULT_BRANCH),
        "address": DEFAULT_ADDRESS,
        "phone": DEFAULT_PHONE,
        "period": period,
        "schedule_raw": schedule_raw,
        "target": target,
        "age_group": infer_age_group(target),
        "category_raw": category,
        "fee": "무료",
        "material_fee": extract_material_fee_amount(description),
        "material_note": "",
        "status": normalize_status(status_text),
        "raw_url": detail_url(eq_sn),
        "application_url": LIST_URL,
        "application_type": "ONLINE" if "인터넷" in method else "",
        "application_method_raw": method,
        "description": description,
        "image_url": "",
        "instructor": "",
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "waitlist_total": waitlist_total,
        "reception_period": reception_period,
        "collection_category": "교육·체험",
        "domain_category": "평생학습",
        "source_group": "lifelong_learning",
        "operator_type": "지자체/공공기관",
        "collection_type": "static_html",
        "program_type": "OFFLINE",
    }


def is_expired(row: dict[str, Any]) -> bool:
    try:
        _start, end = parse_date_range(row.get("period") or row.get("schedule_raw") or "")
    except Exception:  # noqa: BLE001
        return False
    return bool(end and end < datetime.now().date())


def collect(
    limit: int | None = None,
    max_pages: int = 3,
    timeout: int = 20,
    include_expired: bool = False,
    fetch_details: bool = True,
) -> list[dict[str, Any]]:
    session = make_session()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        method = "GET" if page == 1 else "POST"
        soup = fetch_soup(session, LIST_URL, timeout, method=method, data=list_payload(page) if page > 1 else None)
        page_count = 0
        for tr in soup.select("table tbody tr, table tr"):
            row = parse_row(tr, session, timeout, fetch_details=fetch_details)
            if not row or row["provider_course_id"] in seen:
                continue
            seen.add(row["provider_course_id"])
            if not include_expired and is_expired(row):
                continue
            rows.append(row)
            page_count += 1
            if limit and len(rows) >= limit:
                return rows
        if page_count == 0:
            break
    return rows


def quality_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fields = ["title", "branch", "address", "period", "schedule_raw", "target", "fee", "status", "description", "raw_url"]
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
        "address_source": "crawler_fallback",
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
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--include-expired", action="store_true")
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--mark-stale", action="store_true")
    parser.add_argument("--no-detail", action="store_true")
    parser.add_argument("--per-target-limit", type=int)
    parser.add_argument("--max-depth", type=int)
    parser.add_argument("--detail-limit", type=int)
    args = parser.parse_args()

    rows = collect(
        limit=args.limit or args.per_target_limit,
        max_pages=args.max_pages,
        timeout=args.timeout,
        include_expired=args.include_expired,
        fetch_details=not args.no_detail,
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
