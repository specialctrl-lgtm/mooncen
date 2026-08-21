
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


PROVIDER = "MUNI_JHED_JNE_GO_KR_16474ED5"
PROVIDER_NAME = "??????????? ???? ????"
BASE_URL = "https://yeyak.jne.kr"
LIST_PATH = "/yeyak/exprn/selectExprnList.do"
DETAIL_PATH = "/yeyak/exprn/selectExprnInfo.do"
RS_SYS_ID = "jhed"
MI = "10205166"
DEFAULT_BRANCH = "???????????"
DEFAULT_ADDRESS = "\uc804\ub77c\ub0a8\ub3c4 \uc7a5\ud765\uad70 \uc7a5\ud765\uc74d \ub3d9\uad50\ub85c 64-17"

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, setup_logger  # noqa: E402

logger = setup_logger("Crawler_JhedJneExperience")


def normalize_space(value: Any) -> str:
    return clean_text(re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")))


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0 Safari/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        "Accept-Encoding": "identity",
        "Referer": list_url(1),
    })
    return session


def list_url(page: int = 1) -> str:
    params = {
        "mi": MI,
        "exprnSeq": "",
        "exprnPeriodSeq": "",
        "srchRsSysId": RS_SYS_ID,
        "srchExprnSeq": "",
        "currPage": str(page),
        "listTy": "",
        "srchAt": "Y",
        "pageIndex": "10",
        "srchRsvSttus": "",
        "srchPeriodDiv": "rcept",
        "srchRsvBgnde": "",
        "srchRsvEndde": "",
        "srchRsvValue": "",
    }
    return f"{BASE_URL}{LIST_PATH}?{urlencode(params)}"


def detail_url(exprn_seq: str, period_seq: str, rs_sys_id: str = RS_SYS_ID) -> str:
    params = {
        "mi": MI,
        "exprnSeq": exprn_seq,
        "exprnPeriodSeq": period_seq,
        "srchRsSysId": rs_sys_id,
        "srchAt": "Y",
        "currPage": "1",
    }
    return f"{BASE_URL}{DETAIL_PATH}?{urlencode(params)}"


def fetch_soup(session: requests.Session, url: str, timeout: int) -> BeautifulSoup:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def normalize_period(value: Any) -> str:
    text = normalize_space(value)
    text = re.sub(r"(\d{4})/(\d{1,2})/(\d{1,2})", lambda m: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}", text)
    text = re.sub(r"\s*~\s*", " ~ ", text)
    return normalize_space(text)


def normalize_status(value: Any) -> str:
    text = normalize_space(value)
    if "\uc811\uc218\uc911" in text:
        return "OPEN"
    if "\uc608\uc815" in text:
        return "SCHEDULED"
    if any(token in text for token in ["\ub9c8\uac10", "\uc608\uc57d\ubd88\uac00", "\uc885\ub8cc"]):
        return "CLOSED"
    return text or "UNKNOWN"

def row_cells(tr: Tag) -> list[str]:
    return [normalize_space(td.get_text(" ", strip=True)) for td in tr.find_all("td", recursive=False)]


def parse_list_row(tr: Tag, source_url: str) -> dict[str, Any] | None:
    cells = row_cells(tr)
    if len(cells) < 8:
        return None
    link = tr.select_one(".viewExprnInfo[data-id][data-period-id]")
    if not link:
        return None
    exprn_seq = normalize_space(link.get("data-id"))
    period_seq = normalize_space(link.get("data-period-id"))
    rs_sys_id = normalize_space(link.get("data-rssysid")) or RS_SYS_ID
    if rs_sys_id != RS_SYS_ID:
        return None
    category = normalize_space(link.get_text(" ", strip=True))
    detail_title = normalize_space(tr.select_one("td.tdW100 p").get_text(" ", strip=True) if tr.select_one("td.tdW100 p") else "")
    title = normalize_space(" ".join(part for part in [category, detail_title] if part))
    if not title:
        return None
    course_id = f"{exprn_seq}-{period_seq}"
    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": course_id,
        "provider_course_id": course_id,
        "title": title,
        "branch": cells[1] or DEFAULT_BRANCH,
        "branch_code": hashlib.sha1((cells[1] or DEFAULT_BRANCH).encode("utf-8")).hexdigest()[:12],
        "address": DEFAULT_ADDRESS,
        "period": normalize_period(cells[3]),
        "schedule_raw": "",
        "target": cells[5],
        "fee": "\ubb34\ub8cc",
        "status": normalize_status(cells[7]),
        "description": "",
        "image_url": "",
        "raw_url": detail_url(exprn_seq, period_seq, rs_sys_id),
        "application_url": detail_url(exprn_seq, period_seq, rs_sys_id) if normalize_status(cells[7]) == "OPEN" else "",
        "application_type": "ONLINE_RESERVATION",
        "application_method_raw": cells[6],
        "reservation_available": normalize_status(cells[7]) == "OPEN",
        "category": category,
        "collection_category": "?????",
        "domain_category": "\uccb4\ud5d8",
        "operator_type": "\uc9c0\uc790\uccb4/\uacf5\uacf5\uae30\uad00",
        "source_group": "education_office_reservation",
        "collection_type": "reservation_table_detail",
        "program_type": "\uacac\ud559\uccb4\ud5d8",
        "reception_period": normalize_period(cells[4]),
        "raw_fields": {"parser": "jne_experience_list", "source_url": source_url, "list_cells": cells},
    }


