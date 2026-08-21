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


PROVIDER = "MUNI_WWW_SANGJU_GO_KR_AEA6F278"
PROVIDER_NAME = "상주시 통합예약"
BASE_URL = "https://www.sangju.go.kr"
LIST_PATH = "/reserve/reservation/list.tc"
DETAIL_PATH = "/reserve/reservation/detail.tc"
DEFAULT_BRANCH = "상주시 통합예약"
DEFAULT_ADDRESS = "경상북도 상주시 상산로 223"

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, setup_logger  # noqa: E402


logger = setup_logger("Crawler_SangjuReservation")


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
            "Referer": urljoin(BASE_URL, "/reserve/page/15375/11881.tc"),
        }
    )
    return session


def normalize_space(value: Any) -> str:
    text = clean_text(value).replace("\xa0", " ")
    return clean_text(re.sub(r"\s+", " ", text))


def normalize_korean_datetime(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        year, month, day = match.group(1), int(match.group(2)), int(match.group(3))
        hour = int(match.group(4) or 0)
        minute = int(match.group(5) or 0)
        if match.group(4):
            return f"{year}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}"
        return f"{year}-{month:02d}-{day:02d}"

    text = normalize_space(text)
    text = re.sub(
        r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일(?:\s*(\d{1,2})시\s*(\d{1,2})분)?",
        repl,
        text,
    )
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
        "mn": "15375",
        "pageNo": "11881",
        "pageIndex": str(page),
        "searchTrgtClsfCd": "RMS004001",
        "searchFcltNo": "",
    }
    return urljoin(BASE_URL, LIST_PATH) + "?" + urlencode(query)


def detail_url(cycl_no: str) -> str:
    query = {
        "mn": "15375",
        "pageNo": "11881",
        "searchTrgtClsfCd": "RMS004001",
        "searchFcltNo": "",
        "cyclNo": cycl_no,
    }
    return urljoin(BASE_URL, DETAIL_PATH) + "?" + urlencode(query)


def extract_onclick_id(value: str | None, method: str) -> str:
    match = re.search(rf"{re.escape(method)}\(['\"]([^'\"]+)['\"]\)", value or "")
    return match.group(1).strip() if match else ""


def section_pairs(node: Tag) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for li in node.select("ul.tm_cir > li"):
        label = li.select_one("span")
        if not label:
            continue
        key = normalize_space(label.get_text(" ", strip=True).replace("주소복사", ""))
        label.extract()
        value = normalize_space(li.get_text(" ", strip=True))
        if key:
            pairs[key] = value
    return pairs


def table_shape_pairs(node: Tag) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for item in node.select("ul.table_shape > li"):
        keys = item.select(".th_shape")
        values = item.select(".td_shape")
        for key_node, value_node in zip(keys, values):
            key = normalize_space(key_node.get_text(" ", strip=True))
            value = normalize_space(value_node.get_text(" ", strip=True))
            if key:
                pairs[key] = value
    return pairs


def infer_category(text: str) -> str:
    if re.search(r"체육|요가|필라테스|운동|건강|스포츠", text):
        return "체육"
    if re.search(r"도서|독서|인문|사회|교양|강연|콘서트", text):
        return "인문교양"
    if re.search(r"컴퓨터|스마트폰|AI|디지털|정보화|포토샵|코딩", text, re.I):
        return "디지털"
    if re.search(r"박물관|문화|예술|음악|미술|공연", text):
        return "문화예술"
    if re.search(r"청소년|아동|어린이|초등", text):
        return "아동청소년"
    return "공공예약"


def infer_age_group(text: str) -> str:
    if re.search(r"초등|어린이|아동|유아", text):
        return "KIDS"
    if re.search(r"청소년|중등|고등", text):
        return "TEEN"
    if re.search(r"성인|시민|일반|강사", text):
        return "ADULT"
    return ""


