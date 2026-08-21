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


PROVIDER = "MUNI_EDU_YANGYANG_GO_KR_06A9551C"
PROVIDER_NAME = "양양군평생학습관"
BASE_URL = "https://edu.yangyang.go.kr"
LIST_PATH = "/lecture/class_list.php"
LIST_URL = f"{BASE_URL}{LIST_PATH}"
DEFAULT_BRANCH = "양양군평생학습관"
DEFAULT_ADDRESS = "강원특별자치도 양양군 양양읍 안산1길 36"
DEFAULT_PHONE = "033-670-2777"


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from DB.db_utils import get_db_cursor  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, setup_logger  # noqa: E402


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = setup_logger("Crawler_YangyangLifelong")


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


def normalize_period(value: Any) -> str:
    text = normalize_space(value)
    text = re.sub(r"\s*~\s*", " ~ ", text)
    return text


def fetch_soup(session: requests.Session, url: str, timeout: int) -> BeautifulSoup:
    response = session.get(url, timeout=timeout, verify=False)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def build_list_url(page: int, lco_type: str = "") -> str:
    query: dict[str, Any] = {}
    if page > 1:
        query["page"] = page
    if lco_type:
        query["lco_type"] = lco_type
    return LIST_URL if not query else f"{LIST_URL}?{urlencode(query)}"


def stable_id(*parts: Any) -> str:
    seed = "|".join(normalize_space(part) for part in parts if normalize_space(part))
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def branch_code(name: Any) -> str:
    return stable_id(PROVIDER, normalize_space(name) or DEFAULT_BRANCH)[:12]


def title_status(title_node: Tag | None) -> tuple[str, str]:
    if not title_node:
        return "", ""
    status_node = title_node.select_one("a")
    status = normalize_space(status_node.get_text(" ", strip=True)) if status_node else ""
    text = normalize_space(title_node.get_text(" ", strip=True))
    if status:
        text = normalize_space(re.sub(re.escape(status) + r"$", "", text))
    text = normalize_space(re.sub(r"^\d+\.\s*", "", text))
    return text, status


def field_pairs(card: Tag) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for label_node in card.select(".th_st"):
        value_node = label_node.find_next_sibling()
        if not value_node:
            continue
        key = normalize_space(label_node.get_text(" ", strip=True))
        value = normalize_space(value_node.get_text(" ", strip=True))
        if key:
            pairs[key] = value
    return pairs


def normalize_schedule(value: Any) -> str:
    text = normalize_space(value)
    text = text.replace("[", "").replace("]", "")
    text = text.replace("요일", "")
    text = re.sub(r"(\d{1,2})시\s*(\d{1,2})분", lambda m: f"{int(m.group(1)):02d}:{int(m.group(2)):02d}", text)
    text = re.sub(r"(\d{1,2})시", lambda m: f"{int(m.group(1)):02d}:00", text)
    return normalize_space(text)


def normalize_status(value: Any) -> str:
    text = normalize_space(value)
    if any(token in text for token in ["신청마감", "신청종료", "마감", "종료"]):
        return "CLOSED"
    if any(token in text for token in ["접수중", "신청중"]):
        return "OPEN"
    if any(token in text for token in ["예정", "대기"]):
        return "SCHEDULED"
    return text


def normalize_fee(value: Any) -> str:
    text = normalize_space(value)
    if not text:
        return ""
    if text in {"무료", "0", "0원"}:
        return "무료"
    amount = re.search(r"[\d,]+\s*원", text)
    return normalize_space(amount.group(0)) if amount else text


def infer_target(text: str) -> str:
    value = normalize_space(text)
    if any(token in value for token in ["어린이", "초등", "아동"]):
        return "아동"
    if any(token in value for token in ["청소년", "중등", "고등"]):
        return "청소년"
    if any(token in value for token in ["성인", "일반", "남녀노소"]):
        return "성인"
    return "성인"


def infer_category(text: str) -> str:
    value = normalize_space(text)
    if any(token in value for token in ["영어", "일본어", "중국어", "외국어"]):
        return "외국어"
    if any(token in value for token in ["요가", "줌바", "라인댄스", "건강", "다이어트"]):
        return "건강/체육"
    if any(token in value for token in ["요리", "브런치", "음료", "레시피"]):
        return "생활/요리"
    if any(token in value for token in ["미싱", "퀼트", "자수", "공예", "소품"]):
        return "공예"
    if any(token in value for token in ["코딩", "디지털", "AI", "컴퓨터"]):
        return "디지털"
    if any(token in value for token in ["인문", "나무", "철학", "역사"]):
        return "인문교양"
    return "평생교육"


def is_expired_course(row: dict[str, Any]) -> bool:
    _, end_date = parse_date_range(row.get("period"))
    return bool(end_date and end_date < datetime.now().date())


