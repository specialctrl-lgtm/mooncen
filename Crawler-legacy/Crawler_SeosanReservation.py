from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin, urlparse, parse_qs

import requests
import yaml
from bs4 import BeautifulSoup

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from Crawler.Crawler_MunicipalYaml import MunicipalDbWriter
from DB.course_lifecycle import mark_stale_courses, utc_now
from DB.db_utils import get_db_cursor
from utils import clean_text, setup_logger


PROVIDER = "SEOSAN_WELFARE_TOTAL_RESERVATION"
BASE_URL = "https://total.seosan.go.kr"
LIST_URLS = (
    f"{BASE_URL}/total/selectEdcAtrCourseListU.do?key=326",
    f"{BASE_URL}/total/selectEdcAtrCourseListU.do?key=326&searchInsttCode=02&cl1No=29",
    f"{BASE_URL}/total/selectEdcAtrCourseListU.do?key=327&searchInsttCode=02&cl1No=30",
    f"{BASE_URL}/total/selectEdcAtrCourseListU.do?key=186&searchInsttCode=01&cl1No=25",
    f"{BASE_URL}/total/selectEdcAtrCourseListU.do?key=187&searchInsttCode=01&cl1No=26",
    f"{BASE_URL}/total/selectEdcAtrCourseListU.do?key=248&searchInsttCode=04&cl1No=46",
    f"{BASE_URL}/total/selectEdcAtrCourseListU.do?key=249&searchInsttCode=04&cl1No=245",
    f"{BASE_URL}/total/selectEdcAtrCourseListU.do?key=246&searchInsttCode=05&cl1No=55",
    f"{BASE_URL}/total/selectEdcAtrCourseListU.do?key=406&searchInsttCode=06&cl1No=57",
)
REPORT_DIR = Path(__file__).resolve().parents[1] / "logs" / "crawler_reports"

logger = setup_logger(__name__, "logs/crawler_seosan_reservation.log")


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0 Safari/537.36",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    return s


def fetch_soup(s: requests.Session, url: str, timeout: int) -> BeautifulSoup:
    response = s.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = "utf-8"
    return BeautifulSoup(response.text, "lxml")


def provider_course_id(url: str) -> str:
    query = parse_qs(urlparse(url).query)
    course_no = (query.get("edcCourseNo") or [""])[0]
    key = (query.get("key") or [""])[0]
    return f"{key}:{course_no}" if course_no else hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]


def branch_code(value: str) -> str:
    digest = hashlib.sha1(clean_text(value).encode("utf-8")).hexdigest()[:10]
    return f"branch_{digest}"


def normalize_datetime_text(value: str) -> str:
    text = clean_text(value)
    text = text.replace("-", ".")
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_time(value: str) -> str:
    text = clean_text(value)
    text = re.sub(r"(\d{1,2})시\s*(\d{1,2})분", lambda m: f"{int(m.group(1)):02d}:{int(m.group(2)):02d}", text)
    text = text.replace(" ~ ", "-").replace("~", "-")
    text = text.replace("/ 공통", "").replace("/공통", "")
    return clean_text(text)


def normalize_phone(value: str) -> str:
    text = clean_text(value)
    match = re.search(r"0\d{1,2}[-)]?\d{3,4}[-]\d{4}|0\d{1,2}[-]\d{3,4}[-]\d{4}|1422-45", text)
    if match:
        return match.group(0).replace(")", "-")
    return ""


def build_description(row: dict[str, Any]) -> str:
    return clean_text(
        " ".join(
            str(part)
            for part in [
                row.get("title"),
                row.get("branch"),
                row.get("period"),
                row.get("schedule_raw"),
                row.get("apply_period"),
                row.get("target"),
                row.get("instructor"),
                row.get("fee"),
                row.get("material_fee"),
                row.get("room"),
            ]
            if clean_text(part)
        )
    )


def status_from_text(value: str) -> str:
    text = clean_text(value)
    if any(token in text for token in ("마감", "종료")):
        return "CLOSED"
    if "예정" in text:
        return "SCHEDULED"
    if any(token in text for token in ("접수중", "신청중")):
        return "OPEN"
    return text or "OPEN"


