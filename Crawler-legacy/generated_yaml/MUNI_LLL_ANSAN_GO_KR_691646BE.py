from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib3
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag


PROVIDER = "MUNI_LLL_ANSAN_GO_KR_691646BE"
PROVIDER_NAME = "안산시평생학습관"
BASE_URL = "https://lll.ansan.go.kr"
DEFAULT_BRANCH = "안산시평생학습관"
DEFAULT_ADDRESS = "경기도 안산시 상록구 차돌배기로 24-1"
DEFAULT_PHONE = "031-409-1877"


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from DB.db_utils import get_db_cursor  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, extract_krw_amount, extract_material_fee_amount, setup_logger  # noqa: E402


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = setup_logger("Crawler_AnsanLifelongLearning")


COURSE_SECTIONS = [
    {
        "name": "피움과정",
        "list_path": "/web/cop/regEduList.do",
        "detail_path": "/web/cop/regEduDetail.do",
        "id_field": "mId",
        "id_prefix": "EDUMNG_",
        "category_prefix": "피움과정",
    },
    {
        "name": "다채움",
        "list_path": "/web/cop/mulEduList.do",
        "detail_path": "/web/cop/mulEduDetail.do",
        "id_field": "nId",
        "id_prefix": "MULEDU_",
        "category_prefix": "다채움",
    },
    {
        "name": "길거리학습관/아파트학습관",
        "list_path": "/web/cop/roadEduList.do",
        "detail_path": "/web/cop/roadEduDetail.do",
        "id_field": "nId",
        "id_prefix": "ROADMEDU_",
        "category_prefix": "길거리학습관",
    },
    {
        "name": "특별교육",
        "list_path": "/web/cop/norEduList.do",
        "detail_path": "/web/cop/norEduDetail.do",
        "id_field": "nId",
        "id_prefix": "NOREDU_",
        "category_prefix": "특별교육",
    },
]


def make_session() -> requests.Session:
    session = requests.Session()
    session.verify = False
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "identity",
        }
    )
    return session


def normalize_space(value: Any) -> str:
    text = clean_text(value).replace("\xa0", " ")
    return clean_text(re.sub(r"\s+", " ", text))


def stable_id(*parts: Any) -> str:
    seed = "|".join(normalize_space(part) for part in parts if normalize_space(part))
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def branch_code(branch: Any) -> str:
    return stable_id(PROVIDER, normalize_space(branch) or DEFAULT_BRANCH)[:12]


def normalize_date_text(value: Any) -> str:
    text = normalize_space(value)
    text = re.sub(
        r"(\d{4})[./](\d{1,2})[./](\d{1,2})",
        lambda m: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}",
        text,
    )
    text = re.sub(r"\s*~\s*", " ~ ", text)
    text = re.sub(r"\s+", " ", text)
    return normalize_space(text)


def normalize_status(*values: Any) -> str:
    text = normalize_space(" ".join(str(value or "") for value in values))
    if any(token in text for token in ["접수중", "수강신청", "교육접수중"]):
        return "OPEN"
    if any(token in text for token in ["예정", "대기"]):
        return "SCHEDULED"
    if any(token in text for token in ["마감", "종료", "교육진행중", "신청마감"]):
        return "CLOSED"
    return "OPEN"


def is_expired_period(period: str) -> bool:
    _start, end = parse_date_range(period)
    return bool(end and end < date.today())


def fetch_soup(session: requests.Session, url: str, *, params: dict[str, str] | None = None, timeout: int = 30) -> BeautifulSoup:
    response = session.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def list_url(section: dict[str, str], page: int) -> str:
    return urljoin(BASE_URL, section["list_path"])


def extract_course_id(href: str, prefix: str) -> str:
    match = re.search(r"fn_go_detail\('([^']+)'\)", href or "")
    if not match:
        return ""
    course_id = match.group(1)
    return course_id if course_id.startswith(prefix) else ""


def extract_label_pairs(scope: Tag) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for li in scope.select("li"):
        key_el = li.find("strong")
        value_el = li.find("span")
        if not key_el or not value_el:
            continue
        key = normalize_space(normalize_space(key_el.get_text(" ", strip=True)).rstrip(":"))
        value = normalize_space(value_el.get_text(" ", strip=True))
        if key:
            pairs[key] = value
    return pairs


def extract_capacity(card: Tag) -> tuple[int | None, int | None]:
    current = total = None
    for li in card.select(".edu_status li"):
        label = normalize_space(li.select_one(".f").get_text(" ", strip=True) if li.select_one(".f") else "")
        value_text = normalize_space(li.select_one(".s").get_text(" ", strip=True) if li.select_one(".s") else "")
        nums = [int(num.replace(",", "")) for num in re.findall(r"\d[\d,]*", value_text)]
        if not nums:
            continue
        if "신청" in label:
            current = nums[0]
        elif "정원" in label:
            total = nums[0]
    return current, total


