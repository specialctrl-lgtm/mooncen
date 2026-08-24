"""Fail-closed collector for Chungju's separate integrated reservation site.

The ``/rev/reserve/99`` catalogue fans out into 25 official eup/myeon/dong
categories.  It is a resident-centre inventory and is semantically separate
from the city-wide lifelong-learning catalogue owned by
``goodedu.chungju.go.kr``.

The source exposes a declared row count and last-page number for every
category.  A snapshot is returned only after every declared page, each
immediate empty sentinel, every stable ``action-value`` identity, and all
detail pages have been verified.  The returned rows are limited to courses
whose education end date is current or future, but expired details are still
fetched and checked so a partial historical list cannot masquerade as a full
snapshot.

An information/detail URL is never substituted for an application endpoint.
``application_url`` is populated only when an enabled ``action_write`` link
contains a valid source-owned write route for the same course identity.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
import html
import math
import re
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


CHUNGJU_REV_PROVIDER = "MUNI_WWW_CHUNGJU_GO_KR_7EE8620A"
CHUNGJU_REV_HOST = "www.chungju.go.kr"
CHUNGJU_REV_PATH = "/rev/reserve/99"
CHUNGJU_REV_ENTRY_CATEGORY = "37"
CHUNGJU_REV_ALL_CATEGORY = "32"
CHUNGJU_REV_URL = (
    "https://www.chungju.go.kr/rev/reserve/99?document_category_srl=37"
)
CHUNGJU_REV_PAGE_SIZE = 20
CHUNGJU_REV_MAX_WORKERS = 8
CHUNGJU_REV_FETCH_ATTEMPTS = 2
CHUNGJU_REV_PARSER = (
    "chungju_rev_99_complete_categories+pages+empty_sentinels+all_detail"
)
CHUNGJU_REV_OWNERSHIP_SCOPE = (
    "chungju_rev_99_resident_centres_current_future"
)
CHUNGJU_GOODEDU_PROVIDER = "MUNI_GOODEDU_CHUNGJU_GO_KR_66F13E51"
CHUNGJU_MUNICIPALITY_CODE = "4313000000"
CHUNGJU_MUNICIPALITY_NAME = "충청북도 충주시"

# The category identifiers and labels are part of the completeness contract.
# An added/removed source branch deliberately fails closed until it is audited.
CHUNGJU_REV_CATEGORIES: Mapping[str, str] = {
    "34": "주덕읍",
    "35": "살미면",
    "36": "수안보면",
    "37": "대소원면",
    "38": "신니면",
    "39": "노은면",
    "40": "앙성면",
    "42": "중앙탑면",
    "43": "금가면",
    "44": "동량면",
    "45": "산척면",
    "46": "엄정면",
    "47": "소태면",
    "48": "성내충인동",
    "49": "교현안림동",
    "50": "교현2동",
    "51": "용산동",
    "52": "지현동",
    "53": "문화동",
    "54": "호암직동",
    "55": "달천동",
    "56": "봉방동",
    "57": "칠금금릉동",
    "58": "연수동",
    "59": "목행용탄동",
}

SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"[0-9a-f]{32}")
_INTEGER_RE = re.compile(r"[\d,]+")
_COUNTER_RE = re.compile(
    r"총\s*강좌\s*수\s*:\s*([\d,]+)\s*건\s*"
    r"\(총\s*(\d+)\s*페이지\s*중\s*(\d+)\s*페이지\)"
)
_FULL_DATE_RE = re.compile(
    r"(20\d{2})-(\d{1,2})-(\d{1,2})"
    r"(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?"
)
_MONTH_DAY_RE = re.compile(
    r"(\d{1,2})-(\d{1,2})(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?"
)
_DAY_RE = re.compile(r"(\d{1,2})(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?")

_SOURCE_STATUS_MAP: Mapping[str, str] = {
    "접수중": "OPEN",
    "접수예정": "SCHEDULED",
    "접수대기": "SCHEDULED",
    "접수마감": "CLOSED",
    "접수종료": "CLOSED",
}

_DETAIL_LABELS = frozenset(
    {
        "권역 / 읍면동",
        "기관명",
        "강좌명",
        "기수 구분",
        "접수방식",
        "교육 기간",
        "총교육일",
        "교육요일",
        "수업시간",
        "접수 기간",
        "정원",
        "선발방식",
        "우선접수대상",
        "모집연령",
        "수업료",
        "강사",
        "문의 연락처",
        "교육장",
        "교육장주소",
        "준비물",
        "수업 내용",
    }
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(
        " ", html.unescape(str(value or "")).replace("\xa0", " ")
    ).strip()


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def is_chungju_rev_target(target: Any) -> bool:
    """Match only the audited provider and unfiltered category entry URL."""

    parsed = urlparse(_target_url(target))
    return bool(
        _provider(target) == CHUNGJU_REV_PROVIDER
        and parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == CHUNGJU_REV_HOST
        and parsed.port is None
        and parsed.path == CHUNGJU_REV_PATH
        and parse_qsl(parsed.query, keep_blank_values=True)
        == [("document_category_srl", CHUNGJU_REV_ENTRY_CATEGORY)]
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


is_target = is_chungju_rev_target


def chungju_rev_list_url(category_id: Any, page: Any) -> str:
    category = _clean(category_id)
    raw_page = _clean(page)
    if category not in CHUNGJU_REV_CATEGORIES:
        return ""
    if not raw_page.isdigit() or int(raw_page) < 1:
        return ""
    return f"https://{CHUNGJU_REV_HOST}{CHUNGJU_REV_PATH}?" + urlencode(
        {"page": int(raw_page), "document_category_srl": category}
    )


def chungju_rev_detail_url(category_id: Any, identity: Any) -> str:
    category = _clean(category_id)
    source_identity = _clean(identity)
    if category not in CHUNGJU_REV_CATEGORIES:
        return ""
    if not _IDENTITY_RE.fullmatch(source_identity):
        return ""
    return f"https://{CHUNGJU_REV_HOST}{CHUNGJU_REV_PATH}?" + urlencode(
        {
            "action": "read",
            "action-value": source_identity,
            "document_category_srl": category,
        }
    )


def chungju_rev_application_url(category_id: Any, identity: Any) -> str:
    category = _clean(category_id)
    source_identity = _clean(identity)
    if category not in CHUNGJU_REV_CATEGORIES:
        return ""
    if not _IDENTITY_RE.fullmatch(source_identity):
        return ""
    return f"https://{CHUNGJU_REV_HOST}{CHUNGJU_REV_PATH}?" + urlencode(
        {
            "action": "write",
            "action-value": source_identity,
            "document_category_srl": category,
        }
    )


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    return current


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _response_soup(response: Any) -> BeautifulSoup:
    try:
        status = int(getattr(response, "status_code", 200))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise ValueError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise ValueError("HTTP redirects are not accepted")
    content = getattr(response, "content", None)
    if content is None:
        content = getattr(response, "text", None)
    if not content:
        raise ValueError("empty HTML response")
    return BeautifulSoup(content, "lxml")


def _parallel_fetch(
    items: list[tuple[Any, str]],
    *,
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
) -> tuple[dict[Any, BeautifulSoup], list[str]]:
    fetched: dict[Any, BeautifulSoup] = {}
    errors: list[str] = []
    sessions: list[Any] = []
    sessions_lock = threading.Lock()
    local = threading.local()

    def thread_session() -> Any:
        current = getattr(local, "session", None)
        if current is None:
            current = session_factory()
            local.session = current
            with sessions_lock:
                sessions.append(current)
        return current

    def one(item: tuple[Any, str]) -> tuple[Any, Optional[BeautifulSoup], str]:
        key, url = item
        last_error = ""
        for attempt in range(CHUNGJU_REV_FETCH_ATTEMPTS):
            try:
                response = thread_session().get(
                    url,
                    timeout=timeout,
                    allow_redirects=False,
                )
                return key, _response_soup(response), ""
            except Exception as exc:
                last_error = type(exc).__name__
                if attempt + 1 < CHUNGJU_REV_FETCH_ATTEMPTS:
                    time.sleep(0.15 * (2**attempt))
        return key, None, f"{key}: fetch {last_error}"

    if not items:
        return fetched, errors
    try:
        with ThreadPoolExecutor(
            max_workers=min(max_workers, max(1, len(items)))
        ) as executor:
            for key, soup, error in executor.map(one, items):
                if soup is not None:
                    fetched[key] = soup
                if error:
                    errors.append(error)
    finally:
        for current in sessions:
            _close_quietly(current)
    return fetched, errors


def _counter(soup: BeautifulSoup) -> Optional[tuple[int, int, int]]:
    nodes = soup.select(".modules_lecture .count")
    if len(nodes) != 1:
        return None
    match = _COUNTER_RE.fullmatch(_clean(nodes[0].get_text(" ", strip=True)))
    if not match:
        return None
    total = int(match.group(1).replace(",", ""))
    advertised_last = int(match.group(2))
    displayed_page = int(match.group(3))
    return total, displayed_page, advertised_last


def _source_route(
    href: Any,
    *,
    expected_action: Optional[str] = None,
    expected_category: Optional[str] = None,
    expected_identity: Optional[str] = None,
    expected_page: Optional[int] = None,
) -> tuple[dict[str, str], str]:
    parsed = urlparse(urljoin(CHUNGJU_REV_URL, _clean(href)))
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != CHUNGJU_REV_HOST
        or parsed.port is not None
        or parsed.path != CHUNGJU_REV_PATH
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        return {}, "unexpected Chungju reservation route"
    query = parse_qs(parsed.query, keep_blank_values=True)
    course_parameters = {"action", "action-value", "document_category_srl"}
    allowed = course_parameters | {"page"}
    if set(query) - allowed or any(len(values) != 1 for values in query.values()):
        return {}, "unexpected Chungju reservation query"
    flat = {key: values[0] for key, values in query.items()}
    if expected_action is None:
        if set(flat) != {"document_category_srl"}:
            return {}, "category route has unexpected parameters"
    else:
        if not course_parameters.issubset(flat) or flat.get("action") != expected_action:
            return {}, "course action route is malformed"
        if not _IDENTITY_RE.fullmatch(flat.get("action-value", "")):
            return {}, "course identity is malformed"
        if "page" in flat and (not flat["page"].isdigit() or int(flat["page"]) < 1):
            return {}, "course return page is malformed"
        if expected_page is not None and "page" in flat and int(flat["page"]) != expected_page:
            return {}, "course return page mismatch"
    if expected_category is not None and flat.get("document_category_srl") != expected_category:
        return {}, "course/category identity mismatch"
    if expected_identity is not None and flat.get("action-value") != expected_identity:
        return {}, "detail/application identity mismatch"
    return flat, ""


def _discover_categories(
    soup: BeautifulSoup,
) -> tuple[list[tuple[str, str]], list[str]]:
    errors: list[str] = []
    discovered: list[tuple[str, str]] = []
    nodes = soup.select(
        ".modules_lecture .category a[href*='document_category_srl']"
    )
    for node in nodes:
        query, route_error = _source_route(node.get("href"))
        if route_error:
            errors.append(f"category discovery: {route_error}")
            continue
        category = query.get("document_category_srl", "")
        name = _clean(node.get_text(" ", strip=True))
        discovered.append((category, name))
    expected = [(CHUNGJU_REV_ALL_CATEGORY, "전체보기"), *CHUNGJU_REV_CATEGORIES.items()]
    if discovered != expected:
        errors.append("official category identifiers/labels/order changed")
    if len({category for category, _ in discovered}) != len(discovered):
        errors.append("duplicate category identity")
    return [row for row in discovered if row[0] != CHUNGJU_REV_ALL_CATEGORY], errors


def _parse_date_range(value: Any) -> tuple[date, date]:
    parts = [_clean(part) for part in _clean(value).split("~")]
    if len(parts) != 2 or not all(parts):
        raise ValueError("date range must have two endpoints")
    start_match = _FULL_DATE_RE.fullmatch(parts[0])
    if not start_match:
        raise ValueError("date range start must include a four-digit year")
    start_year, start_month, start_day = map(int, start_match.groups())
    start = date(start_year, start_month, start_day)

    full_end = _FULL_DATE_RE.fullmatch(parts[1])
    if full_end:
        return start, date(*map(int, full_end.groups()))
    month_day_end = _MONTH_DAY_RE.fullmatch(parts[1])
    if month_day_end:
        end_month, end_day = map(int, month_day_end.groups())
        end = date(start_year, end_month, end_day)
        if end < start:
            end = date(start_year + 1, end_month, end_day)
        return start, end
    day_end = _DAY_RE.fullmatch(parts[1])
    if day_end:
        end_day = int(day_end.group(1))
        end = date(start_year, start_month, end_day)
        if end < start:
            next_month = 1 if start_month == 12 else start_month + 1
            next_year = start_year + (1 if start_month == 12 else 0)
            end = date(next_year, next_month, end_day)
        return start, end
    raise ValueError("date range end has an unknown shape")


def _definition_value(item: Any, class_name: str) -> str:
    node = item.select_one(f"dd.{class_name}")
    return _clean(node.get_text(" ", strip=True)) if node is not None else ""


def _integer(value: Any) -> Optional[int]:
    raw = _clean(value).replace(",", "")
    return int(raw) if raw.isdigit() else None


def _first_integer(value: Any) -> Optional[int]:
    match = _INTEGER_RE.search(_clean(value))
    return int(match.group(0).replace(",", "")) if match else None


def _branch_code(category_id: str) -> str:
    return f"CHUNGJU_REV_BRANCH_{category_id}"


def _parse_list_page(
    soup: BeautifulSoup,
    *,
    category_id: str,
    category_name: str,
    page: int,
) -> tuple[list[dict[str, Any]], int, list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    empty_markers = 0
    roots = soup.select(".modules_lecture .list > ul")
    if len(roots) != 1:
        return rows, empty_markers, [
            f"category {category_id} page {page}: expected one course list"
        ]
    for item in roots[0].find_all("li", recursive=False):
        links = item.select("a[href*='action-value']")
        text = _clean(item.get_text(" ", strip=True))
        if not links:
            if text == "등록/검색된 정보가 없습니다.":
                empty_markers += 1
            else:
                errors.append(
                    f"category {category_id} page {page}: non-course list item"
                )
            continue
        if len(links) != 1:
            errors.append(
                f"category {category_id} page {page}: ambiguous course link"
            )
            continue
        route, route_error = _source_route(
            links[0].get("href"),
            expected_action="read",
            expected_category=category_id,
            expected_page=page,
        )
        if route_error:
            errors.append(
                f"category {category_id} page {page}: {route_error}"
            )
            continue
        identity = route.get("action-value", "")
        number_raw = _definition_value(item, "no").replace(",", "")
        title = _definition_value(item, "title")
        source_status = _definition_value(item, "regist")
        institution = _definition_value(item, "center")
        education_raw = _definition_value(item, "lecture_date")
        application_raw = _definition_value(item, "regist_date")
        capacity_raw = _definition_value(item, "capacity")
        applicant_raw = _definition_value(item, "count_regist")
        item_errors: list[str] = []
        if not number_raw.isdigit():
            item_errors.append("invalid category sequence")
        if not title:
            item_errors.append("empty title")
        if source_status not in _SOURCE_STATUS_MAP:
            item_errors.append("unknown registration status")
        if not institution:
            item_errors.append("empty institution")
        capacity = _integer(capacity_raw)
        applicants = _integer(applicant_raw)
        if capacity is None:
            item_errors.append("invalid capacity")
        if applicants is None:
            item_errors.append("invalid applicant count")
        try:
            education_start, education_end = _parse_date_range(education_raw)
        except (TypeError, ValueError):
            item_errors.append("invalid education period")
            education_start = education_end = date.min
        try:
            apply_start, apply_end = _parse_date_range(application_raw)
        except (TypeError, ValueError):
            item_errors.append("invalid application period")
            apply_start = apply_end = date.min
        if item_errors:
            errors.extend(
                f"{identity or '?'}: {message}" for message in item_errors
            )
            continue
        raw_url = chungju_rev_detail_url(category_id, identity)
        rows.append(
            {
                "provider": CHUNGJU_REV_PROVIDER,
                "provider_course_id": f"{CHUNGJU_REV_PROVIDER}:lecture:{identity}",
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "branch": institution,
                "branch_code": _branch_code(category_id),
                "preserve_branch": True,
                "provider_organizer": institution,
                "category": "주민자치센터강좌",
                "category_raw": category_name,
                "program_type": "강좌",
                "raw_url": raw_url,
                "application_url": "",
                "application_type": "INFO_ONLY",
                "status": _SOURCE_STATUS_MAP[source_status],
                "fee": "",
                "period": (
                    f"{education_start.isoformat()} ~ {education_end.isoformat()}"
                ),
                "start_date": education_start.isoformat(),
                "end_date": education_end.isoformat(),
                "apply_period": (
                    f"{apply_start.isoformat()} ~ {apply_end.isoformat()}"
                ),
                "apply_start": apply_start.isoformat(),
                "apply_end": apply_end.isoformat(),
                "schedule_raw": "",
                "capacity": capacity,
                "capacity_total": capacity,
                "capacity_current": applicants,
                "description": title,
                "source_group": "municipal_reservation",
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "operator_type": "지자체/공공기관",
                "collection_type": CHUNGJU_REV_PARSER,
                "raw_fields": {
                    "identity": identity,
                    "category_id": category_id,
                    "category_name": category_name,
                    "list_page": page,
                    "category_sequence": int(number_raw),
                    "source_status": source_status,
                    "list_institution": institution,
                    "list_education_period": education_raw,
                    "list_application_period": application_raw,
                    "list_capacity": capacity_raw,
                    "list_applicant_count": applicant_raw,
                    "historical_reversed_education_period": (
                        education_end < education_start
                    ),
                    "historical_reversed_application_period": (
                        apply_end < apply_start
                    ),
                },
            }
        )
    return rows, empty_markers, errors


def _table_pairs(root: Any) -> tuple[dict[str, str], list[str]]:
    pairs: dict[str, str] = {}
    errors: list[str] = []
    for row in root.select("table tr"):
        heading = row.select_one("th[scope='row']")
        value = row.select_one("td")
        if heading is None or value is None:
            continue
        key = _clean(heading.get_text(" ", strip=True))
        if not key:
            continue
        current = _clean(value.get_text(" ", strip=True))
        if key in pairs and pairs[key] != current:
            errors.append(f"duplicate detail label {key}")
        else:
            pairs[key] = current
    return pairs, errors


def _application_link(
    href: Any,
    *,
    category_id: str,
    identity: str,
) -> tuple[str, str]:
    route, error = _source_route(
        href,
        expected_action="write",
        expected_category=category_id,
        expected_identity=identity,
    )
    if error:
        return "", error
    return chungju_rev_application_url(
        route["document_category_srl"], route["action-value"]
    ), ""


def _validate_detail(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    raw_fields = row.setdefault("raw_fields", {})
    identity = _clean(raw_fields.get("identity"))
    category_id = _clean(raw_fields.get("category_id"))
    category_name = _clean(raw_fields.get("category_name"))
    errors: list[str] = []
    roots = soup.select(".modules_lecture .proc_read")
    if len(roots) != 1:
        return [f"{identity}: expected one course detail root"]
    root = roots[0]
    if len(root.select(":scope > table")) != 1:
        errors.append(f"{identity}: expected one primary detail table")
    pairs, pair_errors = _table_pairs(root)
    errors.extend(f"{identity}: {message}" for message in pair_errors)
    missing_labels = sorted(_DETAIL_LABELS - set(pairs))
    if missing_labels:
        errors.append(
            f"{identity}: missing detail labels {','.join(missing_labels)}"
        )
        return errors
    critical_values = (
        "권역 / 읍면동",
        "기관명",
        "강좌명",
        "접수방식",
        "교육 기간",
        "접수 기간",
        "정원",
        "선발방식",
        "모집연령",
        "수업료",
        "교육장주소",
    )
    empty_critical = [label for label in critical_values if not pairs.get(label)]
    if empty_critical:
        errors.append(
            f"{identity}: empty critical detail values {','.join(empty_critical)}"
        )
        return errors
    if pairs["강좌명"] != _clean(row.get("title")):
        errors.append(f"{identity}: detail/list title mismatch")
    if pairs["기관명"] != _clean(row.get("branch")):
        errors.append(f"{identity}: detail/list institution mismatch")
    town = pairs["권역 / 읍면동"]
    town_mismatch = bool(
        category_name not in town and not re.fullmatch(r"-\s*/\s*-", town)
    )
    try:
        detail_education = _parse_date_range(pairs["교육 기간"])
        expected_education = (
            date.fromisoformat(_clean(row.get("start_date"))),
            date.fromisoformat(_clean(row.get("end_date"))),
        )
        if detail_education != expected_education:
            errors.append(f"{identity}: detail/list education period mismatch")
    except (TypeError, ValueError):
        errors.append(f"{identity}: invalid detail education period")
    try:
        detail_application = _parse_date_range(pairs["접수 기간"])
        expected_application = (
            date.fromisoformat(_clean(row.get("apply_start"))),
            date.fromisoformat(_clean(row.get("apply_end"))),
        )
        if detail_application != expected_application:
            errors.append(f"{identity}: detail/list application period mismatch")
    except (TypeError, ValueError):
        errors.append(f"{identity}: invalid detail application period")

    detail_capacity = _first_integer(pairs["정원"])
    if detail_capacity is None or detail_capacity != row.get("capacity_total"):
        errors.append(f"{identity}: detail/list capacity mismatch")
    method = pairs["접수방식"]
    if not any(token in method for token in ("온라인", "방문", "전화", "현장")):
        errors.append(f"{identity}: unknown application method")

    write_controls = root.select("a.action_write[href]")
    if not write_controls:
        errors.append(f"{identity}: missing application controls")
    direct_links: set[str] = set()
    login_controls = 0
    for control in write_controls:
        href = _clean(control.get("href"))
        if href == "#":
            onclick = _clean(control.get("onclick"))
            if "로그인" in onclick and "alert" in onclick and "return false" in onclick:
                login_controls += 1
            else:
                errors.append(f"{identity}: unknown disabled application control")
            continue
        direct_url, direct_error = _application_link(
            href,
            category_id=category_id,
            identity=identity,
        )
        if direct_error:
            errors.append(f"{identity}: {direct_error}")
        elif direct_url:
            direct_links.add(direct_url)
    if len(direct_links) > 1:
        errors.append(f"{identity}: conflicting application endpoints")
    direct_url = next(iter(direct_links), "")
    source_status = _clean(raw_fields.get("source_status"))
    if direct_url and source_status != "접수중":
        errors.append(f"{identity}: non-open course exposes application endpoint")

    branch = pairs["기관명"]
    priority = pairs.get("우선접수대상", "")
    age = pairs.get("모집연령", "")
    target = " · ".join(dict.fromkeys(v for v in (priority, age) if v))
    schedule = " / ".join(
        value
        for value in (pairs.get("교육요일", ""), pairs.get("수업시간", ""))
        if value
    )
    row.update(
        {
            "branch": branch,
            "provider_organizer": branch,
            "branch_area": town,
            "application_url": direct_url if source_status == "접수중" else "",
            "application_method_raw": method,
            "target": target,
            "eligibility_raw": target,
            "fee": pairs.get("수업료", ""),
            "schedule_raw": schedule,
            "instructor": pairs.get("강사", ""),
            "venue_name": pairs.get("교육장", ""),
            "address": pairs.get("교육장주소", ""),
            "venue_address": pairs.get("교육장주소", ""),
            "contact": pairs.get("문의 연락처", ""),
            "description": pairs.get("수업 내용") or _clean(row.get("title")),
            "reservation_available": source_status == "접수중",
        }
    )
    if row["application_url"]:
        row["application_type"] = "ONLINE_RESERVATION"
    elif source_status == "접수중" and "온라인" not in method and any(
        token in method for token in ("방문", "전화", "현장")
    ):
        row["application_type"] = "OFFLINE_APPLY"
    else:
        row["application_type"] = "INFO_ONLY"
    raw_fields.update(
        {
            "detail_pairs": pairs,
            "detail_town": town,
            "detail_category_town_mismatch": town_mismatch,
            "application_control_count": len(write_controls),
            "login_gated_application_control_count": login_controls,
            "direct_application_control_count": len(direct_links),
        }
    )
    return errors


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if value not in (None, "", [], {})
    }


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _failure(message: str, *, source_cap_reached: bool = False) -> dict[str, Any]:
    return {
        "pages": 0,
        "main_discovery_pages": 0,
        "list_requests": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "source_total": 0,
        "source_rows": 0,
        "current_count": 0,
        "returned_count": 0,
        "discovered_categories": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": source_cap_reached,
        "no_current_data": False,
        "configured_collection_error": message,
        "ownership_scope": CHUNGJU_REV_OWNERSHIP_SCOPE,
        "ownership_disjoint_from": [CHUNGJU_GOODEDU_PROVIDER],
    }


def collect_chungju_rev_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 100,
    detail_limit: int = 700,
    *,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = CHUNGJU_REV_MAX_WORKERS,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return a fully validated current/future Chungju rev snapshot."""

    if not is_chungju_rev_target(target):
        return [], CHUNGJU_REV_PARSER, _failure(
            "target does not match the canonical Chungju rev route"
        )
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], CHUNGJU_REV_PARSER, _failure(
                "managed session_factory injection is required"
            )
        session_factory = _default_session_factory
    try:
        allowed_pages = max(0, int(max_pages))
        allowed_details = max(0, int(detail_limit))
        cutoff = _today(today)
        workers = min(max(1, int(max_workers)), CHUNGJU_REV_MAX_WORKERS)
    except (TypeError, ValueError):
        return [], CHUNGJU_REV_PARSER, _failure(
            "max_pages/detail_limit/max_workers/today are invalid"
        )

    errors: list[str] = []
    source_cap_reached = False
    entry_key = (CHUNGJU_REV_ENTRY_CATEGORY, 1)
    page_soups, fetch_errors = _parallel_fetch(
        [(entry_key, CHUNGJU_REV_URL)],
        session_factory=session_factory,
        timeout=timeout,
        max_workers=1,
    )
    errors.extend(fetch_errors)
    first = page_soups.get(entry_key)
    categories: list[tuple[str, str]] = []
    if first is None:
        errors.append("missing category discovery page")
    else:
        categories, discovery_errors = _discover_categories(first)
        errors.extend(discovery_errors)

    if not errors:
        other_first, first_errors = _parallel_fetch(
            [
                ((category_id, 1), chungju_rev_list_url(category_id, 1))
                for category_id, _ in categories
                if category_id != CHUNGJU_REV_ENTRY_CATEGORY
            ],
            session_factory=session_factory,
            timeout=timeout,
            max_workers=workers,
        )
        page_soups.update(other_first)
        errors.extend(first_errors)

    category_totals: dict[str, int] = {}
    category_data_pages: dict[str, int] = {}
    if not errors:
        for category_id, _ in categories:
            soup = page_soups.get((category_id, 1))
            if soup is None:
                errors.append(f"category {category_id}: missing first page")
                continue
            contract = _counter(soup)
            if contract is None:
                errors.append(f"category {category_id}: missing source counter")
                continue
            total, displayed_page, advertised_last = contract
            computed_last = max(1, math.ceil(total / CHUNGJU_REV_PAGE_SIZE))
            if displayed_page != 1 or advertised_last != computed_last:
                errors.append(
                    f"category {category_id}: inconsistent first-page counter"
                )
                continue
            category_totals[category_id] = total
            category_data_pages[category_id] = computed_last

    required_list_requests = sum(
        data_pages + 1 for data_pages in category_data_pages.values()
    )
    if len(category_data_pages) == len(CHUNGJU_REV_CATEGORIES):
        if required_list_requests > allowed_pages:
            source_cap_reached = True
            errors.append(
                f"max_pages cap allows {allowed_pages} of "
                f"{required_list_requests} required list requests"
            )
    elif not errors:
        errors.append("not all official category counters were parsed")

    if not errors:
        requested = set(page_soups)
        remaining_items = [
            ((category_id, page), chungju_rev_list_url(category_id, page))
            for category_id, _ in categories
            for page in range(1, category_data_pages[category_id] + 2)
            if (category_id, page) not in requested
        ]
        remaining, remaining_errors = _parallel_fetch(
            remaining_items,
            session_factory=session_factory,
            timeout=timeout,
            max_workers=workers,
        )
        page_soups.update(remaining)
        errors.extend(remaining_errors)

    listed_rows: list[dict[str, Any]] = []
    page_counts: dict[str, int] = {}
    sentinel_marker_counts: dict[str, int] = {}
    if not errors:
        for category_id, category_name in categories:
            total = category_totals[category_id]
            data_pages = category_data_pages[category_id]
            category_rows: list[dict[str, Any]] = []
            category_sequences: list[int] = []
            for page in range(1, data_pages + 2):
                soup = page_soups.get((category_id, page))
                if soup is None:
                    errors.append(
                        f"category {category_id} page {page}: missing response"
                    )
                    continue
                if _counter(soup) != (total, page, data_pages):
                    errors.append(
                        f"category {category_id} page {page}: source counter changed"
                    )
                parsed, empty_markers, page_errors = _parse_list_page(
                    soup,
                    category_id=category_id,
                    category_name=category_name,
                    page=page,
                )
                errors.extend(page_errors)
                page_counts[f"{category_id}:{page}"] = len(parsed)
                if page <= data_pages:
                    if empty_markers:
                        errors.append(
                            f"category {category_id} page {page}: unexpected empty marker"
                        )
                    category_rows.extend(parsed)
                    category_sequences.extend(
                        int(row["raw_fields"]["category_sequence"])
                        for row in parsed
                    )
                else:
                    sentinel_marker_counts[category_id] = empty_markers
                    if parsed or empty_markers != 1:
                        errors.append(
                            f"category {category_id}: immediate sentinel is not empty"
                        )
            for page in range(1, data_pages):
                if page_counts.get(f"{category_id}:{page}") != CHUNGJU_REV_PAGE_SIZE:
                    errors.append(
                        f"category {category_id} page {page}: non-terminal page is not full"
                    )
            expected_terminal = total - CHUNGJU_REV_PAGE_SIZE * (data_pages - 1)
            if total == 0:
                expected_terminal = 0
            if page_counts.get(f"{category_id}:{data_pages}") != expected_terminal:
                errors.append(
                    f"category {category_id}: terminal page row count mismatch"
                )
            if len(category_rows) != total:
                errors.append(
                    f"category {category_id}: declared {total} != parsed {len(category_rows)}"
                )
            if category_sequences != list(range(total, 0, -1)):
                errors.append(
                    f"category {category_id}: sequence is not a complete descending range"
                )
            listed_rows.extend(category_rows)

    source_total = sum(category_totals.values())
    identities = [
        _clean(row.get("raw_fields", {}).get("identity")) for row in listed_rows
    ]
    duplicate_identity_count = len(identities) - len(set(identities))
    if duplicate_identity_count:
        errors.append(f"{duplicate_identity_count} duplicate source identities")
    raw_urls = [_clean(row.get("raw_url")) for row in listed_rows]
    duplicate_url_count = len(raw_urls) - len(set(raw_urls))
    if duplicate_url_count:
        errors.append(f"{duplicate_url_count} duplicate canonical detail URLs")
    if listed_rows and len(listed_rows) != source_total:
        errors.append(
            f"declared total {source_total} != parsed rows {len(listed_rows)}"
        )

    current_rows: list[dict[str, Any]] = []
    expired_count = 0
    historical_reversed_education_count = 0
    historical_reversed_application_count = 0
    for row in listed_rows:
        raw_fields = row.get("raw_fields", {})
        try:
            start = date.fromisoformat(_clean(row.get("start_date")))
            end = date.fromisoformat(_clean(row.get("end_date")))
            apply_start = date.fromisoformat(_clean(row.get("apply_start")))
            apply_end = date.fromisoformat(_clean(row.get("apply_end")))
        except ValueError:
            errors.append(
                f"{_clean(row.get('provider_course_id'))}: invalid normalized dates"
            )
            continue
        if end < cutoff:
            expired_count += 1
            if end < start:
                historical_reversed_education_count += 1
            if apply_end < apply_start:
                historical_reversed_application_count += 1
        else:
            current_rows.append(row)
            if end < start:
                errors.append(
                    f"{_clean(row.get('provider_course_id'))}: current course has reversed education period"
                )
            if apply_end < apply_start:
                errors.append(
                    f"{_clean(row.get('provider_course_id'))}: current course has reversed application period"
                )
        if bool(raw_fields.get("historical_reversed_education_period")) != (end < start):
            errors.append("normalized education reversal flag mismatch")

    list_complete = bool(
        not errors
        and len(categories) == len(CHUNGJU_REV_CATEGORIES)
        and len(page_soups) == required_list_requests
        and len(listed_rows) == source_total
    )
    required_details = source_total
    detail_attempts = 0
    detail_pages = 0
    detail_errors: list[str] = []
    if required_details > allowed_details:
        source_cap_reached = True
        errors.append(
            f"detail_limit cap allows {allowed_details} of "
            f"{required_details} required source details"
        )
    elif list_complete and listed_rows:
        detail_attempts = required_details
        detail_soups, detail_fetch_errors = _parallel_fetch(
            [
                (
                    _clean(row.get("raw_fields", {}).get("identity")),
                    _clean(row.get("raw_url")),
                )
                for row in listed_rows
            ],
            session_factory=session_factory,
            timeout=timeout,
            max_workers=workers,
        )
        detail_errors.extend(detail_fetch_errors)
        rows_by_identity = {
            _clean(row.get("raw_fields", {}).get("identity")): row
            for row in listed_rows
        }
        for identity, soup in detail_soups.items():
            item_errors = _validate_detail(rows_by_identity[identity], soup)
            if item_errors:
                detail_errors.extend(item_errors)
            else:
                detail_pages += 1
    errors.extend(detail_errors)
    details_complete = bool(
        list_complete
        and detail_attempts == required_details
        and detail_pages == required_details
        and not detail_errors
    )

    result: list[dict[str, Any]] = []
    if list_complete and details_complete and not errors:
        deduper = dedupe_rows or _dedupe_default
        result = list(deduper([_clean_row(row) for row in current_rows]))
        if len(result) != len(current_rows):
            errors.append(
                f"dedupe changed complete row count {len(current_rows)} to {len(result)}"
            )
            result = []
    snapshot_complete = bool(list_complete and details_complete and not errors)
    if not snapshot_complete:
        result = []

    source_status_counts = Counter(
        _clean(row.get("raw_fields", {}).get("source_status"))
        for row in listed_rows
    )
    current_status_counts = Counter(
        _clean(row.get("raw_fields", {}).get("source_status"))
        for row in current_rows
    )
    source_branch_counts = Counter(_clean(row.get("branch")) for row in listed_rows)
    branch_counts = Counter(_clean(row.get("branch")) for row in result)
    current_category_counts = Counter(
        _clean(row.get("category_raw")) for row in result
    )
    direct_application_all = sum(
        bool(row.get("application_url")) for row in listed_rows
    )
    login_gated_control_rows = sum(
        bool(
            row.get("raw_fields", {}).get(
                "login_gated_application_control_count"
            )
        )
        for row in listed_rows
    )
    detail_category_town_mismatch_count = sum(
        bool(
            row.get("raw_fields", {}).get("detail_category_town_mismatch")
        )
        for row in listed_rows
    )
    current_detail_category_town_mismatch_count = sum(
        bool(
            row.get("raw_fields", {}).get("detail_category_town_mismatch")
        )
        for row in current_rows
    )
    empty_detail_venue_count = sum(
        not _clean(row.get("venue_name")) for row in listed_rows
    )
    meta = {
        "pages": len(page_soups),
        "main_discovery_pages": 1 if first is not None else 0,
        "list_requests": len(page_soups),
        "data_pages": sum(category_data_pages.values()),
        "sentinel_pages": len(sentinel_marker_counts),
        "required_list_requests": required_list_requests,
        "detail_attempts": detail_attempts,
        "detail_pages": detail_pages,
        "detail_errors": len(detail_errors),
        "source_total": source_total,
        "source_rows": len(listed_rows),
        "expired_count": expired_count,
        "current_count": len(current_rows),
        "returned_count": len(result),
        "discovered_categories": len(categories),
        "source_category_count": len(
            {_clean(row.get("category_raw")) for row in listed_rows}
        ),
        "current_category_count": len(current_category_counts),
        "category_totals": category_totals,
        "category_data_pages": category_data_pages,
        "page_counts": page_counts,
        "sentinel_marker_counts": sentinel_marker_counts,
        "source_branch_count": len(source_branch_counts),
        "branch_count": len(branch_counts),
        "branch_counts": dict(branch_counts),
        "current_category_counts": dict(current_category_counts),
        "source_status_counts": dict(source_status_counts),
        "current_status_counts": dict(current_status_counts),
        "duplicate_count": duplicate_identity_count,
        "duplicate_url_count": duplicate_url_count,
        "historical_reversed_education_period_count": (
            historical_reversed_education_count
        ),
        "historical_reversed_application_period_count": (
            historical_reversed_application_count
        ),
        "detail_list_mismatch_count": sum(
            "detail/list" in message for message in detail_errors
        ),
        "detail_category_town_mismatch_count": (
            detail_category_town_mismatch_count
        ),
        "current_detail_category_town_mismatch_count": (
            current_detail_category_town_mismatch_count
        ),
        "empty_detail_venue_count": empty_detail_venue_count,
        "application_control_rows": (
            source_total if details_complete else detail_pages
        ),
        "login_gated_application_control_rows": login_gated_control_rows,
        "direct_application_links_all": direct_application_all,
        "discovered_links": len(listed_rows),
        "reservation_discovery_links": sum(
            bool(row.get("application_url")) for row in result
        ),
        "pagination_detected": any(
            value > 1 for value in category_data_pages.values()
        ),
        "pagination_complete": list_complete,
        "details_complete": details_complete,
        "snapshot_complete": snapshot_complete,
        "source_cap_reached": source_cap_reached,
        "no_current_data": bool(snapshot_complete and not current_rows),
        "no_current_reason": (
            "all complete Chungju rev resident-centre courses have ended"
            if snapshot_complete and not current_rows
            else ""
        ),
        "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        "ownership_scope": CHUNGJU_REV_OWNERSHIP_SCOPE,
        "ownership_disjoint_from": [CHUNGJU_GOODEDU_PROVIDER],
    }
    return result, CHUNGJU_REV_PARSER, meta


collect = collect_chungju_rev_courses


__all__ = [
    "CHUNGJU_GOODEDU_PROVIDER",
    "CHUNGJU_MUNICIPALITY_CODE",
    "CHUNGJU_MUNICIPALITY_NAME",
    "CHUNGJU_REV_ALL_CATEGORY",
    "CHUNGJU_REV_CATEGORIES",
    "CHUNGJU_REV_ENTRY_CATEGORY",
    "CHUNGJU_REV_HOST",
    "CHUNGJU_REV_MAX_WORKERS",
    "CHUNGJU_REV_OWNERSHIP_SCOPE",
    "CHUNGJU_REV_PAGE_SIZE",
    "CHUNGJU_REV_PARSER",
    "CHUNGJU_REV_PATH",
    "CHUNGJU_REV_PROVIDER",
    "CHUNGJU_REV_URL",
    "chungju_rev_application_url",
    "chungju_rev_detail_url",
    "chungju_rev_list_url",
    "collect",
    "collect_chungju_rev_courses",
    "is_chungju_rev_target",
    "is_target",
]
