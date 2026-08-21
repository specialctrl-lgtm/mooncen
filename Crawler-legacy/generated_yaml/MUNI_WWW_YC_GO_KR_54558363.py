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


PROVIDER = "MUNI_WWW_YC_GO_KR_54558363"
PROVIDER_NAME = "영천시 평생학습관 수강신청"
BASE_URL = "https://www.yc.go.kr"
LIST_PATH = "/edu/portal/academy/lecture/program/list.do"
DETAIL_PATH = "/edu/portal/academy/lecture/program/view.do"
MENU_ID = "0303000000"
LIST_URL = f"{BASE_URL}{LIST_PATH}?mId={MENU_ID}"
DEFAULT_BRANCH = "영천시 평생학습관"
DEFAULT_ADDRESS = "경상북도 영천시"
DEFAULT_PHONE = "054-330-6000"


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from DB.db_utils import get_db_cursor  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, extract_krw_amount, extract_material_fee_amount, setup_logger  # noqa: E402


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = setup_logger("Crawler_YeongcheonLifelongProgram")


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
    text = re.sub(r"\s*[~∼〜]\s*", " ~ ", text)
    return normalize_space(text)


def normalize_status(value: Any) -> str:
    text = normalize_space(value)
    if any(token in text for token in ["접수 중", "접수중", "신청 가능", "신청가능", "추가 접수", "대기자 접수", "교육 중", "교육중"]):
        return "OPEN"
    if any(token in text for token in ["접수 예정", "접수예정", "예정"]):
        return "SCHEDULED"
    if any(token in text for token in ["접수 마감", "접수마감", "마감", "종료", "폐강"]):
        return "CLOSED"
    return text or "OPEN"


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


def detail_url(idx: str) -> str:
    return f"{BASE_URL}{DETAIL_PATH}?{urlencode({'mId': MENU_ID, 'idx': idx})}"


def fetch_soup(session: requests.Session, url: str, timeout: int, method: str = "GET", data: dict[str, Any] | None = None) -> BeautifulSoup:
    if method.upper() == "POST":
        response = session.post(url, data=data or {}, timeout=timeout)
    else:
        response = session.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def list_payload(page: int) -> dict[str, str]:
    return {
        "type": "1",
        "page": str(page),
        "toggleAllOrgIdxs": "",
        "searchTxt": "",
    }


