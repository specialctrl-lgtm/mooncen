from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


PROVIDER = "MUNI_WWW_DONGGU_GO_KR_9A7A5E6F"
PROVIDER_NAME = "대전광역시 동구 평생학습 수강신청"
BASE_URL = "https://www.donggu.go.kr"
LIST_URL = f"{BASE_URL}/lll/www/selectUserEduList.do?key=733&pageUnit=20"
DETAIL_PATH = "/lll/www/selectUserEduView.do"
DEFAULT_BRANCH = "대전광역시 동구청"
DEFAULT_ADDRESS = "대전광역시 동구 동구청로 147"
DEFAULT_PHONE = "042-251-6688"

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from DB.db_utils import get_db_cursor  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, extract_krw_amount, setup_logger  # noqa: E402


logger = setup_logger("Crawler_DaejeonDongguEdu")


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
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


def fetch_soup(session: requests.Session, url: str, timeout: int, params: dict[str, Any] | None = None) -> BeautifulSoup:
    response = session.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def normalize_mixed_date_range(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return ""
    text = re.sub(
        r"(\d{4})[./-](\d{1,2})[./-](\d{1,2})",
        lambda m: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}",
        text,
    )
    text = re.sub(
        r"(?<!\d)(\d{2})[.](\d{1,2})[.](\d{1,2})",
        lambda m: f"20{int(m.group(1)):02d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}",
        text,
    )
    text = re.sub(r"\s*[~∼]\s*", " ~ ", text)
    return normalize_space(text)


def normalize_status(value: Any) -> str:
    text = normalize_space(value)
    if any(token in text for token in ["접수중", "신청가능", "접수대기"]):
        return "OPEN"
    if any(token in text for token in ["접수예정", "신청예정"]):
        return "SCHEDULED"
    if any(token in text for token in ["신청마감", "접수마감", "마감", "종료", "취소", "교육중"]):
        return "CLOSED"
    return "OPEN" if not text else text


def normalize_fee(value: Any) -> str:
    text = normalize_space(value).replace("수강료", "").strip()
    if not text:
        return ""
    if "무료" in text:
        return "무료"
    amount = extract_krw_amount(text)
    if amount:
        return f"{amount:,}원"
    return text


