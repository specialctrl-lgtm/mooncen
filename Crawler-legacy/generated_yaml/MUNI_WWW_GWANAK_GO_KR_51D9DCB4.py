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
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup, Tag


PROVIDER = "MUNI_WWW_GWANAK_GO_KR_51D9DCB4"
PROVIDER_NAME = "관악구 구민 정보화교육"
BASE_URL = "https://www.gwanak.go.kr"
LIST_PATH = "/site/edu/lecture/Lecture_List.do"
DETAIL_PATH = "/site/edu/lecture/Lecture_View.do"
ORG_CODE = "29000400"
LIST_URL = f"{BASE_URL}{LIST_PATH}?scLcOrganization1={ORG_CODE}"
DEFAULT_BRANCH = "관악구 구민 정보화교육"
DEFAULT_ADDRESS = "서울특별시 관악구 관악로 145"
DEFAULT_PHONE = "02-879-6080"

BRANCH_ADDRESS_HINTS = {
    "난곡 정보화 교육장": DEFAULT_ADDRESS,
    "난곡 정보화 교육장 4층": DEFAULT_ADDRESS,
    "성현 정보화 교육장": DEFAULT_ADDRESS,
    "성현 정보화 교육장 3층": DEFAULT_ADDRESS,
}


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from DB.db_utils import get_db_cursor  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, extract_krw_amount, extract_material_fee_amount, setup_logger  # noqa: E402


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = setup_logger("Crawler_GwanakInfoEducation")


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
        r"(\d{4})[./-](\d{1,2})[./-](\d{1,2})",
        lambda m: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}",
        text,
    )
    text = re.sub(r"(\d{4}-\d{2}-\d{2})/(\d{1,2})(\d{2})", lambda m: f"{m.group(1)} {int(m.group(2)):02d}:{m.group(3)}", text)
    text = re.sub(r"(\d{1,2})\s*:\s*(\d{1,2})", lambda m: f"{int(m.group(1)):02d}:{int(m.group(2)):02d}", text)
    text = re.sub(r"\s*[~∼]\s*", " ~ ", text)
    return normalize_space(text)


def normalize_status(value: Any) -> str:
    text = normalize_space(value)
    if any(token in text for token in ["접수중", "접수 중", "신청가능"]):
        return "OPEN"
    if any(token in text for token in ["접수예정", "대기", "예정"]):
        return "SCHEDULED"
    if any(token in text for token in ["접수 마감", "접수마감", "강좌종료", "종료", "마감", "강좌시작"]):
        return "CLOSED"
    return "OPEN" if not text else text


def clean_title(value: Any) -> str:
    title = normalize_space(value)
    title = re.sub(r"\s*(접수\s*마감|접수중|접수\s*중|접수예정|강좌시작|강좌종료)\s*$", "", title)
    return normalize_space(title)


