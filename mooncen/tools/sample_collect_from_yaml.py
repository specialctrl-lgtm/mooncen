from __future__ import annotations

import argparse
import contextvars
import json
import math
import re
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "logs" / "crawler_samples"
REGISTRY = ROOT / "config" / "crawler_targets.yaml"

if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from data_parser import TargetParser
from target_category_fallback import infer_age_group_from_category
from target_cleaner import extract_target_text
from tools.standard_category_mapper import classify_standard_category
from utils.outbound_http import SafeSession, outbound_request_budget


TARGET_PARSER = TargetParser()
MAX_PROVIDER_PAGES = 100
DEFAULT_COLLECTOR_REQUEST_BUDGET = 200
MAX_COLLECTOR_REQUEST_BUDGET = 3_000
PROVIDER_COLLECTOR_REQUEST_BUDGETS = {
    # Hyundai exposes roughly 7,900 rows over 220 list pages. Required fields
    # are present in the list, so only a bounded sample needs detail enrichment.
    "HYUNDAI_DEPT": 400,
    # AK's list API provides all required fields. Detail descriptions are
    # sampled under a separate cap.
    "AK_PLAZA": 400,
    # Shinsegae currently exposes roughly 9,000 rows over about 900 API pages.
    # Detail enrichment is separately capped, keeping a full snapshot bounded.
    "SHINSEGAE_ACADEMY": 1_500,
    # Galleria exposes roughly 1,500 rows over 130 list pages and requires one
    # detail request per row for room, audience, category, and description.
    "GALLERIA": 2_200,
    # Eland accepts the current catalogue in one large list response. Required
    # fields are present there, and only a bounded sample needs detail requests.
    "ELAND_RETAIL": 300,
    # Lotte Mart exposes about 26,000 current-term rows over roughly 1,350 list
    # pages. List cards contain the required fields; detail enrichment is capped.
    "LOTTE_MART": 3_000,
}
_ACTIVE_COLLECTOR_SESSIONS: contextvars.ContextVar[list[SafeSession] | None] = contextvars.ContextVar(
    "mooncen_yaml_collector_sessions",
    default=None,
)


@dataclass
class SampleReport:
    provider: str
    requested: int
    collected: int = 0
    pages: int = 0
    success: bool = False
    output: str = ""
    fields: dict[str, int] = field(default_factory=dict)
    note: str = ""
    error: str = ""


def session() -> SafeSession:
    s = SafeSession(max_response_bytes=8 * 1024 * 1024, total_timeout_seconds=60)
    s.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0 Safari/537.36",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    active = _ACTIVE_COLLECTOR_SESSIONS.get()
    if active is not None:
        active.append(s)
    return s


@contextmanager
def managed_collector_sessions(request_budget: int = DEFAULT_COLLECTOR_REQUEST_BUDGET):
    if not 1 <= request_budget <= MAX_COLLECTOR_REQUEST_BUDGET:
        raise ValueError(f"request_budget must be between 1 and {MAX_COLLECTOR_REQUEST_BUDGET}")
    sessions: list[SafeSession] = []
    token = _ACTIVE_COLLECTOR_SESSIONS.set(sessions)
    try:
        with outbound_request_budget(request_budget):
            yield
    finally:
        try:
            for session_obj in reversed(sessions):
                try:
                    session_obj.close()
                except Exception:
                    pass
        finally:
            _ACTIVE_COLLECTOR_SESSIONS.reset(token)


def clean(text: str | None) -> str:
    return " ".join((text or "").split())