def table_pairs(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for table in soup.select("table"):
        for tr in table.select("tr"):
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
        if pairs:
            break
    return pairs


def extract_sch_id(tr: Any) -> str:
    onclick = " ".join(normalize_space(a.get("onclick")) for a in tr.select("a[onclick]"))
    match = re.search(r"fn_goView\(['\"]?(\d+)", onclick)
    return match.group(1) if match else ""


def detail_url(base_query: dict[str, list[str]], sch_id: str) -> str:
    query = {key: values[:] for key, values in base_query.items()}
    query["schId"] = [sch_id]
    query.setdefault("key", ["733"])
    return f"{BASE_URL}{DETAIL_PATH}?{urlencode(query, doseq=True)}"


def split_title_location(cell: Any) -> tuple[str, str]:
    text = normalize_space(cell.get_text(" ", strip=True))
    for suffix in ["수강신청", "상세보기"]:
        if text.endswith(suffix):
            text = normalize_space(text[: -len(suffix)])
    spans = [normalize_space(span.get_text(" ", strip=True)) for span in cell.find_all("span")]
    location = spans[-1] if spans else ""
    title_node = cell.select_one(".edu_tit")
    title = normalize_space(title_node.get_text(" ", strip=True) if title_node else "")
    if not title:
        title = text
        if location and title.endswith(location):
            title = normalize_space(title[: -len(location)])
    return title, location


def labeled_dates(cell: Any) -> dict[str, str]:
    text = normalize_space(cell.get_text(" ", strip=True))
    result: dict[str, str] = {}
    for label in ["신청기간", "교육기간"]:
        match = re.search(label + r"\s*(\d{2,4}[.\-/]\d{1,2}[.\-/]\d{1,2}\s*~\s*\d{2,4}[.\-/]\d{1,2}[.\-/]\d{1,2})", text)
        if match:
            result[label] = normalize_mixed_date_range(match.group(1))
    return result


def parse_capacity(value: Any) -> tuple[int | None, int | None, int | None]:
    text = normalize_space(value)
    total_match = re.search(r"모집\s*(\d+)\s*명", text)
    current_match = re.search(r"\[\s*(\d+)\s*/\s*(\d+)\s*명", text)
    wait_match = re.search(r"대기\s*(\d+)\s*명", text)
    if current_match:
        return int(current_match.group(1)), int(current_match.group(2)), int(wait_match.group(1)) if wait_match else None
    return None, int(total_match.group(1)) if total_match else None, int(wait_match.group(1)) if wait_match else None


def normalize_venue(value: Any, fallback_address: str = "") -> tuple[str, str]:
    text = normalize_space(value)
    if not text:
        return DEFAULT_BRANCH, fallback_address or DEFAULT_ADDRESS
    if " / " in text:
        name, addr = [normalize_space(part) for part in text.split(" / ", 1)]
        return name or DEFAULT_BRANCH, addr or fallback_address or DEFAULT_ADDRESS
    if "/" in text:
        name, addr = [normalize_space(part) for part in text.split("/", 1)]
        return name or DEFAULT_BRANCH, addr or fallback_address or DEFAULT_ADDRESS
    addr = normalize_space(re.sub(r"^\[[^\]]+\]\s*", "", fallback_address))
    return text, addr or DEFAULT_ADDRESS


def branch_from_venue(venue_name: str, venue_address: str) -> str:
    text = normalize_space(venue_name)
    if not text:
        return DEFAULT_BRANCH
    if "동구청" in text:
        return DEFAULT_BRANCH
    if re.search(r"^\d+\s*층|강의실|시청각실|컴퓨터교육실", text) and not re.search(r"센터|복지관|도서관|공작소|숲체원|북카페", text):
        return DEFAULT_BRANCH
    if venue_address and "동구청로 147" in venue_address:
        return DEFAULT_BRANCH
    return text


def infer_age_group(target: str, title: str, age_raw: str = "") -> str:
    text = f"{target} {title} {age_raw}"
    if any(token in text for token in ["미취학", "초등", "아동", "어린이"]):
        return "KIDS"
    if any(token in text for token in ["청소년", "중학생", "고등학생", "중/고"]):
        return "TEEN"
    if any(token in text for token in ["성인", "65세", "여성", "누구나"]):
        return "ADULT"
    return ""


def parse_list_row(tr: Any, base_query: dict[str, list[str]]) -> dict[str, Any] | None:
    cells = tr.find_all("td", recursive=False)
    if len(cells) < 7:
        return None
    sch_id = extract_sch_id(tr)
    if not sch_id:
        return None
    title, location = split_title_location(cells[1])
    if not title:
        return None
    raw_url = detail_url(base_query, sch_id)
    dates = labeled_dates(cells[4])
    current, total, wait = parse_capacity(cells[5].get_text(" ", strip=True))
    target = re.sub(r"^모집대상\s*[:：]?\s*", "", normalize_space(cells[3].get_text(" ", strip=True)))
    branch = normalize_space(cells[2].get_text(" ", strip=True)) or DEFAULT_BRANCH
    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": sch_id,
        "provider_course_id": sch_id,
        "title": title,
        "branch": branch,
        "branch_code": branch_code(branch),
        "address": DEFAULT_ADDRESS,
        "phone": DEFAULT_PHONE,
        "period": dates.get("교육기간", ""),
        "reception_period": dates.get("신청기간", ""),
        "schedule_raw": "",
        "target": target,
        "age_group": infer_age_group(target, title),
        "category_raw": "",
        "fee": normalize_fee(cells[6].get_text(" ", strip=True)),
        "material_fee": None,
        "material_note": "",
        "status": normalize_status(cells[0].get_text(" ", strip=True)),
        "raw_url": raw_url,
        "application_url": raw_url,
        "description": normalize_space(tr.get_text(" ", strip=True))[:1200],
        "instructor": "",
        "capacity_current": current,
        "capacity_total": total,
        "waitlist_total": wait,
        "room": location,
        "venue_name": location,
        "venue_address": "",
        "image_url": "",
        "collection_category": "교육·체험",
        "domain_category": "평생학습",
        "source_group": "lifelong_learning",
        "operator_type": "지자체/공공기관",
        "collection_type": "static_html+detail_html",
        "raw_fields": {"parser": "daejeon_donggu_list_detail", "sch_id": sch_id, "list_dates": dates},
    }


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    try:
        soup = fetch_soup(session, row["raw_url"], timeout)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Donggu detail failed %s: %s", row.get("raw_url"), exc)
        return row
    pairs = table_pairs(soup)
    venue_name, venue_address = normalize_venue(pairs.get("교육장소"), pairs.get("교육장소주소"))
    branch_name = branch_from_venue(venue_name, venue_address)
    current, total, wait = parse_capacity(pairs.get("접수인원"))
    target = normalize_space(pairs.get("교육대상") or row.get("target"))
    age_raw = normalize_space(pairs.get("수강가능연령"))
    period = normalize_mixed_date_range(pairs.get("교육기간")) or row.get("period")
    schedule = normalize_space(pairs.get("교육시간"))
    schedule_raw = normalize_space(" ".join(part for part in [period, schedule] if part))
    description = normalize_space(pairs.get("강의내용") or row.get("description"))
    instructor = normalize_space(pairs.get("강사"))
    phone = normalize_space(pairs.get("문의전화")) or row.get("phone") or DEFAULT_PHONE
    row.update(
        {
            "category_raw": normalize_space(pairs.get("강의분류")) or row.get("category_raw"),
            "target": target,
            "age_group": infer_age_group(target, row.get("title", ""), age_raw),
            "status": normalize_status(pairs.get("신청기간") or row.get("status")),
            "period": period,
            "reception_period": normalize_mixed_date_range(pairs.get("신청기간")) or row.get("reception_period"),
            "schedule_raw": schedule_raw or row.get("schedule_raw"),
            "room": venue_name,
            "venue_name": venue_name,
            "venue_address": venue_address,
            "address": venue_address or row.get("address") or DEFAULT_ADDRESS,
            "branch": branch_name,
            "branch_code": branch_code(branch_name),
            "capacity_current": current if current is not None else row.get("capacity_current"),
            "capacity_total": total if total is not None else row.get("capacity_total"),
            "waitlist_total": wait if wait is not None else row.get("waitlist_total"),
            "target_age_raw": age_raw,
            "material_note": normalize_space(pairs.get("재료비설명")),
            "material_fee": extract_krw_amount(pairs.get("재료비설명")),
            "instructor": instructor,
            "teacher": instructor,
            "phone": phone,
            "description": description[:4000],
        }
    )
    row["raw_fields"]["detail_pairs"] = pairs
    return row


