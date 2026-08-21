from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag


PROVIDER = "MUNI_E_JEONJU_GO_KR_91CFC934"
PROVIDER_NAME = "전주시평생학습관"
BASE_URL = "https://e.jeonju.go.kr"
GC = "Program23"
LIST_URL = f"{BASE_URL}/main/menu?gc={GC}"
DEFAULT_BRANCH = "전주시평생학습관"
DEFAULT_ADDRESS = "전북특별자치도 전주시 덕진구 구총목로11"

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, setup_logger  # noqa: E402


logger = setup_logger("Crawler_JeonjuLifelong")


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
    text = re.sub(r"(?<!\d)(\d{2})[.](\d{1,2})[.](\d{1,2})\([^)]*\)", lambda m: f"20{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}", text)
    text = re.sub(r"(?<!\d)(\d{2})[.](\d{1,2})[.](\d{1,2})", lambda m: f"20{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}", text)
    text = re.sub(r"(\d{4})[.](\d{1,2})[.](\d{1,2})", lambda m: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}", text)
    text = re.sub(r"\s*~\s*", " ~ ", text)
    return normalize_space(text)


def fetch_soup(session: requests.Session, url: str, timeout: int) -> BeautifulSoup:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def dl_pairs(node: Tag | None) -> dict[str, str]:
    pairs: dict[str, str] = {}
    if not node:
        return pairs
    for dl in node.select("dl"):
        key_node = dl.select_one("dt")
        value_node = dl.select_one("dd")
        key = normalize_space(key_node.get_text(" ", strip=True) if key_node else "")
        value = normalize_space(value_node.get_text(" ", strip=True) if value_node else "")
        if key:
            pairs[key] = value
    return pairs


def parse_program_id(url: str) -> str:
    return normalize_space((parse_qs(urlparse(url).query).get("program_id") or [""])[0])


def infer_age_group(target: str) -> str:
    if re.search(r"어린이|초등|가족", target):
        return "KIDS"
    if re.search(r"청소년|중등|고등", target):
        return "TEEN"
    if re.search(r"성인|어르신|직장인|학부모|전체|인문강좌", target):
        return "ADULT"
    return ""


def infer_category(category: str, title: str) -> str:
    text = f"{category} {title}"
    if re.search(r"디지털|컴퓨터|스마트폰|AI", text, re.I):
        return "디지털"
    if re.search(r"인문|교양|철학|역사|신화|경제지", text):
        return "인문교양"
    if re.search(r"공예|건강|미술|음악|문화예술|요가", text):
        return "문화예술"
    if re.search(r"문해|한글", text):
        return "성인문해"
    return "평생학습"


def parse_list_item(item: Tag) -> dict[str, Any] | None:
    link = item.select_one("a[href*='program_id']")
    if not link:
        return None
    raw_url = urljoin(BASE_URL, link.get("href", ""))
    cont = link.select_one(".cont") or link
    title_box = cont.select_one(".tit")
    category = normalize_space(title_box.select_one(".cate").get_text(" ", strip=True).strip("[]")) if title_box and title_box.select_one(".cate") else ""
    fee = normalize_space(title_box.select_one("[class*='program_ptype']").get_text(" ", strip=True)) if title_box and title_box.select_one("[class*='program_ptype']") else ""
    title_text = normalize_space(title_box.get_text(" ", strip=True) if title_box else link.get_text(" ", strip=True))
    if category:
        title_text = normalize_space(title_text.replace(f"[{category}]", ""))
    if fee:
        title_text = normalize_space(title_text.replace(fee, ""))
    info: dict[str, str] = {}
    for p in cont.select(".txt p"):
        text = normalize_space(p.get_text(" ", strip=True))
        if ":" in text:
            key, value = text.split(":", 1)
            info[normalize_space(key)] = normalize_space(value)
    status = normalize_space(link.select_one(".btn span").get_text(" ", strip=True) if link.select_one(".btn span") else "")
    period = normalize_period(info.get("진행기간"))
    target = normalize_space(info.get("대상"))
    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "title": title_text,
        "branch": DEFAULT_BRANCH,
        "branch_code": DEFAULT_BRANCH,
        "address": DEFAULT_ADDRESS,
        "venue": DEFAULT_BRANCH,
        "period": period,
        "schedule_raw": period,
        "target": target,
        "age_group": infer_age_group(target),
        "fee": fee,
        "status": status,
        "category": infer_category(category, title_text),
        "collection_category": "평생학습",
        "domain_category": "평생학습",
        "raw_url": raw_url,
        "image_url": "",
        "description": normalize_space(link.get_text(" ", strip=True)),
        "raw_fields": {
            "parser": "jeonju_lifelong_program23",
            "program_id": parse_program_id(raw_url),
            "gc": GC,
            "category_text": category,
            "application_period": normalize_period(info.get("신청기간")),
            "capacity": normalize_space(info.get("정원")),
            "eligibility": normalize_space(info.get("신청가능")),
        },
    }


