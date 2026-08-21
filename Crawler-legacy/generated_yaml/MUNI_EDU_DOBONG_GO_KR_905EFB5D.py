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


PROVIDER = "MUNI_EDU_DOBONG_GO_KR_905EFB5D"
PROVIDER_NAME = "도봉구 교육포털 도봉배움e"
BASE_URL = "https://edu.dobong.go.kr"
LIST_PATH = "/Course_Lifelong/lecture_A_Lst.asp"
DETAIL_PATH = "/Course_Lifelong/lecture_Vw.asp"
COURSE_CODE = "10007718"
COURSE_GUBUN = "A"
COURSE_GNB = "GnbTp1"
BRANCH_NAME = "도봉구 평생학습관"
BRANCH_ADDRESS = "서울특별시 도봉구 시루봉로 128"
MAIN_ADDRESS = "서울특별시 도봉구 마들로 656"

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, setup_logger  # noqa: E402


logger = setup_logger("Crawler_DobongEdu")


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
            "Referer": urljoin(BASE_URL, LIST_PATH),
        }
    )
    return session


def normalize_space(value: Any) -> str:
    text = clean_text(value).replace("\xa0", " ")
    return clean_text(re.sub(r"\s+", " ", text))


def normalize_date_range(value: Any) -> str:
    text = normalize_space(value)
    text = re.sub(r"(?<!\d)(\d{2})\.(\d{2})\.(\d{2})\.?", r"20\1-\2-\3", text)
    text = re.sub(r"(\d{4})\.(\d{1,2})\.(\d{1,2})\.?", lambda m: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}", text)
    text = re.sub(r"\s*[~]\s*", " ~ ", text)
    return normalize_space(text)


def fetch_soup(session: requests.Session, url: str, timeout: int) -> BeautifulSoup:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def list_url(page: int, page_size: int = 100) -> str:
    query = {
        "code": COURSE_CODE,
        "Gubun": COURSE_GUBUN,
        "MCode": "",
        "Gnb": COURSE_GNB,
        "Cate1": "",
        "PageSize": str(page_size),
        "Page": str(page),
        "sltSearchType": "",
        "sltPeriodSt": "2026-01-01",
        "sltPeriodEn": "2026-12-31",
        "txtSearchText": "",
        "Idx": "",
        "Backpage": "",
    }
    return urljoin(BASE_URL, LIST_PATH) + "?" + urlencode(query)


def detail_url(course_id: str, page: int = 1) -> str:
    query = {
        "code": COURSE_CODE,
        "Gubun": COURSE_GUBUN,
        "Idx": course_id,
        "MCode": "",
        "Gnb": COURSE_GNB,
        "Cate1": "",
        "Page": str(page),
        "Backpage": LIST_PATH,
    }
    return urljoin(BASE_URL, DETAIL_PATH) + "?" + urlencode(query)


def course_id_from_onclick(value: str | None) -> str:
    match = re.search(r"goDesc\(['\"]?([^,'\"\)]+)", value or "")
    return match.group(1).strip() if match else ""


def direct_card_text(card: Tag, selector: str) -> str:
    node = card.select_one(selector)
    return normalize_space(node.get_text(" ", strip=True) if node else "")


def extract_info_list(card: Tag) -> dict[str, str]:
    info: dict[str, str] = {}
    for item in card.select(".info_list li"):
        raw = normalize_space(item.get_text(" ", strip=True))
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        info[normalize_space(key)] = normalize_space(value)
    return info


def extract_tags(card: Tag) -> list[str]:
    tags: list[str] = []
    for node in card.select(".has_tag span"):
        text = normalize_space(node.get_text(" ", strip=True)).lstrip("#")
        if text and text not in tags:
            tags.append(text)
    return tags


def infer_category(tags: list[str], title: str) -> str:
    text = " ".join([title, *tags])
    if re.search(r"체육|건강|스포츠|요가|필라테스", text):
        return "체육"
    if re.search(r"미술|음악|예술|공예|사진|드라마|영화|뮤지컬", text):
        return "문화예술"
    if re.search(r"AI|컴퓨터|스마트폰|정보화|코딩", text, re.I):
        return "디지털"
    if re.search(r"영어|외국어|문해|인문|교양|재테크", text):
        return "평생교육"
    return "평생교육"


def infer_age_group(target: str, title: str) -> str:
    text = f"{target} {title}"
    if re.search(r"영유아|유아|어린이|초등|아동", text):
        return "KIDS"
    if re.search(r"청소년|중등|고등", text):
        return "TEEN"
    if re.search(r"성인|신중년|구민|학부모", text):
        return "ADULT"
    return ""