def infer_target(description: str, title: str = "") -> str:
    text = normalize_space(description)
    for pattern in (
        r"(?:운영대상|교육대상|지원대상|참여대상)\s*[:：]\s*(.*?)(?=\s+(?:운영내용|내용|신청|문의|장소|일시|기간|방법|모집|수강료|강사|$))",
        r"대\s*상\s*[:：]\s*(.*?)(?=\s+(?:내용|신청|문의|장소|일시|기간|방법|모집|수강료|강사|$))",
    ):
        match = re.search(pattern, text)
        if match:
            target = normalize_space(match.group(1).strip(" -▶○■"))
            if target:
                return target
    if "상주시 보건소 등록 임신부" in text:
        return "상주시 보건소 등록 임신부"
    if "상주시 시민 누구나" in text:
        return "상주시민 누구나"
    if "상주시 지역주민 누구나" in text:
        return "상주시 지역주민 누구나"
    haystack = f"{title} {text}"
    if "초등" in haystack:
        return "초등학생"
    if any(token in haystack for token in ("어린이", "아동", "유아")):
        return "어린이"
    if any(token in haystack for token in ("청소년", "중등", "고등")):
        return "청소년"
    return "일반 시민"


def normalize_fee(value: str) -> str:
    text = normalize_space(value)
    if not text or text in {"없음", "무료", "미입력"}:
        return "무료" if text in {"없음", "무료"} else ""
    return text


def infer_status(row: dict[str, Any]) -> str:
    status = normalize_space(row.get("status"))
    if status:
        return status
    raw_fields = row.get("raw_fields") if isinstance(row.get("raw_fields"), dict) else {}
    apply_period = normalize_space(raw_fields.get("application_period") if raw_fields else "")
    start_date, end_date = parse_date_range(apply_period)
    today = datetime.now().date()
    if start_date and today < start_date:
        return "접수예정"
    if start_date and end_date and start_date <= today <= end_date:
        return "접수중"
    if end_date and today > end_date:
        return "접수마감"
    return ""


