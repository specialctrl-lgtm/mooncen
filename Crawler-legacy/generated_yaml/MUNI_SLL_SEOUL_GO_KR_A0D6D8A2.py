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


PROVIDER = "MUNI_SLL_SEOUL_GO_KR_A0D6D8A2"
PROVIDER_NAME = "서울런4050 서울시평생학습포털"
BASE_URL = "https://sll.seoul.go.kr"
LIST_ENDPOINT = f"{BASE_URL}/lms/simin_course/courseRequest/doListSiminCourse.do"
DETAIL_ENDPOINT = f"{BASE_URL}/lms/simin_course/courseRequest/doDetailInfo.do"

TARGETS = [
    {
        "name": "???????",
        "simin_yn": "LD",
        "referer": f"{BASE_URL}/lms/simin_course/courseRequest/doListView.do?main_se=ssu&simin_yn=LD&mnid=202501750226",
    },
    {
        "name": "????? ????",
        "simin_yn": "MC",
        "referer": f"{BASE_URL}/lms/simin_course/courseRequest/doListView.do?main_se=ssu&simin_yn=MC&mnid=20250161480",
    },
    {
        "name": "???????",
        "simin_yn": "U",
        "referer": f"{BASE_URL}/lms/simin_course/courseRequest/doListView.do?main_se=ssu&simin_yn=U&mnid=202501211239",
    },
    {
        "name": "????",
        "simin_yn": "PS",
        "referer": f"{BASE_URL}/lms/simin_course/courseRequest/doListView.do?main_se=ssu&simin_yn=PS&mnid=202603506456",
    },
]

CAMPUS_NAMES = {
    "M": "중부권캠퍼스",
    "DC": "동남권캠퍼스",
    "MD": "모두의학교캠퍼스",
    "RG": "다시가는캠퍼스",
    "O": "온라인시민대학",
    "R": "연계시민대학",
    "W": "시민석사",
    "PA": "시민박사",
}

CAMPUS_ADDRESSES = {
    "M": "서울특별시 중구 칠패로 5",
    "DC": "서울특별시 강동구 고덕로 399",
    "MD": "서울특별시 금천구 남부순환로 128길 42",
    "RG": "서울특별시 관악구 낙성대로 70",
    "O": "",
    "R": "",
    "W": "",
    "PA": "",
    "LD": "",
    "MC": "",
    "U": "",
    "PS": "",
}

INSTITUTION_ALIASES = {
    "\uACBD\uD76C\uC0AC\uC774\uBC84\uB300": "\uACBD\uD76C\uC0AC\uC774\uBC84\uB300\uD559\uAD50",
    "\uACBD\uD76C\uB300": "\uACBD\uD76C\uB300\uD559\uAD50",
    "\uB3D9\uAD6D\uB300\uD559\uAD50": "\uB3D9\uAD6D\uB300\uD559\uAD50",
    "\uBA85\uC9C0\uC804\uBB38\uB300": "\uBA85\uC9C0\uC804\uBB38\uB300\uD559",
    "\uBA85\uC9C0\uB300": "\uBA85\uC9C0\uB300\uD559\uAD50",
    "\uBC30\uD654": "\uBC30\uD654\uC5EC\uC790\uB300\uD559\uAD50",
    "\uC0BC\uC721\uB300": "\uC0BC\uC721\uB300\uD559\uAD50",
    "\uC11C\uACBD\uB300": "\uC11C\uACBD\uB300\uD559\uAD50",
    "\uC11C\uC6B8\uAD50\uB300": "\uC11C\uC6B8\uAD50\uC721\uB300\uD559\uAD50",
    "\uC11C\uC6B8\uC5EC\uB300": "\uC11C\uC6B8\uC5EC\uC790\uB300\uD559\uAD50",
    "\uC11C\uC77C\uB300": "\uC11C\uC77C\uB300\uD559\uAD50",
    "\uC131\uADE0\uAD00\uB300": "\uC131\uADE0\uAD00\uB300\uD559\uAD50",
    "\uC131\uC2E0\uC5EC\uB300": "\uC131\uC2E0\uC5EC\uC790\uB300\uD559\uAD50",
    "\uC5F0\uC138\uB300": "\uC5F0\uC138\uB300\uD559\uAD50",
    "\uC774\uD654\uC5EC\uB300": "\uC774\uD654\uC5EC\uC790\uB300\uD559\uAD50",
    "\uC778\uB355\uB300\uD559\uAD50": "\uC778\uB355\uB300\uD559\uAD50",
    "\uC911\uC559\uB300": "\uC911\uC559\uB300\uD559\uAD50",
    "\uCD1D\uC2E0\uB300": "\uCD1D\uC2E0\uB300\uD559\uAD50",
    "\uD55C\uC591\uC5EC\uB300": "\uD55C\uC591\uC5EC\uC790\uB300\uD559\uAD50",
    "\uD64D\uC775\uB300": "\uD64D\uC775\uB300\uD559\uAD50",
}

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, setup_logger  # noqa: E402


