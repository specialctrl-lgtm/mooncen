from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup


PROVIDER = "MUNI_SUGANG_ASAN_GO_KR_FF504CD1"
PROVIDER_NAME = "아산시평생학습관 평생학습강좌"
BASE_URL = "https://sugang.asan.go.kr"
LIST_URL = f"{BASE_URL}/ilms/learning/learningList.do"
DETAIL_URL = f"{BASE_URL}/ilms/learning/learningDetail.do"
DEFAULT_BRANCH = "아산시평생학습관"
DEFAULT_ADDRESS = "충청남도 아산시 남부로 92"
DEFAULT_PHONE = "041-537-3372"

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from DB.db_utils import get_db_cursor  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, extract_krw_amount, extract_material_fee_amount, setup_logger  # noqa: E402


logger = setup_logger("Crawler_AsanSugang")


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
    if "web firewall" in response.text[:2000].lower():
        raise RuntimeError(f"Asan WAF blocked request: {response.url}")
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def normalize_date_text(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return ""
    text = re.sub(
        r"(\d{4})[.](\d{1,2})[.](\d{1,2})",
        lambda m: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}",
        text,
    )
    text = re.sub(
        r"(?<!\d)(\d{2})[.](\d{1,2})[.](\d{1,2})[.]?",
        lambda m: f"20{int(m.group(1)):02d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}",
        text,
    )
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


def normalize_status(*values: Any) -> str:
    text = normalize_space(" ".join(normalize_space(value) for value in values if normalize_space(value)))
    if any(token in text for token in ["접수중", "신청가능", "모집중"]):
        return "OPEN"
    if any(token in text for token in ["대기", "교육예정", "접수예정", "추첨식 대기"]):
        return "SCHEDULED"
    if any(token in text for token in ["마감", "종료", "교육중", "교육완료", "폐강", "취소"]):
        return "CLOSED"
    return "OPEN" if not text else text


def dl_pairs(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for dl in soup.select("dl"):
        dt = dl.select_one("dt")
        dd = dl.select_one("dd")
        key = normalize_space(dt.get_text(" ", strip=True) if dt else "")
        value = normalize_space(dd.get_text(" ", strip=True) if dd else "")
        if key:
            pairs[key] = value
    return pairs


def extract_course_id(tr: Any) -> str:
    onclick = " ".join(normalize_space(a.get("onclick")) for a in tr.select("a[onclick]"))
    match = re.search(r"fn_learning_detail\('([^']+)'\)", onclick)
    return match.group(1) if match else ""


def extract_title_branch(cell: Any) -> tuple[str, str]:
    link = cell.select_one("a")
    title = normalize_space(link.get_text(" ", strip=True) if link else cell.get_text(" ", strip=True))
    org = normalize_space((cell.select_one(".org") or cell.select_one(".institution") or "").get_text(" ", strip=True))
    if org and title.endswith(org):
        title = normalize_space(title[: -len(org)])
    return title, org


def parse_capacity(value: Any) -> tuple[int | None, int | None, int | None]:
    text = normalize_space(value)
    total_match = re.search(r"총모집인원\s*(\d+)\s*명", text)
    current_match = re.search(r"신청인원\s*:\s*(\d+)\s*명", text)
    wait_match = re.search(r"대기\s*(?:인원)?\s*[:：]?\s*(\d+)\s*명", text)
    total = int(total_match.group(1)) if total_match else None
    current = int(current_match.group(1)) if current_match else None
    wait = int(wait_match.group(1)) if wait_match else None
    return current, total, wait


def extract_reception_period(value: Any) -> str:
    text = normalize_date_text(value)
    match = re.search(r"(20\d{2}-\d{2}-\d{2})(?:\s+\d{1,2}:\d{2})?\s*~\s*(20\d{2}-\d{2}-\d{2})(?:\s+\d{1,2}:\d{2})?", text)
    if match:
        return f"{match.group(1)} ~ {match.group(2)}"
    return text


def parse_list_row(tr: Any) -> dict[str, Any] | None:
    cells = tr.find_all("td", recursive=False)
    if len(cells) < 7:
        return None
    course_id = extract_course_id(tr)
    if not course_id:
        return None
    title, org = extract_title_branch(cells[1])
    if not title:
        return None
    current, total, wait = parse_capacity(cells[4].get_text(" ", strip=True))
    raw_url = f"{DETAIL_URL}?{urlencode({'lng_id': course_id})}"
    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": course_id,
        "provider_course_id": course_id,
        "title": title,
        "branch": org or DEFAULT_BRANCH,
        "branch_code": branch_code(org or DEFAULT_BRANCH),
        "address": DEFAULT_ADDRESS,
        "phone": DEFAULT_PHONE,
        "period": normalize_date_text(cells[3].get_text(" ", strip=True)),
        "reception_period": extract_reception_period(cells[4].get_text(" ", strip=True)),
        "schedule_raw": normalize_date_text(cells[3].get_text(" ", strip=True)),
        "target": "",
        "age_group": "",
        "category_raw": normalize_space(cells[2].get_text(" ", strip=True)),
        "fee": "",
        "material_fee": None,
        "material_note": "",
        "status": normalize_status(cells[5].get_text(" ", strip=True)),
        "raw_url": raw_url,
        "application_url": raw_url,
        "description": "",
        "instructor": "",
        "capacity_current": current,
        "capacity_total": total,
        "waitlist_total": wait,
        "image_url": "",
        "collection_category": "교육·체험",
        "domain_category": "평생학습",
        "source_group": "lifelong_learning",
        "operator_type": "지자체/공공기관",
        "collection_type": "static_html+detail_html",
    }