def split_bracket_title(title: str) -> tuple[str, str]:
    text = normalize_space(title)
    match = re.match(r"^\[([^\]]+)\]\s*(.+)$", text)
    if match:
        return normalize_space(match.group(1)), normalize_space(match.group(2))
    return "", text


def line_after(lines: list[str], label: str) -> str:
    for idx, line in enumerate(lines):
        if line == label and idx + 1 < len(lines):
            return normalize_space(lines[idx + 1])
    return ""


def multi_line_after(lines: list[str], label: str) -> str:
    for idx, line in enumerate(lines):
        if line == label:
            values: list[str] = []
            for nxt in lines[idx + 1 : idx + 5]:
                if nxt in {"교육기간", "교육대상", "강의장", "수강료", "재료비", "강의계획서", "강사정보", "목록"}:
                    break
                values.append(nxt)
            return normalize_space(" ".join(values))
    return ""


def extract_description(lines: list[str]) -> str:
    start = -1
    for marker in ["강의계획서", "없음"]:
        try:
            start = max(start, lines.index(marker) + 1)
        except ValueError:
            pass
    if start < 0:
        for idx, line in enumerate(lines):
            if line in {"수강료", "재료비"}:
                start = idx + 2
    if start < 0:
        return ""
    end = len(lines)
    for marker in ["강사정보", "목록", "수강신청"]:
        if marker in lines[start:]:
            end = min(end, start + lines[start:].index(marker))
    return "\n".join(lines[start:end]).strip()


def extract_material_note(description: str) -> str:
    lines = [normalize_space(line) for line in re.split(r"[\r\n]+", description) if normalize_space(line)]
    matches = [line for line in lines if re.search(r"준비물|재료|재료비|실습비|교재비", line)]
    return "\n".join(matches[:5])


def parse_detail(session: requests.Session, section: dict[str, str], course_id: str) -> dict[str, Any]:
    url = urljoin(BASE_URL, section["detail_path"])
    soup = fetch_soup(session, url, params={section["id_field"]: course_id})
    lines = [normalize_space(line) for line in soup.get_text("\n", strip=True).splitlines() if normalize_space(line)]
    period = normalize_date_text(multi_line_after(lines, "교육기간"))
    target = line_after(lines, "교육대상")
    place = line_after(lines, "강의장") or line_after(lines, "장소")
    fee = line_after(lines, "수강료")
    material_note_value = line_after(lines, "재료비")
    description = extract_description(lines)
    material_note = "\n".join(part for part in [material_note_value, extract_material_note(description)] if normalize_space(part))
    return {
        "period": period,
        "target": target,
        "place": place,
        "fee": fee,
        "material_note": material_note,
        "material_fee": extract_material_fee_amount(material_note or description),
        "description": description,
        "raw_url": f"{url}?{section['id_field']}={course_id}",
    }


def parse_card(session: requests.Session, section: dict[str, str], card: Tag) -> dict[str, Any] | None:
    link = card.select_one("a[href*='fn_go_detail']")
    if not link:
        return None
    course_id = extract_course_id(link.get("href", ""), section["id_prefix"])
    if not course_id:
        return None

    category_box = normalize_space(card.select_one(".fix_box").get_text(" ", strip=True) if card.select_one(".fix_box") else "")
    status_text = normalize_space(card.select_one(".cate").get_text(" ", strip=True) if card.select_one(".cate") else "")
    raw_title = normalize_space(link.get_text(" ", strip=True))
    title_category, title = split_bracket_title(raw_title)
    pairs = extract_label_pairs(card)
    current, total = extract_capacity(card)

    list_period = normalize_date_text(pairs.get("교육기간"))
    day = normalize_space(pairs.get("수강일"))
    time = normalize_date_text(pairs.get("시간"))
    schedule_raw = normalize_space(" ".join(part for part in [list_period, day, time] if part))

    detail: dict[str, Any] = {}
    try:
        detail = parse_detail(session, section, course_id)
    except Exception as exc:  # pragma: no cover - network fallback
        logger.warning("Detail fetch failed %s %s: %s", section["name"], course_id, exc)

    period = detail.get("period") or list_period
    if is_expired_period(period):
        return None

    place = normalize_space(detail.get("place") or pairs.get("장소"))
    is_road = section["name"] == "길거리학습관/아파트학습관"
    branch = place if is_road and place else DEFAULT_BRANCH
    category_parts = [section["category_prefix"], category_box, title_category]
    category_raw = " > ".join(dict.fromkeys(part for part in category_parts if normalize_space(part)))

    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": course_id,
        "provider_course_id": course_id,
        "title": title,
        "branch": branch,
        "branch_code": branch_code(branch),
        "address": DEFAULT_ADDRESS if branch == DEFAULT_BRANCH else place,
        "phone": DEFAULT_PHONE,
        "period": period,
        "schedule_raw": schedule_raw,
        "target": detail.get("target") or pairs.get("수강대상자") or "안산시민",
        "category_raw": category_raw,
        "fee": detail.get("fee") or "",
        "material_fee": detail.get("material_fee"),
        "material_note": detail.get("material_note") or "",
        "status": normalize_status(status_text),
        "raw_url": detail.get("raw_url") or f"{urljoin(BASE_URL, section['detail_path'])}?{section['id_field']}={course_id}",
        "application_url": f"{urljoin(BASE_URL, section['detail_path'])}?{section['id_field']}={course_id}",
        "description": detail.get("description") or "\n".join([raw_title, *[f"{k}: {v}" for k, v in pairs.items()]]),
        "image_url": "",
        "instructor": pairs.get("강사명") or "",
        "capacity_current": current,
        "capacity_total": total,
        "collection_category": "평생학습",
        "domain_category": "평생학습",
        "source_group": "lifelong_learning",
    }


