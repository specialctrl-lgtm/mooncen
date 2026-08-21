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
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag


PROVIDER = "MUNI_LLL_GANGDONG_GO_KR_E8F6E943"
PROVIDER_NAME = "강동구 평생학습관 프로그램신청"
BASE_URL = "https://lll.gangdong.go.kr"
LIST_URL = f"{BASE_URL}/program/ProgramBoardList.do?menucode=84"
DETAIL_URL = f"{BASE_URL}/program/ProgramClassroomView.do"
DEFAULT_BRANCH = "강동구 평생학습관"
DEFAULT_ADDRESS = "서울특별시 강동구 성내로 25"
DEFAULT_PHONE = "02-3425-5223"


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from DB.db_utils import get_db_cursor  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, extract_krw_amount, extract_material_fee_amount, setup_logger  # noqa: E402


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = setup_logger("Crawler_GangdongLifelong")


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
        r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일",
        lambda m: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}",
        text,
    )
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
    if any(token in text for token in ["접수중", "신청가능"]):
        return "OPEN"
    if any(token in text for token in ["접수예정", "예정"]):
        return "SCHEDULED"
    if any(token in text for token in ["마감", "종료", "폐강", "취소"]):
        return "CLOSED"
    if "교육진행" in text or "진행" in text:
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


def list_payload(page: int, gn_seq: str = "") -> dict[str, str]:
    return {
        "pageIndex": str(page),
        "search_type": "aca",
        "search_status": "eAll",
        "menucode": "84",
        "gn_seq": gn_seq,
        "search_key": "",
        "search_word": "",
    }


def fetch_soup(session: requests.Session, url: str, timeout: int, method: str = "GET", data: dict[str, str] | None = None) -> BeautifulSoup:
    if method.upper() == "POST":
        response = session.post(url, data=data or {}, timeout=timeout)
    else:
        response = session.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def extract_gn_seq(value: str) -> str:
    match = re.search(r"fn_view\('([^']+)'\)", value or "")
    return normalize_space(match.group(1)) if match else ""


def parse_capacity(value: Any) -> tuple[int | None, int | None, int | None]:
    text = normalize_space(value)
    match = re.search(r"(\d+)\s*/\s*(\d+)", text)
    if match:
        return int(match.group(1)), int(match.group(2)), None
    match = re.search(r"\[\s*(\d+)\s*/\s*(\d+)\s*\].*?대기\s*:\s*\[\s*(\d+)\s*\]", text)
    if match:
        return int(match.group(1)), int(match.group(2)), int(match.group(3))
    return None, None, None


def parse_target_from_title(title: str) -> str:
    match = re.search(r"\(([^)]*(?:초등|중등|고등|성인|학년|세)[^)]*)\)", title)
    if match:
        return normalize_space(match.group(1))
    if "초등" in title:
        return "초등학생"
    if "성인" in title:
        return "성인"
    return ""


def normalize_target(value: Any, title: str) -> str:
    target = normalize_space(value)
    if target:
        return target
    if parse_target_from_title(title):
        return parse_target_from_title(title)
    if "도서관 연계" in title and re.search(r"초등|학년|어린이", title):
        return parse_target_from_title(title) or "아동"
    return "성인"


def infer_age_group(target: str, title: str) -> str:
    text = f"{target} {title}"
    if re.search(r"초등|어린이|아동|유아|학년", text):
        return "KIDS"
    if re.search(r"중등|고등|청소년", text):
        return "TEEN"
    if re.search(r"성인|50|시니어|어르신", text):
        return "ADULT"
    return ""


