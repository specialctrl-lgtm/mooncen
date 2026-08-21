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


PROVIDER = "MUNI_WWW_JINJU_GO_KR_5DF28B13"
PROVIDER_NAME = "진주시 통합예약 육아종합지원센터"
BASE_URL = "https://www.jinju.go.kr"
LIST_URL = f"{BASE_URL}/yeyak/08870/08882/09650.web"
DEFAULT_BRANCH = "진주시 육아종합지원센터"
DEFAULT_ADDRESS = "경상남도 진주시"
DEFAULT_PHONE = "055-749-5435"


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from DB.db_utils import get_db_cursor  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, extract_krw_amount, setup_logger  # noqa: E402


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = setup_logger("Crawler_JinjuChildcareReservation")


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


def normalize_fee(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return ""
    if re.fullmatch(r"0\s*원", text) or "무료" in text:
        return "무료"
    amount = extract_krw_amount(text)
    if amount is not None:
        return f"{amount:,}원"
    return text


def normalize_status(value: Any, period: str) -> str:
    text = normalize_space(value)
    if any(token in text for token in ["접수중", "대기접수"]):
        return "OPEN"
    if "홍보중" in text:
        return "SCHEDULED"
    if any(token in text for token in ["정원마감", "접수마감", "마감", "종료"]):
        return "CLOSED"
    _start, end = parse_date_range(period)
    if end and end < date.today():
        return "CLOSED"
    return "OPEN"


def page_url(base_url: str, page: int) -> str:
    if page <= 1:
        return base_url
    parsed = urlparse(base_url)
    query = parse_qs(parsed.query)
    query["cpage"] = [str(page)]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def fetch_soup(session: requests.Session, url: str, timeout: int) -> BeautifulSoup:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def discover_menu_links(soup: BeautifulSoup) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if "/yeyak/08870/08882/" not in href:
            continue
        if "amode=" in href or "Download" in href:
            continue
        url = urljoin(BASE_URL, href)
        if url in seen:
            continue
        label = normalize_space(a.get_text(" ", strip=True))
        if not label:
            continue
        seen.add(url)
        links.append((label, url))
    if not links:
        links = [("전체강좌", LIST_URL)]
    return links


def card_pairs(card: Tag) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for li in card.select(".cp31dlist1 li.di"):
        key = normalize_space(li.select_one(".dt").get_text(" ", strip=True) if li.select_one(".dt") else "")
        key = normalize_space(key.rstrip(":："))
        value = normalize_space(li.select_one(".dd").get_text(" ", strip=True) if li.select_one(".dd") else "")
        if key:
            pairs[key] = value
    return pairs


def parse_capacity(capacity: str, current_text: str) -> tuple[int | None, int | None, int | None]:
    nums = [int(num.replace(",", "")) for num in re.findall(r"\d[\d,]*", normalize_space(capacity))]
    total = nums[0] if len(nums) >= 1 else None
    wait_total = nums[2] if len(nums) >= 3 else None
    current_match = re.search(r"(\d[\d,]*)\s*명", normalize_space(current_text))
    current = int(current_match.group(1).replace(",", "")) if current_match else (nums[1] if len(nums) >= 2 else None)
    return current, total, wait_total


def infer_age_group(target: str, title: str) -> str:
    text = f"{target} {title}"
    if re.search(r"성인|부모|보육교직원|교직원", text):
        return "ADULT"
    if re.search(r"청소년|중학생|고등학생", text):
        return "TEEN"
    if re.search(r"영유아|유아|영아|아동|어린이|[0-9]{4}년생|[0-9]+세", text):
        return "KIDS"
    return ""


def parse_card(card: Tag, menu_label: str, current_url: str) -> dict[str, Any] | None:
    link = card.select_one("a.tg1[href*='amode=view'][href*='lecture=']")
    if not link:
        return None
    raw_url = urljoin(current_url, link.get("href", ""))
    lecture = parse_qs(urlparse(raw_url).query).get("lecture", [""])[0]
    if not lecture:
        return None
    status_text = normalize_space(link.select_one("em").get_text(" ", strip=True) if link.select_one("em") else "")
    title = normalize_space(link.select_one("strong.t1").get_text(" ", strip=True) if link.select_one("strong.t1") else link.get_text(" ", strip=True))
    pairs = card_pairs(card)
    category = normalize_space(" / ".join(part for part in [menu_label, pairs.get("교육구분")] if part))
    target = normalize_space(pairs.get("신청대상"))
    apply_period = normalize_date_text(pairs.get("접수일시"))
    period = normalize_date_text(pairs.get("교육기간"))
    schedule_time = normalize_date_text(pairs.get("요일시간"))
    capacity_current, capacity_total, wait_total = parse_capacity(
        pairs.get("정원/접수인원/대기자정원", ""),
        pairs.get("신청현황", ""),
    )
    fee = normalize_fee(pairs.get("수강료"))
    img = card.select_one("img[src]")
    image_url = urljoin(BASE_URL, img.get("src", "")) if img else ""
    description = "\n".join([title, *[f"{key}: {value}" for key, value in pairs.items() if value]])

    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": lecture,
        "provider_course_id": lecture,
        "title": title,
        "branch": DEFAULT_BRANCH,
        "branch_code": branch_code(DEFAULT_BRANCH),
        "address": DEFAULT_ADDRESS,
        "phone": DEFAULT_PHONE,
        "period": period,
        "schedule_raw": normalize_space(" ".join(part for part in [period, schedule_time] if part)),
        "target": target,
        "age_group": infer_age_group(target, title),
        "category_raw": category,
        "fee": fee,
        "material_fee": None,
        "material_note": "",
        "status": normalize_status(status_text, period),
        "raw_url": raw_url,
        "application_url": raw_url,
        "application_type": "ONLINE" if "인터넷" in card.get_text(" ", strip=True) else "",
        "description": description,
        "image_url": image_url,
        "instructor": "",
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "waitlist_total": wait_total,
        "apply_period": apply_period,
    }


def is_expired(row: dict[str, Any]) -> bool:
    _start, end = parse_date_range(row.get("period"))
    return bool(end and end < date.today())


def collect(
    limit: int | None = None,
    max_pages: int = 5,
    timeout: int = 25,
    include_expired: bool = False,
) -> list[dict[str, Any]]:
    session = make_session()
    first_soup = fetch_soup(session, LIST_URL, timeout=timeout)
    menu_links = discover_menu_links(first_soup)
    logger.info("%s discovered menu links=%s", PROVIDER, len(menu_links))
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for menu_label, base_url in menu_links:
        for page in range(1, max_pages + 1):
            current_url = page_url(base_url, page)
            soup = first_soup if page == 1 and base_url == LIST_URL else fetch_soup(session, current_url, timeout=timeout)
            cards = soup.select(".cp31edu1list1 li.li1")
            logger.info("%s menu=%s page=%s cards=%s", PROVIDER, menu_label, page, len(cards))
            if not cards:
                break
            page_added = 0
            for card in cards:
                row = parse_card(card, menu_label, current_url)
                if not row:
                    continue
                key = normalize_space(row.get("provider_course_id"))
                if key in seen:
                    continue
                seen.add(key)
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
        "image_url",
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
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--mark-stale", action="store_true")
    parser.add_argument("--per-target-limit", type=int)
    parser.add_argument("--max-depth", type=int)
    parser.add_argument("--detail-limit", type=int)
    parser.add_argument("--no-detail", action="store_true")
    args = parser.parse_args()

    rows = collect(
        limit=args.limit or args.per_target_limit,
        max_pages=args.max_pages,
        timeout=args.timeout,
        include_expired=args.include_expired,
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