def is_expired(row: dict[str, Any]) -> bool:
    try:
        _start, end = parse_date_range(row.get("period") or row.get("schedule_raw") or "")
    except Exception:  # noqa: BLE001
        return False
    if not end:
        return False
    end_day = end.date() if hasattr(end, "date") else end
    return end_day < date.today()


def collect(limit: int | None = None, max_pages: int = 20, timeout: int = 25, include_expired: bool = False, detail: bool = True) -> list[dict[str, Any]]:
    session = make_session()
    parsed = urlparse(LIST_URL)
    base_query = parse_qs(parsed.query)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        query = {key: values[:] for key, values in base_query.items()}
        query["page"] = [str(page)]
        query.setdefault("pageUnit", ["20"])
        current_url = f"{BASE_URL}/lll/www/selectUserEduList.do?{urlencode(query, doseq=True)}"
        soup = fetch_soup(session, current_url, timeout)
        page_count = 0
        for tr in soup.select("table.edu_list_table tbody tr, table tbody tr"):
            row = parse_list_row(tr, query)
            if not row or row["provider_course_id"] in seen:
                continue
            seen.add(row["provider_course_id"])
            if detail:
                row = enrich_detail(session, row, timeout)
            if not include_expired and is_expired(row):
                continue
            rows.append(row)
            page_count += 1
            if limit and len(rows) >= limit:
                return rows
        if page_count == 0:
            break
    return rows


def quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fields = ["title", "branch", "address", "period", "schedule_raw", "target", "fee", "status", "description", "raw_url"]
    counts = {field: sum(1 for row in rows if normalize_space(row.get(field))) for field in fields}
    score = round(sum(counts.values()) / (len(rows) * len(fields)) * 100, 1) if rows else 0.0
    grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D"
    return {"rows": len(rows), "score": score, "grade": grade, "field_counts": counts}


def print_quality(rows: list[dict[str, Any]]) -> None:
    print(json.dumps(quality(rows), ensure_ascii=False, indent=2))
    print("\nSAMPLE")
    for row in rows[:10]:
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
        "address": normalize_space(row.get("address") or DEFAULT_ADDRESS),
        "phone": normalize_space(row.get("phone") or DEFAULT_PHONE),
        "website_url": LIST_URL,
        "address_source": "crawler_detail",
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


def save_rows(rows: list[dict[str, Any]], mark_stale: bool = False) -> int:
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
    if mark_stale and saved > 0:
        from DB.course_lifecycle import mark_stale_courses, utc_now

        mark_stale_courses(PROVIDER, utc_now())
    logger.info("%s saved %s/%s rows.", PROVIDER, saved, len(rows))
    return saved


def main() -> int:
    parser = argparse.ArgumentParser(description=f"{PROVIDER_NAME} crawler")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--per-target-limit", type=int)
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--include-expired", action="store_true")
    parser.add_argument("--no-detail", action="store_true")
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--mark-stale", action="store_true")
    parser.add_argument("--max-depth", type=int)
    parser.add_argument("--detail-limit", type=int)
    args = parser.parse_args()

    limit = args.limit if args.limit is not None else args.per_target_limit
    started = datetime.now()
    rows = collect(limit=limit, max_pages=args.max_pages, timeout=args.timeout, include_expired=args.include_expired, detail=not args.no_detail)
    print_quality(rows)
    saved = save_rows(rows, mark_stale=args.mark_stale) if args.save_db else 0
    logger.info("%s completed collected=%s saved=%s elapsed=%.1fs", PROVIDER, len(rows), saved, (datetime.now() - started).total_seconds())
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