def parse_list_cards(soup: BeautifulSoup, page: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for title_node in soup.select("a.tit[onclick*='goDesc']"):
        course_id = course_id_from_onclick(title_node.get("onclick"))
        title = normalize_space(title_node.get_text(" ", strip=True))
        if not course_id or course_id in seen or not title:
            continue
        seen.add(course_id)
        card = title_node.find_parent("li", class_="course_box")
        if not isinstance(card, Tag):
            continue

        info = extract_info_list(card)
        tags = extract_tags(card)
        raw_url = detail_url(course_id, page)
        image_node = card.select_one(".img_area img")
        image_url = urljoin(BASE_URL, image_node.get("src")) if image_node and image_node.get("src") else ""
        period = normalize_date_range(info.get("교육기간"))
        schedule_raw = normalize_space(info.get("교육기간"))
        venue = normalize_space(info.get("교육장소"))
        target = normalize_space(info.get("교육대상"))
        fee = normalize_space(direct_card_text(card, ".price"))
        status = normalize_space(direct_card_text(card, ".state .btn") or direct_card_text(card, ".btn_area .btn_blue"))
        category = infer_category(tags, title)
        age_group = infer_age_group(target, title)

        rows.append(
            {
                "provider": PROVIDER,
                "provider_name": PROVIDER_NAME,
                "title": title,
                "branch": BRANCH_NAME,
                "branch_code": BRANCH_NAME,
                "address": BRANCH_ADDRESS if venue else MAIN_ADDRESS,
                "venue": venue,
                "period": period,
                "schedule_raw": schedule_raw,
                "target": target,
                "age_group": age_group,
                "fee": fee,
                "status": status,
                "category": category,
                "collection_category": "평생학습",
                "domain_category": "평생학습",
                "raw_url": raw_url,
                "image_url": image_url,
                "description": "",
                "material_fee": "",
                "material_note": "",
                "contact": "",
                "raw_fields": {
                    "parser": "dobong_edu_course_card",
                    "course_id": course_id,
                    "tags": tags,
                    "application_period": normalize_date_range(info.get("신청기간")),
                    "list_page": page,
                },
            }
        )
    return rows


def extract_detail_pairs(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for dl in soup.select(".view dl"):
        key_node = dl.select_one("dt")
        value_node = dl.select_one("dd")
        key = normalize_space(key_node.get_text(" ", strip=True) if key_node else "").rstrip(":").strip()
        value = normalize_space(value_node.get_text(" ", strip=True) if value_node else "")
        if key:
            pairs[key] = value
    return pairs


def extract_description(soup: BeautifulSoup) -> str:
    container = soup.select_one(".bbsList.write")
    if not container:
        return ""
    clone = BeautifulSoup(str(container), "html.parser")
    for node in clone.select(".head_tit_are, .view, .btn_area, form, script, style"):
        node.decompose()
    text = clone.get_text("\n", strip=True)
    lines = [normalize_space(line) for line in text.splitlines()]
    lines = [line for line in lines if line and line not in {"상세보기", "목록"}]
    return "\n".join(lines)


def material_note_from(description: str, pairs: dict[str, str]) -> str:
    notes: list[str] = []
    if pairs.get("재료비"):
        notes.append(f"재료비: {pairs['재료비']}")
    for line in description.splitlines():
        if re.search(r"준비물|재료|재료비|교재", line):
            notes.append(line)
    deduped: list[str] = []
    for note in notes:
        note = normalize_space(note)
        if note and note not in deduped:
            deduped.append(note)
    return "\n".join(deduped)


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    soup = fetch_soup(session, str(row["raw_url"]), timeout)
    pairs = extract_detail_pairs(soup)
    title_node = soup.select_one(".head_tit_are .tit")
    status_node = soup.select_one(".head_btn_area .btn")
    description = extract_description(soup)

    row = dict(row)
    if title_node:
        row["title"] = normalize_space(title_node.get_text(" ", strip=True)) or row["title"]
    row["period"] = normalize_date_range(pairs.get("교육기간") or row.get("period"))
    row["schedule_raw"] = normalize_space(pairs.get("강의시간") or row.get("schedule_raw"))
    row["target"] = normalize_space(pairs.get("교육대상") or row.get("target"))
    row["fee"] = normalize_space(pairs.get("수강료") or row.get("fee"))
    row["status"] = normalize_space(status_node.get_text(" ", strip=True) if status_node else pairs.get("수강인원") or row.get("status"))
    row["category"] = infer_category([pairs.get("분야", "")], row["title"])
    row["age_group"] = infer_age_group(row.get("target", ""), row["title"])
    row["description"] = description
    row["material_fee"] = normalize_space(pairs.get("재료비"))
    row["material_note"] = material_note_from(description, pairs)
    row["contact"] = normalize_space(pairs.get("문의"))
    place = normalize_space(pairs.get("학교(장소)") or row.get("venue"))
    if place:
        row["venue"] = place
        address_match = re.search(r"\((서울[^)]*|경기[^)]*|[^)]*로\s*\d+[^)]*)\)", f"{place}\n{description}")
        if address_match:
            row["address"] = normalize_space(address_match.group(1))
    row.setdefault("raw_fields", {})["detail_pairs"] = pairs
    return row


def is_expired_course(row: dict[str, Any]) -> bool:
    _, end_date = parse_date_range(row.get("period"))
    if end_date is None:
        return False
    return end_date < datetime.now().date()


def stable_course_id(row: dict[str, Any]) -> str:
    raw_fields = row.get("raw_fields") if isinstance(row.get("raw_fields"), dict) else {}
    course_id = normalize_space(raw_fields.get("course_id") if raw_fields else "")
    if course_id:
        return course_id
    seed = "|".join([PROVIDER, normalize_space(row.get("title")), normalize_space(row.get("raw_url"))])
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def collect(limit: int | None = None, max_pages: int = 5, timeout: int = 20) -> list[dict[str, Any]]:
    session = make_session()
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for page in range(1, max_pages + 1):
        soup = fetch_soup(session, list_url(page), timeout)
        page_rows = parse_list_cards(soup, page)
        if not page_rows:
            break
        added = 0
        for base_row in page_rows:
            course_id = stable_course_id(base_row)
            if course_id in seen_ids:
                continue
            seen_ids.add(course_id)
            try:
                row = enrich_detail(session, base_row, timeout)
            except Exception as exc:
                logger.warning("Dobong detail failed %s: %s", base_row.get("raw_url"), exc)
                row = base_row
            if is_expired_course(row):
                logger.info("Skipping expired Dobong course: %s / %s", row.get("title"), row.get("period"))
                continue
            rows.append(row)
            added += 1
            if limit is not None and len(rows) >= limit:
                return rows
        if added == 0:
            break
    return rows


def quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fields = [
        "title",
        "branch",
        "raw_url",
        "address",
        "period",
        "schedule_raw",
        "target",
        "fee",
        "status",
        "description",
        "image_url",
    ]
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


def save_rows(rows: list[dict[str, Any]], mark_stale: bool = False) -> int:
    crawler = YamlSourceCrawler.__new__(YamlSourceCrawler)
    crawler.provider = PROVIDER
    crawler.target_parser = TargetParser()
    crawler.schedule_parser = ScheduleParser()
    saved = 0
    branch_ids: dict[str, str] = {}
    for row in rows:
        branch_name = normalize_space(row.get("branch")) or BRANCH_NAME
        branch_code = normalize_space(row.get("branch_code")) or branch_name
        branch_id = branch_ids.get(branch_code)
        if not branch_id:
            branch_id = crawler.save_branch(branch_code, branch_name)
            branch_ids[branch_code] = branch_id
        course = crawler.normalize_course(row, branch_id)
        if crawler.save_course(course):
            saved += 1
    if mark_stale and saved > 0:
        from DB.course_lifecycle import mark_stale_courses, utc_now

        mark_stale_courses(PROVIDER, utc_now())
    return saved


def main() -> int:
    parser = argparse.ArgumentParser(description="Dobong lifelong learning crawler")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--mark-stale", action="store_true")
    parser.add_argument("--per-target-limit", type=int)
    parser.add_argument("--max-depth", type=int)
    parser.add_argument("--detail-limit", type=int)
    args = parser.parse_args()

    started = datetime.now()
    rows = collect(limit=args.limit or args.per_target_limit, max_pages=args.max_pages, timeout=args.timeout)
    print_quality(rows)
    saved = 0
    if args.save_db:
        saved = save_rows(rows, mark_stale=args.mark_stale)
        logger.info("%s saved %s/%s rows.", PROVIDER, saved, len(rows))
    elapsed = (datetime.now() - started).total_seconds()
    logger.info("%s completed collected=%s saved=%s elapsed=%.1fs", PROVIDER, len(rows), saved, elapsed)
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
