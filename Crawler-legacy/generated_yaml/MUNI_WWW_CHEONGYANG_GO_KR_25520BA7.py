from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag


PROVIDER = "MUNI_WWW_CHEONGYANG_GO_KR_25520BA7"
PROVIDER_NAME = "청양군 평생학습관"
BASE_URL = "https://www.cheongyang.go.kr"
LIST_PATH = "/prog/educate/lll/sub02_01/list.do"
REGION = "충청남도 청양군"
DEFAULT_BRANCH = "청양군 평생학습관"
DEFAULT_ADDRESS = "충청남도 청양군 청양읍 문화예술로 150"

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, setup_logger  # noqa: E402


logger = setup_logger("Crawler_CheongyangLifelong")


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


def normalize_period(value: Any) -> str:
    text = normalize_space(value)
    text = re.sub(r"(\d{4})[./-](\d{1,2})[./-](\d{1,2})", lambda m: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}", text)
    text = re.sub(r"\s*~\s*", " ~ ", text)
    return normalize_space(text)


def fetch_soup(session: requests.Session, url: str, timeout: int) -> BeautifulSoup:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def list_url(page: int) -> str:
    query = {
        "pageIndex": str(page),
        "pageUnit": "10",
        "pageSize": "10",
        "mno": "sub02_01",
        "siteCode": "lll",
        "reservYn": "",
    }
    return urljoin(BASE_URL, LIST_PATH) + "?" + urlencode(query)


def edu_no_from_url(url: str) -> str:
    query = parse_qs(urlparse(url).query)
    return normalize_space((query.get("eduNo") or [""])[0])


def infer_age_group(target: str) -> str:
    if re.search(r"유아|어린이|초등|아동", target):
        return "KIDS"
    if re.search(r"중학생|고등학생|청소년", target):
        return "TEEN"
    if re.search(r"성인|여성|어르신|남녀노소", target):
        return "ADULT"
    return ""


def infer_category(title: str, schedule: str, detail_category: str = "") -> str:
    text = f"{title} {schedule} {detail_category}"
    if re.search(r"파크골프|경락|댄스|운동|스포츠|건강", text):
        return "체육"
    if re.search(r"미술|어반스케치|오카리나|민화|드럼|문화|예술", text):
        return "문화예술"
    if re.search(r"논어|인문|교양|언어|외국어", text):
        return "인문교양"
    if re.search(r"자격증|지도자|강사|취업|창업", text):
        return "직업교육"
    return "평생교육"


