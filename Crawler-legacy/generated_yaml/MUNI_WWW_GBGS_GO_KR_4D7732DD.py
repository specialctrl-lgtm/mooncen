from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib3
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup, Tag


PROVIDER = "MUNI_WWW_GBGS_GO_KR_4D7732DD"
PROVIDER_NAME = "경산시 평생학습관 읍면동학습관"
BASE_URL = "https://www.gbgs.go.kr"
LIST_PATH = "/lll/page/2391/1649.tc"
MN = "2391"
PAGE_NO = "1649"
SEARCH_INST_NO = "1"
CATEGORY_RAW = "읍면동학습관"
LIST_URL = (
    f"{BASE_URL}{LIST_PATH}?mn={MN}&pageIndex=1&pageNo={PAGE_NO}&paramIdx=&eduNo=-1"
    f"&searchInstNo={SEARCH_INST_NO}&srchCtgryCd=&srchLlPrgrmCd=&srchRgnCd=&srchEduNm="
)
DEFAULT_BRANCH = "경산시 평생학습관"
DEFAULT_ADDRESS = "경상북도 경산시"
DEFAULT_PHONE = "053-810-5385"
DEFAULT_TARGET = "경산시민"


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from DB.db_utils import get_db_cursor  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, extract_krw_amount, extract_material_fee_amount, setup_logger  # noqa: E402


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = setup_logger("Crawler_GyeongsanLifelongTown")


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


def page_url(page: int) -> str:
    query = {
        "mn": MN,
        "pageIndex": str(page),
        "pageNo": PAGE_NO,
        "paramIdx": "",
        "eduNo": "-1",
        "searchInstNo": SEARCH_INST_NO,
        "srchCtgryCd": "",
        "srchLlPrgrmCd": "",
        "srchRgnCd": "",
        "srchEduNm": "",
    }
    return f"{BASE_URL}{LIST_PATH}?{urlencode(query)}"


def normalize_date_text(value: Any) -> str:
    text = normalize_space(value)
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
    if "무료" in text or text == "0원":
        return "무료"
    amount = extract_krw_amount(text)
    if amount is not None:
        return f"{amount:,}원"
    return text


def normalize_status(apply_period: str, period: str) -> str:
    _apply_start, apply_end = parse_date_range(apply_period)
    _course_start, course_end = parse_date_range(period)
    today = date.today()
    if course_end and course_end < today:
        return "CLOSED"
    if apply_end and apply_end < today:
        return "CLOSED"
    return "OPEN"


def fetch_soup(session: requests.Session, url: str, timeout: int) -> BeautifulSoup:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def lines_from_card(card: Tag) -> list[str]:
    return [normalize_space(line) for line in card.get_text("\n", strip=True).splitlines() if normalize_space(line)]


def extract_labeled(lines: list[str], label: str) -> str:
    prefix = f"{label} :"
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            value = normalize_space(line.split(":", 1)[1])
            if value:
                return value
            if index + 1 < len(lines):
                return normalize_space(lines[index + 1])
    text = " ".join(lines)
    labels = ["수강기간", "수강시간", "접수기간", "신청/정원", "교육장소", "수강료"]
    stop = "|".join(re.escape(f"{item} :") for item in labels if item != label)
    match = re.search(rf"{re.escape(prefix)}\s*(.*?)(?={stop}|$)", text)
    return normalize_space(match.group(1)) if match else ""


def split_title_branch(title_line: str) -> tuple[str, str]:
    match = re.match(r"^\[([^\]]+)\]\s*(.+)$", normalize_space(title_line))
    if match:
        return normalize_space(match.group(1)), normalize_space(match.group(2))
    return DEFAULT_BRANCH, normalize_space(title_line)


def branch_address(branch: str, room: str) -> str:
    branch_text = normalize_space(branch)
    room_text = normalize_space(room)
    paren_values = re.findall(r"\(([^)]*(?:로|길|읍|면|동)[^)]*)\)", room_text)
    if paren_values:
        address = normalize_space(paren_values[-1])
        if not address.startswith(("경상북도", "경산시")):
            address = f"경상북도 경산시 {address}"
        elif address.startswith("경산시"):
            address = f"경상북도 {address}"
        return address
    if re.search(r"(읍|면|동)$", branch_text):
        return f"경상북도 경산시 {branch_text}"
    if "학습관" in room_text and branch_text:
        return f"경상북도 경산시 {branch_text}"
    return DEFAULT_ADDRESS


