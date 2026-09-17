from __future__ import annotations

import argparse
import logging
import os
import re
import ssl
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from DB.db_utils import get_db_cursor
from DB.course_lifecycle import enrich_course_lifecycle, mark_stale_courses, utc_now
from utils import clean_instructor_name, clean_text, infer_course_status, setup_logger
from title_cleaner import clean_course_title
from data_parser import TargetParser, parse_crawler_target

logger = setup_logger(__name__, "logs/crawler_yongin_lifelong.log")

PROVIDER = "YONGIN_LIFELONG_LEARNING"
BASE_URL = "https://lll.yongin.go.kr"
LIST_URL = "https://lll.yongin.go.kr/yongin/rgEdu/list.do"
VIEW_URL = "https://lll.yongin.go.kr/yongin/rgEdu/view.do?idx="

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def fetch_html(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, context=SSL_CTX, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def ensure_branch_id(cursor) -> str:
    cursor.execute("""
        INSERT INTO branches (provider, branch_code, name, address, region_sido, region_sigungu)
        VALUES ('YONGIN_LIFELONG_LEARNING', 'YONGIN_MAIN', '용인시평생학습관', '경기도 용인시 수지구 문정로7번길 23', '경기도', '용인시 수지구')
        ON CONFLICT (provider, branch_code)
        DO UPDATE SET name = EXCLUDED.name, address = EXCLUDED.address
        RETURNING id;
    """)
    row = cursor.fetchone()
    return str(row["id"])


def parse_card(sec, target_parser: TargetParser, branch_id: str) -> Optional[Dict[str, Any]]:
    # 1. idx 추출
    onclick_match = None
    title_a = sec.select_one(".tp a, a")
    if title_a:
        href = title_a.get("href", "")
        onclick = title_a.get("onclick", "")
        onclick_match = re.search(r"goView\s*\(\s*['\"]?(\d+)['\"]?\s*\)", href + " " + onclick)
    if not onclick_match:
        for a in sec.find_all("a"):
            onclick_match = re.search(r"goView\s*\(\s*['\"]?(\d+)['\"]?\s*\)", a.get("href", "") + " " + a.get("onclick", ""))
            if onclick_match:
                break
    if not onclick_match:
        return None

    idx = onclick_match.group(1)
    provider_course_id = f"YONGIN_{idx}"
    raw_url = f"{VIEW_URL}{idx}"

    # 2. 카테고리
    cate_elem = sec.select_one(".board-cate")
    category_raw = clean_text(cate_elem.get_text()) if cate_elem else None
    if category_raw and category_raw.startswith("[") and category_raw.endswith("]"):
        category_raw = category_raw[1:-1].strip()

    # 3. 상태
    status_elem = sec.select_one(".ed-con")
    status_text = clean_text(status_elem.get_text()) if status_elem else ""
    status = infer_course_status(status_text)

    # 4. 강좌명
    tp_elem = sec.select_one(".tp")
    if tp_elem:
        # Clone to remove category and status
        tp_text = clean_text(tp_elem.get_text())
        if cate_elem:
            tp_text = tp_text.replace(cate_elem.get_text(), "")
        if status_elem:
            tp_text = tp_text.replace(status_elem.get_text(), "")
        raw_title = clean_text(tp_text)
    else:
        raw_title = clean_text(title_a.get_text()) if title_a else ""

    if not raw_title:
        return None

    clean_title, removed_prefix = clean_course_title(raw_title)

    # 5. 교육기간, 수강일, 시간, 강사명
    start_date = None
    end_date = None
    schedule_days = []
    time_start = None
    time_end = None
    instructor = None

    text_all = clean_text(sec.get_text(" ", strip=True))

    period_match = re.search(r"교육기간\s*:\s*(\d{4}[.\-]\d{2}[.\-]\d{2})\s*~\s*(\d{4}[.\-]\d{2}[.\-]\d{2})", text_all)
    if period_match:
        start_date = period_match.group(1).replace(".", "-")
        end_date = period_match.group(2).replace(".", "-")

    days_match = re.search(r"수강일\s*:\s*([월화수목금토일,\s]+)", text_all)
    if days_match:
        found_days = re.findall(r"[월화수목금토일]", days_match.group(1))
        schedule_days = sorted(list(set(found_days)))

    time_match = re.search(r"시간\s*:\s*(\d{1,2}:\d{2})\s*~\s*(\d{1,2}:\d{2})", text_all)
    if time_match:
        time_start = time_match.group(1)
        time_end = time_match.group(2)

    inst_match = re.search(r"강사명\s*:\s*([^\s<]+)", text_all)
    if inst_match:
        instructor = clean_instructor_name(inst_match.group(1))

    schedule_raw = " ".join(filter(None, [
        f"수강일: {','.join(schedule_days)}" if schedule_days else "",
        f"시간: {time_start}~{time_end}" if time_start and time_end else ""
    ])).strip() or "상세참조"

    # 6. 대상 연령 파싱
    parsed_target = parse_crawler_target(raw_title, target_parser)
    target_age_group = parsed_target.get("age_group") or "ADULT"
    target_min_age = parsed_target.get("min_age") or (240 if target_age_group == "ADULT" else None)
    target_max_age = parsed_target.get("max_age") or (719 if target_age_group == "ADULT" else None)

    return {
        "branch_id": branch_id,
        "provider": PROVIDER,
        "provider_course_id": provider_course_id,
        "title": clean_title,
        "title_raw": raw_title,
        "title_prefix_removed": removed_prefix or None,
        "instructor": instructor,
        "category_raw": category_raw or "정기교육",
        "fee": 0,
        "material_fee": 0,
        "sessions": None,
        "schedule_raw": schedule_raw,
        "schedule_days": schedule_days or None,
        "schedule_time_start": time_start,
        "schedule_time_end": time_end,
        "start_date": start_date,
        "end_date": end_date,
        "apply_start": None,
        "apply_end": None,
        "apply_period_raw": None,
        "status": status,
        "application_url": raw_url,
        "raw_url": raw_url,
        "description": f"{raw_title} (용인시평생학습관 정기교육)",
        "image_url": None,
        "venue_name": "용인시평생학습관",
        "collection_category": "평생학습",
        "domain_category": "평생학습",
        "source_group": "lifelong_learning",
        "operator_type": "지자체/공공기관",
        "service_group": "공공강좌",
        "collection_type": "static_html",
        "target": "성인" if target_age_group == "ADULT" else (parsed_target.get("target") or "일반"),
        "target_age_group": target_age_group,
        "target_min_age": target_min_age,
        "target_max_age": target_max_age,
        "target_with_parent": parsed_target.get("with_parent", False),
        "target_tags": parsed_target.get("tags", []),
        "target_age_is_explicit": parsed_target.get("age_is_explicit", False),
    }


def save_courses(courses: List[Dict[str, Any]], cursor) -> int:
    saved = 0
    for course in courses:
        enrich_course_lifecycle(course)
        cursor.execute("""
            INSERT INTO courses (
                branch_id, provider, provider_course_id, title, title_raw, title_prefix_removed,
                instructor, target, category_raw, fee, material_fee, sessions, schedule_raw,
                start_date, end_date, apply_start, apply_end, apply_period_raw, status,
                application_url, raw_url, description, image_url, venue_name,
                collection_category, domain_category, source_group, operator_type, service_group,
                collection_type, is_active, first_seen_at, last_seen_at, removed_at,
                target_age_group, target_min_age, target_max_age, target_with_parent, target_tags,
                target_age_is_explicit, schedule_days, schedule_time_start, schedule_time_end
            )
            VALUES (
                %(branch_id)s, %(provider)s, %(provider_course_id)s, %(title)s, %(title_raw)s, %(title_prefix_removed)s,
                %(instructor)s, %(target)s, %(category_raw)s, %(fee)s, %(material_fee)s, %(sessions)s, %(schedule_raw)s,
                %(start_date)s, %(end_date)s, %(apply_start)s, %(apply_end)s, %(apply_period_raw)s, %(status)s,
                %(application_url)s, %(raw_url)s, %(description)s, %(image_url)s, %(venue_name)s,
                %(collection_category)s, %(domain_category)s, %(source_group)s, %(operator_type)s, %(service_group)s,
                %(collection_type)s, TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL,
                %(target_age_group)s, %(target_min_age)s, %(target_max_age)s, %(target_with_parent)s, %(target_tags)s,
                %(target_age_is_explicit)s, %(schedule_days)s, %(schedule_time_start)s, %(schedule_time_end)s
            )
            ON CONFLICT (provider, provider_course_id)
            DO UPDATE SET
                title = EXCLUDED.title,
                instructor = EXCLUDED.instructor,
                category_raw = EXCLUDED.category_raw,
                schedule_raw = EXCLUDED.schedule_raw,
                schedule_days = EXCLUDED.schedule_days,
                schedule_time_start = EXCLUDED.schedule_time_start,
                schedule_time_end = EXCLUDED.schedule_time_end,
                start_date = EXCLUDED.start_date,
                end_date = EXCLUDED.end_date,
                status = EXCLUDED.status,
                target = EXCLUDED.target,
                target_age_group = EXCLUDED.target_age_group,
                target_min_age = EXCLUDED.target_min_age,
                target_max_age = EXCLUDED.target_max_age,
                is_active = TRUE,
                last_seen_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            RETURNING id;
        """, course)
        saved += 1
    return saved


def run(save_db: bool = False, max_pages: int = 10, limit: Optional[int] = None) -> int:
    crawl_started_at = utc_now()
    target_parser = TargetParser()
    all_courses: List[Dict[str, Any]] = []

    logger.info("Starting Yongin lifelong learning crawler...")
    branch_id = None
    if save_db:
        with get_db_cursor() as cursor:
            branch_id = ensure_branch_id(cursor)

    for page in range(1, max_pages + 1):
        url = f"{LIST_URL}?pageIndex={page}"
        logger.info("Fetching page %d: %s", page, url)
        try:
            html = fetch_html(url)
            soup = BeautifulSoup(html, "html.parser")
            sections = soup.select(".list-board .board-section")
            if not sections:
                logger.info("Page %d has no board-sections. Stopping pagination.", page)
                break

            for sec in sections:
                course = parse_card(sec, target_parser, branch_id or "dummy_branch")
                if course:
                    all_courses.append(course)
                    if limit and len(all_courses) >= limit:
                        break

            if limit and len(all_courses) >= limit:
                break
            time.sleep(0.5)
        except Exception as exc:
            logger.error("Error crawling Yongin page %d: %s", page, exc)
            break

    print(f"provider=YONGIN_LIFELONG_LEARNING collected={len(all_courses)}")
    if save_db and all_courses:
        with get_db_cursor() as cursor:
            saved_cnt = save_courses(all_courses, cursor)
            stale_cnt = mark_stale_courses(PROVIDER, crawl_started_at)
            print(f"provider=YONGIN_LIFELONG_LEARNING saved={saved_cnt} stale_marked={stale_cnt}")
            return 0 if saved_cnt > 0 else 1
    elif all_courses:
        # dry-run success
        print(f"Dry-run sample: {all_courses[0]['title']} | {all_courses[0]['schedule_days']} | {all_courses[0]['instructor']}")
        return 0

    return 1 if not all_courses else 0


def main():
    parser = argparse.ArgumentParser(description="Yongin Lifelong Learning Crawler")
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_known_args()[0]

    sys.exit(run(save_db=args.save_db, max_pages=args.max_pages, limit=args.limit))


if __name__ == "__main__":
    main()
