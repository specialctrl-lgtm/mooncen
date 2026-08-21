from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib3
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse, parse_qs, urlunparse

import requests
from bs4 import BeautifulSoup, Tag


PROVIDER = "MUNI_WWW_YEONGDO_GO_KR_33400564"
PROVIDER_NAME = "영도구 통합예약 강좌교육"
BASE_URL = "https://www.yeongdo.go.kr"
LIST_URL = f"{BASE_URL}/reserve/01785/01791.web"
DEFAULT_BRANCH = "영도구 통합예약"
DEFAULT_ADDRESS = "부산광역시 영도구 태종로 423"
DEFAULT_PHONE = "051-419-4000"


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from DB.db_utils import get_db_cursor  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, extract_krw_amount, extract_material_fee_amount, setup_logger  # noqa: E402


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = setup_logger("Crawler_YeongdoReservation")


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
        r"(\d{4})[./](\d{1,2})[./](\d{1,2})(?:\s*(\d{1,2})시)?",
        lambda m: (
            f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            + (f" {int(m.group(4)):02d}:00" if m.group(4) else "")
        ),
        text,
    )
    text = re.sub(r"(\d{1,2})\s*:\s*(\d{1,2})", lambda m: f"{int(m.group(1)):02d}:{int(m.group(2)):02d}", text)
    text = re.sub(r"\s*[~∼〜]\s*", " ~ ", text)
    return normalize_space(text)


