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
import urllib3
from bs4 import BeautifulSoup, Tag


PROVIDER = "MUNI_WWW_TONGYEONG_GO_KR_DC5CDBF8"
PROVIDER_NAME = "통영시 평생학습 강좌정보"
BASE_URL = "https://www.tongyeong.go.kr"
LIST_PATH = "/tylearning/04266/04267/05286.web"
LIST_URL = f"{BASE_URL}{LIST_PATH}"
DEFAULT_BRANCH = "통영시청"
DEFAULT_ADDRESS = "경상남도 통영시 무전3길 29"
DEFAULT_PHONE = "055-650-2623"


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from DB.db_utils import get_db_cursor  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, setup_logger  # noqa: E402


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = setup_logger("Crawler_TongyeongLifelong")


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
    text = re.sub(
        r"\b(\d{4})[.](\d{1,2})[.](\d{1,2})\b",
        lambda m: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}",
        text,
    )
    text = re.sub(r"\s*~\s*", " ~ ", text)
    return normalize_space(text)


def fetch_soup(session: requests.Session, url: str, timeout: int) -> BeautifulSoup:
    response = session.get(url, timeout=timeout, verify=False)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def list_url(page: int) -> str:
    if page <= 1:
        return LIST_URL
    return f"{LIST_URL}?{urlencode({'cpage': page})}"


def stable_id(*parts: Any) -> str:
    seed = "|".join(normalize_space(part) for part in parts if normalize_space(part))
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def branch_code(branch: Any) -> str:
    return stable_id(PROVIDER, normalize_space(branch) or DEFAULT_BRANCH)[:12]


def normalize_status(value: Any) -> str:
    text = normalize_space(value)
    if any(token in text for token in ["접수중", "접수대기"]):
        return "OPEN"
    if any(token in text for token in ["접수마감", "신청마감", "마감"]):
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


def parse_counts(value: Any) -> tuple[int | None, int | None]:
    nums = [int(num.replace(",", "")) for num in re.findall(r"\d[\d,]*", normalize_space(value))]
    if len(nums) >= 2:
        return nums[0], nums[1]
    return None, None


def table_pairs(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for tr in soup.select("table tr"):
        cells = [cell for cell in tr.find_all(["th", "td"], recursive=False)]
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


def parse_list_row(row: Tag, current_url: str) -> dict[str, Any] | None:
    cells = row.find_all(["th", "td"], recursive=False)
    if len(cells) < 8 or cells[0].name != "th" or cells[1].name != "td":
        return None
    link = cells[1].select_one("a[href]")
    title = normalize_space(link.get_text(" ", strip=True) if link else cells[1].get_text(" ", strip=True))
    if not title:
        return None
    raw_url = urljoin(current_url, link.get("href") if link else "")
    idx_match = re.search(r"[?&]idx=(\d+)", raw_url)
    external_id = idx_match.group(1) if idx_match else stable_id(raw_url, title)
    branch = normalize_space(cells[0].get_text(" ", strip=True)).strip("[]") or DEFAULT_BRANCH
    capacity_current, capacity_total = parse_counts(cells[2].get_text(" ", strip=True))
    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": external_id,
        "provider_course_id": external_id,
        "title": title,
        "branch": branch,
        "branch_code": branch_code(branch),
        "address": DEFAULT_ADDRESS,
        "phone": DEFAULT_PHONE,
        "period": normalize_date_text(cells[4].get_text(" ", strip=True)),
        "schedule_raw": normalize_date_text(cells[4].get_text(" ", strip=True)),
        "target": "",
        "age_group": "ADULT",
        "fee": normalize_fee(cells[6].get_text(" ", strip=True)),
        "status": normalize_status(cells[7].get_text(" ", strip=True)),
        "description": "",
        "image_url": "",
        "raw_url": raw_url,
        "application_url": raw_url,
        "application_type": "ONLINE" if "온라인" in cells[5].get_text(" ", strip=True) else "",
        "application_method_raw": normalize_space(cells[5].get_text(" ", strip=True)),
        "reservation_available": normalize_status(cells[7].get_text(" ", strip=True)) == "OPEN",
        "category": "평생학습",
        "venue_name": "",
        "venue_address": DEFAULT_ADDRESS,
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "reception_period": normalize_date_text(cells[3].get_text(" ", strip=True)),
        "collection_category": "평생학습",
        "domain_category": "평생학습",
        "source_group": "lifelong_learning",
        "operator_type": "지자체/공공기관",
        "collection_type": "static_html",
        "program_type": "OFFLINE",
        "raw_fields": {"list_parser": "tongyeong_lifelong_table"},
    }