def normalize_branch(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return DEFAULT_BRANCH
    text = re.sub(r"\s+", " ", text)
    return text[:100]


def parse_list_row(tr: Tag) -> dict[str, Any] | None:
    cells = tr.find_all("td", recursive=False)
    if len(cells) < 7:
        return None
    link = tr.select_one("a[onclick]")
    gn_seq = extract_gn_seq(link.get("onclick", "") if link else "")
    if not gn_seq:
        return None
    title = normalize_space(tr.select_one(".td_title .tit").get_text(" ", strip=True) if tr.select_one(".td_title .tit") else cells[2].get_text(" ", strip=True))
    schedule_bits = normalize_date_text(tr.select_one(".edu_time").get_text(" ", strip=True) if tr.select_one(".edu_time") else "")
    fee = normalize_fee(tr.select_one(".edu_price").get_text(" ", strip=True) if tr.select_one(".edu_price") else "")
    reception_period = normalize_date_text(tr.select_one(".req_date").get_text(" ", strip=True).replace("신청 :", "") if tr.select_one(".req_date") else "")
    period = normalize_date_text(tr.select_one(".edu_date").get_text(" ", strip=True).replace("교육 :", "") if tr.select_one(".edu_date") else "")
    capacity_current, capacity_total, waitlist_total = parse_capacity(cells[4].get_text(" ", strip=True))
    status = normalize_status(cells[-1].get_text(" ", strip=True))
    target = normalize_target("", title)
    img = tr.select_one("img[src]")
    image_url = urljoin(BASE_URL, img.get("src")) if img and img.get("src") and "noimage" not in img.get("src", "") else ""
    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": gn_seq,
        "provider_course_id": gn_seq,
        "title": title,
        "branch": DEFAULT_BRANCH,
        "branch_code": branch_code(DEFAULT_BRANCH),
        "address": DEFAULT_ADDRESS,
        "phone": DEFAULT_PHONE,
        "period": period,
        "schedule_raw": normalize_space(" ".join(part for part in [period, schedule_bits] if part)),
        "target": target,
        "age_group": infer_age_group(target, title),
        "category_raw": "",
        "fee": fee,
        "material_fee": None,
        "material_note": "",
        "status": status,
        "raw_url": f"{DETAIL_URL}?menucode=84&gn_seq={gn_seq}",
        "application_url": f"{DETAIL_URL}?menucode=84&gn_seq={gn_seq}",
        "application_type": "ONLINE",
        "application_method_raw": "",
        "description": "",
        "image_url": image_url,
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


def first_date_range(value: Any) -> str:
    text = normalize_date_text(value)
    match = re.search(r"\d{4}-\d{2}-\d{2}\s*~\s*\d{4}-\d{2}-\d{2}", text)
    return normalize_space(match.group(0)) if match else text


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    soup = fetch_soup(session, DETAIL_URL, timeout, method="POST", data=list_payload(1, str(row["external_id"])))
    pairs = table_pairs(soup)
    title = normalize_space(pairs.get("강의명") or row.get("title"))
    venue = normalize_branch(pairs.get("교육 장소") or pairs.get("교육장소") or row.get("branch"))
    period = first_date_range(pairs.get("강의 기간") or row.get("period"))
    schedule = normalize_date_text(pairs.get("강의 시간") or row.get("schedule_raw"))
    if period and schedule and period not in schedule:
        schedule = normalize_space(f"{period} {schedule}")
    reception_period = first_date_range(pairs.get("접수 기간") or row.get("reception_period"))
    capacity_current, capacity_total, waitlist_total = parse_capacity(pairs.get("신청 현황"))
    instructor = normalize_space(pairs.get("강사명"))
    instructor_intro = normalize_space(pairs.get("강사소개"))
    description = normalize_space(" ".join(part for part in [instructor_intro, pairs.get("문의 (담당자)")] if normalize_space(part)))
    target = normalize_target(row.get("target"), title)
    row.update(
        {
            "title": title,
            "branch": venue,
            "branch_code": branch_code(venue),
            "address": DEFAULT_ADDRESS,
            "phone": normalize_space(pairs.get("담당자/문의") or pairs.get("연락처") or row.get("phone")),
            "period": period,
            "schedule_raw": schedule,
            "target": target,
            "age_group": infer_age_group(target, title),
            "category_raw": normalize_space(pairs.get("분류")),
            "fee": normalize_fee(pairs.get("수강료") or row.get("fee")),
            "material_fee": extract_material_fee_amount(description),
            "material_note": "",
            "description": description or normalize_space(" ".join(f"{k}: {v}" for k, v in pairs.items() if normalize_space(v)))[:4000],
            "instructor": instructor,
            "reception_period": reception_period,
        }
    )
    if capacity_current is not None:
        row["capacity_current"] = capacity_current
    if capacity_total is not None:
        row["capacity_total"] = capacity_total
    if waitlist_total is not None:
        row["waitlist_total"] = waitlist_total
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
        soup = fetch_soup(session, LIST_URL if page == 1 else f"{BASE_URL}/program/ProgramBoardList.do", timeout, method=method, data=list_payload(page) if page > 1 else None)
        page_count = 0
        for tr in soup.select("table.program tbody tr, table.list tbody tr, table tbody tr"):
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
