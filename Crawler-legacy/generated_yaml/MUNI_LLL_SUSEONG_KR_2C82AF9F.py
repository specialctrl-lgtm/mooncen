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


PROVIDER = "MUNI_LLL_SUSEONG_KR_2C82AF9F"
PROVIDER_NAME = "수성구 평생교육 플랫폼 러닝톡 강좌 및 수강신청"
BASE_URL = "https://lll.suseong.kr"
MENU_ID = "00001969"
LIST_URL = f"{BASE_URL}/index.do?menu_id={MENU_ID}&menu_link=/reservation/learning/searchLearning.do"
DETAIL_MENU_LINK = "/reservation/learning/details.do"
DEFAULT_BRANCH = "수성구 평생교육 플랫폼 러닝톡"
DEFAULT_ADDRESS = "대구광역시 수성구"
DEFAULT_PHONE = ""


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from DB.db_utils import get_db_cursor  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, extract_krw_amount, extract_material_fee_amount, setup_logger  # noqa: E402


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = setup_logger("Crawler_SuseongLearningTalk")


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

    def year2(match: re.Match[str]) -> str:
        return f"20{int(match.group(1)):02d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"

    text = re.sub(r"\b(\d{2})[.](\d{1,2})[.](\d{1,2})\b", year2, text)
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
    if any(token in text for token in ["신청마감", "마감", "교육종료", "종료"]):
        return "CLOSED"
    if any(token in text for token in ["신청하기", "신청중", "접수중"]):
        return "OPEN"
    if any(token in text for token in ["신청예정", "교육예정", "예정"]):
        return "SCHEDULED"
    if "교육중" in text:
        return "OPEN"
    return text or "OPEN"


