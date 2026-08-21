from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup, Tag


PROVIDER = "MUNI_YEYAK_HSCITY_GO_KR_E7FCC3C0"
PROVIDER_NAME = "화성특례시 통합예약 도서관 강좌"
BASE_URL = "https://yeyak.hscity.go.kr"
LIST_PATH = "/1002/3001/lectureList.do"
DETAIL_PATH = "/1002/3001/lectureDetail.do"
LIST_URL = f"{BASE_URL}{LIST_PATH}"
DEFAULT_ADDRESS = "경기도 화성시 남양읍 시청로 159"
DEFAULT_PHONE = "070-8664-1116"


BRANCH_ADDRESS_MAP = {
    "봉담도서관": "경기도 화성시 봉담읍 샘마을1길 8",
    "병점도서관": "경기도 화성시 병점3로 132-6",
    "둥지나래어린이도서관": "경기도 화성시 향남읍 행정중앙2로 88",
    "봉담와우도서관": "경기도 화성시 봉담읍 와우로34번길 11",
    "화성동탄중앙도서관": "경기도 화성시 동탄대로시범길 122",
    "동탄중앙이음터도서관": "경기도 화성시 동탄대로시범길 117",
    "송린이음터도서관": "경기도 화성시 수노을2로 150",
    "다원이음터도서관": "경기도 화성시 동탄순환대로 754-14",
    "목동이음터도서관": "경기도 화성시 동탄순환대로20길 6",
    "남양도서관": "경기도 화성시 남양읍 역골중앙로 143",
    "태안도서관": "경기도 화성시 병점3로 132-6",
}


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from DB.db_utils import get_db_cursor  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, setup_logger  # noqa: E402


logger = setup_logger("Crawler_HwaseongLibraryLecture")


def make_session() -> requests.Session:
    session = requests.Session()
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


def normalize_date_text(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return ""
    text = re.sub(
        r"\b(\d{4})[.](\d{1,2})[.](\d{1,2})\b",
        lambda m: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}",
        text,
    )
    text = re.sub(
        r"\b(\d{1,2})[.](\d{1,2})\b",
        lambda m: f"2026-{int(m.group(1)):02d}-{int(m.group(2)):02d}",
        text,
    )
    text = re.sub(r"\s*~\s*", " ~ ", text)
    return normalize_space(text)


def fetch_soup(session: requests.Session, url: str, timeout: int) -> BeautifulSoup:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def list_url(page: int) -> str:
    query = {
        "currentPageNo": str(page),
        "recordCountPerPage": "10",
        "searchCondition": "lectureNm",
        "searchInstitutionTypeCd": "INS01",
        "searchAreaEmd": "",
        "statusCd": "",
        "freeYn": "",
        "targetCd": "",
    }
    return f"{LIST_URL}?{urlencode(query)}"


def detail_url(lecture_idx: str) -> str:
    return f"{BASE_URL}{DETAIL_PATH}?{urlencode({'lectureIdx': lecture_idx})}"


def stable_id(*parts: Any) -> str:
    seed = "|".join(normalize_space(part) for part in parts if normalize_space(part))
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def branch_code(branch: Any) -> str:
    return stable_id(PROVIDER, normalize_space(branch))[:12]


def normalize_status(value: Any) -> str:
    text = normalize_space(value)
    if "접수중" in text or "신청하기" in text:
        return "OPEN"
    if "접수예정" in text:
        return "SCHEDULED"
    if any(token in text for token in ["마감", "종료", "완료"]):
        return "CLOSED"
    return text


