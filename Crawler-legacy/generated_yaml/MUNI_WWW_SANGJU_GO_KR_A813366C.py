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


PROVIDER = "MUNI_WWW_SANGJU_GO_KR_A813366C"
PROVIDER_NAME = "상주시 통합예약 교육강좌"
BASE_URL = "https://www.sangju.go.kr"
LIST_PATH = "/page/15375/11881.tc"
DETAIL_PATH = "/reserve/reservation/detail.tc"
LIST_URL = f"{BASE_URL}{LIST_PATH}"
DEFAULT_BRANCH = "상주시 통합예약"
DEFAULT_ADDRESS = "경상북도 상주시 상산로 223"


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from DB.db_utils import get_db_cursor  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, setup_logger  # noqa: E402


logger = setup_logger("Crawler_SangjuEducationReservation")


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
            "Referer": LIST_URL,
        }
    )
    return session


def normalize_space(value: Any) -> str:
    text = clean_text(value).replace("\xa0", " ")
    return clean_text(re.sub(r"\s+", " ", text))


def normalize_period(value: Any) -> str:
    text = normalize_space(value)
    text = re.sub(
        r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일(?:\s*(\d{1,2})시\s*(\d{1,2})분)?",
        lambda m: (
            f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            + (f" {int(m.group(4)):02d}:{int(m.group(5)):02d}" if m.group(4) else "")
        ),
        text,
    )
    text = re.sub(
        r"(\d{4})[.](\d{1,2})[.](\d{1,2})",
        lambda m: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}",
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
    return f"{LIST_URL}?{urlencode({'pageIndex': page})}"


def detail_url(cycl_no: str) -> str:
    query = {
        "mn": "15375",
        "pageNo": "11881",
        "searchTrgtClsfCd": "RMS004001",
        "searchFcltNo": "",
        "cyclNo": cycl_no,
    }
    return urljoin(BASE_URL, DETAIL_PATH) + "?" + urlencode(query)


def onclick_id(value: str | None, method: str) -> str:
    match = re.search(rf"{re.escape(method)}\(['\"]([^'\"]+)['\"]\)", value or "")
    return match.group(1).strip() if match else ""


def section_pairs(node: Tag | None) -> dict[str, str]:
    pairs: dict[str, str] = {}
    if not node:
        return pairs
    for li in node.select("ul.tm_cir > li"):
        label = li.select_one("span")
        if not label:
            continue
        key = normalize_space(label.get_text(" ", strip=True))
        label.extract()
        value = normalize_space(li.get_text(" ", strip=True))
        if key:
            pairs[key] = value
    return pairs


def table_shape_pairs(node: Tag | None) -> dict[str, str]:
    pairs: dict[str, str] = {}
    if not node:
        return pairs
    for item in node.select("ul.table_shape > li"):
        keys = item.select(".th_shape")
        values = item.select(".td_shape")
        for key_node, value_node in zip(keys, values):
            key = normalize_space(key_node.get_text(" ", strip=True))
            value = normalize_space(value_node.get_text(" ", strip=True))
            if key:
                pairs[key] = value
    return pairs