def normalize_fee(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return ""
    if "무료" in text or text == "0원":
        return "무료"
    amount = extract_krw_amount(text)
    if amount:
        return f"{amount:,}원"
    return text


def detail_url(crs_id: str) -> str:
    menu_link = f"{DETAIL_MENU_LINK}?crsId={crs_id}"
    return f"{BASE_URL}/index.do?{urlencode({'menu_id': MENU_ID, 'menu_link': menu_link})}"


def list_payload(page: int) -> dict[str, str]:
    return {
        "c_search_ch_sub": "",
        "keyword": "",
        "b_search_ch": "",
        "c_search_ch": "",
        "searchKeyword2": "",
        "u_search_ch": "",
        "d_search_ch": "",
        "e_search_ch": "",
        "f_search_ch": "",
        "pageIndex": str(page),
    }


def fetch_soup(session: requests.Session, url: str, timeout: int, method: str = "GET", data: dict[str, str] | None = None) -> BeautifulSoup:
    if method.upper() == "POST":
        response = session.post(url, data=data or {}, timeout=timeout)
    else:
        response = session.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


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
                value = normalize_space(cells[i + 1].get_text(" ", strip=True))
                i += 2
            else:
                i += 1
            if key:
                pairs[key] = value
    return pairs


def extract_crs_id(href_or_onclick: str) -> str:
    match = re.search(r"fn_learning_details\('([^']+)'", href_or_onclick or "")
    return normalize_space(match.group(1)) if match else ""


def parse_capacity(value: Any) -> tuple[int | None, int | None, int | None]:
    text = normalize_space(value)
    current = total = None
    wait_total = None
    match = re.search(r"(?:인터넷|온라인)\s*:\s*(\d+)\s*/\s*(\d+)", text)
    if match:
        current, total = int(match.group(1)), int(match.group(2))
    match = re.search(r"온라인\s*:\s*(\d+)명\s*\(현재\s*신청인원\s*:\s*(\d+)명\)", text)
    if match:
        total, current = int(match.group(1)), int(match.group(2))
    return current, total, wait_total


def parse_year_age_limit(value: Any) -> tuple[int | None, int | None]:
    text = normalize_space(value)
    match = re.search(r"(\d{4})\s*~\s*(\d{4})\s*년생", text)
    if not match:
        return None, None
    current_year = datetime.now().year
    birth_min, birth_max = int(match.group(1)), int(match.group(2))
    min_age = max(0, current_year - birth_max)
    max_age = max(0, current_year - birth_min)
    return min_age * 12, max_age * 12


def infer_age_group(target: str, title: str) -> str:
    text = f"{target} {title}"
    if re.search(r"유아|어린이|초등|아동|6~7세|초등", text):
        return "KIDS"
    if re.search(r"청소년|중학생|고등학생", text):
        return "TEEN"
    if re.search(r"성인|학부모|인생학교|어르신|중장년", text):
        return "ADULT"
    return ""


def parse_list_row(tr: Tag) -> dict[str, Any] | None:
    cells = tr.find_all("td", recursive=False)
    if len(cells) < 8:
        return None
    link = cells[1].select_one("a[onclick]")
    crs_id = extract_crs_id(link.get("onclick", "") if link else "")
    if not crs_id:
        return None
    title = normalize_space(cells[1].select_one(".lecture").get_text(" ", strip=True) if cells[1].select_one(".lecture") else cells[1].get_text(" ", strip=True))
    branch = normalize_space(cells[1].select_one(".educational").get_text(" ", strip=True) if cells[1].select_one(".educational") else DEFAULT_BRANCH)
    time_text = normalize_date_text(cells[2].get_text(" ", strip=True))
    period_text = normalize_date_text(cells[3].get_text(" ", strip=True))
    dates = re.findall(r"\d{4}-\d{2}-\d{2}\s*~\s*\d{4}-\d{2}-\d{2}", period_text)
    reception_period = normalize_space(dates[0]) if len(dates) >= 1 else ""
    period = normalize_space(dates[1]) if len(dates) >= 2 else ""
    day_time_match = re.search(r"요일 시간\s*(.+)$", period_text)
    day_time = normalize_space(day_time_match.group(1)) if day_time_match else time_text.replace("시간 :", "").strip()
    fee_text = normalize_space(cells[4].get_text(" ", strip=True))
    material_match = re.search(r"재료비\s*:\s*(.+)$", fee_text)
    capacity_current, capacity_total, waitlist_total = parse_capacity(cells[5].get_text(" ", strip=True))
    method = normalize_space(cells[6].get_text(" ", strip=True)).replace("접수방법 :", "").strip()
    status_text = normalize_space(cells[7].get_text(" ", strip=True))

    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": crs_id,
        "provider_course_id": crs_id,
        "title": title,
        "branch": branch,
        "branch_code": branch_code(branch),
        "address": DEFAULT_ADDRESS,
        "phone": DEFAULT_PHONE,
        "period": period,
        "schedule_raw": normalize_space(" ".join(part for part in [period, day_time] if part)),
        "target": "",
        "age_group": infer_age_group("", title),
        "category_raw": "",
        "fee": normalize_fee(fee_text.split("/", 1)[0].replace("수강료 :", "")),
        "material_fee": extract_krw_amount(material_match.group(1)) if material_match else None,
        "material_note": normalize_space(material_match.group(1)) if material_match else "",
        "status": normalize_status(status_text),
        "raw_url": detail_url(crs_id),
        "application_url": detail_url(crs_id),
        "application_type": "ONLINE" if "인터넷" in method else "",
        "application_method_raw": method,
        "description": "",
        "image_url": "",
        "instructor": "",
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "waitlist_total": waitlist_total,
        "reception_period": reception_period,
        "collection_category": "평생학습",
        "domain_category": "평생학습",
        "source_group": "lifelong_learning",
        "operator_type": "지자체/공공기관",
        "collection_type": "static_html+detail_html",
        "program_type": "OFFLINE",
    }


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    soup = fetch_soup(session, row["raw_url"], timeout)
    pairs = table_pairs(soup)
    branch = normalize_space(pairs.get("교육기관") or row.get("branch") or DEFAULT_BRANCH)
    title = normalize_space(pairs.get("강좌명") or row.get("title"))
    period = normalize_date_text(pairs.get("교육기간") or row.get("period"))
    schedule_raw = normalize_date_text(pairs.get("교육시간") or row.get("schedule_raw"))
    if period and schedule_raw and period not in schedule_raw:
        schedule_raw = normalize_space(f"{period} {schedule_raw}")
    target = normalize_space(pairs.get("교육대상")).strip("| ") or row.get("target", "")
    address = normalize_space(pairs.get("주소") or row.get("address") or DEFAULT_ADDRESS)
    description = normalize_space(" ".join(part for part in [pairs.get("강좌소개"), pairs.get("오시는 길")] if normalize_space(part)))
    capacity_current, capacity_total, waitlist_total = parse_capacity(pairs.get("모집인원"))
    min_month, max_month = parse_year_age_limit(pairs.get("연령제한"))

    row.update(
        {
            "title": title,
            "branch": branch,
            "branch_code": branch_code(branch),
            "address": address,
            "phone": normalize_space(pairs.get("문의전화") or row.get("phone")),
            "period": period,
            "schedule_raw": schedule_raw,
            "target": target,
            "age_group": infer_age_group(target, title),
            "category_raw": normalize_space(" > ".join(part for part in [pairs.get("강좌분류"), pairs.get("내용분류"), pairs.get("내용별 분류")] if normalize_space(part))),
            "fee": normalize_fee(pairs.get("수강료") or row.get("fee")),
            "material_fee": extract_krw_amount(pairs.get("재료비")) or extract_material_fee_amount(description),
            "material_note": normalize_space(pairs.get("재료비")),
            "status": normalize_status(pairs.get("신청상태") or row.get("status")),
            "description": description[:4000],
            "instructor": normalize_space(pairs.get("강사")),
        }
    )
    if capacity_current is not None:
        row["capacity_current"] = capacity_current
    if capacity_total is not None:
        row["capacity_total"] = capacity_total
    if waitlist_total is not None:
        row["waitlist_total"] = waitlist_total
    if min_month is not None or max_month is not None:
        row["target_min_age"] = min_month
        row["target_max_age"] = max_month
        row["target_age_is_explicit"] = True
    return row


def is_expired(row: dict[str, Any]) -> bool:
    try:
        _start, end = parse_date_range(row.get("period") or row.get("schedule_raw") or "")
    except Exception:  # noqa: BLE001
        return False
    return bool(end and end < datetime.now().date())


def collect(limit: int | None = None, max_pages: int = 5, timeout: int = 25, include_expired: bool = False, detail: bool = True) -> list[dict[str, Any]]:
    session = make_session()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        method = "GET" if page == 1 else "POST"
        soup = fetch_soup(session, LIST_URL, timeout, method=method, data=list_payload(page) if page > 1 else None)
        page_count = 0
        for tr in soup.select("table.tbl_eduprog tbody tr, table tbody tr"):
            row = parse_list_row(tr)
            if not row or row["provider_course_id"] in seen:
                continue
            seen.add(row["provider_course_id"])
            if detail:
                try:
                    row = enrich_detail(session, row, timeout)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("detail fetch failed provider=%s id=%s error=%s", PROVIDER, row.get("external_id"), exc)
            if not include_expired and is_expired(row):
                continue
            rows.append(row)
            page_count += 1
            if limit and len(rows) >= limit:
                return rows
        if page_count == 0 and page > 1:
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
    parser.add_argument("--max-pages", type=int, default=5)
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