def clean_branch(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return DEFAULT_BRANCH
    parts = re.split(r"\s{2,}| / |,", text)
    first = normalize_space(parts[0])
    words = first.split()
    if len(words) >= 2 and words[-1] == words[-2]:
        words.pop()
        first = " ".join(words)
    elif len(words) >= 2 and words[-2].endswith(words[-1]):
        words.pop()
        first = " ".join(words)
    first = normalize_space(first)
    return first or text


def infer_address(branch: str, venue: str) -> str:
    text = normalize_space(venue or branch)
    if not text:
        return DEFAULT_ADDRESS
    if "아산" in text or any(token in text for token in ["읍", "면", "동", "로", "길"]):
        return f"충청남도 아산시 {text}"
    return DEFAULT_ADDRESS


def normalize_target(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return ""
    if "성인" in text:
        return "성인"
    if any(token in text for token in ["초등", "아동", "어린이", "유아"]):
        return text
    if any(token in text for token in ["청소년", "중학생", "고등학생"]):
        return text
    return text


def infer_age_group(target: str, title: str, category: str) -> str:
    text = f"{target} {title} {category}"
    if any(token in text for token in ["유아", "어린이", "아동", "초등"]):
        return "KIDS"
    if any(token in text for token in ["청소년", "중학생", "고등학생"]):
        return "TEEN"
    if "성인" in text:
        return "ADULT"
    return ""


def extract_image_url(soup: BeautifulSoup) -> str:
    for img in soup.select("#content img[src], .contents img[src], table img[src], dl img[src]"):
        src = normalize_space(img.get("src"))
        if not src or src.startswith("data:"):
            continue
        lower = src.lower()
        if any(skip in lower for skip in ["logo", "icon", "ico_", "bullet", "btn_"]):
            continue
        return urljoin(BASE_URL, src)
    return ""


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    try:
        soup = fetch_soup(session, row["raw_url"], timeout)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Asan detail failed %s: %s", row.get("raw_url"), exc)
        return row
    pairs = dl_pairs(soup)
    title = normalize_space(pairs.get("강좌명") or row.get("title"))
    category = normalize_space(pairs.get("강좌분류") or row.get("category_raw"))
    target = normalize_target(pairs.get("교육대상"))
    venue = normalize_space(pairs.get("교육장소"))
    branch = clean_branch(venue or row.get("branch"))
    description = normalize_space(" ".join(part for part in [pairs.get("강좌소개"), pairs.get("강의계획서"), pairs.get("주의사항")] if part))
    period = normalize_date_text(pairs.get("교육기간") or row.get("period"))
    schedule = normalize_space(pairs.get("교육시간"))
    schedule_raw = normalize_space(" ".join(part for part in [period, schedule] if part))
    current, total, wait = parse_capacity(pairs.get("접수인원") or "")

    row.update(
        {
            "title": title,
            "branch": branch,
            "branch_code": branch_code(branch),
            "category_raw": category,
            "target": target,
            "age_group": infer_age_group(target, title, category),
            "phone": normalize_space(pairs.get("문의전화")) or row.get("phone") or DEFAULT_PHONE,
            "room": venue,
            "venue_name": venue,
            "address": infer_address(branch, venue),
            "venue_address": infer_address(branch, venue),
            "period": period,
            "schedule_raw": schedule_raw,
            "reception_period": extract_reception_period(
                pairs.get("일반신청기간") or pairs.get("추가접수기간") or pairs.get("접수인원") or row.get("reception_period")
            ),
            "fee": normalize_fee(pairs.get("수강료")),
            "material_fee": extract_material_fee_amount(pairs.get("재료비") or description),
            "material_note": normalize_space(pairs.get("재료비")),
            "capacity_current": current if current is not None else row.get("capacity_current"),
            "capacity_total": total if total is not None else row.get("capacity_total"),
            "waitlist_total": wait if wait is not None else row.get("waitlist_total"),
            "status": normalize_status(pairs.get("신청상태"), pairs.get("교육상태"), row.get("status")),
            "instructor": normalize_space(pairs.get("강사")),
            "description": description[:4000],
            "image_url": extract_image_url(soup),
            "raw_fields": {"detail_pairs": pairs, "parser": "asan_sugang_list_dl_detail"},
        }
    )
    return row


def is_expired_course(row: dict[str, Any]) -> bool:
    try:
        _start, end_date = parse_date_range(row.get("period") or row.get("schedule_raw") or "")
    except Exception:  # noqa: BLE001
        return False
    if end_date is None:
        return False
    end_day = end_date.date() if hasattr(end_date, "date") else end_date
    return end_day < date.today()


def collect(
    limit: int | None = None,
    max_pages: int = 20,
    timeout: int = 25,
    include_expired: bool = False,
    detail: bool = True,
) -> list[dict[str, Any]]:
    session = make_session()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        soup = fetch_soup(session, LIST_URL, timeout, params={"pageIndex": page, "pageUnit": 50})
        page_count = 0
        for tr in soup.select("table tr"):
            row = parse_list_row(tr)
            if not row or row["provider_course_id"] in seen:
                continue
            seen.add(row["provider_course_id"])
            if detail:
                row = enrich_detail(session, row, timeout)
            if not include_expired and is_expired_course(row):
                continue
            rows.append(row)
            page_count += 1
            if limit is not None and len(rows) >= limit:
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
        "address_source": "crawler_inferred",
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
    rows = collect(
        limit=limit,
        max_pages=args.max_pages,
        timeout=args.timeout,
        include_expired=args.include_expired,
        detail=not args.no_detail,
    )
    print_quality(rows)
    saved = save_rows(rows, mark_stale=args.mark_stale) if args.save_db else 0
    elapsed = (datetime.now() - started).total_seconds()
    logger.info("%s completed collected=%s saved=%s elapsed=%.1fs", PROVIDER, len(rows), saved, elapsed)
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
