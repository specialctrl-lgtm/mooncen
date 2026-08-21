from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup, Tag


PROVIDER = "MUNI_LIFELONG_YUSEONG_GO_KR_E36DECD2"
PROVIDER_NAME = "유성구 평생학습센터"
BASE_URL = "https://lifelong.yuseong.go.kr"
LIST_PATH = "/lly/prog/lctr/lly/sub02_01/SEARCH/classList.do"
DETAIL_PATH = "/lly/prog/lctr/lly/sub02_01/SEARCH/classDetail.do"
LIST_URL = f"{BASE_URL}{LIST_PATH}"
DETAIL_URL = f"{BASE_URL}{DETAIL_PATH}"
DEFAULT_BRANCH = "유성구 평생학습센터"
DEFAULT_ADDRESS = "대전광역시 유성구 유성대로626번길 57"


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from DB.db_utils import get_db_cursor  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, setup_logger  # noqa: E402


logger = setup_logger("Crawler_YuseongLifelong")


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
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


def post_soup(session: requests.Session, url: str, data: dict[str, str], timeout: int) -> BeautifulSoup:
    response = session.post(url, data=data, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def list_payload(page: int) -> dict[str, str]:
    return {
        "pageIndex": str(page),
        "lctrNo": "",
        "searchLctrNo": "",
        "lctrGroupType": "",
        "searchLctrGroupCd": "",
        "searchLctrRcptGubun": "",
        "searchLctrRcptResult": "",
        "searchLctrYr": str(datetime.now().year),
        "searchKeyword": "",
    }


def detail_payload(lctr_no: str, page: int = 1) -> dict[str, str]:
    data = list_payload(page)
    data["lctrNo"] = lctr_no
    return data


def card_pairs(card: Tag) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for item in card.select(".list-1st li"):
        key_node = item.select_one(".tit")
        value_node = item.select_one(".txt")
        key = normalize_space(key_node.get_text(" ", strip=True) if key_node else "")
        value = normalize_space(value_node.get_text(" ", strip=True) if value_node else "")
        if key:
            pairs[key] = value
    return pairs


def normalize_fee(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return ""
    if "무료" in text:
        return "무료"
    amount = re.search(r"[\d,]+\s*원", text)
    if amount:
        return normalize_space(amount.group(0))
    return text


def extract_status_and_group(card: Tag) -> tuple[str, str]:
    statuses = [normalize_space(node.get_text(" ", strip=True)) for node in card.select(".status-wrap .status")]
    group = statuses[0] if statuses else ""
    status = ""
    for item in statuses[1:]:
        if any(keyword in item for keyword in ["접수", "마감", "폐강", "예정", "대기"]):
            status = item
            break
    return group, status or (statuses[-1] if statuses else "")


def parse_list_card(card: Tag) -> dict[str, Any] | None:
    lctr_no = normalize_space(card.get("data-key-no"))
    title_node = card.select_one(".title")
    title = normalize_space(title_node.get_text(" ", strip=True) if title_node else "")
    if not lctr_no or not title:
        return None

    group, status = extract_status_and_group(card)
    pairs = card_pairs(card)
    row = {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": lctr_no,
        "provider_course_id": lctr_no,
        "title": title,
        "branch": group or DEFAULT_BRANCH,
        "branch_code": branch_code(group or DEFAULT_BRANCH),
        "address": DEFAULT_ADDRESS,
        "phone": "",
        "period": normalize_period(pairs.get("교육기간")),
        "schedule_raw": normalize_space(pairs.get("교육일시")),
        "target": normalize_space(pairs.get("교육대상")),
        "fee": normalize_fee(pairs.get("수강료")),
        "status": status,
        "capacity_text": normalize_space(pairs.get("모집인원")),
        "application_method": normalize_space(pairs.get("접수방법")),
        "reception_count": normalize_space(pairs.get("접수인원")),
        "raw_url": f"{DETAIL_URL}?{urlencode({'lctrNo': lctr_no})}",
        "application_url": f"{DETAIL_URL}?{urlencode({'lctrNo': lctr_no})}",
        "description": "",
        "image_url": "",
        "category": group or "평생학습",
        "collection_category": "평생학습",
        "domain_category": "평생학습",
        "source_group": "municipal_lifelong_learning",
        "operator_type": "지자체/공공기관",
        "program_type": "강좌",
    }
    row["course_id"] = stable_course_id(row)
    return row


def detail_pairs(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for key_node, value_node in zip(soup.select(".subjact"), soup.select(".con")):
        key = normalize_space(key_node.get_text(" ", strip=True))
        value = normalize_space(value_node.get_text(" ", strip=True))
        if key:
            pairs[key] = value
    return pairs


def detail_sections(soup: BeautifulSoup) -> dict[str, str]:
    sections: dict[str, str] = {}
    for section in soup.select(".detail-index"):
        heading = section.select_one("h2, h3, h4")
        body = section.select_one(".dsc") or section
        key = normalize_space(heading.get_text(" ", strip=True) if heading else "")
        value = normalize_space(body.get_text(" ", strip=True))
        if key and value:
            sections[key] = value
    return sections


def parse_center_info(value: str) -> tuple[str, str]:
    text = normalize_space(value)
    address_match = re.search(r"\)\s*([^/]+)", text)
    phone_match = re.search(r"전화\s*:\s*([0-9-]+)", text)
    address = normalize_space(address_match.group(1)) if address_match else ""
    phone = normalize_space(phone_match.group(1)) if phone_match else ""
    return address, phone


def infer_branch(row: dict[str, Any], pairs: dict[str, str]) -> tuple[str, str, str]:
    text = " ".join(
        [
            normalize_space(row.get("title")),
            normalize_space(row.get("branch")),
            normalize_space(pairs.get("문의처")),
            normalize_space(pairs.get("교육장소")),
        ]
    )
    center_key = "전민센터" if "전민" in text else "구암센터" if "구암" in text else ""
    if not center_key:
        center_key = "구암센터" if pairs.get("구암센터") else "전민센터" if pairs.get("전민센터") else ""

    if center_key:
        address, fallback_phone = parse_center_info(pairs.get(center_key, ""))
        phone = ""
        contact = normalize_space(pairs.get("문의처"))
        contact_phone = re.search(r"([0-9]{2,3}-[0-9]{3,4}-[0-9]{4})", contact)
        if contact_phone:
            phone = contact_phone.group(1)
        return center_key, address or DEFAULT_ADDRESS, phone or fallback_phone
    return DEFAULT_BRANCH, DEFAULT_ADDRESS, ""


def extract_material_fee(value: str) -> str:
    text = normalize_space(value)
    amount = re.search(r"[\d,]+\s*(?:만)?원", text)
    return normalize_space(amount.group(0)) if amount else ""


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    lctr_no = normalize_space(row.get("external_id"))
    soup = post_soup(session, DETAIL_URL, detail_payload(lctr_no), timeout)
    pairs = detail_pairs(soup)
    sections = detail_sections(soup)

    title_node = soup.select_one(".sub-tit .title, .view-title, .title")
    if title_node:
        row["title"] = normalize_space(title_node.get_text(" ", strip=True)) or row.get("title")

    row["period"] = normalize_period(pairs.get("교육기간") or row.get("period"))
    row["schedule_raw"] = normalize_space(pairs.get("교육시간") or row.get("schedule_raw"))
    row["target"] = normalize_space(pairs.get("교육대상") or row.get("target"))
    row["fee"] = normalize_fee(pairs.get("수강료") or row.get("fee"))
    row["instructor"] = normalize_space(pairs.get("강사명"))
    row["room"] = normalize_space(pairs.get("교육장소"))
    row["venue_name"] = row["room"]
    row["capacity_text"] = normalize_space(pairs.get("인원") or row.get("capacity_text"))
    row["material_note"] = normalize_space(pairs.get("준비물"))
    row["material_fee"] = extract_material_fee(row["material_note"])

    branch, address, phone = infer_branch(row, pairs)
    row["branch"] = branch
    row["branch_code"] = branch_code(branch)
    row["address"] = address
    row["venue_address"] = address
    row["phone"] = phone

    description_parts = []
    for key in ["강좌내용", "유의사항"]:
        if sections.get(key):
            description_parts.append(f"{key}: {sections[key]}")
    if row.get("material_note"):
        description_parts.append(f"준비물: {row['material_note']}")
    row["description"] = normalize_space(" ".join(description_parts))
    row["raw_fields"] = {"detail_pairs": pairs, "detail_sections": sections, "parser": "yuseong_lifelong_card_detail"}
    row["course_id"] = stable_course_id(row)
    return row


def parse_list(soup: BeautifulSoup) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for card in soup.select("a.inner-box.button_view[data-key-no]"):
        row = parse_list_card(card)
        if row:
            rows.append(row)
    return rows


def stable_course_id(row: dict[str, Any]) -> str:
    key = "|".join(
        [
            PROVIDER,
            normalize_space(row.get("external_id")),
            normalize_space(row.get("title")),
            normalize_space(row.get("period")),
        ]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def branch_code(name: str) -> str:
    text = normalize_space(name) or DEFAULT_BRANCH
    if "전민" in text:
        return "JEONMIN"
    if "구암" in text:
        return "GUAM"
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def is_expired_course(row: dict[str, Any]) -> bool:
    parsed = parse_date_range(row.get("period"))
    end_date = parsed[1] if parsed else None
    if not end_date:
        return False
    end_day = end_date.date() if hasattr(end_date, "date") else end_date
    return end_day < datetime.now().date()


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
        soup = fetch_soup(session, LIST_URL, timeout) if page == 1 else post_soup(session, LIST_URL, list_payload(page), timeout)
        page_rows = parse_list(soup)
        if not page_rows:
            break
        expired_on_page = 0
        for row in page_rows:
            key = normalize_space(row.get("external_id"))
            if key in seen:
                continue
            seen.add(key)
            try:
                row = enrich_detail(session, row, timeout)
            except Exception as exc:
                logger.warning("Yuseong detail failed %s: %s", row.get("raw_url"), exc)
            if not include_expired and is_expired_course(row):
                expired_on_page += 1
                logger.info("Skipping expired Yuseong course: %s / %s", row.get("title"), row.get("period"))
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
        "address": normalize_space(row.get("address")),
        "phone": normalize_space(row.get("phone")),
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
    parser = argparse.ArgumentParser(description="Yuseong lifelong learning center crawler")
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
