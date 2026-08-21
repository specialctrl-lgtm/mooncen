from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


PROVIDER = "HONAM_BIOLOGICAL_RESOURCES"
NAME = "국립호남권생물자원관"
BRANCH_CODE = "HONAM_BIOLOGICAL_RESOURCES_MAIN"
BRANCH_NAME = "국립호남권생물자원관"
BRANCH_ADDRESS = "전라남도 목포시 고하도안길 99"
LIST_URL = "https://resve.hnibr.re.kr/front/edu/eduFrontList.do"
DETAIL_URL = "https://resve.hnibr.re.kr/index.do?menu_id=00000440&menu_link=front/edu/eduFrontDetail.do&edu_id={edu_id}"

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def stable_id(*parts: str) -> str:
    raw = "|".join(clean_text(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:32]


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0 Safari/537.36",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    return s


def text_after_marker(text: str, marker: str) -> str:
    lines = [clean_text(line) for line in text.splitlines()]
    for index, line in enumerate(lines):
        if marker not in line:
            continue
        value = clean_text(line.split(marker, 1)[-1].lstrip("):：: "))
        if value:
            return value
        for next_line in lines[index + 1 : index + 4]:
            if next_line:
                return next_line
    return ""


def normalize_period(value: str) -> str:
    text = clean_text(value).replace("‘", "").replace("’", "")
    slash_dates = re.findall(r"(?:(\d{2,4})[./])?(\d{1,2})/(\d{1,2})", text)
    if slash_dates:
        year = datetime.now().year
        dates = []
        for year_text, month_text, day_text in slash_dates:
            current_year = int(year_text) if year_text else year
            if current_year < 100:
                current_year += 2000
            dates.append(f"{current_year:04d}-{int(month_text):02d}-{int(day_text):02d}")
        return " ~ ".join(dates) if len(dates) > 1 else dates[0]
    match = re.search(r"(\d{2,4})[.년]\s*(\d{1,2})[.월]\s*(\d{1,2})", text)
    if match:
        year = int(match.group(1))
        if year < 100:
            year += 2000
        return f"{year:04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    match = re.search(r"(\d{4})[-.](\d{1,2})[-.](\d{1,2})", text)
    if match:
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    return text


def parse_money_value(value: object) -> int | None:
    text = clean_text(value)
    if not text:
        return None
    if "\ubb34\ub8cc" in text or "臾대즺" in text:
        return 0
    match = re.search(r"\d[\d,]*", text)
    return int(match.group(0).replace(",", "")) if match else None


def date_range_from_period(value: object) -> tuple[str | None, str | None]:
    dates = re.findall(r"\d{4}-\d{2}-\d{2}", clean_text(value))
    if not dates:
        return None, None
    return dates[0], dates[-1]


def description_from_detail(text: str) -> str:
    source = clean_text(text)
    if "상세설명" in source:
        source = source.split("상세설명", 1)[-1]
    for stop in ("이용요금", "예약안내", "환불안내", "문의처", "장소안내"):
        if stop in source:
            source = source.split(stop, 1)[0]
    return clean_text(source)[:2000]


def db_status(value: str) -> str:
    text = clean_text(value)
    if any(token in text for token in ("접수중", "예약가능", "모집중")):
        return "OPEN"
    if any(token in text for token in ("준비중", "예정")):
        return "SCHEDULED"
    if any(token in text for token in ("대기")):
        return "WAITING"
    if any(token in text for token in ("마감", "종료")):
        return "CLOSED"
    return "SCHEDULED" if text else None


def labeled_value(node: BeautifulSoup, selector: str, label: str = "") -> str:
    target = node.select_one(selector)
    if not target:
        return ""
    value = clean_text(target.get_text(" ", strip=True))
    if label and value.startswith(label):
        value = clean_text(value[len(label) :].lstrip(":： "))
    return value


def collect(limit: int, max_pages: int, detail_limit: int, timeout: int) -> tuple[list[dict], dict]:
    s = session()
    rows: list[dict] = []
    detail_count = 0
    pages = 0
    for page in range(1, max_pages + 1):
        response = s.get(LIST_URL, params={"menu_id": "00000440", "pageIndex": page}, timeout=timeout)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        items = soup.select("ul.lst.type_card > li")
        if not items:
            break
        pages += 1
        for item in items:
            title = labeled_value(item, ".tit")
            if not title:
                continue
            onclick = clean_text((item.find("a") or {}).get("onclick"))
            match = re.search(r"fn_detail\('([^']+)'\)", onclick)
            edu_id = match.group(1) if match else stable_id(title)
            raw_url = DETAIL_URL.format(edu_id=edu_id) if match else LIST_URL
            image_node = item.find("img")
            image_url = urljoin(LIST_URL, image_node.get("src")) if image_node and image_node.get("src") else ""
            item_text = clean_text(item.get_text(" ", strip=True))
            method = labeled_value(item, ".n4", "접수방법")
            row = {
                "provider": PROVIDER,
                "provider_course_id": stable_id(PROVIDER, edu_id, title),
                "title": title,
                "branch": BRANCH_NAME,
                "branch_code": BRANCH_CODE,
                "address": BRANCH_ADDRESS,
                "raw_url": raw_url,
                "application_url": raw_url if "예약하기" in item_text or "온라인" in method else "",
                "application_type": "ONLINE_RESERVATION" if "온라인" in method or "예약하기" in item_text else "OFFLINE_APPLY",
                "reservation_available": "예약하기" in item_text,
                "status": labeled_value(item, ".type1"),
                "fee": labeled_value(item, ".type2"),
                "apply_period": labeled_value(item, ".n2", "접수기간"),
                "schedule_raw": labeled_value(item, ".n3", "교육시간"),
                "target": labeled_value(item, ".n1", "이용대상"),
                "application_method_raw": method,
                "image_url": image_url,
                "period": "",
                "description": "",
                "venue_name": "",
                "venue_address": BRANCH_ADDRESS,
                "collection_category": "기타",
                "domain_category": "생태/체험",
                "source_group": "arboretum_ecology",
                "operator_type": "국립/공공기관",
                "collection_type": "static_html",
                "program_type": "교육",
            }
            if match and detail_count < detail_limit:
                detail_response = s.get(raw_url, timeout=timeout)
                detail_response.raise_for_status()
                detail_soup = BeautifulSoup(detail_response.text, "html.parser")
                detail_text = detail_soup.get_text("\n", strip=True)
                detail_count += 1
                education_date = text_after_marker(detail_text, "(교육일)") or text_after_marker(detail_text, "교육일")
                venue = text_after_marker(detail_text, "(교육장소)") or text_after_marker(detail_text, "교육장소")
                row["period"] = normalize_period(education_date)
                row["venue_name"] = venue
                row["description"] = description_from_detail(detail_text)
            rows.append(row)
            if limit and len(rows) >= limit:
                return rows, {"pages": pages, "detail_pages": detail_count, "pagination_detected": True}
    return rows, {"pages": pages, "detail_pages": detail_count, "pagination_detected": pages > 1}


def save_db(rows: list[dict]) -> int:
    if not rows:
        return 0
    from DB.db_utils import get_db_cursor

    with get_db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO branches(provider, branch_code, name, address, website_url, address_source)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (provider, branch_code)
            DO UPDATE SET name = EXCLUDED.name, address = EXCLUDED.address, website_url = EXCLUDED.website_url, updated_at = now()
            RETURNING id
            """,
            (PROVIDER, BRANCH_CODE, BRANCH_NAME, BRANCH_ADDRESS, "https://www.hnibr.re.kr", "crawler_fixed"),
        )
        branch_id = cur.fetchone()["id"]
        saved = 0
        for row in rows:
            start_date, end_date = date_range_from_period(row.get("period"))
            cur.execute(
                """
                INSERT INTO courses(
                    provider, provider_course_id, branch_id, title, target, category_raw,
                    collection_category, domain_category, source_group, operator_type, collection_type,
                    fee, schedule_raw, start_date, end_date, apply_period_raw,
                    venue_name, venue_address, application_url, application_type, application_method_raw,
                    reservation_available, discovery_status, program_type, raw_fields,
                    status, raw_url, description, image_url, is_active, last_seen_at
                )
                VALUES (
                    %(provider)s, %(provider_course_id)s, %(branch_id)s, %(title)s, %(target)s, %(category_raw)s,
                    %(collection_category)s, %(domain_category)s, %(source_group)s, %(operator_type)s, %(collection_type)s,
                    %(fee)s, %(schedule_raw)s, %(start_date)s, %(end_date)s, %(apply_period_raw)s,
                    %(venue_name)s, %(venue_address)s, %(application_url)s, %(application_type)s, %(application_method_raw)s,
                    %(reservation_available)s, %(discovery_status)s, %(program_type)s, %(raw_fields)s,
                    %(status)s, %(raw_url)s, %(description)s, %(image_url)s, TRUE, now()
                )
                ON CONFLICT (provider, provider_course_id)
                DO UPDATE SET
                    branch_id = EXCLUDED.branch_id,
                    title = EXCLUDED.title,
                    target = EXCLUDED.target,
                    fee = EXCLUDED.fee,
                    schedule_raw = EXCLUDED.schedule_raw,
                    start_date = EXCLUDED.start_date,
                    end_date = EXCLUDED.end_date,
                    apply_period_raw = EXCLUDED.apply_period_raw,
                    venue_name = EXCLUDED.venue_name,
                    venue_address = EXCLUDED.venue_address,
                    application_url = EXCLUDED.application_url,
                    application_type = EXCLUDED.application_type,
                    application_method_raw = EXCLUDED.application_method_raw,
                    reservation_available = EXCLUDED.reservation_available,
                    raw_fields = EXCLUDED.raw_fields,
                    status = EXCLUDED.status,
                    raw_url = EXCLUDED.raw_url,
                    description = EXCLUDED.description,
                    image_url = EXCLUDED.image_url,
                    is_active = TRUE,
                    last_seen_at = now()
                """,
                {
                    **row,
                    "branch_id": branch_id,
                    "category_raw": row.get("domain_category"),
                    "fee": parse_money_value(row.get("fee")),
                    "start_date": start_date,
                    "end_date": end_date,
                    "apply_period_raw": row.get("apply_period"),
                    "discovery_status": "honam_bio_cards",
                    "raw_fields": json.dumps({"source": "honam_bio_cards", "period_raw": row.get("period"), "fee_raw": row.get("fee")}, ensure_ascii=False),
                    "status": db_status(row.get("status")),
                },
            )
            saved += 1
    return saved


def print_report(rows: list[dict], meta: dict, saved: int) -> None:
    fields = {
        "title": sum(1 for row in rows if row.get("title")),
        "branch": sum(1 for row in rows if row.get("branch")),
        "raw_url": sum(1 for row in rows if row.get("raw_url")),
        "address": sum(1 for row in rows if row.get("address")),
        "period": sum(1 for row in rows if row.get("period")),
        "schedule_raw": sum(1 for row in rows if row.get("schedule_raw")),
        "fee": sum(1 for row in rows if row.get("fee")),
        "status": sum(1 for row in rows if row.get("status")),
        "target": sum(1 for row in rows if row.get("target")),
        "description": sum(1 for row in rows if row.get("description")),
        "image_url": sum(1 for row in rows if row.get("image_url")),
        "application_url": sum(1 for row in rows if row.get("application_url")),
    }
    print(f"provider={PROVIDER} rows={len(rows)} saved={saved} parser=honam_bio_cards")
    print("field_counts " + " ".join(f"{key}={value}" for key, value in fields.items()))
    print("| provider | ok | rows | saved | pages | detail | parser | title | branch | address | period | schedule | fee | target | desc | image | apply |")
    print("| -------- | -- | ---- | ----- | ----- | ------ | ------ | ----- | ------ | ------- | ------ | -------- | --- | ------ | ---- | ----- | ----- |")
    print(
        f"| {PROVIDER} | Y | {len(rows)} | {saved} | {meta.get('pages', 0)} | {meta.get('detail_pages', 0)} | "
        f"honam_bio_cards | {fields['title']} | {fields['branch']} | {fields['address']} | {fields['period']} | "
        f"{fields['schedule_raw']} | {fields['fee']} | {fields['target']} | {fields['description']} | {fields['image_url']} | {fields['application_url']} |"
    )
    for row in rows[:3]:
        print(f"- {row['title']} / {row.get('period') or '-'} / {row.get('schedule_raw') or '-'} / {row.get('raw_url')}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--detail-limit", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--save-db", action="store_true")
    args = parser.parse_args()

    rows, meta = collect(args.limit, args.max_pages, args.detail_limit, args.timeout)
    saved = save_db(rows) if args.save_db else 0
    print_report(rows, meta, saved)
    return 0 if rows else 2


if __name__ == "__main__":
    raise SystemExit(main())