def normalize_fee(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return ""
    if text in {"없음", "무료", "미입력"}:
        return "무료" if text in {"없음", "무료"} else ""
    return text


def parse_capacity(value: Any) -> tuple[str, str]:
    text = normalize_space(value)
    match = re.search(r"(\d+)\s*/\s*(\d+)\s*명", text)
    if match:
        return match.group(1), match.group(2)
    return "", text


def infer_target(text: str) -> str:
    if "초등" in text:
        return "초등학생"
    if any(token in text for token in ["어린이", "아동"]):
        return "아동"
    if any(token in text for token in ["청소년", "중학생", "고등학생"]):
        return "청소년"
    if any(token in text for token in ["성인", "일반"]):
        return "성인"
    return ""


def infer_category(text: str) -> str:
    if any(token in text for token in ["도서관", "독서", "책", "리터러시"]):
        return "도서관"
    if any(token in text for token in ["박물관", "문화", "예술", "인문"]):
        return "문화예술"
    if any(token in text for token in ["디지털", "AI", "정보", "경제"]):
        return "디지털"
    if any(token in text for token in ["보건", "건강"]):
        return "건강"
    return "교육강좌"


def is_expired_course(row: dict[str, Any]) -> bool:
    _, end_date = parse_date_range(row.get("period"))
    if end_date is None:
        return False
    return end_date < datetime.now().date()


def stable_course_id(row: dict[str, Any]) -> str:
    external_id = normalize_space(row.get("external_id"))
    if external_id:
        return external_id
    seed = "|".join([PROVIDER, normalize_space(row.get("title")), normalize_space(row.get("raw_url"))])
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def parse_list_section(section: Tag, page: int) -> dict[str, Any] | None:
    title_node = section.select_one("h1 a[onclick*='reserveList.detail']")
    if not title_node:
        return None
    cycl_no = onclick_id(title_node.get("onclick"), "reserveList.detail")
    title = normalize_space(title_node.get_text(" ", strip=True))
    if not cycl_no or not title:
        return None
    right = section.select_one(".right") or section
    pairs = section_pairs(right)
    sub_pairs = section_pairs(section.select_one(".list_sub") or section)
    status = normalize_space(section.select_one(".top span").get_text(" ", strip=True) if section.select_one(".top span") else "")
    branch = normalize_space(pairs.get("시설명")) or DEFAULT_BRANCH
    address = normalize_space(pairs.get("주소")) or DEFAULT_ADDRESS
    period = normalize_period(pairs.get("운영기간"))
    apply_period = normalize_period(sub_pairs.get("접수기간"))
    capacity_current, capacity_total = parse_capacity(sub_pairs.get("정원"))
    raw_url = detail_url(cycl_no)
    text = normalize_space(section.get_text(" ", strip=True))
    row = {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": cycl_no,
        "provider_course_id": cycl_no,
        "title": title,
        "branch": branch,
        "branch_code": branch_code(branch),
        "address": address,
        "period": period,
        "schedule_raw": period,
        "target": infer_target(text),
        "fee": "",
        "status": status,
        "category": infer_category(f"{title} {pairs.get('분류')}"),
        "reception_period": apply_period,
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "venue_name": branch,
        "venue_address": address,
        "raw_url": raw_url,
        "application_url": raw_url if "접수중" in status else "",
        "description": text,
        "image_url": "",
        "collection_category": "공공예약",
        "domain_category": "평생학습",
        "source_group": "public_reservation",
        "operator_type": "지자체/공공기관",
        "program_type": "강좌",
        "raw_fields": {
            "list_pairs": pairs,
            "sub_pairs": sub_pairs,
            "parser": "sangju_education_reservation",
            "list_page": page,
        },
    }
    row["course_id"] = stable_course_id(row)
    return row


def description_from_detail(soup: BeautifulSoup) -> str:
    panel = soup.select_one("#tab1_panel .bd_scroll") or soup.select_one(".bd_scroll")
    if panel:
        lines = [normalize_space(part) for part in panel.get_text("\n", strip=True).splitlines()]
        return "\n".join(line for line in lines if line)
    return ""


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    soup = fetch_soup(session, row["raw_url"], timeout)
    top = soup.select_one(".img_jb") or soup
    title_node = top.select_one("h1")
    if title_node:
        row["title"] = normalize_space(title_node.get_text(" ", strip=True)) or row["title"]
    pairs = section_pairs(top)
    if pairs:
        row["branch"] = normalize_space(pairs.get("시설명")) or row["branch"]
        row["branch_code"] = branch_code(row["branch"])
        row["address"] = normalize_space(pairs.get("주소")) or row["address"]
        row["venue_name"] = row["branch"]
        row["venue_address"] = row["address"]
        row["period"] = normalize_period(pairs.get("운영기간")) or row["period"]
        row["schedule_raw"] = row["period"]
    detail_pairs = table_shape_pairs(soup.select_one(".hidden_box") or soup)
    fee_text = ""
    fee_panel = soup.select_one("#tab2_panel .bd_scroll")
    if fee_panel:
        fee_text = fee_panel.get_text(" ", strip=True)
    row["fee"] = normalize_fee(detail_pairs.get("이용료") or fee_text or row.get("fee"))
    description = description_from_detail(soup)
    if description:
        row["description"] = description
    if not row.get("target"):
        row["target"] = infer_target(row.get("description", ""))
    row["category"] = infer_category(f"{row.get('title')} {row.get('description')}")
    image = top.select_one(".slide img[src], img[src*='fms'], img[src*='upload']")
    if image and image.get("src"):
        row["image_url"] = urljoin(BASE_URL, image["src"])
    row["raw_fields"]["detail_pairs"] = detail_pairs
    row["raw_fields"]["detail_parser"] = "sangju_education_reservation_detail"
    row["course_id"] = stable_course_id(row)
    return row


def branch_code(name: Any) -> str:
    text = normalize_space(name) or DEFAULT_BRANCH
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def collect(
    limit: int | None = None,
    max_pages: int = 5,
    timeout: int = 20,
    include_expired: bool = False,
) -> list[dict[str, Any]]:
    session = make_session()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        soup = fetch_soup(session, list_url(page), timeout)
        page_rows = [row for row in (parse_list_section(sec, page) for sec in soup.select("#reserveList section")) if row]
        if not page_rows:
            break
        expired_on_page = 0
        for row in page_rows:
            key = stable_course_id(row)
            if key in seen:
                continue
            seen.add(key)
            try:
                row = enrich_detail(session, row, timeout)
            except Exception as exc:
                logger.warning("Sangju detail failed %s: %s", row.get("raw_url"), exc)
            if not include_expired and is_expired_course(row):
                expired_on_page += 1
                logger.info("Skipping expired Sangju course: %s / %s", row.get("title"), row.get("period"))
                continue
            rows.append(row)
            if limit and len(rows) >= limit:
                return rows
        if not include_expired and expired_on_page == len(page_rows):
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
        "name": (normalize_space(row.get("branch")) or DEFAULT_BRANCH)[:100],
        "address": normalize_space(row.get("address")),
        "phone": "",
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
    parser = argparse.ArgumentParser(description="Sangju education reservation crawler")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--include-expired", action="store_true")
    parser.add_argument("--mark-stale", action="store_true")
    parser.add_argument("--per-target-limit", type=int)
    parser.add_argument("--max-depth", type=int)
    parser.add_argument("--detail-limit", type=int)
    args = parser.parse_args()

    effective_limit = args.limit or args.per_target_limit
    started = datetime.now()
    rows = collect(limit=effective_limit, max_pages=args.max_pages, timeout=args.timeout, include_expired=args.include_expired)
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