def table_pairs(scope: Tag | BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for tr in scope.select("table tr"):
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


def extract_idx(card: Tag) -> str:
    button = card.select_one("a[data-action][data-keyset]")
    keyset = button.get("data-keyset", "") if button else ""
    match = re.search(r"idx['\"]?\s*:\s*['\"]?(\d+)", keyset)
    return match.group(1) if match else ""


def clean_detail_title(title: str, branch: str) -> str:
    text = normalize_space(title)
    if branch:
        text = re.sub(rf"^\[{re.escape(branch)}\]\s*", "", text)
    text = re.sub(r"\s*-\s*(대기자\s*)?(추가\s*)?(접수 중|접수중|접수|접수 마감|접수마감|마감|종료|교육 중|교육중)\s*$", "", text)
    return normalize_space(text)


def parse_capacity(value: Any) -> tuple[int | None, int | None, int | None]:
    text = normalize_space(value)
    total = current = waitlist = None
    match = re.search(r"정원\s*:?\s*(\d[\d,]*)", text)
    if match:
        total = int(match.group(1).replace(",", ""))
    match = re.search(r"신청\s*:?\s*(\d[\d,]*)", text)
    if match:
        current = int(match.group(1).replace(",", ""))
    match = re.search(r"후보자\s*:?\s*(\d[\d,]*)", text)
    if match:
        waitlist = int(match.group(1).replace(",", ""))
    return current, total, waitlist


def infer_age_group(target: str, title: str) -> str:
    text = f"{target} {title}"
    if re.search(r"성인|시민|19세\s*이상|대학생|직장인", text):
        return "ADULT"
    if re.search(r"중학생|고등학생|청소년", text):
        return "TEEN"
    if re.search(r"초등|어린이|아동|유아|미취학", text):
        return "KIDS"
    return ""


def extract_description(pairs: dict[str, str]) -> str:
    parts = []
    for key in ["강좌 정보", "유의 사항"]:
        value = normalize_space(pairs.get(key))
        if value:
            parts.append(f"{key}: {value}")
    return "\n".join(parts)


def parse_card(card: Tag) -> dict[str, Any] | None:
    idx = extract_idx(card)
    title = normalize_space(card.select_one(".cardTop .title").get_text(" ", strip=True) if card.select_one(".cardTop .title") else "")
    if not idx or not title:
        return None
    branch = normalize_space(card.select_one(".cardTop .course").get_text(" ", strip=True) if card.select_one(".cardTop .course") else DEFAULT_BRANCH)
    status_text = normalize_space(card.select_one(".cardTop .process1").get_text(" ", strip=True) if card.select_one(".cardTop .process1") else "")
    pairs = table_pairs(card)
    current, total, waitlist = parse_capacity(" ".join([pairs.get("신청현황", ""), pairs.get("모집인원", "")]))
    period = normalize_date_text(pairs.get("교육기간"))
    schedule_time = normalize_date_text(pairs.get("교육일시"))
    target = normalize_space(pairs.get("교육대상"))
    fee_text = normalize_space(pairs.get("수강료/재료비") or pairs.get("수강료"))
    phone = normalize_space(pairs.get("문의처") or DEFAULT_PHONE)

    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": idx,
        "provider_course_id": idx,
        "title": title,
        "branch": branch,
        "branch_code": branch_code(branch),
        "address": DEFAULT_ADDRESS,
        "phone": phone,
        "period": period,
        "schedule_raw": normalize_space(" ".join(part for part in [period, schedule_time] if part)),
        "target": target,
        "age_group": infer_age_group(target, title),
        "category_raw": "",
        "fee": normalize_fee(fee_text),
        "material_fee": None,
        "material_note": "",
        "status": normalize_status(status_text),
        "raw_url": detail_url(idx),
        "application_url": detail_url(idx),
        "application_type": "ONLINE",
        "description": "",
        "image_url": "",
        "instructor": "",
        "capacity_current": current,
        "capacity_total": total,
        "waitlist_total": waitlist,
    }


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    idx = normalize_space(row.get("external_id"))
    if not idx:
        return row
    try:
        soup = fetch_soup(session, detail_url(idx), timeout=timeout)
    except Exception as exc:
        logger.warning("Detail fetch failed %s: %s", idx, exc)
        return row

    pairs = table_pairs(soup)
    detail_title = clean_detail_title(pairs.get("강좌명", ""), normalize_space(row.get("branch")))
    if detail_title:
        row["title"] = detail_title
    row["period"] = normalize_date_text(pairs.get("교육 기간") or pairs.get("교육기간") or row.get("period"))
    schedule_time = normalize_date_text(pairs.get("교육 시간") or pairs.get("교육시간"))
    row["schedule_raw"] = normalize_space(" ".join(part for part in [row.get("period"), schedule_time] if part))
    row["target"] = normalize_space(pairs.get("교육 대상") or pairs.get("교육대상") or row.get("target"))
    row["category_raw"] = normalize_space(pairs.get("분류") or row.get("category_raw"))
    row["fee"] = normalize_fee(pairs.get("수강료") or row.get("fee"))
    material_text = normalize_space(pairs.get("재료비") or pairs.get("교재 및 준비물"))
    row["material_note"] = material_text
    row["material_fee"] = extract_krw_amount(material_text) or extract_material_fee_amount(material_text)
    row["instructor"] = normalize_space(pairs.get("강사명") or row.get("instructor"))
    row["description"] = extract_description(pairs)
    detail_status = normalize_status(pairs.get("강좌명"))
    row["status"] = detail_status if detail_status in {"OPEN", "SCHEDULED", "CLOSED"} else normalize_status(row.get("status"))
    row["phone"] = normalize_space(pairs.get("문의 전화") or pairs.get("문의전화") or row.get("phone"))
    place = normalize_space(pairs.get("강의 장소") or pairs.get("강의장소"))
    if place:
        row["address"] = place if re.search(r"[시군구]|로|길|읍|면|동", place) else DEFAULT_ADDRESS
        row["branch"] = normalize_space(row.get("branch") or place)
        row["branch_code"] = branch_code(row["branch"])
    row["age_group"] = infer_age_group(row.get("target", ""), row.get("title", ""))
    return row


def is_expired(row: dict[str, Any]) -> bool:
    _start, end = parse_date_range(row.get("period"))
    return bool(end and end < date.today())


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
        soup = fetch_soup(session, LIST_URL, timeout=timeout, method="POST", data=list_payload(page))
        cards = soup.select(".cardWrap")
        logger.info("%s page %s cards=%s", PROVIDER, page, len(cards))
        if not cards:
            break
        page_added = 0
        for card in cards:
            row = parse_card(card)
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
        if page_added == 0 and page > 1:
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
        "instructor",
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
    parser.add_argument("--max-pages", type=int, default=20)
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
