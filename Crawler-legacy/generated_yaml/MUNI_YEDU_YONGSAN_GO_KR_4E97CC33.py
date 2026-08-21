from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


PROVIDER = "MUNI_YEDU_YONGSAN_GO_KR_4E97CC33"
PROVIDER_NAME = "용산구교육종합포털 통합 수강신청"
BASE_URL = "https://yedu.yongsan.go.kr"
DEFAULT_BRANCH = "용산구교육종합포털"
DEFAULT_ADDRESS = "서울특별시 용산구 녹사평대로 150"
DEFAULT_PHONE = "02-2199-6490"

TARGET_URLS = [
    "https://yedu.yongsan.go.kr/site/edtotal/lesson/userlist.do?sitecdv=S0000500&decorator=user27EdTotal&menucdv=02020000&searchEdutypecdv=F0810101",
    "https://yedu.yongsan.go.kr/site/edtotal/lifeStudy/userlist.do?sitecdv=S0000500&menucdv=02070000&decorator=user27EdTotal",
    "https://yedu.yongsan.go.kr/site/edtotal/eachOther/userlist.do?sitecdv=S0000500&menucdv=02040100&decorator=user27EdTotal",
    "https://yedu.yongsan.go.kr/site/edtotal/happyStudy/userlist.do?sitecdv=S0000500&menucdv=02060000&decorator=user27EdTotal",
]

PATH_CATEGORY = {
    "lesson": "정보화교육",
    "lifeStudy": "평생학습관",
    "eachOther": "서로서로학교",
    "happyStudy": "동네배움터",
}

BRANCH_ADDRESS_HINTS = {
    "용산구청": "서울특별시 용산구 녹사평대로 150",
    "용산구청(지하3층)": "서울특별시 용산구 녹사평대로 150",
    "용산구청 지하3층": "서울특별시 용산구 녹사평대로 150",
    "용산구평생학습관": "서울특별시 용산구 이태원로 224-19",
    "여성플라자 요리교실": "서울특별시 용산구 이태원로 224-19",
}

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from DB.db_utils import get_db_cursor  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, extract_krw_amount, extract_material_fee_amount, setup_logger  # noqa: E402


logger = setup_logger("Crawler_YongsanEduIntegrated")


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
    if not text:
        return ""
    text = re.sub(
        r"(\d{4})[./](\d{1,2})[./](\d{1,2})",
        lambda m: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}",
        text,
    )

    current_year = date.today().year

    def add_year(match: re.Match[str]) -> str:
        return f"{current_year}-{int(match.group(1)):02d}-{int(match.group(2)):02d}"

    text = re.sub(r"(?<!\d{4}-)(?<!\d)(\d{1,2})[./](\d{1,2})(?!\d)", add_year, text)
    text = re.sub(r"\s*[~∼]\s*", " ~ ", text)
    return normalize_space(text)


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


def normalize_status(value: Any) -> str:
    text = normalize_space(value)
    if any(token in text for token in ["모집중", "접수중", "신청가능"]):
        return "OPEN"
    if any(token in text for token in ["모집예정", "접수예정", "대기"]):
        return "SCHEDULED"
    if any(token in text for token in ["모집마감", "마감", "종료", "폐강", "취소"]):
        return "CLOSED"
    return "OPEN" if not text else text


def parse_capacity(value: Any) -> tuple[int | None, int | None, int | None]:
    nums = [int(num) for num in re.findall(r"\d+", normalize_space(value))]
    if len(nums) >= 3:
        return nums[0], nums[1], nums[2]
    if len(nums) >= 2:
        return nums[0], nums[1], None
    if len(nums) == 1:
        return None, nums[0], None
    return None, None, None


def fetch_soup(session: requests.Session, url: str, timeout: int, params: dict[str, str] | None = None) -> BeautifulSoup:
    response = session.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def target_category(url: str) -> str:
    path = urlparse(url).path
    for key, value in PATH_CATEGORY.items():
        if f"/{key}/" in path:
            return value
    return "평생학습"


def list_params(url: str, page: int) -> dict[str, str]:
    parsed = urlparse(url)
    params = {key: values[-1] for key, values in parse_qs(parsed.query).items() if values}
    params["currentPage"] = str(max(0, page - 1))
    return params


def base_list_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def detail_url(list_url: str, lesseqn: str, edutype: str) -> str:
    params = list_params(list_url, 1)
    params.update({"edutypecdv": edutype, "lesseqn": lesseqn})
    parsed = urlparse(list_url)
    base = f"{parsed.scheme}://{parsed.netloc}{str(Path(parsed.path).parent).replace(chr(92), '/')}/form.do"
    return f"{base}?{urlencode(params)}"


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