def normalize_fee(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return ""
    if "무료" in text or text in {"0원", "0 원"}:
        return "무료"
    amount = extract_krw_amount(text)
    if amount is not None:
        return f"{amount:,}원"
    return text


def normalize_status(value: Any, apply_period: str, period: str) -> str:
    text = normalize_space(value)
    if any(token in text for token in ["접수중", "접수 중"]):
        return "OPEN"
    if any(token in text for token in ["접수대기", "대기"]):
        return "SCHEDULED"
    if any(token in text for token in ["마감", "종료", "정원"]):
        return "CLOSED"
    _apply_start, apply_end = parse_date_range(apply_period)
    _start, end = parse_date_range(period)
    today = date.today()
    if end and end < today:
        return "CLOSED"
    if apply_end and apply_end < today:
        return "CLOSED"
    return "OPEN"


def page_url(page: int) -> str:
    if page <= 1:
        return LIST_URL
    return f"{LIST_URL}?{urlencode({'cpage': str(page)})}"


def fetch_soup(session: requests.Session, url: str, timeout: int) -> BeautifulSoup:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def table_pairs(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for tr in soup.select("table tr"):
        th = tr.find("th")
        td = tr.find("td")
        if not th or not td:
            continue
        key = normalize_space(th.get_text(" ", strip=True))
        value = normalize_space(td.get_text(" ", strip=True))
        if key:
            pairs[key] = value
    return pairs


def split_title_branch(value: str) -> tuple[str, str]:
    text = normalize_space(value)
    match = re.match(r"^\[([^\]]+)\]\s*(.+)$", text)
    if match:
        return normalize_space(match.group(1)), normalize_space(match.group(2))
    return DEFAULT_BRANCH, text


def list_pairs(card: Tag) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for li in card.select("ul.bu li"):
        text = normalize_space(li.get_text(" ", strip=True))
        if ":" not in text:
            continue
        key, value = text.split(":", 1)
        pairs[normalize_space(key)] = normalize_space(value)
    return pairs


def parse_capacity(value: Any) -> tuple[int | None, int | None, int | None]:
    nums = [int(num.replace(",", "")) for num in re.findall(r"\d[\d,]*", normalize_space(value))]
    if len(nums) >= 2:
        return nums[0], nums[1], None
    if len(nums) == 1:
        return None, nums[0], None
    return None, None, None


def infer_age_group(target: str, title: str) -> str:
    text = f"{target} {title}"
    if re.search(r"성인|직장인|어르신|노인", text):
        return "ADULT"
    if re.search(r"청소년|중학생|고등학생", text):
        return "TEEN"
    if re.search(r"영유아|유아|초등|어린이|아동|[0-9]+세", text):
        return "KIDS"
    return ""


def branch_address(branch: str, place: str) -> str:
    text = normalize_space(place or branch)
    if text and text not in {"온라인", "비대면"}:
        if text.startswith("부산"):
            return text
        return f"부산광역시 영도구 {text}"
    return DEFAULT_ADDRESS


def parse_card(card: Tag, current_url: str) -> dict[str, Any] | None:
    link = card.select_one("a.a1[href*='amode=view'][href*='idx=']")
    if not link:
        return None
    raw_url = urljoin(current_url, link.get("href", ""))
    idx = parse_qs(urlparse(raw_url).query).get("idx", [""])[0]
    if not idx:
        return None
    title_text = normalize_space(link.select_one("strong.t1").get_text(" ", strip=True) if link.select_one("strong.t1") else link.get_text(" ", strip=True))
    branch, title = split_title_branch(title_text)
    pairs = list_pairs(card)
    period = normalize_date_text(pairs.get("교육기간"))
    schedule_time = normalize_date_text(pairs.get("교육시간"))
    apply_period = normalize_date_text(pairs.get("모집기간"))
    target = normalize_space(pairs.get("모집대상"))
    current, total, wait_total = parse_capacity(pairs.get("모집인원"))
    status = normalize_space(card.select_one(".btns").get_text(" ", strip=True) if card.select_one(".btns") else "")
    img = card.select_one("img[src]")
    image_url = urljoin(BASE_URL, img.get("src", "")) if img else ""
    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": idx,
        "provider_course_id": idx,
        "title": title,
        "branch": branch,
        "branch_code": branch_code(branch),
        "address": branch_address(branch, ""),
        "phone": DEFAULT_PHONE,
        "period": period,
        "schedule_raw": normalize_space(" ".join(part for part in [period, schedule_time] if part)),
        "target": target,
        "age_group": infer_age_group(target, title),
        "category_raw": branch,
        "fee": "",
        "material_fee": None,
        "material_note": "",
        "status": normalize_status(status, apply_period, period),
        "raw_url": raw_url,
        "application_url": raw_url,
        "application_type": "ONLINE" if "온라인" in pairs.get("접수방법", "") else "",
        "description": "\n".join([title_text, *[f"{k}: {v}" for k, v in pairs.items() if v]]),
        "image_url": image_url,
        "instructor": "",
        "capacity_current": current,
        "capacity_total": total,
        "waitlist_total": wait_total,
        "apply_period": apply_period,
    }


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    try:
        soup = fetch_soup(session, normalize_space(row.get("raw_url")), timeout=timeout)
    except Exception as exc:
        logger.warning("Detail fetch failed %s: %s", row.get("external_id"), exc)
        return row
    pairs = table_pairs(soup)
    row["period"] = normalize_date_text(pairs.get("교육기간") or row.get("period"))
    schedule_time = normalize_date_text(pairs.get("교육시간"))
    row["schedule_raw"] = normalize_space(" ".join(part for part in [row.get("period"), schedule_time] if part))
    place = normalize_space(pairs.get("교육장소"))
    if place:
        row["branch"] = normalize_space(row.get("branch") or place)
        row["branch_code"] = branch_code(row["branch"])
        row["address"] = branch_address(row["branch"], place)
    row["fee"] = normalize_fee(pairs.get("수강료") or row.get("fee"))
    material = normalize_space(pairs.get("준비물"))
    row["material_note"] = material
    row["material_fee"] = extract_material_fee_amount(material)
    row["apply_period"] = normalize_date_text(pairs.get("접수기간") or row.get("apply_period"))
    row["target"] = normalize_space(pairs.get("모집대상") or row.get("target"))
    row["instructor"] = normalize_space(pairs.get("강사"))
    row["phone"] = normalize_space(pairs.get("이용문의") or row.get("phone"))
    row["application_type"] = "ONLINE" if "온라인" in normalize_space(pairs.get("접수방법")) else row.get("application_type")
    detail_summary = "\n".join(f"{k}: {v}" for k, v in pairs.items() if v)
    row["description"] = detail_summary or row.get("description")
    row["status"] = normalize_status(row.get("status"), row.get("apply_period", ""), row.get("period", ""))
    row["age_group"] = infer_age_group(row.get("target", ""), row.get("title", ""))
    return row


def is_expired(row: dict[str, Any]) -> bool:
    _start, end = parse_date_range(row.get("period"))
    return bool(end and end < date.today())


def collect(
    limit: int | None = None,
    max_pages: int = 10,
    timeout: int = 25,
    include_expired: bool = False,
    detail: bool = True,
) -> list[dict[str, Any]]:
    session = make_session()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        current_url = page_url(page)
        soup = fetch_soup(session, current_url, timeout=timeout)
        cards = soup.select(".list1f1t2b2 li.li1")
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
            if detail:
                row = enrich_detail(session, row, timeout=timeout)
            if not include_expired and is_expired(row):
                continue
            rows.append(row)
            page_added += 1
            if limit and len(rows) >= limit:
                return rows
        if page_added == 0 and page > 3:
            break
    return rows


def quality_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fields = ["title", "branch", "address", "period", "schedule_raw", "target", "fee", "status", "description", "raw_url", "image_url"]
    counts = {field: sum(1 for row in rows if normalize_space(row.get(field))) for field in fields}
    score = round((sum(counts.values()) / (len(fields) * max(1, len(rows)))) * 100, 1)
    grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D"
    return {"provider": PROVIDER, "collected": len(rows), "score": score, "grade": grade, "field_counts": counts, "sample_titles": [row.get("title") for row in rows[:5]]}


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
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--include-expired", action="store_true")
    parser.add_argument("--no-detail", action="store_true")
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--mark-stale", action="store_true")
    parser.add_argument("--per-target-limit", type=int)
    parser.add_argument("--max-depth", type=int)
    parser.add_argument("--detail-limit", type=int)
    args = parser.parse_args()

    rows = collect(limit=args.limit or args.per_target_limit, max_pages=args.max_pages, timeout=args.timeout, include_expired=args.include_expired, detail=not args.no_detail)
    saved = save_rows(rows) if args.save_db else 0
    report = quality_report(rows)
    report["saved"] = saved
    print(json.dumps(report, ensure_ascii=False, indent=2))
    for row in rows[: min(10, len(rows))]:
        print("SAMPLE\t{title}\t{branch}\t{period}\t{schedule}\t{target}\t{fee}\t{status}".format(title=row.get("title", ""), branch=row.get("branch", ""), period=row.get("period", ""), schedule=row.get("schedule_raw", ""), target=row.get("target", ""), fee=row.get("fee", ""), status=row.get("status", "")))
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