def labeled_value(text: str, label: str) -> str:
    labels = [
        "\uc6b4\uc601\uae30\uad00",
        "\uccb4\ud5d8\uae30\uac04",
        "\uc2e0\uccad\uae30\uac04",
        "\uccb4\ud5d8\ub300\uc0c1",
        "\uc2e0\uccad\ub300\uc0c1",
        "\uc608\uc57d\uc9c0\uc5ed",
        "\ucca8\ubd80\ud30c\uc77c",
        "\ub2f4\ub2f9\uc790 \uc5f0\ub77d\ucc98(\ubb38\uc758\uc804\ud654)",
        "\uc774\uc804 \uc6d4",
    ]
    escaped = "|".join(re.escape(item) for item in labels if item != label)
    match = re.search(re.escape(label) + r"\s*(.*?)(?=" + escaped + r"|$)", text)
    return normalize_space(match.group(1)) if match else ""


def detail_description(soup: BeautifulSoup) -> str:
    text = normalize_space(soup.get_text(" ", strip=True))
    start_markers = ["\uc774\uc6a9\uc548\ub0b4", "\uccb4\ud5d8\uc548\ub0b4", "\uc720\uc758\uc0ac\ud56d"]
    desc = ""
    for marker in start_markers:
        idx = text.find(marker)
        if idx >= 0:
            desc = text[idx:]
            break
    for stop in ["\uc804\ub77c\ub0a8\ub3c4\uad50\uc721\uccad \ud1b5\ud569\uc608\uc57d\uc2dc\uc2a4\ud15c", "Copyright"]:
        pos = desc.find(stop)
        if pos >= 0:
            desc = desc[:pos]
    return normalize_space(desc)


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    soup = fetch_soup(session, row["raw_url"], timeout)
    text = normalize_space(soup.select_one(".subContent").get_text(" ", strip=True) if soup.select_one(".subContent") else soup.get_text(" ", strip=True))
    branch = labeled_value(text, "\uc6b4\uc601\uae30\uad00") or row.get("branch") or DEFAULT_BRANCH
    row["branch"] = branch
    row["branch_code"] = hashlib.sha1(branch.encode("utf-8")).hexdigest()[:12]
    row["period"] = normalize_period(labeled_value(text, "\uccb4\ud5d8\uae30\uac04") or row.get("period"))
    row["reception_period"] = normalize_period(labeled_value(text, "\uc2e0\uccad\uae30\uac04") or row.get("reception_period"))
    row["target"] = labeled_value(text, "\uccb4\ud5d8\ub300\uc0c1") or row.get("target")
    row["application_method_raw"] = labeled_value(text, "\uc2e0\uccad\ub300\uc0c1") or row.get("application_method_raw")
    row["region"] = labeled_value(text, "\uc608\uc57d\uc9c0\uc5ed")
    row["phone"] = labeled_value(text, "\ub2f4\ub2f9\uc790 \uc5f0\ub77d\ucc98(\ubb38\uc758\uc804\ud654)")
    row["description"] = detail_description(soup) or row.get("description")
    cal_texts = [normalize_space(td.get_text(" ", strip=True)) for td in soup.select("table td")]
    available = [item for item in cal_texts if any(token in item for token in ["\uc608\uc57d\uac00\ub2a5", "\uc608\uc57d\ubd88\uac00", "\ub300\uae30\uc811\uc218"])]
    if available:
        schedule = max(available, key=len)
        time_match = re.search(r"(\uc624\uc804|\uc624\ud6c4)?\s*\d{1,2}\uc2dc(?:\s*~\s*\d{1,2}\uc2dc)?", schedule)
        row["schedule_raw"] = normalize_space(time_match.group(0) if time_match else schedule)
        cap_match = re.search(r"\[(\d+)\s*/\s*(\d+)\]", schedule)
        if cap_match:
            row["capacity_current"] = int(cap_match.group(1))
            row["capacity_total"] = int(cap_match.group(2))
            row["capacity_remaining"] = max(0, int(cap_match.group(2)) - int(cap_match.group(1)))
    row["raw_fields"] = {**(row.get("raw_fields") or {}), "detail_parser": "jne_experience_detail"}
    return row