def extract_detail_ids(href: str) -> tuple[str, str] | None:
    match = re.search(r"goWrite\(\s*['\"]?(\d+)['\"]?\s*,\s*['\"]([^'\"]+)['\"]", href or "")
    if not match:
        return None
    return match.group(1), match.group(2)


def parse_list_row(tr: Any, list_url: str) -> dict[str, Any] | None:
    cells = tr.find_all("td", recursive=False)
    if len(cells) < 7 or not re.search(r"\d", normalize_space(cells[0].get_text(" ", strip=True))):
        return None

    link = cells[1].select_one("a[href]")
    ids = extract_detail_ids(link.get("href", "") if link else "")
    if not ids:
        return None

    lesseqn, edutype = ids
    category = target_category(list_url)
    title = normalize_space(cells[1].get_text(" ", strip=True))
    branch = normalize_space(cells[3].get_text(" ", strip=True)) or DEFAULT_BRANCH
    current, total, waitlist = parse_capacity(cells[4].get_text(" ", strip=True))
    raw_url = detail_url(list_url, lesseqn, edutype)

    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": f"{category}:{lesseqn}",
        "provider_course_id": f"{category}:{lesseqn}",
        "title": title,
        "branch": branch,
        "branch_code": branch_code(branch),
        "address": resolve_address(branch, ""),
        "phone": DEFAULT_PHONE,
        "period": "",
        "reception_period": normalize_date_text(cells[2].get_text(" ", strip=True)),
        "schedule_raw": "",
        "target": "",
        "age_group": "",
        "category_raw": category,
        "fee": normalize_fee(cells[5].get_text(" ", strip=True)),
        "material_fee": None,
        "material_note": "",
        "status": normalize_status(cells[6].get_text(" ", strip=True)),
        "raw_url": raw_url,
        "description": "",
        "instructor": "",
        "capacity_current": current,
        "capacity_total": total,
        "waitlist_total": waitlist,
        "image_url": "",
        "collection_category": "교육·체험",
        "domain_category": "평생학습",
        "source_group": "lifelong_learning",
        "operator_type": "지자체/공공기관",
        "collection_type": "static_html+detail_html",
    }


def resolve_address(branch: str, description: str) -> str:
    text = normalize_space(f"{branch} {description}")
    for key, address in BRANCH_ADDRESS_HINTS.items():
        if key in text:
            return address
    match = re.search(r"(?:세부주소|주소|운영장소)\s*[:：]\s*([^。.\n\r]+?\d+(?:-\d+)?)", description)
    if match:
        return normalize_space(match.group(1))
    return DEFAULT_ADDRESS


def normalize_target(value: Any, title: str, description: str) -> str:
    text = normalize_space(value)
    if not text or text in {"남자 무 여자 무", "남자 무관 여자 무관", "무", "무관"}:
        text = "누구나"
    age_ranges = [
        (int(start), int(end))
        for start, end in re.findall(r"(\d+)\s*세\s*에서\s*~?\s*(\d+)\s*세\s*까지", text)
    ]
    if age_ranges:
        min_age = min(start for start, _end in age_ranges)
        max_age = max(end for _start, end in age_ranges)
        if max_age >= 100:
            return f"성인 {min_age}세 이상"
        return f"성인 {min_age}~{max_age}세"

    merged = f"{title} {description}"
    if text == "누구나":
        if re.search(r"어르신|시니어|50\+|중장년|구민강사", merged):
            return "성인"
        if re.search(r"청소년|중학생|고등학생", merged):
            return "청소년"
        if re.search(r"어린이|초등|아동|유아", merged):
            return "아동"
    return text


def infer_age_group(target: str, title: str, description: str) -> str:
    text = f"{target} {title} {description}"
    if re.search(r"유아|어린이|아동|초등", text):
        return "KIDS"
    if re.search(r"청소년|중학생|고등학생", text):
        return "TEEN"
    if re.search(r"성인|구민|시니어|어르신|50\+|중장년", text):
        return "ADULT"
    return ""