def pairs_from_detail_table(soup: BeautifulSoup) -> dict[str, str]:
    lines = [clean_text(line) for line in soup.get_text("\n", strip=True).splitlines() if clean_text(line)]
    labels = {
        "기관명",
        "강좌명",
        "기수",
        "접수기간",
        "접수방식",
        "모집인원",
        "선발방식",
        "대기인원",
        "강사명",
        "교육기간",
        "총교육일",
        "교육시간",
        "교육대상",
        "수강료",
        "재료비",
        "교육장소",
        "강의개요",
        "교재 및 참고자료",
        "수강신청 유의사항",
        "교육과정문의",
        "강의계획서",
    }
    pairs: dict[str, str] = {}
    for index, line in enumerate(lines):
        if line not in labels:
            continue
        collected: list[str] = []
        for next_line in lines[index + 1 :]:
            if next_line in labels:
                break
            collected.append(next_line)
        pairs[line] = clean_text(" ".join(collected))
    return pairs


def parse_place(value: str) -> tuple[str, str]:
    text = clean_text(value)
    match = re.search(r"(.+?)\(([^)]*(?:로|길|동|읍|면|서산시)[^)]*)\)", text)
    if match:
        return clean_text(match.group(1)), clean_text(match.group(2))
    return text, ""


def parse_detail(s: requests.Session, url: str, timeout: int) -> dict[str, Any]:
    soup = fetch_soup(s, url, timeout)
    pairs = pairs_from_detail_table(soup)
    place, address = parse_place(pairs.get("교육장소", ""))
    title = pairs.get("강좌명", "")
    if pairs.get("기수") and pairs["기수"] not in title:
        title = f"{title}({pairs['기수']})"
    period = normalize_datetime_text(pairs.get("교육기간", ""))
    schedule = normalize_time(" ".join(part for part in [pairs.get("교육시간", ""), pairs.get("총교육일", "")] if part))
    description = clean_text(" ".join(part for part in [pairs.get("강의개요"), pairs.get("교재 및 참고자료"), pairs.get("수강신청 유의사항")] if part))
    row = {
        "provider": PROVIDER,
        "provider_course_id": provider_course_id(url),
        "title": title,
        "branch": place or pairs.get("기관명", ""),
        "branch_code": branch_code(place or pairs.get("기관명", "")),
        "address": address,
        "category": "서산시 통합예약",
        "raw_url": url,
        "period": period,
        "apply_period": normalize_datetime_text(pairs.get("접수기간", "")),
        "schedule_raw": schedule,
        "target": pairs.get("교육대상", ""),
        "fee": pairs.get("수강료", ""),
        "material_fee": pairs.get("재료비", ""),
        "instructor": pairs.get("강사명", ""),
        "room": place,
        "phone": normalize_phone(pairs.get("교육과정문의", "")),
        "description": description,
    }
    if not row["description"]:
        row["description"] = build_description(row)
    return row


def parse_list_status(soup: BeautifulSoup) -> dict[str, str]:
    status_by_url: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        href = urljoin(BASE_URL, a["href"])
        if "selectEdcAtrCourseViewU.do" not in href:
            continue
        container = a.find_parent(["li", "tr", "div"]) or a
        text = clean_text(container.get_text(" ", strip=True))
        match = re.search(r"(접수중|접수예정|마감|종료)", text)
        if match:
            status_by_url[href] = status_from_text(match.group(1))
    return status_by_url


def detail_links_from_list(soup: BeautifulSoup, page_url: str) -> list[str]:
    links = []
    for a in soup.find_all("a", href=True):
        href = urljoin(page_url, a["href"])
        if "selectEdcAtrCourseViewU.do" in href:
            links.append(href)
    return list(dict.fromkeys(links))