def normalize_fee(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return ""
    if "무료" in text:
        return "무료"
    amount = re.search(r"[\d,]+\s*원", text)
    return normalize_space(amount.group(0)) if amount else text


def infer_age_group(target: str, title: str) -> str:
    text = f"{target} {title}"
    if re.search(r"영유아|유아|어린이|초등|아동|개월|7세", text):
        return "KIDS"
    if re.search(r"청소년|중등|고등", text):
        return "TEEN"
    if re.search(r"성인|신중년|50\\+|시민|일반", text):
        return "ADULT"
    return ""


def dt_dd_pairs(root: Tag | BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for dl in root.select("dl.item-desc"):
        key_node = dl.select_one("dt.desc-title")
        value_node = dl.select_one("dd.desc-txt")
        key = normalize_space(key_node.get_text(" ", strip=True) if key_node else "")
        value = normalize_space(value_node.get_text(" ", strip=True) if value_node else "")
        if key:
            pairs[key] = value
    return pairs


def list_info_pairs(item: Tag) -> dict[str, str]:
    pairs = dt_dd_pairs(item)
    branch_node = item.select_one(".info-title")
    if branch_node:
        pairs["운영기관"] = normalize_space(branch_node.get_text(" ", strip=True))
    return pairs


def parse_counts(value: Any) -> tuple[int | None, int | None]:
    nums = [int(num.replace(",", "")) for num in re.findall(r"\d[\d,]*", normalize_space(value))]
    if len(nums) >= 2:
        return nums[0], nums[1]
    return None, None


def branch_address(branch: str) -> str:
    for key, address in BRANCH_ADDRESS_MAP.items():
        if key in branch:
            return address
    return DEFAULT_ADDRESS


def clean_branch(value: Any) -> str:
    return normalize_space(re.sub(r"\s*바로가기\s*$", "", normalize_space(value)))


def parse_list_item(item: Tag) -> dict[str, Any] | None:
    if "total-cart" in (item.get("class") or []):
        return None
    onclick = normalize_space(item.select_one(".table-list-main").get("onclick") if item.select_one(".table-list-main") else "")
    idx_match = re.search(r"fnDetail\('([^']+)'\)", onclick)
    hidden_idx = item.select_one('input[name$="serviceIdx"]')
    lecture_idx = idx_match.group(1) if idx_match else normalize_space(hidden_idx.get("value") if hidden_idx else "")
    title_node = item.select_one(".main-title a")
    title = normalize_space(title_node.get_text(" ", strip=True) if title_node else "")
    if not lecture_idx or not title:
        return None
    flags = [normalize_space(flag.get_text(" ", strip=True)) for flag in item.select(".flag-list .flag")]
    target = flags[0] if flags else ""
    pairs = list_info_pairs(item)
    branch = clean_branch(pairs.get("운영기관")) or "화성특례시 도서관"
    apply_current, apply_total = parse_counts(pairs.get("신청자수"))
    wait_current, wait_total = parse_counts(pairs.get("대기자수"))
    period = normalize_date_text(pairs.get("강좌기간"))
    reception_period = normalize_date_text(pairs.get("접수기간"))
    raw_url = detail_url(lecture_idx)
    status_text = normalize_space(item.get_text(" ", strip=True))
    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": lecture_idx,
        "provider_course_id": lecture_idx,
        "title": title,
        "branch": branch,
        "branch_code": branch_code(branch),
        "address": branch_address(branch),
        "phone": normalize_space(pairs.get("문의처")) or DEFAULT_PHONE,
        "period": period,
        "schedule_raw": period,
        "target": target,
        "age_group": infer_age_group(target, title),
        "fee": normalize_fee(pairs.get("수강료") or (flags[1] if len(flags) > 1 else "")),
        "status": normalize_status(status_text),
        "description": "",
        "image_url": "",
        "raw_url": raw_url,
        "application_url": raw_url,
        "application_type": "ONLINE",
        "application_method_raw": normalize_space(pairs.get("접수방법")),
        "reservation_available": normalize_status(status_text) == "OPEN",
        "category": "도서관",
        "venue_name": branch,
        "venue_address": branch_address(branch),
        "capacity_current": apply_current,
        "capacity_total": apply_total,
        "waitlist_current": wait_current,
        "waitlist_total": wait_total,
        "reception_period": reception_period,
        "collection_category": "도서관",
        "domain_category": "도서관",
        "source_group": "library",
        "operator_type": "교육청/도서관",
        "collection_type": "static_html",
        "program_type": "OFFLINE",
        "raw_fields": {
            "list_parser": "hscity_library_card",
            "list_pairs": pairs,
        },
    }


def is_expired_course(row: dict[str, Any]) -> bool:
    _, end_date = parse_date_range(row.get("period"))
    return bool(end_date and end_date < datetime.now().date())


def description_from_detail(soup: BeautifulSoup) -> str:
    node = soup.select_one(".detail-tab.info-tab")
    return normalize_space(node.get_text(" ", strip=True)) if node else ""


def image_from_detail(soup: BeautifulSoup) -> str:
    node = soup.select_one(".detail-tab.info-tab img[src], .detail-info-article img[src]")
    src = normalize_space(node.get("src") if node else "")
    return urljoin(BASE_URL, src) if src else ""


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    soup = fetch_soup(session, row["raw_url"], timeout)
    title = normalize_space(soup.select_one(".detail-info-head-title .main-txt").get_text(" ", strip=True)) if soup.select_one(".detail-info-head-title .main-txt") else ""
    if title:
        row["title"] = title
    status = normalize_space(soup.select_one(".detail-info-head .flag").get_text(" ", strip=True)) if soup.select_one(".detail-info-head .flag") else ""
    if status:
        row["status"] = normalize_status(status)
        row["reservation_available"] = row["status"] == "OPEN"
    pairs = dt_dd_pairs(soup.select_one(".detail-info") or soup)
    row["branch"] = clean_branch(pairs.get("운영기관")) or row["branch"]
    row["branch_code"] = branch_code(row["branch"])
    row["category"] = normalize_space(pairs.get("강좌분류")) or row.get("category", "")
    row["target"] = normalize_space(pairs.get("교육대상")) or row.get("target", "")
    row["reception_period"] = normalize_date_text(pairs.get("접수일시")) or row.get("reception_period", "")
    row["period"] = normalize_date_text(pairs.get("수강기간")) or row.get("period", "")
    row["schedule_raw"] = normalize_space(f"{row['period']} {pairs.get('요일/시간', '')}")
    row["venue_name"] = normalize_space(pairs.get("장소")) or row.get("venue_name", "")
    row["venue_address"] = branch_address(row["branch"])
    row["address"] = row["venue_address"]
    apply_current, apply_total = parse_counts(pairs.get("신청/대기"))
    if apply_total:
        row["capacity_current"] = apply_current
        row["capacity_total"] = apply_total
    row["fee"] = normalize_fee(pairs.get("수강료")) or row.get("fee", "")
    row["material_fee"] = normalize_fee(pairs.get("재료비"))
    row["instructor"] = normalize_space(pairs.get("강사명"))
    row["application_method_raw"] = normalize_space(pairs.get("접수방법")) or row.get("application_method_raw", "")
    row["phone"] = normalize_space(pairs.get("문의처")) or row.get("phone", "")
    row["description"] = description_from_detail(soup)
    row["image_url"] = image_from_detail(soup)
    row["age_group"] = infer_age_group(row.get("target", ""), row["title"])
    row["raw_fields"]["detail_pairs"] = pairs
    row["raw_fields"]["detail_parser"] = "hscity_library_lecture_detail"
    return row


def collect(
    limit: int | None = None,
    max_pages: int = 5,
    timeout: int = 20,
    include_expired: bool = False,
    detail: bool = True,
) -> list[dict[str, Any]]:
    session = make_session()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        soup = fetch_soup(session, list_url(page), timeout)
        items = [item for item in soup.select("li.table-list-item") if "total-cart" not in (item.get("class") or [])]
        if not items:
            break
        page_count = 0
        for item in items:
            row = parse_list_item(item)
            if not row or row["provider_course_id"] in seen:
                continue
            seen.add(row["provider_course_id"])
            if detail:
                try:
                    row = enrich_detail(session, row, timeout)
                except Exception as exc:
                    logger.warning("Hwaseong detail failed %s: %s", row.get("raw_url"), exc)
            if not include_expired and is_expired_course(row):
                logger.info("Skipping expired Hwaseong course: %s / %s", row.get("title"), row.get("period"))
                continue
            rows.append(row)
            page_count += 1
            if limit and len(rows) >= limit:
                return rows
        if page_count == 0:
            break
    return rows


def quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fields = ["title", "branch", "raw_url", "address", "period", "schedule_raw", "target", "fee", "status", "description", "image_url"]
    counts = {field: sum(1 for row in rows if clean_text(row.get(field))) for field in fields}
    score = round(sum(counts.values()) / (len(rows) * len(fields)) * 100, 1) if rows else 0.0
    return {"rows": len(rows), "score": score, "field_counts": counts}


def print_quality(rows: list[dict[str, Any]]) -> None:
    print(json.dumps(quality(rows), ensure_ascii=False, indent=2))
    print("\nSAMPLE")
    for row in rows[:5]:
        print(
            " | ".join(
                [
                    normalize_space(row.get("title")),
                    normalize_space(row.get("branch")),
                    normalize_space(row.get("address")),
                    normalize_space(row.get("period")),
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
        "name": normalize_space(row.get("branch"))[:100],
        "address": normalize_space(row.get("address")),
        "phone": normalize_space(row.get("phone")),
        "website_url": LIST_URL,
        "address_source": "crawler_static",
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
        crawler.save_course(course)
        saved += 1
    logger.info("%s saved %s/%s rows.", PROVIDER, saved, len(rows))
    return saved


def main() -> int:
    parser = argparse.ArgumentParser(description="Hwaseong library lecture crawler")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--include-expired", action="store_true")
    parser.add_argument("--no-detail", action="store_true")
    parser.add_argument("--mark-stale", action="store_true")
    parser.add_argument("--per-target-limit", type=int)
    parser.add_argument("--max-depth", type=int)
    parser.add_argument("--detail-limit", type=int)
    args = parser.parse_args()

    started = datetime.now()
    rows = collect(
        limit=args.limit or args.per_target_limit,
        max_pages=args.max_pages,
        timeout=args.timeout,
        include_expired=args.include_expired,
        detail=not args.no_detail,
    )
    saved = save_rows(rows) if args.save_db else 0
    print_quality(rows)
    logger.info(
        "%s completed collected=%s saved=%s elapsed=%.1fs",
        PROVIDER,
        len(rows),
        saved,
        (datetime.now() - started).total_seconds(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