def parse_capacity(value: Any) -> tuple[int | None, int | None, int | None]:
    text = normalize_space(value)
    if not text or text == "-":
        return None, None, None
    nums = [int(num.replace(",", "")) for num in re.findall(r"\d[\d,]*", text)]
    if len(nums) >= 2:
        return nums[0], nums[1], None
    if len(nums) == 1:
        return None, nums[0], None
    return None, None, None


def parse_card(card: Tag, current_url: str) -> dict[str, Any] | None:
    lines = lines_from_card(card)
    if not lines or "자료가 없습니다" in " ".join(lines):
        return None
    period = normalize_date_text(extract_labeled(lines, "수강기간"))
    schedule = normalize_date_text(extract_labeled(lines, "수강시간"))
    if not period or not schedule:
        return None
    branch, title = split_title_branch(lines[0])
    apply_period = normalize_date_text(extract_labeled(lines, "접수기간"))
    capacity = extract_labeled(lines, "신청/정원")
    room = normalize_space(extract_labeled(lines, "교육장소"))
    fee_raw = normalize_space(extract_labeled(lines, "수강료"))
    fee = normalize_fee(fee_raw)
    material_note = ""
    if "교재비" in fee_raw or "재료비" in fee_raw:
        material_note = fee_raw
    current, total, waitlist = parse_capacity(capacity)
    raw_url = current_url
    row_id = stable_id(title, branch, period, schedule, room)

    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": row_id,
        "provider_course_id": row_id,
        "title": title,
        "branch": branch,
        "branch_code": branch_code(branch),
        "address": branch_address(branch, room),
        "phone": DEFAULT_PHONE,
        "period": period,
        "schedule_raw": normalize_space(" ".join(part for part in [period, schedule] if part)),
        "target": DEFAULT_TARGET,
        "age_group": "ADULT",
        "category_raw": CATEGORY_RAW,
        "fee": fee,
        "material_fee": extract_material_fee_amount(fee_raw),
        "material_note": material_note,
        "status": normalize_status(apply_period, period),
        "raw_url": raw_url,
        "application_url": raw_url,
        "application_type": "ONLINE",
        "description": "\n".join(lines),
        "image_url": "",
        "instructor": "",
        "capacity_current": current,
        "capacity_total": total,
        "waitlist_total": waitlist,
        "apply_period": apply_period,
        "room": room,
        "venue_name": room,
    }


def is_expired(row: dict[str, Any]) -> bool:
    _start, end = parse_date_range(row.get("period"))
    return bool(end and end < date.today())


def collect(
    limit: int | None = None,
    max_pages: int = 20,
    timeout: int = 25,
    include_expired: bool = False,
) -> list[dict[str, Any]]:
    session = make_session()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        current_url = page_url(page)
        soup = fetch_soup(session, current_url, timeout=timeout)
        cards = soup.select("ul.content_list")
        logger.info("%s page %s cards=%s", PROVIDER, page, len(cards))
        if not cards:
            break
        page_added = 0
        for card in cards:
            row = parse_card(card, current_url)
            if not row:
                continue
            key = normalize_space(row.get("provider_course_id"))
            if key in seen:
                continue
            seen.add(key)
            if not include_expired and is_expired(row):
                continue
            rows.append(row)
            page_added += 1
            if limit and len(rows) >= limit:
                return rows
        if page_added == 0:
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
        "address_source": "crawler_branch_fallback",
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
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--mark-stale", action="store_true")
    parser.add_argument("--per-target-limit", type=int)
    parser.add_argument("--max-depth", type=int)
    parser.add_argument("--detail-limit", type=int)
    parser.add_argument("--no-detail", action="store_true")
    args = parser.parse_args()

    rows = collect(
        limit=args.limit or args.per_target_limit,
        max_pages=args.max_pages,
        timeout=args.timeout,
        include_expired=args.include_expired,
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