def collect_courses(limit: int | None = None, max_pages: int = 30, timeout: int = 30) -> list[dict[str, Any]]:
    session = make_session()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for section in COURSE_SECTIONS:
        empty_pages = 0
        for page in range(1, max_pages + 1):
            params = {"pageIndex": str(page), "pageUnit": "100"}
            soup = fetch_soup(session, list_url(section, page), params=params, timeout=timeout)
            cards = soup.select(".list-board .board_section")
            if not cards:
                empty_pages += 1
                if empty_pages >= 2:
                    break
                continue
            new_on_page = 0
            for card in cards:
                row = parse_card(session, section, card)
                if not row:
                    continue
                key = normalize_space(row.get("provider_course_id"))
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)
                new_on_page += 1
                if limit and len(rows) >= limit:
                    return rows
            if new_on_page == 0:
                break
    return rows


def quality_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fields = [
        "title",
        "branch",
        "address",
        "period",
        "schedule_raw",
        "target",
        "fee",
        "status",
        "description",
        "raw_url",
        "instructor",
    ]
    counts = {field: sum(1 for row in rows if normalize_space(row.get(field))) for field in fields}
    score = round((sum(counts.values()) / max(1, len(rows) * len(fields))) * 100, 1)
    grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D"
    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "collected": len(rows),
        "score": score,
        "grade": grade,
        "field_counts": counts,
    }


class AnsanLifelongCrawler(YamlSourceCrawler):
    def __init__(self) -> None:
        self.provider = PROVIDER
        self.target_parser = TargetParser()
        self.schedule_parser = ScheduleParser()

    def save_branch_with_address(self, row: dict[str, Any]) -> str | None:
        branch = {
            "provider": PROVIDER,
            "branch_code": normalize_space(row.get("branch_code"))[:50] or PROVIDER,
            "name": normalize_space(row.get("branch"))[:100] or DEFAULT_BRANCH,
            "address": normalize_space(row.get("address"))[:255],
            "phone": normalize_space(row.get("phone"))[:50],
        }
        with get_db_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO branches (provider, branch_code, name, address, phone)
                VALUES (%(provider)s, %(branch_code)s, %(name)s, %(address)s, %(phone)s)
                ON CONFLICT (provider, branch_code)
                DO UPDATE SET
                    name = EXCLUDED.name,
                    address = COALESCE(NULLIF(EXCLUDED.address, ''), branches.address),
                    phone = COALESCE(NULLIF(EXCLUDED.phone, ''), branches.phone),
                    updated_at = CURRENT_TIMESTAMP
                RETURNING id
                """,
                branch,
            )
            return str(cursor.fetchone()["id"])

    def save_rows(self, rows: list[dict[str, Any]]) -> int:
        saved = 0
        for row in rows:
            branch_id = self.save_branch_with_address(row)
            if not branch_id:
                continue
            course = self.normalize_course(row, branch_id)
            if self.save_course(course):
                saved += 1
        return saved


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect Ansan Lifelong Learning Center courses")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-pages", type=int, default=30)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--per-target-limit", type=int, default=None)
    parser.add_argument("--max-depth", type=int, default=None)
    parser.add_argument("--detail-limit", type=int, default=None)
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    limit = args.limit or args.per_target_limit
    rows = collect_courses(limit=limit, max_pages=args.max_pages, timeout=args.timeout)
    report = quality_report(rows)
    if args.save_db:
        report["saved"] = AnsanLifelongCrawler().save_rows(rows)

    if args.json:
        print(json.dumps({"report": report, "rows": rows[: args.limit or 20]}, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