def extract_image_url(soup: BeautifulSoup) -> str:
    for img in soup.select("#contents img[src], .contents img[src], table img[src]"):
        src = normalize_space(img.get("src"))
        if not src or src.startswith("data:"):
            continue
        lower = src.lower()
        if any(skip in lower for skip in ["logo", "icon", "ico_", "bullet", "btn_", "banner_footer"]):
            continue
        return urljoin(BASE_URL, src)
    return ""


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    try:
        soup = fetch_soup(session, row["raw_url"], timeout)
    except Exception as exc:  # noqa: BLE001
        logger.warning("detail fetch failed provider=%s id=%s error=%s", PROVIDER, row.get("external_id"), exc)
        return row

    pairs = table_pairs(soup)
    description_parts = [pairs.get("강좌소개", ""), pairs.get("강좌계획서", "")]
    description = normalize_space(" ".join(part for part in description_parts if part))
    branch = normalize_space(pairs.get("교육장") or pairs.get("장소") or row.get("branch") or DEFAULT_BRANCH)
    target = normalize_target(pairs.get("접수나이"), row.get("title", ""), description)
    period = normalize_date_text(pairs.get("교육기간") or row.get("period"))
    day = normalize_space(pairs.get("수업요일"))
    time_text = normalize_space(pairs.get("교육시간"))
    schedule_raw = normalize_space(" ".join(part for part in [period, day, time_text] if part))
    contact = normalize_space(pairs.get("담당부서"))
    phone_match = re.search(r"0\d{1,2}-\d{3,4}-\d{4}", contact)

    row.update(
        {
            "title": normalize_space(pairs.get("강좌명") or row.get("title")),
            "branch": branch,
            "branch_code": branch_code(branch),
            "address": resolve_address(branch, description),
            "phone": phone_match.group(0) if phone_match else row.get("phone") or DEFAULT_PHONE,
            "period": period,
            "schedule_raw": schedule_raw,
            "target": target,
            "age_group": infer_age_group(target, row.get("title", ""), description),
            "fee": normalize_fee(pairs.get("수강료") or row.get("fee")),
            "material_fee": extract_material_fee_amount(description),
            "material_note": extract_material_note(description),
            "status": normalize_status(pairs.get("접수상태") or row.get("status")),
            "description": description[:4000],
            "image_url": extract_image_url(soup),
        }
    )

    capacity_match = re.search(r"\d+", normalize_space(pairs.get("정원")))
    if capacity_match:
        row["capacity_total"] = int(capacity_match.group(0))
    return row


def extract_material_note(description: str) -> str:
    lines = re.split(r"(?<=[.!?])\s+|[\r\n]+", description)
    material_lines = [
        normalize_space(line)
        for line in lines
        if any(token in line for token in ["재료", "준비물", "교재", "교구"])
    ]
    return " ".join(material_lines)[:500]


def is_expired(row: dict[str, Any]) -> bool:
    try:
        _start, end = parse_date_range(row.get("period") or row.get("schedule_raw") or "")
    except Exception:  # noqa: BLE001
        return False
    return bool(end and end < date.today())


def collect(
    limit: int,
    max_pages: int,
    timeout: int,
    include_expired: bool = False,
    detail: bool = True,
) -> list[dict[str, Any]]:
    session = make_session()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for target_url in TARGET_URLS:
        list_url = base_list_url(target_url)
        for page in range(1, max_pages + 1):
            soup = fetch_soup(session, list_url, timeout, params=list_params(target_url, page))
            parsed_this_page = 0
            for tr in soup.select("table tbody tr"):
                row = parse_list_row(tr, target_url)
                if not row or row["provider_course_id"] in seen:
                    continue
                seen.add(row["provider_course_id"])
                if detail:
                    row = enrich_detail(session, row, timeout)
                if not include_expired and is_expired(row):
                    continue
                rows.append(row)
                parsed_this_page += 1
                if len(rows) >= limit:
                    return rows
            if parsed_this_page == 0:
                break
    return rows


def quality_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    required = [
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
    counts = {field: sum(1 for row in rows if normalize_space(row.get(field))) for field in required}
    score = round((sum(counts.values()) / (len(required) * max(1, len(rows)))) * 100, 1)
    if score >= 90:
        grade = "A"
    elif score >= 75:
        grade = "B"
    elif score >= 60:
        grade = "C"
    else:
        grade = "D"
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
        "website_url": TARGET_URLS[0],
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
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--per-target-limit", type=int, default=None)
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--include-expired", action="store_true")
    parser.add_argument("--no-detail", action="store_true")
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--mark-stale", action="store_true")
    parser.add_argument("--max-depth", type=int, default=None)
    parser.add_argument("--detail-limit", type=int, default=None)
    args = parser.parse_args()

    rows = collect(
        limit=args.limit,
        max_pages=args.max_pages,
        timeout=args.timeout,
        include_expired=args.include_expired,
        detail=not args.no_detail,
    )
    report = quality_report(rows)
    saved = save_rows(rows) if args.save_db else 0
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