def parse_card(card: Tag, page: int, lco_type: str) -> dict[str, Any] | None:
    title, status_text = title_status(card.select_one(".title"))
    if not title:
        return None

    pairs = field_pairs(card)
    collapse = card.select_one(".collapse")
    external_id = normalize_space((collapse.get("id") if collapse else "") or stable_id(title, page, lco_type))
    raw_url = build_list_url(page, lco_type)
    if external_id:
        raw_url = f"{raw_url}#{external_id}"

    description_node = None
    for label_node in card.select(".th_st"):
        if normalize_space(label_node.get_text(" ", strip=True)) == "강의내용":
            description_node = label_node.find_next_sibling()
            break
    description = normalize_space(description_node.get_text(" ", strip=True) if description_node else "")
    images = [urljoin(BASE_URL, img.get("src", "")) for img in card.select("img[src]") if img.get("src")]
    image_url = images[0] if images else ""
    schedule = normalize_schedule(pairs.get("강의시간"))
    course_type = normalize_space(pairs.get("강의구분") or ("특별강좌" if lco_type else "일반강좌"))
    target = infer_target(f"{title} {course_type} {description}")
    row = {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": external_id,
        "provider_course_id": external_id,
        "title": title,
        "branch": DEFAULT_BRANCH,
        "branch_code": branch_code(DEFAULT_BRANCH),
        "address": DEFAULT_ADDRESS,
        "phone": DEFAULT_PHONE,
        "period": normalize_period(pairs.get("강의기간")),
        "schedule_raw": schedule,
        "target": target,
        "fee": normalize_fee(pairs.get("수강료")),
        "status": normalize_status(status_text),
        "description": description,
        "image_url": image_url,
        "raw_url": raw_url,
        "category": infer_category(f"{title} {course_type} {description}"),
        "course_type": course_type,
        "venue_name": DEFAULT_BRANCH,
        "venue_address": DEFAULT_ADDRESS,
        "room": normalize_space(pairs.get("강의장소")),
        "reception_period": normalize_period(pairs.get("접수기간")),
        "payment_period": normalize_period(pairs.get("납부기간")),
        "capacity_text": normalize_space(pairs.get("정원/신청인원")),
        "selection_method": normalize_space(pairs.get("선발기준")),
        "raw_fields": {
            "parser": "yangyang_lifelong_class_list",
            "page": page,
            "lco_type": lco_type,
            "pairs": pairs,
            "images": images,
        },
    }
    return row


def collect(
    limit: int | None = None,
    max_pages: int = 5,
    timeout: int = 20,
    include_expired: bool = False,
    include_special: bool = True,
) -> list[dict[str, Any]]:
    session = make_session()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    lco_types = ["", "1"] if include_special else [""]
    for lco_type in lco_types:
        for page in range(1, max_pages + 1):
            url = build_list_url(page, lco_type)
            soup = fetch_soup(session, url, timeout)
            cards = soup.select(".req_list")
            if not cards:
                break
            page_added = 0
            for card in cards:
                row = parse_card(card, page, lco_type)
                if not row:
                    continue
                key = row["provider_course_id"]
                if key in seen:
                    continue
                seen.add(key)
                if not include_expired and is_expired_course(row):
                    logger.info("Skipping expired Yangyang course: %s / %s", row.get("title"), row.get("period"))
                    continue
                rows.append(row)
                page_added += 1
                if limit and len(rows) >= limit:
                    return rows
            if page_added == 0 and page > 1:
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
                    normalize_space(row.get("period")),
                    normalize_space(row.get("schedule_raw")),
                    normalize_space(row.get("target")),
                    normalize_space(row.get("fee")),
                    normalize_space(row.get("status")),
                ]
            )
        )


def save_branch_with_address() -> str:
    branch = {
        "provider": PROVIDER,
        "branch_code": branch_code(DEFAULT_BRANCH),
        "name": DEFAULT_BRANCH,
        "address": DEFAULT_ADDRESS,
        "phone": DEFAULT_PHONE,
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
    branch_id = save_branch_with_address()
    saved = 0
    for row in rows:
        course = crawler.normalize_course(row, branch_id)
        crawler.save_course(course)
        saved += 1
    logger.info("%s saved %s/%s rows.", PROVIDER, saved, len(rows))
    return saved


def main() -> int:
    parser = argparse.ArgumentParser(description="Yangyang lifelong learning crawler")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--include-expired", action="store_true")
    parser.add_argument("--no-special", action="store_true")
    parser.add_argument("--mark-stale", action="store_true")
    parser.add_argument("--per-target-limit", type=int)
    parser.add_argument("--max-depth", type=int)
    parser.add_argument("--detail-limit", type=int)
    args = parser.parse_args()

    effective_limit = args.limit or args.per_target_limit
    started = datetime.now()
    rows = collect(
        limit=effective_limit,
        max_pages=args.max_pages,
        timeout=args.timeout,
        include_expired=args.include_expired,
        include_special=not args.no_special,
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