logger = setup_logger("Crawler_SeoulSllApplication")


def make_session(referer: str | None = None) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        }
    )
    if referer:
        session.headers["Referer"] = referer
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


def stable_course_id(row: dict[str, Any]) -> str:
    key = "|".join(
        [
            PROVIDER,
            normalize_space(row.get("external_id")),
            normalize_space(row.get("class_no")),
            normalize_space(row.get("title")),
            normalize_space(row.get("period")),
        ]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def list_payload(simin_yn: str) -> dict[str, str]:
    return {
        "simin_yn": simin_yn,
        "menu_se": "",
        "course_category_id": "",
        "course_gubun": "",
        "fee": "",
        "sc_status": "",
        "course_str_dt": "",
        "course_end_dt": "",
        "weekday": "",
        "keyword": "",
        "keyword_status": "",
        "course_nm": "",
        "sorting": "",
    }


def fetch_list(session: requests.Session, simin_yn: str, timeout: int) -> list[dict[str, Any]]:
    response = session.post(LIST_ENDPOINT, data=list_payload(simin_yn), timeout=timeout)
    response.raise_for_status()
    return response.json().get("simin_course_list") or []


def build_raw_url(item: dict[str, Any]) -> str:
    params = {
        "course_id": item.get("course_id", ""),
        "class_no": item.get("class_no", ""),
        "course_gubun": item.get("course_gubun", ""),
        "simin_yn": item.get("simin_yn", ""),
    }
    return f"{DETAIL_ENDPOINT}?{urlencode(params)}"


def time_range(item: dict[str, Any]) -> str:
    start_h = normalize_space(item.get("course_str_time_h"))
    start_m = normalize_space(item.get("course_str_time_m"))
    end_h = normalize_space(item.get("course_end_time_h"))
    end_m = normalize_space(item.get("course_end_time_m"))
    if start_h and end_h:
        return f"{start_h}:{start_m or '00'}~{end_h}:{end_m or '00'}"
    return ""


def branch_name(item: dict[str, Any]) -> str:
    simin_yn = normalize_space(item.get("simin_yn"))
    place = normalize_space(item.get("course_place"))
    compact_place = place.replace(" ", "")
    for name in CAMPUS_NAMES.values():
        if name and name.replace(" ", "") in compact_place:
            return name
    return CAMPUS_NAMES.get(simin_yn) or place or PROVIDER_NAME


def branch_address(item: dict[str, Any]) -> str:
    simin_yn = normalize_space(item.get("simin_yn"))
    place = normalize_space(item.get("course_place"))
    compact_place = place.replace(" ", "")
    for code, name in CAMPUS_NAMES.items():
        if name and name.replace(" ", "") in compact_place:
            return CAMPUS_ADDRESSES.get(code, "")
    return CAMPUS_ADDRESSES.get(simin_yn, "")


def institution_from_title(title: Any) -> str:
    text = normalize_space(title)
    parenthesized = " ".join(re.findall(r"\(([^)]+)\)", text))
    source = parenthesized or text
    for alias, full_name in sorted(INSTITUTION_ALIASES.items(), key=lambda pair: len(pair[0]), reverse=True):
        if alias in source or full_name in source:
            return full_name
    return ""


def has_institution_name(branch: Any) -> bool:
    text = normalize_space(branch)
    if not text:
        return False
    if any(alias in text or full_name in text for alias, full_name in INSTITUTION_ALIASES.items()):
        return True
    return bool(re.search(r"(?:\uB300\uD559\uAD50|\uC804\uBB38\uB300\uD559|\uB300\uD559|\uB300)(?:[\s(]|$)", text))


def normalize_branch_display_name(branch: Any, title: Any) -> str:
    name = normalize_space(branch)
    institution = institution_from_title(title)
    if institution and name and not has_institution_name(name):
        return normalize_space(f"{institution} {name}")
    return name or institution or PROVIDER_NAME


def status_text(value: Any) -> str:
    code = normalize_space(value)
    return {
        "ING": "접수중",
        "WAIT": "접수예정",
        "END": "접수마감",
        "CLOSE": "접수마감",
        "FULL": "접수마감",
        "PAST": "종료",
    }.get(code, code)


def row_from_item(item: dict[str, Any], source_name: str) -> dict[str, Any]:
    period = normalize_period(f"{item.get('course_str_dt', '')} ~ {item.get('course_end_dt', '')}")
    weekday = normalize_space(item.get("weekday"))
    schedule = normalize_space(" ".join(part for part in [weekday, time_range(item)] if part))
    reception = normalize_period(f"{item.get('course_request_str_dt', '')} ~ {item.get('course_request_end_dt', '')}")
    title = normalize_space(item.get("course_nm"))
    raw_branch = branch_name(item)
    branch = normalize_branch_display_name(raw_branch, title)
    row = {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": normalize_space(item.get("course_id")),
        "class_no": normalize_space(item.get("class_no")),
        "course_id": "",
        "title": title,
        "branch": branch,
        "branch_code": hashlib.sha1(normalize_space(raw_branch).encode("utf-8")).hexdigest()[:12],
        "address": branch_address(item),
        "period": period,
        "schedule_raw": schedule,
        "target": "서울시민",
        "fee": normalize_space(item.get("fee")),
        "status": status_text(item.get("status")),
        "description": "",
        "image_url": "",
        "raw_url": build_raw_url(item),
        "category": normalize_space(item.get("category_nm2")),
        "collection_category": "평생학습",
        "domain_category": "평생학습",
        "operator_type": "지자체/공공기관",
        "reception_period": reception,
        "capacity_text": normalize_space(item.get("capacity")),
        "instructor": normalize_space(item.get("prof_nm")),
        "tags": normalize_space(item.get("tag_list")),
        "source_target": source_name,
        "simin_yn": normalize_space(item.get("simin_yn")),
        "course_gubun": normalize_space(item.get("course_gubun")),
    }
    row["course_id"] = stable_course_id(row)
    return row


def table_pairs(table: Tag | None) -> dict[str, str]:
    pairs: dict[str, str] = {}
    if not table:
        return pairs
    for tr in table.select("tr"):
        cells = tr.select("th,td")
        if len(cells) < 2:
            continue
        key = normalize_space(cells[0].get_text(" ", strip=True))
        value = normalize_space(cells[1].get_text(" ", strip=True))
        if key:
            pairs[key] = value
    return pairs


def detail_side_pairs(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for box in soup.select(".detail_side_list .sl_box"):
        key = normalize_space(box.select_one(".sl_title").get_text(" ", strip=True) if box.select_one(".sl_title") else "")
        value = normalize_space(box.select_one(".sl_item").get_text(" ", strip=True) if box.select_one(".sl_item") else "")
        if key:
            pairs[key] = value
    return pairs


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    try:
        response = session.get(row["raw_url"], timeout=timeout)
        response.raise_for_status()
    except Exception as exc:
        logger.warning("Detail fetch failed: %s %s", row.get("raw_url"), exc)
        return row
    response.encoding = response.apparent_encoding or "utf-8"
    soup = BeautifulSoup(response.text, "html.parser")

    title = normalize_space(soup.select_one(".detail_title").get_text(" ", strip=True) if soup.select_one(".detail_title") else "")
    if title:
        row["title"] = title
        row["branch"] = normalize_branch_display_name(row.get("branch"), row.get("title"))

    top = detail_side_pairs(soup)
    detail_table = table_pairs(soup.select_one("table.tbl.row.data"))

    row["target"] = detail_table.get("수강대상") or top.get("수강대상") or row.get("target")
    row["capacity_text"] = detail_table.get("정원") or top.get("정원") or row.get("capacity_text")
    row["period"] = normalize_period((detail_table.get("교육기간") or top.get("교육기간") or row.get("period")).split("(")[0])
    if detail_table.get("교육기간") and "(" in detail_table["교육기간"]:
        match = re.search(r"\((\d{1,2}:\d{2}\s*~\s*\d{1,2}:\d{2})\)", detail_table["교육기간"])
        if match:
            row["schedule_raw"] = normalize_space(" ".join([extract_weekday(detail_table["교육기간"]), match.group(1)]))
    row["instructor"] = detail_table.get("강사정보") or top.get("강사") or row.get("instructor")
    row["description"] = detail_table.get("과정소개") or row.get("description")
    row["reception_period"] = normalize_period(detail_table.get("신청기간") or top.get("신청기간") or row.get("reception_period"))
    place = detail_table.get("교육장소") or top.get("교육장소")
    if place:
        raw_branch = branch_from_place(place, row.get("simin_yn")) or row.get("branch")
        row["branch"] = normalize_branch_display_name(raw_branch, row.get("title"))
        row["branch_code"] = hashlib.sha1(normalize_space(raw_branch).encode("utf-8")).hexdigest()[:12]
        row["address"] = branch_address({"simin_yn": row.get("simin_yn"), "course_place": place}) or row.get("address")
    row["course_id"] = stable_course_id(row)
    return row


def extract_weekday(value: str) -> str:
    match = re.search(r"\(([월화수목금토일, ]+)\)", value)
    return normalize_space(match.group(1)) if match else ""


def branch_from_place(place: Any, simin_yn: Any) -> str:
    text = normalize_space(place)
    compact_text = text.replace(" ", "")
    for name in CAMPUS_NAMES.values():
        if name and name.replace(" ", "") in compact_text:
            return name
    return CAMPUS_NAMES.get(normalize_space(simin_yn), "") or text


def is_expired_course(row: dict[str, Any]) -> bool:
    parsed = parse_date_range(row.get("period"))
    end_date = parsed[1] if parsed else None
    if not end_date:
        return False
    end_day = end_date.date() if hasattr(end_date, "date") else end_date
    return end_day < datetime.now().date()


def collect(
    limit: int | None = None,
    timeout: int = 20,
    include_expired: bool = False,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for target in TARGETS:
        session = make_session(target["referer"])
        items = fetch_list(session, target["simin_yn"], timeout)
        for item in items:
            row = row_from_item(item, target["name"])
            key = "|".join([row["external_id"], row["class_no"], row.get("simin_yn", "")])
            if key in seen:
                continue
            seen.add(key)
            if not include_expired and is_expired_course(row):
                logger.info("Skipping expired Seoul SLL course: %s / %s", row.get("title"), row.get("period"))
                continue
            row = enrich_detail(session, row, timeout)
            rows.append(row)
            if limit and len(rows) >= limit:
                return rows
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
        branch_name = normalize_space(row.get("branch")) or PROVIDER_NAME
        if branch_code not in branch_ids:
            branch_ids[branch_code] = crawler.save_branch(branch_code, branch_name)
        course = crawler.normalize_course(row, branch_ids[branch_code])
        crawler.save_course(course)
        saved += 1
    logger.info("%s saved %s/%s rows.", PROVIDER, saved, len(rows))
    return saved


def main() -> int:
    parser = argparse.ArgumentParser(description="Seoul SLL application course crawler")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--include-expired", action="store_true")
    parser.add_argument("--mark-stale", action="store_true")
    parser.add_argument("--per-target-limit", type=int)
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--max-depth", type=int)
    parser.add_argument("--detail-limit", type=int)
    args = parser.parse_args()

    effective_limit = args.limit or args.per_target_limit
    started = datetime.now()
    rows = collect(limit=effective_limit, timeout=args.timeout, include_expired=args.include_expired)
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