def parse_list_rows(soup: BeautifulSoup, page: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for section in soup.select("#reserveList section"):
        title_node = section.select_one("h1 a[onclick*='reserveList.detail']")
        if not title_node:
            continue
        cycl_no = extract_onclick_id(title_node.get("onclick"), "reserveList.detail")
        title = normalize_space(title_node.get_text(" ", strip=True))
        if not cycl_no or not title:
            continue
        pairs = section_pairs(section.select_one(".right") or section)
        sub_pairs = section_pairs(section.select_one(".list_sub") or section)
        status = normalize_space(section.select_one(".top span").get_text(" ", strip=True) if section.select_one(".top span") else "")
        period = normalize_korean_datetime(pairs.get("운영기간", ""))
        address = normalize_space(pairs.get("주소")) or DEFAULT_ADDRESS
        branch = normalize_space(pairs.get("시설명")) or DEFAULT_BRANCH
        category_text = normalize_space(pairs.get("분류"))
        description = normalize_space(section.get_text(" ", strip=True))
        raw_url = detail_url(cycl_no)
        rows.append(
            {
                "provider": PROVIDER,
                "provider_name": PROVIDER_NAME,
                "title": title,
                "branch": branch,
                "branch_code": branch,
                "address": address,
                "venue": branch,
                "period": period,
                "schedule_raw": period,
                "target": "",
                "age_group": infer_age_group(description),
                "fee": "",
                "status": status,
                "category": infer_category(f"{title} {category_text}"),
                "collection_category": "공공예약",
                "domain_category": "공공예약",
                "raw_url": raw_url,
                "image_url": "",
                "description": description,
                "raw_fields": {
                    "parser": "sangju_reserve_list",
                    "cycl_no": cycl_no,
                    "category_text": category_text,
                    "application_period": normalize_korean_datetime(sub_pairs.get("접수기간", "")),
                    "capacity": normalize_space(sub_pairs.get("정원")),
                    "waitlist": normalize_space(sub_pairs.get("후보")),
                    "list_page": page,
                },
            }
        )
    return rows


def extract_description(soup: BeautifulSoup) -> str:
    panel = soup.select_one("#tab1_panel .bd_scroll")
    if panel:
        return "\n".join(
            line
            for line in [normalize_space(part) for part in panel.get_text("\n", strip=True).splitlines()]
            if line
        )
    bg_sub = soup.select_one(".bg_sub")
    if not bg_sub:
        return ""
    clone = BeautifulSoup(str(bg_sub), "html.parser")
    for node in clone.select(".motion_wrap, .com_tab, #tab2_panel, #tab3_panel, script, style"):
        node.decompose()
    lines = [normalize_space(part) for part in clone.get_text("\n", strip=True).splitlines()]
    return "\n".join(line for line in lines if line and line not in {"이용안내", "이용료안내", "예약절차안내"})


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    soup = fetch_soup(session, str(row["raw_url"]), timeout)
    row = dict(row)
    top = soup.select_one(".img_jb")
    if top:
        title_node = top.select_one("h1")
        if title_node:
            row["title"] = normalize_space(title_node.get_text(" ", strip=True)) or row["title"]
        pairs = section_pairs(top)
        row["branch"] = normalize_space(pairs.get("시설명")) or row["branch"]
        row["branch_code"] = row["branch"]
        row["venue"] = row["branch"]
        row["address"] = normalize_space(pairs.get("주소")) or row["address"]
        row["period"] = normalize_korean_datetime(pairs.get("운영기간")) or row["period"]
        row["schedule_raw"] = row["period"]
        row["teacher"] = normalize_space(pairs.get("강사"))
        image = top.select_one(".slide img[src]")
        if image:
            row["image_url"] = urljoin(BASE_URL, image.get("src"))

    detail_pairs = table_shape_pairs(soup.select_one(".hidden_box") or soup)
    info_pairs = table_shape_pairs(soup.select_one("#tab1_panel") or soup)
    apply_period = normalize_korean_datetime(detail_pairs.get("접수 기간", ""))
    operating_period = normalize_korean_datetime(detail_pairs.get("운영 기간", ""))
    if operating_period:
        row["period"] = operating_period
        row["schedule_raw"] = operating_period
    fee = normalize_fee(detail_pairs.get("이용료") or soup.select_one("#tab2_panel .bd_scroll").get_text(" ", strip=True) if soup.select_one("#tab2_panel .bd_scroll") else "")
    row["fee"] = fee
    description = extract_description(soup)
    row["description"] = description or row.get("description", "")
    target_match = re.search(
        r"(?:대상|교육대상)\s*[:：]\s*(.*?)(?=\s+(?:내용|신청|장소|강사|일시)\s*[:：]|$)",
        row["description"],
    )
    if target_match:
        row["target"] = normalize_space(target_match.group(1))
        row["age_group"] = infer_age_group(row["target"])
    row["category"] = infer_category(f"{row['title']} {row.get('description', '')}")
    row.setdefault("raw_fields", {})["detail_pairs"] = detail_pairs
    row["raw_fields"]["info_pairs"] = info_pairs
    row["raw_fields"]["application_period"] = apply_period or row["raw_fields"].get("application_period", "")
    if not normalize_space(row.get("target")):
        row["target"] = infer_target(row.get("description", ""), row.get("title", ""))
        row["age_group"] = infer_age_group(row["target"])
    if not normalize_space(row.get("fee")) and "무료" in normalize_space(row.get("description")):
        row["fee"] = "무료"
    row["status"] = infer_status(row)
    return row


def is_expired_course(row: dict[str, Any]) -> bool:
    _, end_date = parse_date_range(row.get("period"))
    if end_date is None:
        return False
    return end_date < datetime.now().date()


def stable_course_id(row: dict[str, Any]) -> str:
    raw_fields = row.get("raw_fields") if isinstance(row.get("raw_fields"), dict) else {}
    cycl_no = normalize_space(raw_fields.get("cycl_no") if raw_fields else "")
    if cycl_no:
        return cycl_no
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
                logger.warning("Sangju detail failed %s: %s", base_row.get("raw_url"), exc)
                row = base_row
            if is_expired_course(row):
                logger.info("Skipping expired Sangju reservation: %s / %s", row.get("title"), row.get("period"))
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
    return {
        "provider": PROVIDER,
        "rows": len(rows),
        "collected": len(rows),
        "score": score,
        "parser": "sangju_reserve_list",
        "field_counts": counts,
    }


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
    parser = argparse.ArgumentParser(description="Sangju public reservation crawler")
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
