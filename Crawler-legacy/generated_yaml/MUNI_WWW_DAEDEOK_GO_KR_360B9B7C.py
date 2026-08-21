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


PROVIDER = "MUNI_WWW_DAEDEOK_GO_KR_360B9B7C"
PROVIDER_NAME = "대덕구 평생학습관"
BASE_URL = "https://edu.daedeok.go.kr"
PATH = "/damoa/contents/dms/edu/02/edu.02.001.motion"
LIST_URL = f"{BASE_URL}{PATH}?mnucd=MENU0100010"
DEFAULT_BRANCH = "대덕구 평생학습관"
DEFAULT_ADDRESS = "대전광역시 대덕구 대덕대로 1579, 3층"

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, setup_logger  # noqa: E402


logger = setup_logger("Crawler_DaedeokResidentAutonomy")


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
        r"(?<!\d)(\d{2})[.](\d{1,2})[.](\d{1,2})",
        lambda m: f"20{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}",
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


def post_soup(session: requests.Session, data: dict[str, str], timeout: int) -> BeautifulSoup:
    response = session.post(f"{BASE_URL}{PATH}", data=data, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def row_cells(tr: Tag) -> dict[str, str]:
    values: dict[str, str] = {}
    for td in tr.select("td"):
        key = normalize_space(td.select_one(".add-head").get_text(" ", strip=True) if td.select_one(".add-head") else "")
        value_node = td.select_one(".tds") or td
        value = normalize_space(value_node.get_text(" ", strip=True))
        if key:
            values[key] = value
    if not values:
        headers = ["번호", "기관명", "프로그램명", "모집기간", "운영기간", "요일/시간", "인원", "대상", "수강료", "진행"]
        cells = [normalize_space(td.get_text(" ", strip=True)) for td in tr.select("td")]
        values = {key: cells[idx] for idx, key in enumerate(headers) if idx < len(cells)}
    return values


def parse_onclick(value: str) -> tuple[str, str, str, str]:
    args = re.findall(r"'([^']*)'", value)
    if len(args) >= 4:
        return args[-4], args[-3], args[-2], args[-1]
    match = re.search(r"fn_egov_select1\([^,]+,\s*'([^']+)',\s*'([^']+)',\s*'([^']+)',\s*'([^']+)'\)", value)
    if not match:
        return "", "", "", ""
    return match.group(1), match.group(2), match.group(3), match.group(4)


def stable_course_id(row: dict[str, Any]) -> str:
    key = "|".join(
        [
            PROVIDER,
            normalize_space(row.get("external_id")),
            normalize_space(row.get("ord_cd")),
            normalize_space(row.get("title")),
            normalize_space(row.get("period")),
        ]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def parse_list_row(tr: Tag) -> dict[str, Any] | None:
    values = row_cells(tr)
    link = tr.select_one("a[onclick*='fn_egov_select1']")
    onclick = (link.get("onclick", "") if link else "") or tr.get("onclick", "")
    if not onclick:
        return None
    lec_id, ord_cd, ord_sido_cd, ord_local_cd = parse_onclick(onclick)
    title = normalize_space(values.get("프로그램명"))
    if not title or not lec_id:
        return None
    branch = normalize_space(values.get("기관명")) or DEFAULT_BRANCH
    row = {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": lec_id,
        "ord_cd": ord_cd,
        "ord_sido_cd": ord_sido_cd,
        "ord_local_cd": ord_local_cd,
        "course_id": "",
        "title": title,
        "branch": branch,
        "branch_code": hashlib.sha1(branch.encode("utf-8")).hexdigest()[:12],
        "address": DEFAULT_ADDRESS,
        "period": normalize_period(values.get("운영기간")),
        "schedule_raw": normalize_space(values.get("요일/시간")),
        "target": normalize_space(values.get("대상")),
        "fee": normalize_space(values.get("수강료")),
        "status": normalize_space(values.get("진행")),
        "description": "",
        "image_url": "",
        "raw_url": f"{BASE_URL}{PATH}?{urlencode({'mnucd': 'MENU0100010', 'bmode': 'detail1', 'lecId': lec_id, 'ordCd': ord_cd, 'ordSidoCd': ord_sido_cd, 'ordLocalCd': ord_local_cd})}",
        "category": "평생학습관 프로그램",
        "collection_category": "평생학습",
        "domain_category": "평생학습",
        "operator_type": "지자체/공공기관",
        "reception_period": normalize_period(values.get("모집기간")),
        "capacity_text": normalize_space(values.get("인원")),
    }
    row["course_id"] = stable_course_id(row)
    return row


def parse_list(soup: BeautifulSoup) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    table = soup.select_one("table.simple") or soup.select_one("table.table")
    if not table:
        return rows
    for tr in table.select("tbody tr"):
        row = parse_list_row(tr)
        if row:
            rows.append(row)
    return rows


def detail_payload(row: dict[str, Any], page: int = 1) -> dict[str, str]:
    return {
        "mnucd": "MENU0100010",
        "searchLecDivArray": "",
        "bmode": "detail1",
        "pageIndex": str(page),
        "lecId": normalize_space(row.get("external_id")),
        "ordCd": normalize_space(row.get("ord_cd")),
        "ordSidoCd": normalize_space(row.get("ord_sido_cd")),
        "ordLocalCd": normalize_space(row.get("ord_local_cd")),
        "searchCondition": "1",
        "searchLecDivLvl1": "",
        "searchLecDivLvl2": "",
        "searchLecTarget": "",
        "searchIspaidArray": "2",
        "searchKeyword": "",
    }


def detail_pairs(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for li in soup.select(".board_view ul.detail > li"):
        key_node = li.select_one(".titles")
        value_node = li.select_one(".txts")
        key = normalize_space(key_node.get_text(" ", strip=True) if key_node else "")
        value = normalize_space(value_node.get_text(" ", strip=True) if value_node else "")
        if key:
            pairs[key] = value
    return pairs


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    soup = post_soup(session, detail_payload(row), timeout)
    pairs = detail_pairs(soup)
    title = pairs.get("프로그램명")
    if title:
        row["title"] = title
    row["schedule_raw"] = pairs.get("교육일정") or row.get("schedule_raw")
    row["target"] = pairs.get("교육대상") or row.get("target")
    row["fee"] = pairs.get("수강료") or row.get("fee")
    row["material_fee"] = pairs.get("수강료외 부대비용", "")
    row["material_note"] = pairs.get("학습자준비물", "")
    row["age_note"] = pairs.get("수강가능연령", "")
    row["capacity_text"] = pairs.get("모집인원") or row.get("capacity_text")
    row["instructor"] = pairs.get("강 사 명", "")
    place = pairs.get("교육장소", "")
    if place:
        row["branch"] = place or row.get("branch") or DEFAULT_BRANCH
        row["branch_code"] = hashlib.sha1(row["branch"].encode("utf-8")).hexdigest()[:12]
    row["period"] = normalize_period(pairs.get("교육기간") or row.get("period"))
    row["reception_period"] = normalize_period(pairs.get("수강신청기간") or row.get("reception_period"))
    description_parts = []
    for key in ["강의목표", "강의내용", "강좌속성", "모집방법", "모집제한", "수강료납부안내"]:
        if pairs.get(key):
            description_parts.append(f"{key}: {pairs[key]}")
    row["description"] = normalize_space(" ".join(description_parts))
    row["raw_fields"] = {"detail_pairs": pairs, "parser": "daedeok_lifelong_detail1"}
    row["course_id"] = stable_course_id(row)
    return row


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
        if page == 1:
            soup = fetch_soup(session, LIST_URL, timeout)
        else:
            soup = post_soup(session, {"mnucd": "MENU0100010", "bmode": "list", "pageIndex": str(page), "searchCondition": "1"}, timeout)
        page_rows = parse_list(soup)
        if not page_rows:
            break
        expired_on_page = 0
        for row in page_rows:
            key = row.get("external_id")
            if key in seen:
                continue
            seen.add(str(key))
            row = enrich_detail(session, row, timeout)
            if not include_expired and is_expired_course(row):
                expired_on_page += 1
                logger.info("Skipping expired Daedeok course: %s / %s", row.get("title"), row.get("period"))
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
                    normalize_space(row.get("period")),
                    normalize_space(row.get("schedule_raw")),
                    normalize_space(row.get("target")),
                    normalize_space(row.get("fee")),
                    normalize_space(row.get("status")),
                ]
            )
        )


def save_rows(rows: list[dict[str, Any]]) -> int:
    crawler = YamlSourceCrawler.__new__(YamlSourceCrawler)
    crawler.provider = PROVIDER
    crawler.target_parser = TargetParser()
    crawler.schedule_parser = ScheduleParser()
    saved = 0
    branch_ids: dict[str, str] = {}
    for row in rows:
        branch_code = normalize_space(row.get("branch_code")) or hashlib.sha1(normalize_space(row.get("branch")).encode("utf-8")).hexdigest()[:12]
        branch_name = normalize_space(row.get("branch")) or DEFAULT_BRANCH
        if branch_code not in branch_ids:
            branch_ids[branch_code] = crawler.save_branch(branch_code, branch_name)
        course = crawler.normalize_course(row, branch_ids[branch_code])
        crawler.save_course(course)
        saved += 1
    logger.info("%s saved %s/%s rows.", PROVIDER, saved, len(rows))
    return saved


def main() -> int:
    parser = argparse.ArgumentParser(description="Daedeok resident autonomy program crawler")
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