class SeosanDbWriter(MunicipalDbWriter):
    def save_rows(self, rows: list[dict[str, Any]]) -> int:
        branch_ids: dict[str, str] = {}
        saved = 0
        for row in rows:
            code = clean_text(row.get("branch_code")) or branch_code(row.get("branch") or "SEOSAN")
            branch_id = branch_ids.get(code)
            if not branch_id:
                branch_id = self.save_branch_from_row(row)
                if not branch_id:
                    continue
                branch_ids[code] = branch_id
            course = self.normalize_course(row, branch_id)
            if self.save_course(course):
                saved += 1
        return saved

    def save_branch_from_row(self, row: dict[str, Any]) -> Optional[str]:
        data = {
            "provider": self.provider,
            "branch_code": clean_text(row.get("branch_code"))[:50],
            "name": clean_text(row.get("branch"))[:100],
            "address": clean_text(row.get("address")),
            "phone": clean_text(row.get("phone")),
            "website_url": BASE_URL,
            "address_source": "crawler" if row.get("address") else None,
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
                    address_source = COALESCE(EXCLUDED.address_source, branches.address_source),
                    updated_at = CURRENT_TIMESTAMP
                RETURNING id
                """,
                data,
            )
            return str(cursor.fetchone()["id"])


def collect(limit: Optional[int], timeout: int, max_pages: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    s = session()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    pages = 0
    detail_pages = 0
    for list_url in LIST_URLS:
        if pages >= max_pages or (limit is not None and len(rows) >= limit):
            break
        soup = fetch_soup(s, list_url, timeout)
        pages += 1
        statuses = parse_list_status(soup)
        for detail_url in detail_links_from_list(soup, list_url):
            if detail_url in seen:
                continue
            seen.add(detail_url)
            if limit is not None and len(rows) >= limit:
                break
            try:
                row = parse_detail(s, detail_url, timeout)
                row["status"] = statuses.get(detail_url, row.get("status") or "OPEN")
                rows.append({key: value for key, value in row.items() if value})
                detail_pages += 1
            except Exception as exc:
                logger.warning("Detail fetch failed %s: %s", detail_url, exc)
    return rows, {"pages": pages, "detail_pages": detail_pages, "pagination_detected": False}


def score_fields(rows: list[dict[str, Any]]) -> dict[str, int]:
    keys = ["title", "branch", "address", "period", "schedule_raw", "target", "fee", "status", "description", "image_url"]
    return {key: sum(1 for row in rows if row.get(key)) for key in keys}


def write_report(rows: list[dict[str, Any]], meta: dict[str, Any], saved: int, error: str = "") -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "provider": PROVIDER,
        "success": bool(rows) and not error,
        "collected": len(rows),
        "saved": saved,
        "error": error,
        "meta": meta,
        "fields": score_fields(rows),
        "samples": rows[:3],
        "notes": ["Most Seosan education detail pages do not expose representative images."],
    }
    path = REPORT_DIR / f"seosan_reservation_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
    return path


def print_quality(rows: list[dict[str, Any]], meta: dict[str, Any], saved: int, report_path: Path) -> None:
    fields = score_fields(rows)
    headers = ["provider", "rows", "saved", "pages", "detail", "pg", "title", "period", "schedule", "target", "fee", "desc", "image"]
    values = [
        PROVIDER,
        str(len(rows)),
        str(saved),
        str(meta.get("pages", 0)),
        str(meta.get("detail_pages", 0)),
        "Y" if meta.get("pagination_detected") else "N",
        str(fields["title"]),
        str(fields["period"]),
        str(fields["schedule_raw"]),
        str(fields["target"]),
        str(fields["fee"]),
        str(fields["description"]),
        str(fields["image_url"]),
    ]
    widths = [max(len(h), len(v)) for h, v in zip(headers, values)]
    fmt = lambda row: "| " + " | ".join(cell.ljust(width) for cell, width in zip(row, widths)) + " |"
    print(fmt(headers))
    print("| " + " | ".join("-" * width for width in widths) + " |")
    print(fmt(values))
    print(f"report={report_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Crawler for Seosan integrated reservation education courses")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--max-pages", type=int, default=8)
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--mark-stale", action="store_true")
    args = parser.parse_args()

    saved = 0
    error = ""
    try:
        rows, meta = collect(args.limit, args.timeout, args.max_pages)
        if args.save_db and rows:
            writer = SeosanDbWriter(PROVIDER)
            saved = writer.save_rows(rows)
            if args.mark_stale and saved > 0:
                mark_stale_courses(PROVIDER, utc_now())
    except Exception as exc:
        rows = []
        meta = {}
        error = f"{type(exc).__name__}: {exc}"
        logger.exception("Seosan reservation crawler failed")
    report_path = write_report(rows, meta, saved, error)
    print_quality(rows, meta, saved, report_path)
    return 0 if rows and not error else 1


if __name__ == "__main__":
    raise SystemExit(main())