def is_expired_course(row: dict[str, Any]) -> bool:
    _, end_date = parse_date_range(row.get("period"))
    return bool(end_date and end_date < datetime.now().date())


def description_from_pairs(pairs: dict[str, str]) -> str:
    parts = []
    for key in ["교육기간", "교육장소", "모집대상", "접수방법", "이용문의", "첨부파일"]:
        value = normalize_space(pairs.get(key))
        if value:
            parts.append(f"{key}: {value}")
    return normalize_space(" ".join(parts))


def image_from_detail(soup: BeautifulSoup) -> str:
    for image in soup.select("img[src]"):
        src = normalize_space(image.get("src"))
        alt = normalize_space(image.get("alt"))
        if "ImagePrint.do" in src or "사진" in alt:
            return urljoin(BASE_URL, src)
    return ""


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    soup = fetch_soup(session, row["raw_url"], timeout)
    heading = soup.select_one("#body h1, h1")
    if heading:
        title = normalize_space(heading.get_text(" ", strip=True))
        row["title"] = re.sub(r"^\[[^]]+\]\s*", "", title) or row["title"]
    pairs = table_pairs(soup)
    row["period"] = normalize_date_text(pairs.get("교육기간")) or row["period"]
    row["schedule_raw"] = row["period"]
    row["venue_name"] = normalize_space(pairs.get("교육장소"))
    row["target"] = normalize_space(pairs.get("모집대상")) or row.get("target", "")
    row["reception_period"] = normalize_date_text(pairs.get("접수기간")) or row.get("reception_period", "")
    row["fee"] = normalize_fee(pairs.get("수강료")) or row.get("fee", "")
    row["application_method_raw"] = normalize_space(pairs.get("접수방법")) or row.get("application_method_raw", "")
    row["phone"] = normalize_space(pairs.get("이용문의")) or row.get("phone", "")
    row["description"] = description_from_pairs(pairs)
    row["image_url"] = image_from_detail(soup)
    row["raw_fields"]["detail_pairs"] = pairs
    row["raw_fields"]["detail_parser"] = "tongyeong_lifelong_detail"
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
        current_url = list_url(page)
        soup = fetch_soup(session, current_url, timeout)
        table = soup.select_one("table")
        if not table:
            break
        page_count = 0
        for tr in table.select("tr"):
            row = parse_list_row(tr, current_url)
            if not row or row["provider_course_id"] in seen:
                continue
            seen.add(row["provider_course_id"])
            if detail:
                try:
                    row = enrich_detail(session, row, timeout)
                except Exception as exc:
                    logger.warning("Tongyeong detail failed %s: %s", row.get("raw_url"), exc)
            if not include_expired and is_expired_course(row):
                logger.info("Skipping expired Tongyeong course: %s / %s", row.get("title"), row.get("period"))
                continue
            rows.append(row)
            page_count += 1
            if limit and len(rows) >= limit:
                return rows
        if page_count == 0 and not include_expired:
            break
    return rows


def quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fields = ["title", "branch", "address", "period", "schedule_raw", "target", "fee", "status", "description", "image_url"]
    counts = {field: sum(1 for row in rows if clean_text(row.get(field))) for field in fields}
    score = round(sum(counts.values()) / (len(rows) * len(fields)) * 100, 1) if rows else 0.0
    return {"rows": len(rows), "score": score, "field_counts": counts}


def print_quality(rows: list[dict[str, Any]]) -> None:
    print(json.dumps(quality(rows), ensure_ascii=False, indent=2))
    print("\nSAMPLE")
    for row in rows[:5]:
        print(" | ".join([normalize_space(row.get(key)) for key in ["title", "branch", "address", "period", "target", "fee", "status"]]))


def save_branch_with_address(row: dict[str, Any]) -> str:
    branch = {
        "provider": PROVIDER,
        "branch_code": (normalize_space(row.get("branch_code")) or branch_code(row.get("branch")))[:50],
        "name": (normalize_space(row.get("branch")) or DEFAULT_BRANCH)[:100],
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
    parser = argparse.ArgumentParser(description="Tongyeong lifelong course crawler")
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