def parse_list(soup: BeautifulSoup) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in soup.select("ul.class_list_wrap > li"):
        row = parse_list_item(item)
        if row:
            rows.append(row)
    return rows


def extract_description(soup: BeautifulSoup) -> str:
    box = soup.select_one(".program_viewbox .cont")
    if not box:
        return ""
    lines = [normalize_space(line) for line in box.get_text("\n", strip=True).splitlines()]
    return "\n".join(line for line in lines if line)


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    soup = fetch_soup(session, str(row["raw_url"]), timeout)
    row = dict(row)
    wrap = soup.select_one(".class_view_wrap")
    pairs = dl_pairs(wrap)
    title_node = wrap.select_one(".tit strong") if wrap else None
    status_node = wrap.select_one(".tit span") if wrap else None
    if title_node:
        row["title"] = normalize_space(title_node.get_text(" ", strip=True)) or row["title"]
    if status_node:
        row["status"] = normalize_space(status_node.get_text(" ", strip=True)) or row["status"]
    category = normalize_space(pairs.get("강좌분류"))
    target = normalize_space(pairs.get("대상"))
    if category:
        row["category"] = infer_category(category, row["title"])
        row.setdefault("raw_fields", {})["category_text"] = category
    if target:
        row["target"] = target
        row["age_group"] = infer_age_group(target)
    row["schedule_raw"] = normalize_space(pairs.get("강의일시")) or row.get("schedule_raw", "")
    row["period"] = normalize_period(pairs.get("진행기간")) or row.get("period", "")
    row["fee"] = normalize_space(pairs.get("수강료")) or row.get("fee", "")
    row["address"] = normalize_space(re.sub(r"^\[[^\]]+\]\s*", "", pairs.get("교육장 주소", ""))) or row.get("address", "")
    row["contact"] = normalize_space(pairs.get("문의"))
    row["description"] = extract_description(soup) or row.get("description", "")
    row["teacher"] = ""
    row.setdefault("raw_fields", {})["detail_pairs"] = pairs
    row["raw_fields"]["application_period"] = normalize_period(pairs.get("신청기간")) or row["raw_fields"].get("application_period", "")
    row["raw_fields"]["capacity"] = normalize_space(pairs.get("정원")) or row["raw_fields"].get("capacity", "")
    row["raw_fields"]["class_duration"] = normalize_space(pairs.get("강의기간"))
    row["raw_fields"]["age_restriction"] = normalize_space(pairs.get("연령제한"))
    return row


def is_expired_course(row: dict[str, Any]) -> bool:
    _, end_date = parse_date_range(row.get("period"))
    if end_date is None:
        return False
    return end_date < datetime.now().date()


def stable_course_id(row: dict[str, Any]) -> str:
    raw_fields = row.get("raw_fields") if isinstance(row.get("raw_fields"), dict) else {}
    program_id = normalize_space(raw_fields.get("program_id") if raw_fields else "")
    if program_id:
        return program_id
    seed = "|".join([PROVIDER, normalize_space(row.get("title")), normalize_space(row.get("raw_url"))])
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def collect(limit: int | None = None, max_pages: int = 1, timeout: int = 20) -> list[dict[str, Any]]:
    session = make_session()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _page in range(1, max_pages + 1):
        soup = fetch_soup(session, LIST_URL, timeout)
        page_rows = parse_list(soup)
        if not page_rows:
            break
        for base_row in page_rows:
            course_id = stable_course_id(base_row)
            if course_id in seen:
                continue
            seen.add(course_id)
            try:
                row = enrich_detail(session, base_row, timeout)
            except Exception as exc:
                logger.warning("Jeonju detail failed %s: %s", base_row.get("raw_url"), exc)
                row = base_row
            if is_expired_course(row):
                logger.info("Skipping expired Jeonju course: %s / %s", row.get("title"), row.get("period"))
                continue
            rows.append(row)
            if limit is not None and len(rows) >= limit:
                return rows
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
    parser = argparse.ArgumentParser(description="Jeonju lifelong learning Program23 crawler")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-pages", type=int, default=1)
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