def is_expired_course(row: dict[str, Any]) -> bool:
    parsed = parse_date_range(row.get("period"))
    end_date = parsed[1] if parsed else None
    if not end_date:
        return False
    end_day = end_date.date() if hasattr(end_date, "date") else end_date
    return end_day < datetime.now().date()


def should_skip(row: dict[str, Any]) -> bool:
    title = normalize_space(row.get("title"))
    return "???" in title or "???" in title


def max_page(soup: BeautifulSoup) -> int:
    found = [1]
    for a in soup.select("a[onclick*='goPaging']"):
        m = re.search(r"goPaging\((\d+)\)", a.get("onclick") or "")
        if m:
            found.append(int(m.group(1)))
        txt = normalize_space(a.get_text(" ", strip=True))
        if txt.isdigit():
            found.append(int(txt))
    return max(found)


def collect(limit: int | None = None, max_pages: int = 3, timeout: int = 20, include_expired: bool = False, detail: bool = True) -> list[dict[str, Any]]:
    session = make_session()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    first_soup: BeautifulSoup | None = None
    for page in range(1, max_pages + 1):
        url = list_url(page)
        soup = fetch_soup(session, url, timeout)
        if first_soup is None:
            first_soup = soup
            max_pages = min(max_pages, max_page(soup))
        body_rows = soup.select("table tbody tr")
        if not body_rows:
            break
        for tr in body_rows:
            row = parse_list_row(tr, url)
            if not row or row["provider_course_id"] in seen:
                continue
            seen.add(row["provider_course_id"])
            if detail:
                try:
                    row = enrich_detail(session, row, timeout)
                except Exception as exc:
                    logger.warning("JNE detail failed %s: %s", row.get("raw_url"), exc)
            if should_skip(row):
                continue
            if not include_expired and is_expired_course(row):
                logger.info("Skipping expired JNE experience: %s / %s", row.get("title"), row.get("period"))
                continue
            rows.append(row)
            if limit and len(rows) >= limit:
                return rows
    return rows


def quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fields = ["title", "branch", "raw_url", "address", "period", "schedule_raw", "target", "fee", "status", "description", "image_url"]
    counts = {field: sum(1 for row in rows if normalize_space(row.get(field))) for field in fields}
    score = round(sum(counts.values()) / (len(rows) * len(fields)) * 100, 1) if rows else 0.0
    return {"rows": len(rows), "score": score, "field_counts": counts}


def print_quality(rows: list[dict[str, Any]]) -> None:
    print(json.dumps(quality(rows), ensure_ascii=False, indent=2))
    print("\nSAMPLE")
    for row in rows[:5]:
        print(" | ".join([normalize_space(row.get(k)) for k in ["title", "branch", "period", "schedule_raw", "target", "fee", "status"]]))


def save_rows(rows: list[dict[str, Any]]) -> int:
    crawler = YamlSourceCrawler.__new__(YamlSourceCrawler)
    crawler.provider = PROVIDER
    crawler.target_parser = TargetParser()
    crawler.schedule_parser = ScheduleParser()
    saved = 0
    branch_ids: dict[str, str] = {}
    for row in rows:
        code = normalize_space(row.get("branch_code")) or hashlib.sha1(normalize_space(row.get("branch")).encode("utf-8")).hexdigest()[:12]
        if code not in branch_ids:
            branch_ids[code] = crawler.save_branch(code, normalize_space(row.get("branch")) or DEFAULT_BRANCH)
        course = crawler.normalize_course(row, branch_ids[code])
        crawler.save_course(course)
        saved += 1
    logger.info("%s saved %s/%s rows.", PROVIDER, saved, len(rows))
    return saved


def main() -> int:
    parser = argparse.ArgumentParser(description="Jangheung education office JNE experience reservation crawler")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--per-target-limit", type=int)
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--include-expired", action="store_true")
    parser.add_argument("--no-detail", action="store_true")
    parser.add_argument("--mark-stale", action="store_true")
    parser.add_argument("--max-depth", type=int)
    parser.add_argument("--detail-limit", type=int)
    args = parser.parse_args()
    rows = collect(limit=args.limit or args.per_target_limit, max_pages=args.max_pages, timeout=args.timeout, include_expired=args.include_expired, detail=not args.no_detail)
    saved = save_rows(rows) if args.save_db else 0
    print_quality(rows)
    logger.info("%s completed collected=%s saved=%s", PROVIDER, len(rows), saved)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