def write_samples(provider: str, rows: list[dict[str, Any]]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{provider.lower()}_sample_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def score_fields(rows: list[dict[str, Any]]) -> dict[str, int]:
    keys = [
        "title",
        "branch",
        "raw_url",
        "status",
        "fee",
        "schedule_raw",
        "period",
        "instructor",
        "target",
        "target_age_group",
        "target_min_age",
        "target_max_age",
        "image_url",
        "description",
        "material_fee",
        "material_note",
        "category",
        "category_raw",
    ]
    def present(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (list, tuple, set, dict)):
            return bool(value)
        return True

    return {key: sum(1 for row in rows if present(row.get(key))) for key in keys}


def target_fields_from_title(title: str) -> dict[str, Any]:
    explicit_target = extract_target_text(title)
    if not explicit_target:
        return {}

    parsed = TARGET_PARSER.parse(f"{explicit_target} {title}")
    return {
        "target": explicit_target,
        "target_age_group": parsed["age_group"],
        "target_min_age": parsed["min_age"],
        "target_max_age": parsed["max_age"],
        "target_with_parent": parsed["with_parent"],
        "target_tags": parsed["tags"],
    }


def extract_age_target_from_text(value: str) -> str:
    text = clean(value)
    patterns = [
        r"\(([^)]*(?:\uac1c\uc6d4|\ub144\uc0dd|\uc138|\ucd08\ub4f1|\uc911\ub4f1|\uace0\ub4f1)[^)]*)\)",
        r"(\d{1,3}\s*[~-]\s*\d{1,3}\s*\uac1c\uc6d4)",
        r"(\d{1,3}\s*\uac1c\uc6d4\s*(?:\uc774\uc0c1|\uc774\ud558|\ubd80\ud130|\uae4c\uc9c0)?)",
        r"(\d{2,4}\s*[~-]\s*\d{2,4}\s*\ub144\uc0dd)",
        r"(\d{1,2}\s*[~-]\s*\d{1,2}\s*\uc138)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return clean(match.group(1))
    return ""


def target_fields_from_text(value: str) -> dict[str, Any]:
    explicit_target = extract_age_target_from_text(value)
    if not explicit_target:
        return {}
    parsed = TARGET_PARSER.parse(explicit_target)
    return {
        "target": explicit_target,
        "target_age_group": parsed["age_group"],
        "target_min_age": parsed["min_age"],
        "target_max_age": parsed["max_age"],
        "target_with_parent": parsed["with_parent"],
        "target_tags": parsed["tags"],
    }


def detail_value_by_dt(soup: BeautifulSoup, label: str) -> str:
    for dt in soup.find_all("dt"):
        if label in clean(dt.get_text(" ", strip=True)):
            dd = dt.find_next("dd")
            return clean(dd.get_text(" ", strip=True)) if dd else ""
    return ""


def extract_section_after_heading(soup: BeautifulSoup, selector: str, heading_text: str) -> str:
    for heading in soup.select(selector):
        if clean(heading.get_text(" ", strip=True)) != heading_text:
            continue

        parent = heading.parent
        if parent:
            parent_text = clean(parent.get_text(" ", strip=True))
            if parent_text.startswith(heading_text):
                parent_text = clean(parent_text[len(heading_text) :])
            if parent_text:
                return parent_text

        values = []
        for sibling in heading.find_next_siblings():
            if sibling.name in {"h1", "h2", "h3"}:
                break
            text = clean(sibling.get_text(" ", strip=True))
            if text:
                values.append(text)
        return clean(" ".join(values))
    return ""


def extract_material_note(text: str) -> str:
    if not text:
        return ""

    keywords = ("준비물", "재료", "재료비", "교재비")
    chunks = re.split(r"(?<=[.!?。])\s+|[\r\n]+|(?=\s*[※■*\-]\s*)", text)
    notes = [clean(chunk) for chunk in chunks if any(keyword in chunk for keyword in keywords)]
    return clean(" ".join(notes))


def extract_material_fee_text(*values: str) -> str:
    combined = clean(" ".join(value for value in values if value))
    if not combined or not any(keyword in combined for keyword in ("재료", "재료비", "교재비")):
        return ""

    match = re.search(r"(?:재료비|교재비|재료)[^0-9]{0,20}([\d,]+\s*원)", combined)
    return clean(match.group(1)) if match else ""


def extract_image_url(soup: BeautifulSoup, base_url: str, selectors: list[str]) -> str:
    for selector in selectors:
        node = soup.select_one(selector)
        if not node:
            continue
        image_url = clean(node.get("src") or node.get("data-src") or node.get("content"))
        if image_url:
            normalized = urljoin(base_url, image_url)
            return re.sub(r"(?<!:)//+", "/", normalized)
    return ""


HYUNDAI_BASE_URL = "https://www.ehyundai.com"
HYUNDAI_LIST_URL = f"{HYUNDAI_BASE_URL}/newCulture/CT/CT010100_L.do"
HYUNDAI_ALL_BRANCH_CODE = "ALL"
HYUNDAI_MAX_PAGES = 300
HYUNDAI_DETAIL_LIMIT = 100


def hyundai_table_values(soup: BeautifulSoup) -> dict[str, str]:
    values: dict[str, str] = {}
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = tr.find_all(["th", "td"], recursive=False)
            index = 0
            while index < len(cells) - 1:
                if cells[index].name == "th" and cells[index + 1].name == "td":
                    key = clean(cells[index].get_text(" ", strip=True))
                    value = clean(cells[index + 1].get_text(" ", strip=True))
                    if key and value and key not in values:
                        values[key] = value
                    index += 2
                else:
                    index += 1
    return values


def hyundai_provider_course_id(raw_url: str) -> str:
    match = re.search(r"[?&](stCd|sqCd|crsSqNo|crsCd)=([^&]+)", raw_url)
    if not match:
        return raw_url.rstrip("/").split("/")[-1][:100]
    parts = re.findall(r"[?&](stCd|sqCd|crsSqNo|crsCd)=([^&]+)", raw_url)
    return ":".join(value for _, value in parts)[:100]


def hyundai_list_params(page: int) -> dict[str, str]:
    # Same state as selecting "모든지점" in the custom ui-select control and
    # clicking the search button on CT010100_L.do.
    return {
        "page": str(page),
        "stCd": HYUNDAI_ALL_BRANCH_CODE,
        "keyword": "",
        "nickCrsNm": "",
        "timeCntGubn": "",
        "applyGubn": "",
        "yearGubnSta": "2026",
        "yearGubnEnd": "2027",
        "monthGubnSta": "05",
        "monthGubnEnd": "12",
        "dayGubnSta": "01",
        "dayGubnEnd": "31",
        "timeGubnSta": "",
        "timeGubnEnd": "",
        "day": "",
        "upCrsTy2": "",
        "partnerQuotaGubn": "",
        "orderGubn": "status",
        "pageSize": "36",
        "detailSearch": "",
        "ctGubn": "",
        "promCrsKind": "all",
    }


def hyundai_list_rows(soup: BeautifulSoup) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for anchor in soup.select('a[href*="CT010100_V.do"]'):
        node = anchor.find_parent("li") or anchor
        raw_url = urljoin(HYUNDAI_BASE_URL, anchor.get("href", ""))
        title_node = node.select_one("dt")
        status_node = node.select_one(".branch_info .state")
        category_node = node.select_one(".branch_info .etc")
        image_url = extract_image_url(BeautifulSoup(str(node), "lxml"), HYUNDAI_BASE_URL, ["img"])

        info_nodes = [clean(item.get_text(" ", strip=True)) for item in node.select(".class_info .info")]
        branch = ""
        sessions: int | str = ""
        instructor = ""
        period = ""
        schedule_raw = ""
        if info_nodes:
            branch_match = re.search(r"\[([^\]]+)\]", info_nodes[0])
            branch = clean(branch_match.group(1)) if branch_match else ""
            sessions_match = re.search(r"(\d+\s*회)", info_nodes[0])
            sessions = int(sessions_match.group(1).replace("회", "").strip()) if sessions_match else ""
            spans = node.select(".class_info .info:first-child span")
            if len(spans) >= 2:
                instructor = clean(spans[1].get_text(" ", strip=True))
        if len(info_nodes) >= 2:
            period = info_nodes[1]
        if len(info_nodes) >= 3:
            schedule_raw = info_nodes[2]

        fee_node = node.select_one(".price")
        fee = clean(fee_node.get_text(" ", strip=True)) if fee_node else ""
        query_codes = dict(re.findall(r"[?&](stCd|sqCd|crsSqNo|crsCd)=([^&]+)", raw_url))
        row = {
            "provider": "HYUNDAI_DEPT",
            "provider_course_id": hyundai_provider_course_id(raw_url),
            "title": clean(title_node.get_text(" ", strip=True) if title_node else anchor.get_text(" ", strip=True)),
            "branch": branch,
            "branch_code": query_codes.get("stCd", branch),
            "raw_url": raw_url,
            "status": clean(status_node.get_text(" ", strip=True) if status_node else ""),
            "target": clean(category_node.get_text(" ", strip=True) if category_node else ""),
            "category": clean(category_node.get_text(" ", strip=True) if category_node else ""),
            "period": period,
            "schedule_raw": schedule_raw,
            "instructor": instructor,
            "sessions": sessions,
            "fee": fee,
            "image_url": image_url,
        }
        row.update(target_fields_from_title(row["title"]))
        if category_node and not row.get("category"):
            row["category"] = clean(category_node.get_text(" ", strip=True))
        rows.append(row)
    return rows


def hyundai_detail_fields(session_obj: requests.Session, raw_url: str) -> dict[str, Any]:
    if not raw_url:
        return {}

    response = session_obj.get(raw_url, timeout=20)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "lxml")
    values = hyundai_table_values(soup)

    description = values.get("클래스 소개", "")
    material_note = clean(" ".join(values.get(key, "") for key in ("준비물", "교재정보", "유의사항") if values.get(key)))
    material_fee = values.get("재료비", "") or extract_material_fee_text(material_note, description)
    image_url = extract_image_url(
        soup,
        raw_url,
        [
            'img[src*="imgprism.ehyundai.com"]',
            'img[src*="/derivedImage/"]',
        ],
    )
    schedule_raw = values.get("강의 일시", "")
    room_match = re.search(r"\(([^)]*(?:층|강의실|스튜디오|플레이|룸)[^)]*)\)", schedule_raw)
    sessions_match = re.search(r"(\d+)", values.get("강의 횟수", ""))

    return {
        "branch": values.get("지점명", ""),
        "instructor": values.get("강사명", ""),
        "schedule_raw": schedule_raw,
        "period": values.get("강의 기간", ""),
        "sessions": int(sessions_match.group(1)) if sessions_match else "",
        "fee": values.get("수강료", ""),
        "material_fee": material_fee,
        "material_note": material_note,
        "room": clean(room_match.group(1)) if room_match else "",
        "description": description,
        "image_url": image_url,
    }


def hyundai(limit: int) -> tuple[list[dict[str, Any]], int, str]:
    s = session()
    s.headers.update({"Referer": HYUNDAI_LIST_URL})
    rows: list[dict[str, Any]] = []
    pages = 0
    seen: set[str] = set()
    detail_attempts = 0
    detail_failures = 0
    total_pages = 1
    page_cap_reached = False

    page = 1
    while len(rows) < limit and page <= total_pages:
        response = s.get(HYUNDAI_LIST_URL, params=hyundai_list_params(page), timeout=20)
        response.raise_for_status()
        pages += 1
        soup = BeautifulSoup(response.text, "lxml")
        page_rows = hyundai_list_rows(soup)
        if not page_rows:
            break

        paging_html = str(soup.select_one(".paging") or "")
        page_numbers = [int(value) for value in re.findall(r"[?&]page=(\d+)", paging_html)]
        if page_numbers:
            source_total_pages = max(page_numbers)
            page_cap_reached = page_cap_reached or source_total_pages > HYUNDAI_MAX_PAGES
            total_pages = min(max(total_pages, source_total_pages), HYUNDAI_MAX_PAGES)

        for row in page_rows:
            key = row.get("provider_course_id") or row.get("raw_url")
            if not key or key in seen:
                continue
            seen.add(str(key))
            detail: dict[str, Any] = {}
            if detail_attempts < HYUNDAI_DETAIL_LIMIT:
                detail_attempts += 1
                try:
                    detail = hyundai_detail_fields(s, row.get("raw_url", ""))
                except requests.RequestException as exc:
                    detail_failures += 1
                    row["detail_error"] = f"{type(exc).__name__}: {exc}"
            for field_name in (
                "branch",
                "instructor",
                "schedule_raw",
                "period",
                "sessions",
                "fee",
                "material_fee",
                "material_note",
                "room",
                "description",
                "image_url",
            ):
                if detail.get(field_name):
                    row[field_name] = detail[field_name]
            rows.append(row)
            if len(rows) >= limit:
                break
        page += 1

    note = (
        f"pages_seen={pages}; total_pages_hint={total_pages}; "
        f"detail_attempts={detail_attempts}; "
        f"snapshot_complete={str(pages == total_pages and not page_cap_reached).lower()}"
    )
    if detail_failures:
        note += f"; detail_failures={detail_failures}"
    return rows, pages, note


def value_after_line(lines: list[str], label: str, stop_labels: set[str] | None = None, max_values: int = 8) -> str:
    stop_labels = stop_labels or set()
    for index, line in enumerate(lines):
        if line == label:
            values = []
            for value in lines[index + 1 :]:
                if value in stop_labels:
                    break
                values.append(value)
                if len(values) >= max_values:
                    break
            return clean(" ".join(values))
    return ""


def detail_lines(session_obj: requests.Session, url: str) -> list[str]:
    response = session_obj.get(url, timeout=20)
    soup = BeautifulSoup(response.text, "lxml")
    lines = [clean(line) for line in soup.get_text("\n", strip=True).splitlines()]
    return [line for line in lines if line]


SHINSEGAE_STORES = [
    {"code": "ON", "name": "신세계 온 아카데미"},
    {"code": "01", "name": "본점"},
    {"code": "03", "name": "타임스퀘어 & ON"},
    {"code": "14", "name": "강남점"},
    {"code": "15", "name": "마산점"},
    {"code": "16", "name": "신세계 사우스시티"},
    {"code": "18", "name": "센텀시티"},
    {"code": "19", "name": "의정부점"},
    {"code": "37", "name": "김해점"},
    {"code": "40", "name": "스타필드하남점"},
    {"code": "70", "name": "천안아산점"},
    {"code": "90", "name": "대구신세계"},
    {"code": "D1", "name": "대전신세계"},
]
SHINSEGAE_MAX_PAGES_PER_STORE = 200
SHINSEGAE_DETAIL_LIMIT = 100
CULTURE_CENTER_CATEGORY_CONFIG = str(
    ROOT / "config" / "culture_center_standard_categories.yaml"
)
SHINSEGAE_CATEGORY_CONFIG = CULTURE_CENTER_CATEGORY_CONFIG
SHINSEGAE_TARGET_LABELS = {
    "대중": "성인",
}


def shinsegae_provider_course_id(item: dict[str, Any], store_code: str) -> str:
    return ":".join(
        clean(str(value))
        for value in (
            item.get("yearCode"),
            item.get("smstCode"),
            item.get("storeCode") or store_code,
            item.get("lectCode"),
        )
        if clean(str(value))
    )[:100]


def shinsegae_total_pages(payload: dict[str, Any], item_count: int) -> int:
    params = payload.get("param")
    if not isinstance(params, dict):
        return 1 if item_count else 0
    try:
        total_count = max(0, int(params.get("totalCount") or 0))
        page_size = max(1, int(params.get("pageSize") or item_count or 10))
    except (TypeError, ValueError):
        return 1 if item_count else 0
    return math.ceil(total_count / page_size) if total_count else (1 if item_count else 0)


def shinsegae_category(title: str, description: str = "") -> str:
    result = classify_standard_category(
        {
            "title": title,
            "description": description,
            "collection_category": "문화센터",
        },
        SHINSEGAE_CATEGORY_CONFIG,
    )
    return result.label if result.key != "uncategorized" else "문화센터"


def shinsegae(limit: int) -> tuple[list[dict[str, Any]], int, str]:
    s = session()
    s.headers.update({"Referer": "https://sacademy.shinsegae.com/sdotcom/web/HP0010P0/HP0010P0.do"})
    rows: list[dict[str, Any]] = []
    pages = 0
    stores = SHINSEGAE_STORES
    unlimited = limit <= 0
    per_store_limit = 0 if unlimited else max(1, math.ceil(limit / len(stores)))
    url = "https://sacademy.shinsegae.com/sdotcom/web/HP0010P0/getLectList.do"
    seen_ids: set[str] = set()
    stores_with_rows = 0
    expected_rows = 0
    page_caps: list[str] = []
    detail_attempts = 0
    detail_failures = 0
    for store in stores:
        if not unlimited and len(rows) >= limit:
            break
        store_code = store["code"]
        store_name = store["name"]
        page = 1
        store_rows = 0
        total_pages: int | None = None
        while page <= SHINSEGAE_MAX_PAGES_PER_STORE:
            if not unlimited and (len(rows) >= limit or store_rows >= per_store_limit):
                break
            if total_pages is not None and page > total_pages:
                break
            data = {
                "storeCode": store_code,
                "curPage": str(page),
                "schSmstCode": "",
                "lectGrType": "",
                "lectGrCode": "",
                "rcptStat": "",
                "dayCode": "",
                "lectTimeCode": "",
                "targetCode": "",
                "ordKey": "",
                "lectName": "",
                "tchName": "",
                "srchCndCd": "",
                "srchWrd": "",
            }
            r = s.post(url, data=data, timeout=20)
            r.raise_for_status()
            pages += 1
            payload = r.json()
            if payload.get("result") != "SUCCESS":
                raise RuntimeError(f"Shinsegae list API failed for store={store_code} page={page}")
            items = payload.get("lectList") or []
            if total_pages is None:
                total_pages = shinsegae_total_pages(payload, len(items))
                params = payload.get("param") if isinstance(payload.get("param"), dict) else {}
                expected_rows += int(params.get("totalCount") or 0)
                if total_pages > SHINSEGAE_MAX_PAGES_PER_STORE:
                    page_caps.append(store_code)
            if not items:
                break
            for item in items:
                if not unlimited and (len(rows) >= limit or store_rows >= per_store_limit):
                    break
                provider_course_id = shinsegae_provider_course_id(item, store_code)
                if not provider_course_id or provider_course_id in seen_ids:
                    continue
                seen_ids.add(provider_course_id)
                raw_url = (
                    "https://sacademy.shinsegae.com/sdotcom/web/HP0010P0/HP0010P1.do"
                    f"?yearCode={item.get('yearCode')}&smstCode={item.get('smstCode')}"
                    f"&storeCode={item.get('storeCode')}&lectCode={item.get('lectCode')}#"
                )
                stop = {
                    "점포",
                    "강사명",
                    "수강기간",
                    "요일/시간",
                    "수강대상",
                    "강의실",
                    "수강료",
                    "교재/재료비",
                    "접수기간",
                    "강좌소개",
                    "수강 신청 및 취소 환불 안내",
                    "첫시간 안내사항",
                    "커리큘럼",
                    "강사정보",
                }
                details: list[str] = []
                image_url = ""
                if detail_attempts < SHINSEGAE_DETAIL_LIMIT:
                    detail_attempts += 1
                    try:
                        detail_response = s.get(raw_url, timeout=20)
                        detail_response.raise_for_status()
                        detail_soup = BeautifulSoup(detail_response.text, "lxml")
                        details = [clean(line) for line in detail_soup.get_text("\n", strip=True).splitlines()]
                        details = [line for line in details if line]
                        image_url = extract_image_url(
                            detail_soup,
                            raw_url,
                            [
                                ".slider-for.mb10 img",
                                ".slider-for img",
                                ".slider-nav img",
                            ],
                        )
                    except requests.RequestException:
                        detail_failures += 1
                        details = []
                title = clean(str(item.get("lectName") or ""))
                title_target_fields = target_fields_from_title(title)
                target = (
                    value_after_line(details, "수강대상", stop, 3)
                    or title_target_fields.get("target")
                    or SHINSEGAE_TARGET_LABELS.get(
                        clean(str(item.get("tlectTargetMemCodeName") or "")),
                        clean(str(item.get("tlectTargetMemCodeName") or "")),
                    )
                    or "전체"
                )
                description = value_after_line(details, "강좌소개", stop, 12)
                branch = (
                    value_after_line(details, "점포", stop, 2)
                    or clean(str(item.get("storeName") or ""))
                    or store_name
                )
                room = value_after_line(details, "강의실", stop, 3)
                rows.append(
                    {
                        "provider": "SHINSEGAE_ACADEMY",
                        "provider_course_id": provider_course_id,
                        "title": title,
                        "branch": branch,
                        "branch_code": clean(str(item.get("storeCode") or "")) or store_code,
                        "category": shinsegae_category(title, description),
                        "raw_url": raw_url,
                        "application_url": raw_url,
                        "status": item.get("lectStat"),
                        "fee": value_after_line(details, "수강료", stop, 4)
                        or item.get("lectAmtCurr")
                        or item.get("lectAmt"),
                        "schedule_raw": value_after_line(details, "요일/시간", stop, 3)
                        or clean(f"{item.get('dayCodeName') or ''} {item.get('lectHm') or ''}"),
                        "period": value_after_line(details, "수강기간", stop, 4)
                        or clean(str(item.get("lectPeriod") or "")),
                        "apply_period": clean(str(item.get("inetLectPeriod") or ""))
                        or value_after_line(details, "접수기간", stop, 4),
                        "sessions": item.get("lectCnt"),
                        "instructor": value_after_line(details, "강사명", stop, 2)
                        or clean(str(item.get("tchName") or "")),
                        "target": target,
                        "room": room,
                        "venue_name": room or branch,
                        "material_fee": value_after_line(details, "교재/재료비", stop, 4),
                        "image_url": image_url,
                        "description": description,
                        **title_target_fields,
                    }
                )
                store_rows += 1
            page += 1
        if store_rows:
            stores_with_rows += 1
    note = (
        f"stores={len(stores)}; stores_with_rows={stores_with_rows}; "
        f"expected_rows={expected_rows}; per_store_limit={per_store_limit or 'unlimited'}; "
        f"detail_attempts={detail_attempts}; detail_failures={detail_failures}; "
        f"snapshot_complete={str(len(rows) == expected_rows and not page_caps).lower()}"
    )
    if page_caps:
        note += f"; page_cap_stores={','.join(page_caps)}"
    return rows, pages, note


ELAND_LECTURE_TYPE_LABELS = {
    "REC": "추천강좌",
    "NEW": "신규강좌",
    "A": "성인",
    "B": "엄마랑아기랑",
    "C": "아동",
    "D": "초등",
    "E": "성인단기",
    "F": "아동단기",
    "G": "성인일일",
    "I": "기타",
    "J": "방학특강",
    "K": "중도수강",
    "L": "아동일일",
    "X": "미술관",
    "M": "엄마랑 아가랑 단기",
}
ELAND_LIST_PAGE_SIZE = 5_000
ELAND_DETAIL_LIMIT = 100


def eland_lecture_type_label(code: str | None) -> str:
    value = clean(code)
    if not value:
        return ""
    upper = value.upper()
    if upper in ELAND_LECTURE_TYPE_LABELS:
        return ELAND_LECTURE_TYPE_LABELS[upper]
    match = re.match(r"([A-Z]+)", upper)
    if match:
        return ELAND_LECTURE_TYPE_LABELS.get(match.group(1), "")
    return ""


def eland_target_from_category(category: str | None) -> str:
    value = clean(category)
    if value == "중도수강":
        return "일반"

    age_group = infer_age_group_from_category(category)
    return {
        "INFANT": "영아",
        "TODDLER": "유아",
        "CHILD": "아동",
        "TEEN": "청소년",
        "ADULT": "성인",
        "SENIOR": "시니어",
        "ALL": "전체",
    }.get(age_group or "", "")


def eland(limit: int) -> tuple[list[dict[str, Any]], int, str]:
    s = session()
    s.headers.update({"Referer": "https://www.elandretail.com/culture/culture07.do", "Content-Type": "application/json"})
    # Mirror the site workflow: open the search page, keep selStoreId as the
    # empty "지점전체" option, then request the AJAX list with StoreID="".
    s.get("https://www.elandretail.com/culture/culture07.do", timeout=20)
    rows: list[dict[str, Any]] = []
    pages = 0
    seen_ids: set[str] = set()
    detail_attempts = 0
    detail_failures = 0
    for page in range(1, 2):
        body = {
            "CurrentPage": page,
            "PageSize": ELAND_LIST_PAGE_SIZE,
            "StoreID": "",
            "LecTypeID": "",
            "WeekDay": "",
            "Teacher": "",
            "LectureName": "",
            "Status": "",
        }
        r = s.post("https://www.elandretail.com/culture/getLectureList.do", data=json.dumps(body), timeout=20)
        pages += 1
        soup = BeautifulSoup(r.text, "lxml")
        trs = soup.select("#tbodyList tr")
        found = 0
        for tr in trs:
            cells = [clean(td.get_text(" ", strip=True)) for td in tr.select("td")]
            if len(cells) < 8 or "조회정보가 없습니다" in " ".join(cells):
                continue
            onclick = ""
            link = tr.find(attrs={"onclick": True})
            if link:
                onclick = link.get("onclick", "")
            args = re.findall(r"'([^']*)'", onclick)
            raw_url = ""
            if len(args) >= 4:
                raw_url = (
                    "https://www.elandretail.com/culture/culture09.do"
                    f"?storeid={args[0]}&semnum={args[1]}&lectypeid={args[2]}&seq={args[3]}"
                )
            provider_course_id = ":".join(args[:4]) if len(args) >= 4 else f"{cells[0]}:{cells[1]}:{cells[2]}"
            if provider_course_id in seen_ids:
                continue
            seen_ids.add(provider_course_id)
            stop = {
                "지점명",
                "카테고리 (코드)",
                "강사명",
                "강의실",
                "강좌기간",
                "요일/시간",
                "전체정원",
                "수강료",
                "강좌 개요",
                "강좌개요",
                "재료비",
                "교재비",
                "첫 시간 준비물",
                "수강신청",
                "관심 강좌 등록",
                "목록",
            }
            details: list[str] = []
            if raw_url and detail_attempts < ELAND_DETAIL_LIMIT:
                detail_attempts += 1
                try:
                    details = detail_lines(s, raw_url)
                except requests.RequestException:
                    detail_failures += 1
            title_target_fields = target_fields_from_title(cells[2])
            lecture_type_code = args[2] if len(args) >= 3 else cells[1]
            category = eland_lecture_type_label(lecture_type_code) or value_after_line(details, "카테고리 (코드)", stop, 2)
            target_text = title_target_fields.get("target") or eland_target_from_category(category)
            rows.append(
                {
                    "provider": "ELAND_RETAIL",
                    "title": cells[2],
                    "branch": value_after_line(details, "지점명", stop, 2) or cells[0],
                    "branch_code": args[0] if len(args) >= 1 else "",
                    "provider_course_id": provider_course_id,
                    "category": category,
                    "category_raw": category,
                    "course_type_code": lecture_type_code,
                    "raw_url": raw_url,
                    "application_url": raw_url,
                    "instructor": value_after_line(details, "강사명", stop, 2) or cells[3],
                    "period": value_after_line(details, "강좌기간", stop, 3) or cells[4],
                    "schedule_raw": value_after_line(details, "요일/시간", stop, 4) or cells[5],
                    "fee": value_after_line(details, "수강료", stop, 3) or cells[6],
                    "status": cells[7],
                    "target": target_text,
                    "room": value_after_line(details, "강의실", stop, 3),
                    "description": value_after_line(details, "강좌개요", stop, 20),
                    "material_fee": value_after_line(details, "재료비", stop, 3),
                    "textbook_fee": value_after_line(details, "교재비", stop, 3),
                    "first_materials": value_after_line(details, "첫 시간 준비물", stop, 3),
                    **title_target_fields,
                }
            )
            found += 1
            if len(rows) >= limit:
                return (
                    rows,
                    pages,
                    "snapshot_complete=false; "
                    f"row_limit_reached={limit}; "
                    f"detail_attempts={detail_attempts}; "
                    f"detail_failures={detail_failures}",
                )
        if found == 0:
            break
    snapshot_complete = len(rows) < ELAND_LIST_PAGE_SIZE
    return (
        rows,
        pages,
        f"list_rows={len(rows)}; detail_attempts={detail_attempts}; "
        f"detail_failures={detail_failures}; "
        f"snapshot_complete={str(snapshot_complete).lower()}",
    )


def galleria_target_fields(title: str, description: str) -> dict[str, Any]:
    source = clean(f"{title} {description}")
    fields = target_fields_from_title(source) or target_fields_from_text(source)
    target = re.sub(
        r"^[A-Za-z]\s*:\s*",
        "",
        clean(fields.get("target")),
    )
    if not target:
        return {"target": "연령 미정"}
    parsed = TARGET_PARSER.parse(f"{target} {title}")
    return {
        "target": target,
        "target_age_group": parsed["age_group"],
        "target_min_age": parsed["min_age"],
        "target_max_age": parsed["max_age"],
        "target_with_parent": parsed["with_parent"],
        "target_tags": parsed["tags"],
    }


def galleria_category(title: str, description: str) -> str:
    result = classify_standard_category(
        {
            "title": title,
            "description": description,
            "collection_category": "문화센터",
        },
        CULTURE_CENTER_CATEGORY_CONFIG,
    )
    return result.label if result.key != "uncategorized" else "문화센터"


def galleria(limit: int) -> tuple[list[dict[str, Any]], int, str]:
    s = session()
    rows: list[dict[str, Any]] = []
    pages = 0
    branches = {
        "gwanggyo": "광교점",
        "timeworld": "타임월드점",
        "centercity": "센터시티점",
        "jinju": "진주점",
    }
    exhausted_branches = 0
    seen_course_urls: set[str] = set()
    for branch, branch_name in branches.items():
        branch_exhausted = False
        seen_page_signatures: set[tuple[str, ...]] = set()
        for page in range(80):
            if len(rows) >= limit:
                return (
                    rows,
                    pages,
                    "snapshot_complete=false "
                    f"row_limit_reached={limit} exhausted_branches={exhausted_branches}",
                )
            url = f"https://dept.galleria.co.kr/g-culture/culture-center/branch/{branch}/open-lecture"
            if page > 0:
                url += f"?p={page}"
            r = s.get(url, timeout=20)
            r.raise_for_status()
            pages += 1
            soup = BeautifulSoup(r.text, "lxml")
            items = soup.select(".item-cont a.item-a")
            if not items:
                branch_exhausted = True
                exhausted_branches += 1
                break
            page_urls = tuple(
                urljoin("https://dept.galleria.co.kr", clean(item.get("href")))
                for item in items
            )
            if page_urls in seen_page_signatures:
                raise ValueError(
                    f"Galleria pagination repeated branch={branch} page={page}"
                )
            seen_page_signatures.add(page_urls)
            for a in items:
                card = a.find_parent(class_="item")
                status = clean(card.select_one(".badge").get_text(" ", strip=True)) if card and card.select_one(".badge") else ""
                title_node = a.select_one(".title")
                title = clean(
                    title_node.get_text(" ", strip=True)
                    if title_node
                    else a.get_text(" ", strip=True)
                )
                raw_url = urljoin("https://dept.galleria.co.kr", a.get("href", ""))
                if raw_url in seen_course_urls:
                    raise ValueError(
                        f"Galleria course duplicated branch={branch} page={page}"
                    )
                seen_course_urls.add(raw_url)
                detail_response = s.get(raw_url, timeout=20)
                detail_response.raise_for_status()
                detail_soup = BeautifulSoup(detail_response.text, "lxml")
                fee = detail_value_by_dt(detail_soup, "수강료")
                description = extract_section_after_heading(detail_soup, "h2.h", "강좌 소개")
                material_note = extract_material_note(description)
                place = detail_value_by_dt(detail_soup, "강의장소")
                target_fields = galleria_target_fields(title, description)
                category = galleria_category(title, description)
                image_url = extract_image_url(
                    detail_soup,
                    raw_url,
                    [
                        ".article-pic-mb img",
                        "img.article-pic-mb",
                    ],
                )
                rows.append(
                    {
                        "provider": "GALLERIA",
                        "provider_course_id": (
                            f"{branch}:{raw_url.rstrip('/').rsplit('/', 1)[-1]}"
                        ),
                        "title": title,
                        "branch": branch_name,
                        "branch_code": branch,
                        "raw_url": raw_url,
                        "application_url": raw_url,
                        "status": status,
                        "fee": fee,
                        "category": category,
                        "category_raw": category,
                        "period": detail_value_by_dt(detail_soup, "강의기간"),
                        "schedule_raw": detail_value_by_dt(detail_soup, "강의시간"),
                        "instructor": detail_value_by_dt(detail_soup, "강사"),
                        "place": place,
                        "address": place,
                        "room": detail_value_by_dt(detail_soup, "강의실"),
                        "image_url": image_url,
                        "description": description,
                        "material_note": material_note,
                        "material_fee": detail_value_by_dt(detail_soup, "재료비")
                        or extract_material_fee_text(fee, material_note, description),
                        **target_fields,
                    }
                )
                if len(rows) >= limit:
                    return (
                        rows,
                        pages,
                        "snapshot_complete=false "
                        f"row_limit_reached={limit} exhausted_branches={exhausted_branches}",
                    )
        if not branch_exhausted:
            raise ValueError(
                f"Galleria pagination cap reached branch={branch} max_pages=80"
            )
    return (
        rows,
        pages,
        "snapshot_complete=true "
        f"branches={len(branches)} rows={len(rows)} pages={pages}",
    )


AK_PLAZA_STORES = {
    "02": "수원점",
    "03": "분당점",
    "04": "평택점",
    "05": "원주점",
}
AK_PLAZA_DETAIL_LIMIT = 100


def ak_plaza_period(item: dict[str, Any]) -> str:
    start = clean(str(item.get("START_YMD") or ""))
    end = clean(str(item.get("END_YMD") or ""))
    if not start or not end:
        return start or end
    if re.fullmatch(r"\d{8}", start) and re.fullmatch(r"\d{8}", end) and end < start:
        try:
            sessions = int(item.get("LECT_CNT") or 0)
        except (TypeError, ValueError):
            sessions = 0
        if sessions == 1:
            end = start
        else:
            start, end = end, start
    return f"{start}-{end}"


def ak_plaza(limit: int) -> tuple[list[dict[str, Any]], int, str]:
    s = session()
    s.headers.update(
        {
            "Referer": "https://culture.akplaza.com/course/list01",
            "X-Requested-With": "XMLHttpRequest",
        }
    )
    rows: list[dict[str, Any]] = []
    pages = 0
    detail_attempts = 0
    detail_failures = 0
    # The live API accepts 1,000 and returns each audited store in one bounded
    # response.  This leaves the provider-specific budget for detail pages.
    list_size = 1_000
    s.get("https://culture.akplaza.com/course/list01", timeout=20)

    for store, store_name in AK_PLAZA_STORES.items():
        change = s.post(
            "https://culture.akplaza.com/common/change_main_store",
            data={"store": store},
            timeout=20,
        )
        try:
            changed = change.json().get("isSuc") == "success"
        except Exception:
            changed = False
        if not changed:
            return rows, pages, f"Failed to change AK PLAZA store session: {store} {store_name}"

        page = 1
        page_num = None
        while True:
            r = s.post(
                "https://culture.akplaza.com/course/getPeltList",
                data={
                    "page": page,
                    "sort_type": "reco_cnt",
                    "listSize": list_size,
                    "search_name": "",
                    "store": store,
                    "main_cd": "",
                    "sect_cd": "",
                    "yoil": "0000000",
                    "subject_fg": "",
                    "month_val": "",
                },
                timeout=20,
            )
            pages += 1
            payload = r.json()
            items = payload.get("list") or []
            if not items:
                break
            page_num = int(payload.get("pageNum") or page_num or page)
            for item in items:
                subject = item.get("SUBJECT_CD") or item.get("subject_cd")
                store_code = str(item.get("STORE") or store)
                possible = item.get("POSSIBLE_NO")
                raw_url = f"https://culture.akplaza.com/course/detail?store={store_code}&sSubject_cd={subject}" if subject else ""
                fee = item.get("REGIS_FEE")
                if fee is None:
                    fee = item.get("REG_FEE")
                if fee is None:
                    fee = item.get("regis_fee")
                image_name = clean(item.get("THUMBNAIL_IMG") or item.get("DETAIL_IMG"))
                image_url = (
                    f"https://img-culture.akplaza.com/upload/wlect/{image_name}"
                    if image_name
                    else ""
                )
                description = ""
                if raw_url and detail_attempts < AK_PLAZA_DETAIL_LIMIT:
                    detail_attempts += 1
                    try:
                        detail_soup = BeautifulSoup(s.get(raw_url, timeout=20).text, "lxml")
                        description_node = detail_soup.select_one("#lect_info")
                        description = (
                            clean(description_node.get_text(" ", strip=True))
                            if description_node
                            else ""
                        )
                    except requests.RequestException:
                        detail_failures += 1
                title = clean(
                    item.get("SUBJECT_NM")
                    or item.get("SUBJECT_NAME")
                    or item.get("subject_nm")
                    or item.get("TITLE")
                )
                row = {
                    "provider": "AK_PLAZA",
                    "provider_course_id": f"{store_code}:{subject}" if subject else "",
                    "title": title,
                    "branch": clean(
                        item.get("STORE_NM")
                        or item.get("STORE_NAME")
                        or AK_PLAZA_STORES.get(store_code)
                        or store_name
                    ),
                    "branch_code": store_code,
                    "store_code": store_code,
                    "raw_url": raw_url,
                    "status": clean(
                        item.get("REGIS_NM")
                        or item.get("REG_STATUS_NM")
                        or item.get("status")
                    )
                    or ("수강신청" if isinstance(possible, int) and possible > 0 else "마감"),
                    "fee": fee,
                    "period": ak_plaza_period(item),
                    "schedule_raw": clean(
                        f"{item.get('DAY') or ''} {item.get('LECT_HOUR') or ''}"
                    ),
                    "instructor": clean(
                        item.get("LECTURER_NM") or item.get("TEACHER_NM") or ""
                    ),
                    "target": clean(item.get("MAIN_NM") or ""),
                    "category": clean(item.get("SECT_NM") or ""),
                    "course_type": clean(item.get("SUBJECT_FG_NM") or ""),
                    "sessions": item.get("LECT_CNT"),
                    "room": clean(item.get("CLASSROOM") or ""),
                    "material_fee": item.get("FOOD_AMT"),
                    "description": description,
                    "image_url": image_url,
                }
                row.update(target_fields_from_text(title))
                rows.append(row)
                if len(rows) >= limit:
                    note = "snapshot_complete=false"
                    if len(rows) > detail_attempts or detail_failures:
                        note += (
                            "; "
                            f"detail_attempts={detail_attempts}; "
                            f"detail_skipped={len(rows) - detail_attempts}; "
                            f"detail_failures={detail_failures}"
                        )
                    return rows, pages, note
            if page_num is not None and page >= page_num:
                break
            page += 1
    if not rows:
        return rows, pages, "API returned 0 rows for all configured stores"
    note = "snapshot_complete=true"
    if len(rows) > detail_attempts or detail_failures:
        note += (
            "; "
            f"detail_attempts={detail_attempts}; "
            f"detail_skipped={len(rows) - detail_attempts}; "
            f"detail_failures={detail_failures}"
        )
    return rows, pages, note


LOTTE_MART_LIST_URL = "https://culture.lottemart.com/cu/gus/course/courseinfo/courselist.do?"
LOTTE_MART_SEARCH_URL = "https://culture.lottemart.com/cu/gus/course/courseinfo/searchList.do"
LOTTE_MART_DETAIL_URL = "https://culture.lottemart.com/cu/gus/course/courseinfo/courseview.do"
LOTTE_MART_STORE_RE = re.compile(
    r"fn_commChooseStore\([^,]+,\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*'([^']+)'"
)
LOTTE_MART_DEFAULT_IMAGE_RE = re.compile(r"/resources/images/culture/ClassDefault/", re.IGNORECASE)
LOTTE_MART_MAX_LIST_PAGES = 2_500
LOTTE_MART_DETAIL_LIMIT = 100


def lotte_mart_stores(session_obj: requests.Session) -> list[dict[str, str]]:
    response = session_obj.get(LOTTE_MART_LIST_URL, timeout=20)
    response.raise_for_status()

    stores: list[dict[str, str]] = []
    seen: set[str] = set()
    for area_code, store_code, name in LOTTE_MART_STORE_RE.findall(response.text):
        if store_code in seen:
            continue
        seen.add(store_code)
        stores.append(
            {
                "area_code": clean(area_code),
                "code": clean(store_code),
                "name": clean(name),
            }
        )
    return stores


def lotte_mart_page_info(soup: BeautifulSoup, html: str = "") -> tuple[int, int, int] | None:
    node = soup.find(lambda tag: tag.has_attr("pageinfo") or tag.has_attr("pageInfo"))
    value = ""
    if node:
        value = node.get("pageInfo") or node.get("pageinfo") or ""
    if not value and html:
        match = re.search(r"pageInfo\s*=\s*['\"]([^'\"]+)['\"]", html, re.IGNORECASE)
        value = match.group(1) if match else ""
    if not value:
        return None

    parts = value.split("|")
    if len(parts) < 2:
        return None

    try:
        current_page = int(parts[0])
        total_pages = int(parts[1])
        total_count = int(parts[2]) if len(parts) > 2 and parts[2] else 0
    except ValueError:
        return None
    return current_page, total_pages, total_count


def lotte_mart_course_id(node: Any) -> str:
    html = str(node)
    match = re.search(r"fn_clsView\(\s*['\"]([^'\"]+)['\"]", html)
    return clean(match.group(1)) if match else ""


def lotte_mart_target_fields(title: str, category: str) -> dict[str, Any]:
    fields = target_fields_from_text(title)
    if fields:
        return fields

    category_targets = (
        ("엄마", "영아 보호자동반"),
        ("영아", "영아"),
        ("유아", "유아"),
        ("어린이", "어린이/청소년"),
        ("청소년", "어린이/청소년"),
        ("성인", "성인"),
    )
    target = next(
        (value for token, value in category_targets if token in category),
        "연령 미정",
    )
    parsed = TARGET_PARSER.parse(f"{target} {title}")
    return {
        "target": target,
        "target_age_group": parsed["age_group"],
        "target_min_age": parsed["min_age"],
        "target_max_age": parsed["max_age"],
        "target_with_parent": parsed["with_parent"],
        "target_tags": parsed["tags"],
    }


def lotte_mart_list_rows(soup: BeautifulSoup, store: dict[str, str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    anchors = soup.select("a[onclick*='fn_clsView']")
    for anchor in anchors:
        node = anchor.find_parent("li") or anchor
        course_id = lotte_mart_course_id(anchor) or lotte_mart_course_id(node)
        if not course_id or course_id in seen:
            continue
        seen.add(course_id)

        text = clean(node.get_text(" ", strip=True))
        if len(text) < 8 or text in {"더보기", "목록"}:
            continue

        title_node = node.select_one(".tit, .title, .showBalloon")
        title = clean(title_node.get_text(" ", strip=True) if title_node else anchor.get_text(" ", strip=True) or text[:160])
        sub_info = [
            clean(item.get_text(" ", strip=True))
            for item in node.select(".lct_sub-info > li")
        ]
        category = sub_info[0] if sub_info else ""
        schedule_raw = sub_info[1] if len(sub_info) > 1 else ""
        period_match = re.search(
            r"\d{4}[./-]\d{1,2}[./-]\d{1,2}",
            sub_info[2] if len(sub_info) > 2 else "",
        )
        sessions_match = re.search(
            r"(\d+)\s*회",
            sub_info[3] if len(sub_info) > 3 else "",
        )
        fee_match = re.findall(r"[\d,]+\s*원", text)
        status_match = re.search(
            r"(접수중|접수예정|마감임박|마감|대기신청|온라인마감|현장접수|바로신청)",
            text,
        )
        image_node = node.select_one(".thumb-img img[src], img[src]")
        image_url = urljoin(
            LOTTE_MART_LIST_URL,
            clean(image_node.get("src")) if image_node else "",
        )
        if LOTTE_MART_DEFAULT_IMAGE_RE.search(image_url):
            image_url = ""
        raw_url = f"{LOTTE_MART_DETAIL_URL}?cls_cd={course_id}&search_str_cd={store['code']}"
        rows.append(
            {
                "provider": "LOTTE_MART",
                "provider_course_id": f"{store['code']}:{course_id}",
                "course_id": course_id,
                "title": title,
                "branch": store["name"],
                "branch_code": store["code"],
                "area_code": store["area_code"],
                "raw_url": raw_url,
                "application_url": raw_url,
                "status": status_match.group(1) if status_match else "",
                "fee": fee_match[-1] if fee_match else "",
                "period": period_match.group(0) if period_match else "",
                "schedule_raw": schedule_raw,
                "sessions": int(sessions_match.group(1)) if sessions_match else None,
                "category": category,
                "category_raw": category,
                "place": store["name"],
                "venue_name": store["name"],
                "image_url": image_url,
                **lotte_mart_target_fields(title, category),
            }
        )
    return rows


def lotte_mart_fetch_list_page(
    session_obj: requests.Session,
    store: dict[str, str],
    page: int,
) -> tuple[list[dict[str, Any]], tuple[int, int, int] | None]:
    data = {
        "currPageNo": str(page),
        "search_list_type": "A",
        "search_str_cd": store["code"],
        "search_order_gbn": "",
        "search_reg_status": "",
        "is_category_open": "N",
        "search_child_age": "",
    }
    response = session_obj.post(LOTTE_MART_SEARCH_URL, data=data, timeout=20)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "lxml")
    return lotte_mart_list_rows(soup, store), lotte_mart_page_info(soup, response.text)


def lotte_mart_detail_category(lines: list[str]) -> str:
    skip_tokens = (
        "수강신청",
        "장바구니",
        "접수",
        "대기신청",
        "강좌소개",
        "강좌기간",
        "강좌시간",
        "강좌코드",
        "강사",
        "대상",
        "연령",
        "재료비",
        "교재비",
        "총 주문 금액",
    )
    for line in lines[:80]:
        match = re.match(r"^\[[^\]]+\]\s*(.+)$", line)
        if not match:
            continue
        value = clean(match.group(1))
        if not value or len(value) > 100:
            continue
        if any(token in value for token in skip_tokens):
            continue
        return value

    for line in lines[:120]:
        value = clean(line)
        if len(value) > 100 or any(token in value for token in skip_tokens):
            continue
        if re.search(r"(?:영아|유아|아동|초등|성인|키즈|엄마랑).{0,20}강좌\s*>\s*", value):
            return value

    source_categories = {
        "영아강좌",
        "유아강좌",
        "아동강좌",
        "초등강좌",
        "성인강좌",
        "키즈강좌",
        "엄마랑 아가랑",
        "엄마랑아기랑",
    }
    for line in lines[:120]:
        value = clean(line)
        if value in source_categories:
            return value
    return ""


def lotte_mart_detail_category_from_soup(soup: BeautifulSoup) -> str:
    node = soup.select_one(".lct-depth")
    if not node:
        return ""
    parts = [clean(text) for text in node.stripped_strings]
    parts = [part for part in parts if part and part != "|"]
    if not parts:
        return ""
    return " > ".join(dict.fromkeys(parts))[:100]


def lotte_mart_detail_fields(session_obj: requests.Session, raw_url: str) -> dict[str, str]:
    if not raw_url:
        return {}

    detail = session_obj.get(raw_url, timeout=20)
    detail.raise_for_status()
    detail_soup = BeautifulSoup(detail.text, "lxml")
    lines = [clean(line) for line in detail_soup.get_text("\n", strip=True).splitlines()]
    lines = [line for line in lines if line]
    stop = {
        "강사",
        "강사소개",
        "강좌기간",
        "대상",
        "강좌시간",
        "연령",
        "접수/수강료",
        "횟수/수강료",
        "재료비/교재비",
        "첫시간 준비물",
        "강좌코드",
        "강의실",
        "강좌소개",
        "강좌 수강 Tip",
    }

    fee = value_after_line(lines, "접수/수강료", stop) or value_after_line(lines, "횟수/수강료", stop)
    description = value_after_line(lines, "강좌소개", stop, 20)
    material_note = value_after_line(lines, "첫시간 준비물", stop, 12) or extract_material_note(description)
    material_fee = value_after_line(lines, "재료비/교재비", stop) or extract_material_fee_text(
        fee,
        material_note,
        description,
    )
    image_url = ""
    for selector in (
        "div.lct-visual.left img[src]",
        ".lct-visual.left img[src]",
        ".lct-visual img[src]",
        "img[alt*='이미지'][src]",
        "meta[property='og:image']",
    ):
        image_url = extract_image_url(detail_soup, LOTTE_MART_DETAIL_URL, [selector])
        if image_url and not LOTTE_MART_DEFAULT_IMAGE_RE.search(image_url):
            break
        image_url = ""

    category = lotte_mart_detail_category_from_soup(detail_soup) or lotte_mart_detail_category(lines)

    return {
        "category": category,
        "category_raw": category,
        "instructor": value_after_line(lines, "강사", stop),
        "period": value_after_line(lines, "강좌기간", stop),
        "target": value_after_line(lines, "대상", stop),
        "schedule_raw": value_after_line(lines, "강좌시간", stop),
        "age": value_after_line(lines, "연령", stop),
        "fee": fee,
        "material_fee": material_fee,
        "material_note": material_note,
        "room": value_after_line(lines, "강의실", stop),
        "description": description,
        "image_url": image_url,
    }


def lotte_mart(limit: int) -> tuple[list[dict[str, Any]], int, str]:
    """Collect LOTTE_MART rows by replaying the site's "more" AJAX pagination.

    The public page appends additional rows by posting currPageNo to searchList.do.
    Each appended list item exposes pageInfo as current|totalPages|totalCount|...
    so we first exhaust every list page, then fetch detail pages for the collected
    list rows. Keeping those phases separate prevents a long detail crawl from
    looking like the list was parsed before all "more" pages were loaded.
    """

    s = session()
    s.headers.update({"Referer": LOTTE_MART_LIST_URL})
    list_rows: list[dict[str, Any]] = []
    pages = 0
    expected_rows = 0
    unlimited = limit <= 0
    page_caps: list[str] = []
    stores_processed = 0
    stores = lotte_mart_stores(s)
    if not stores:
        return [], pages, "LOTTE_MART store list was empty"

    for store in stores:
        if not unlimited and len(list_rows) >= limit:
            break

        page = 1
        total_pages: int | None = None
        seen_store_courses: set[str] = set()
        no_growth_pages = 0
        store_pages = 0
        while unlimited or len(list_rows) < limit:
            if (
                store_pages >= MAX_PROVIDER_PAGES
                or pages >= LOTTE_MART_MAX_LIST_PAGES
            ):
                page_caps.append(store["code"])
                break
            store_pages += 1
            pages += 1
            page_rows, page_info = lotte_mart_fetch_list_page(s, store, page)
            if page_info:
                current_page, total_pages, total_count = page_info
                if page == 1:
                    expected_rows += total_count
            else:
                current_page = page

            added = 0
            for row in page_rows:
                course_key = row.get("provider_course_id") or row.get("course_id")
                if not course_key or course_key in seen_store_courses:
                    continue
                seen_store_courses.add(course_key)
                list_rows.append(row)
                added += 1
                if not unlimited and len(list_rows) >= limit:
                    break

            if added == 0:
                no_growth_pages += 1
            else:
                no_growth_pages = 0
            if no_growth_pages >= 2:
                break
            if total_pages is not None and current_page >= total_pages:
                break
            page += 1
        stores_processed += 1
        if pages >= LOTTE_MART_MAX_LIST_PAGES:
            break

    rows: list[dict[str, Any]] = []
    detail_failures = 0
    detail_attempts = min(len(list_rows), LOTTE_MART_DETAIL_LIMIT)
    if detail_attempts <= 1:
        detail_indexes = {0} if detail_attempts else set()
    else:
        detail_indexes = {
            round(index * (len(list_rows) - 1) / (detail_attempts - 1))
            for index in range(detail_attempts)
        }
    for index, row in enumerate(list_rows):
        detail_fields: dict[str, str] = {}
        if index in detail_indexes and row.get("raw_url"):
            try:
                detail_fields = lotte_mart_detail_fields(s, row["raw_url"])
            except requests.RequestException as exc:
                detail_failures += 1
                row["detail_error"] = f"{type(exc).__name__}: {exc}"
        row.update(
            {
                "category": detail_fields.get("category") or row.get("category", ""),
                "category_raw": detail_fields.get("category_raw") or row.get("category_raw", ""),
                "fee": detail_fields.get("fee") or row.get("fee", ""),
                "period": detail_fields.get("period") or row.get("period", ""),
                "schedule_raw": detail_fields.get("schedule_raw") or row.get("schedule_raw", ""),
                "instructor": detail_fields.get("instructor") or row.get("instructor", ""),
                "target": detail_fields.get("target") or row.get("target", ""),
                "age": detail_fields.get("age") or row.get("age", ""),
                "material_fee": detail_fields.get("material_fee") or row.get("material_fee", ""),
                "material_note": detail_fields.get("material_note") or row.get("material_note", ""),
                "room": detail_fields.get("room") or row.get("room", ""),
                "description": detail_fields.get("description") or row.get("description", ""),
                "image_url": detail_fields.get("image_url") or row.get("image_url", ""),
            }
        )
        rows.append(row)

    snapshot_complete = (
        stores_processed == len(stores)
        and not page_caps
        and len(list_rows) == expected_rows
    )
    note = (
        f"discovered {len(stores)} stores; stores_processed={stores_processed}; "
        f"list_rows={len(list_rows)}; pageInfo total rows={expected_rows}; "
        f"detail_attempts={len(detail_indexes)}; "
        f"snapshot_complete={str(snapshot_complete).lower()}"
    )
    if page_caps:
        note += f"; page_cap_stores={','.join(page_caps)}"
    if detail_failures:
        note += f"; detail_failures={detail_failures}"
    return rows, pages, note


COLLECTORS = {
    "HYUNDAI_DEPT": hyundai,
    "SHINSEGAE_ACADEMY": shinsegae,
    "ELAND_RETAIL": eland,
    "GALLERIA": galleria,
    "AK_PLAZA": ak_plaza,
    "LOTTE_MART": lotte_mart,
}


def collect_provider(
    provider: str,
    limit: int,
    *,
    request_budget: int | None = None,
) -> tuple[list[dict[str, Any]], int, str]:
    if provider not in COLLECTORS:
        raise ValueError(f"Unsupported YAML collector provider: {provider}")
    if not 1 <= int(limit) <= 100_000:
        raise ValueError("limit must be between 1 and 100000")
    effective_request_budget = (
        int(request_budget)
        if request_budget is not None
        else PROVIDER_COLLECTOR_REQUEST_BUDGETS.get(provider, DEFAULT_COLLECTOR_REQUEST_BUDGET)
    )
    with managed_collector_sessions(request_budget=effective_request_budget):
        return COLLECTORS[provider](int(limit))


def run_provider(provider: str, limit: int) -> SampleReport:
    report = SampleReport(provider=provider, requested=limit)
    try:
        rows, pages, note = collect_provider(provider, limit)
        path = write_samples(provider, rows)
        report.collected = len(rows)
        report.pages = pages
        report.success = len(rows) >= limit or "snapshot_complete=true" in note
        report.output = str(path)
        report.fields = score_fields(rows)
        report.note = note
    except Exception as exc:
        report.error = f"{type(exc).__name__}: {exc}"
    return report


def print_table(reports: list[SampleReport]) -> None:
    headers = [
        "Provider",
        "요청",
        "수집",
        "페이지",
        "성공",
        "title",
        "branch",
        "url",
        "status",
        "fee",
        "target",
        "age_group",
        "image",
        "desc",
        "material_fee",
        "material_note",
        "메모",
    ]
    rows = []
    for r in reports:
        rows.append(
            [
                r.provider,
                str(r.requested),
                str(r.collected),
                str(r.pages),
                "Y" if r.success else "N",
                str(r.fields.get("title", 0)),
                str(r.fields.get("branch", 0)),
                str(r.fields.get("raw_url", 0)),
                str(r.fields.get("status", 0)),
                str(r.fields.get("fee", 0)),
                str(r.fields.get("target", 0)),
                str(r.fields.get("target_age_group", 0)),
                str(r.fields.get("image_url", 0)),
                str(r.fields.get("description", 0)),
                str(r.fields.get("material_fee", 0)),
                str(r.fields.get("material_note", 0)),
                (r.error or r.note or r.output)[:90],
            ]
        )
    widths = [len(h) for h in headers]
    for row in rows:
        widths = [max(w, len(c)) for w, c in zip(widths, row)]
    def fmt(row: list[str]) -> str:
        return "| " + " | ".join(c.ljust(w) for c, w in zip(row, widths)) + " |"
    print(fmt(headers))
    print("| " + " | ".join("-" * w for w in widths) + " |")
    for row in rows:
        print(fmt(row))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--provider", action="append", choices=sorted(COLLECTORS))
    args = parser.parse_args()
    providers = args.provider or list(COLLECTORS)
    reports = [run_provider(provider, args.limit) for provider in providers]
    print_table(reports)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary = OUT_DIR / f"sample_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    summary.write_text(json.dumps([asdict(r) for r in reports], ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nsummary={summary}")
    return 0 if all(r.success for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