def table_pairs(table: Tag | None) -> dict[str, str]:
    pairs: dict[str, str] = {}
    if not table:
        return pairs
    for row in table.select("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        index = 0
        while index < len(cells):
            key_node = cells[index]
            value_node = cells[index + 1] if index + 1 < len(cells) else None
            if key_node.name == "th" and value_node is not None:
                key = normalize_space(key_node.get_text(" ", strip=True))
                value = normalize_space(value_node.get_text(" ", strip=True))
                if key:
                    pairs[key] = value
                index += 2
            else:
                index += 1
    return pairs


def parse_list_rows(soup: BeautifulSoup, page: int) -> list[dict[str, Any]]:
    table = soup.select_one("table.basic_table.center")
    rows: list[dict[str, Any]] = []
    if not table:
        return rows
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td", recursive=False)
        if len(cells) < 7:
            continue
        title_link = cells[0].select_one("a[href*='view.do']")
        if not title_link:
            continue
        raw_url = urljoin(BASE_URL, title_link.get("href", ""))
        title = normalize_space(title_link.get_text(" ", strip=True))
        target = normalize_space(cells[1].get_text(" ", strip=True))
        apply_period = normalize_period(cells[2].get_text(" ", strip=True))
        period = normalize_period(cells[3].get_text(" ", strip=True))
        capacity = normalize_space(cells[4].get_text(" ", strip=True))
        schedule_raw = normalize_space(cells[5].get_text(" ", strip=True))
        status_node = cells[6].select_one(".typeA a") or cells[6].select_one("a")
        status = normalize_space(status_node.get_text(" ", strip=True) if status_node else cells[6].get_text(" ", strip=True))
        edu_no = edu_no_from_url(raw_url)
        rows.append(
            {
                "provider": PROVIDER,
                "provider_name": PROVIDER_NAME,
                "title": title,
                "branch": DEFAULT_BRANCH,
                "branch_code": DEFAULT_BRANCH,
                "address": DEFAULT_ADDRESS,
                "venue": "",
                "period": period,
                "schedule_raw": schedule_raw,
                "target": target,
                "age_group": infer_age_group(target),
                "fee": "",
                "status": status,
                "category": infer_category(title, schedule_raw),
                "collection_category": "평생학습",
                "domain_category": "평생학습",
                "raw_url": raw_url,
                "image_url": "",
                "description": "",
                "contact": "",
                "teacher": "",
                "raw_fields": {
                    "parser": "cheongyang_lifelong_table",
                    "edu_no": edu_no,
                    "application_period": apply_period,
                    "capacity": capacity,
                    "list_page": page,
                },
            }
        )
    return rows


def extract_image_url(soup: BeautifulSoup) -> str:
    for node in soup.select("a[title*='첨부파일 다운로드']"):
        text = normalize_space(node.get_text(" ", strip=True))
        if re.search(r"\.(jpg|jpeg|png|gif|webp)\b", text, re.I):
            onclick = node.get("href") or ""
            match = re.search(r"fn_egov_downFile\(['\"]([^'\"]+)['\"],['\"]([^'\"]+)['\"]", onclick)
            if match:
                return urljoin(BASE_URL, f"/cmm/fms/FileDown.do?atchFileId={match.group(1)}&fileSn={match.group(2)}")
    return ""


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    soup = fetch_soup(session, str(row["raw_url"]), timeout)
    pairs = table_pairs(soup.select_one("table.basic_table"))
    row = dict(row)
    title = normalize_space(pairs.get("강좌명"))
    period = normalize_period(pairs.get("교육기간"))
    schedule_raw = normalize_space(pairs.get("교육시간"))
    target = normalize_space(pairs.get("교육대상"))
    venue = normalize_space(pairs.get("교육장소"))
    institution = normalize_space(pairs.get("교육기관"))
    description = normalize_space(pairs.get("교육내용"))
    note = normalize_space(pairs.get("기타사항"))
    contact = normalize_space(pairs.get("문의전화"))
    teacher = normalize_space(pairs.get("담당자"))

    if title:
        row["title"] = title
    if period:
        row["period"] = period
    if schedule_raw:
        row["schedule_raw"] = schedule_raw
    if target:
        row["target"] = target
        row["age_group"] = infer_age_group(target)
    if venue:
        row["venue"] = venue
        row["branch"] = venue
        row["branch_code"] = venue
        row["address"] = f"{REGION} {venue}"
    elif institution:
        row["branch"] = institution
        row["branch_code"] = institution
    row["description"] = "\n".join([part for part in [description, note] if part])
    row["contact"] = contact
    row["teacher"] = teacher
    row["category"] = infer_category(row["title"], row.get("schedule_raw", ""), "")
    row["image_url"] = extract_image_url(soup)
    row.setdefault("raw_fields", {})["detail_pairs"] = pairs
    return row


def is_expired_course(row: dict[str, Any]) -> bool:
    _, end_date = parse_date_range(row.get("period"))
    if end_date is None:
        return False
    return end_date < datetime.now().date()


def stable_course_id(row: dict[str, Any]) -> str:
    raw_fields = row.get("raw_fields") if isinstance(row.get("raw_fields"), dict) else {}
    edu_no = normalize_space(raw_fields.get("edu_no") if raw_fields else "")
    if edu_no:
        return edu_no
    seed = "|".join([PROVIDER, normalize_space(row.get("title")), normalize_space(row.get("raw_url"))])
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def collect(limit: int | None = None, max_pages: int = 5, timeout: int = 20) -> list[dict[str, Any]]:
    session = make_session()
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for page in range(1, max_pages + 1):
        soup = fetch_soup(session, list_url(page), timeout)
        page_rows = parse_list_rows(soup, page)
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
                logger.warning("Cheongyang detail failed %s: %s", base_row.get("raw_url"), exc)
                row = base_row
            if is_expired_course(row):
                logger.info("Skipping expired Cheongyang course: %s / %s", row.get("title"), row.get("period"))
                continue
            rows.append(row)
            added += 1
            if limit is not None and len(rows) >= limit:
                return rows
        if added == 0:
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


def save_rows(rows: list[dict[str, Any]], mark_stale: bool = False) -> int:
    crawler = YamlSourceCrawler.__new__(YamlSourceCrawler)
    crawler.provider = PROVIDER
    crawler.target_parser = TargetParser()
    crawler.schedule_parser = ScheduleParser()
    saved = 0
    branch_ids: dict[str, str] = {}
    for row in rows:
        branch_name = normalize_space(row.get("branch")) or DEFAULT_BRANCH
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
    parser = argparse.ArgumentParser(description="Cheongyang lifelong learning crawler")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--mark-stale", action="store_true")
    parser.add_argument("--per-target-limit", type=int, default=None)
    parser.add_argument("--max-depth", type=int, default=None)
    parser.add_argument("--detail-limit", type=int, default=None)
    args = parser.parse_args()

    started = datetime.now()
    limit = args.limit if args.limit is not None else args.per_target_limit
    rows = collect(limit=limit, max_pages=args.max_pages, timeout=args.timeout)
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