def normalize_fee(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return ""
    if "무료" in text:
        return "무료"
    amount = extract_krw_amount(text)
    if amount:
        return f"{amount:,}원"
    return text


def parse_capacity(value: Any) -> tuple[int | None, int | None, int | None]:
    nums = [int(num.replace(",", "")) for num in re.findall(r"\d[\d,]*", normalize_space(value))]
    if len(nums) >= 2:
        return nums[0], nums[1], None
    if len(nums) == 1:
        return None, nums[0], None
    return None, None, None


def parse_limit_capacity(value: Any) -> tuple[int | None, int | None]:
    text = normalize_space(value)
    match = re.search(r"(\d+)\s*/\s*\(?(\d+)\)?", text)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None, None


def extract_detail_ids(href: str) -> tuple[str, str] | None:
    match = re.search(r"doLectureView\(\s*'([^']+)'\s*,\s*'[^']*'\s*,\s*'([^']+)'\s*\)", href or "")
    if not match:
        return None
    return match.group(1), match.group(2)


def detail_url(course_id: str, org_code: str = ORG_CODE) -> str:
    return f"{BASE_URL}{DETAIL_PATH}?{urlencode({'clIdx': course_id, 'scLcOrganization1': org_code})}"


def list_payload(page: int) -> dict[str, str]:
    return {
        "pageIndex": str(page),
        "lectureMenu": "",
        "imageListYn": "",
        "scClStsCode": "",
        "onlinePayDelayYn": "",
        "searchKeyword": "",
        "scLcStDate": "",
        "scLcEdDate": "",
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
    for table in soup.select("table.info-table, table"):
        caption = normalize_space(table.select_one("caption").get_text(" ", strip=True) if table.select_one("caption") else "")
        if pairs and "강좌신청" not in caption:
            continue
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


def infer_age_group(target: str, title: str) -> str:
    text = f"{target} {title}"
    if re.search(r"어르신|55세|60세|시니어|성인|구민", text):
        return "ADULT"
    if re.search(r"청소년|중학생|고등학생", text):
        return "TEEN"
    if re.search(r"어린이|초등|아동|유아", text):
        return "KIDS"
    return ""


def normalize_target(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return ""
    if "연령제한" in text:
        match = re.search(r"(\d+)\s*세.*?~\s*(\d+)\s*세", text)
        if match:
            start, end = int(match.group(1)), int(match.group(2))
            return f"성인 {start}세 이상" if end >= 100 else f"성인 {start}~{end}세"
    return text


def branch_for(place: Any) -> str:
    place_text = normalize_space(place)
    if "난곡" in place_text:
        return "난곡 정보화 교육장"
    if "성현" in place_text:
        return "성현 정보화 교육장"
    return place_text or DEFAULT_BRANCH


def address_for(branch: str) -> str:
    return BRANCH_ADDRESS_HINTS.get(branch, DEFAULT_ADDRESS)


def parse_list_row(tr: Tag) -> dict[str, Any] | None:
    cells = tr.find_all("td", recursive=False)
    if len(cells) < 7:
        return None
    link = cells[1].select_one("a[href]")
    ids = extract_detail_ids(link.get("href", "") if link else "")
    if not ids:
        return None
    course_id, org_code = ids
    category = normalize_space(cells[1].select_one(".educate").get_text(" ", strip=True) if cells[1].select_one(".educate") else "구민정보화교육")
    title = clean_title(normalize_space(cells[1].get_text(" ", strip=True)).replace(category, "", 1).strip())
    period_text = normalize_date_text(cells[2].get_text(" ", strip=True))
    period_match = re.search(r"\d{4}-\d{2}-\d{2}\s*~\s*\d{4}-\d{2}-\d{2}", period_text)
    period = normalize_space(period_match.group(0)) if period_match else period_text.replace("[교육]", "").strip()
    branch = branch_for(cells[3].get_text(" ", strip=True))
    capacity_current, capacity_total, waitlist_total = parse_capacity(cells[5].get_text(" ", strip=True))
    status_text = cells[6].select_one(".state").get_text(" ", strip=True) if cells[6].select_one(".state") else cells[6].get_text(" ", strip=True)
    method_text = cells[6].select_one(".method").get_text(" ", strip=True) if cells[6].select_one(".method") else ""

    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": course_id,
        "provider_course_id": course_id,
        "title": title,
        "branch": branch,
        "branch_code": branch_code(branch),
        "address": address_for(branch),
        "phone": DEFAULT_PHONE,
        "period": period,
        "schedule_raw": period,
        "target": "어르신",
        "age_group": "ADULT",
        "category_raw": category.strip("[]"),
        "fee": normalize_fee(cells[4].get_text(" ", strip=True)),
        "material_fee": None,
        "material_note": "",
        "status": normalize_status(status_text),
        "raw_url": detail_url(course_id, org_code),
        "application_url": detail_url(course_id, org_code),
        "application_type": "ONLINE" if "온라인" in method_text else "",
        "application_method_raw": method_text,
        "description": "",
        "image_url": "",
        "instructor": "",
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "waitlist_total": waitlist_total,
        "collection_category": "평생학습",
        "domain_category": "평생학습",
        "source_group": "lifelong_learning",
        "operator_type": "지자체/공공기관",
        "collection_type": "static_html+detail_html",
        "program_type": "OFFLINE",
    }


def extract_image_url(soup: BeautifulSoup) -> str:
    for img in soup.select("img[src*='Synapeditor'], #contents img[src], .sub-container img[src]"):
        src = normalize_space(img.get("src"))
        if not src or src.startswith("data:"):
            continue
        if any(skip in src.lower() for skip in ["logo", "popup", "icon", "btn_", "favicon"]):
            continue
        return urljoin(BASE_URL, src)
    return ""


def description_from_detail(soup: BeautifulSoup, pairs: dict[str, str]) -> str:
    parts = []
    for key in ["교육기관", "교육대상", "강좌분야", "교육장소", "교육기간", "수강요일", "접수기간", "접수방법", "신청제한", "비고"]:
        value = normalize_space(pairs.get(key))
        if value:
            parts.append(f"{key}: {value}")
    image_alts = [normalize_space(img.get("alt")) for img in soup.select("img[src*='Synapeditor']") if normalize_space(img.get("alt")) and normalize_space(img.get("alt")) != "-"]
    parts.extend(image_alts)
    return normalize_space(" ".join(parts))[:4000]


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    soup = fetch_soup(session, row["raw_url"], timeout)
    pairs = table_pairs(soup)
    place = normalize_space(pairs.get("교육장소") or row.get("branch"))
    branch = branch_for(place)
    target = normalize_target(pairs.get("신청제한")) or normalize_space(pairs.get("교육대상")) or row.get("target", "")
    period = normalize_date_text(pairs.get("교육기간") or row.get("period"))
    day_time = normalize_date_text(pairs.get("수강요일"))
    phone = normalize_space(pairs.get("전화문의")) or row.get("phone", "")
    capacity_total, waitlist_total = parse_limit_capacity(pairs.get("정원(예비)"))
    capacity_current, waitlist_current = parse_limit_capacity(pairs.get("접수인원(예비)"))
    description = description_from_detail(soup, pairs)

    row.update(
        {
            "title": clean_title(row.get("title")),
            "branch": branch,
            "branch_code": branch_code(branch),
            "address": address_for(branch),
            "phone": phone,
            "period": period,
            "schedule_raw": normalize_space(" ".join(part for part in [period, day_time] if part)),
            "target": target,
            "age_group": infer_age_group(target, row.get("title", "")),
            "category_raw": normalize_space(pairs.get("교육기관") or row.get("category_raw")),
            "fee": normalize_fee(pairs.get("수강료") or row.get("fee")),
            "material_fee": extract_material_fee_amount(description),
            "material_note": "",
            "status": normalize_status(row.get("status")),
            "description": description,
            "image_url": extract_image_url(soup),
            "instructor": normalize_space(pairs.get("강사명")),
        }
    )
    if capacity_total is not None:
        row["capacity_total"] = capacity_total
    if capacity_current is not None:
        row["capacity_current"] = capacity_current
    if waitlist_total is not None:
        row["waitlist_total"] = waitlist_total
    if waitlist_current is not None:
        row["waitlist_current"] = waitlist_current
    return row


def is_expired(row: dict[str, Any]) -> bool:
    try:
        _start, end = parse_date_range(row.get("period") or row.get("schedule_raw") or "")
    except Exception:  # noqa: BLE001
        return False
    return bool(end and end < datetime.now().date())


def collect(limit: int | None = None, max_pages: int = 9, timeout: int = 20, include_expired: bool = False, detail: bool = True) -> list[dict[str, Any]]:
    session = make_session()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        soup = fetch_soup(session, LIST_URL, timeout, method="POST", data=list_payload(page))
        page_rows = 0
        for tr in soup.select("table.list tbody tr"):
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
            page_rows += 1
            if limit and len(rows) >= limit:
                return rows
        if page_rows == 0 and page > 1:
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
    parser.add_argument("--max-pages", type=int, default=9)
    parser.add_argument("--timeout", type=int, default=20)
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
