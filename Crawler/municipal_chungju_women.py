"""Fail-closed collector for Chungju Women's Culture Center courses.

The official Chungju integrated-reservation route ``/rev/reserve/451`` is a
small, independent mixed education/experience ledger.  It is not part of the
GoodEdu catalogue or the resident-centre ``/rev/reserve/99`` owner.

Only public catalogue pages and current/future ``action=read`` details are
requested.  Login, application, application-check, identity, applicant,
attachment, preview, and download routes are never requested.  The source is
returned atomically only after its declared pages, immediate empty sentinel,
stable first-page recheck, exact per-course classification, and every
current/future public detail have been verified.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import html
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


CHUNGJU_WOMEN_PROVIDER = "MUNI_WWW_CHUNGJU_GO_KR_BDA0BB78"
CHUNGJU_WOMEN_HOST = "www.chungju.go.kr"
CHUNGJU_WOMEN_PATH = "/rev/reserve/451"
CHUNGJU_WOMEN_URL = f"https://{CHUNGJU_WOMEN_HOST}{CHUNGJU_WOMEN_PATH}"
CHUNGJU_WOMEN_PAGE_SIZE = 20
CHUNGJU_WOMEN_BRANCH = "충주여성문화회관"
CHUNGJU_WOMEN_BRANCH_CODE = "CHUNGJU_WOMEN_CULTURE_CENTER"
CHUNGJU_WOMEN_MUNICIPALITY_CODE = "4313000000"
CHUNGJU_WOMEN_MUNICIPALITY_NAME = "충청북도 충주시"
CHUNGJU_WOMEN_OWNERSHIP_SCOPE = (
    "chungju_rev_451_womens_culture_mixed_current_future"
)
CHUNGJU_GOODEDU_PROVIDER = "MUNI_GOODEDU_CHUNGJU_GO_KR_66F13E51"
CHUNGJU_RESIDENT_PROVIDER = "MUNI_WWW_CHUNGJU_GO_KR_7EE8620A"
CHUNGJU_WOMEN_PARSER = (
    "chungju_rev_451_complete_mixed_ledger+declared_pages+exact_empty_sentinel+"
    "stable_page1+current_public_details+exact_course_classification+"
    "login_controls_observed_not_called+no_application_check_attachment_download_or_pii_calls"
)

# Exact audited title semantics.  Unknown titles fail the complete snapshot
# instead of being guessed into education or experience.
CHUNGJU_WOMEN_CLASSIFICATIONS: Mapping[str, str] = {
    "(정규강좌) 다이어트댄스(라인)": "education",
    "(정규강좌) 차밍스트레칭&근력": "education",
    "(정규강좌) 스크린파크골프 A반": "education",
    "(특별강좌) 우리가족 달콤한 하루:쌀 클레이": "experience",
    "(특별강좌) 평생월급 국민연금 더 받는 방법": "education",
    "(특별강좌) 은퇴후 건강보험료 절감 방법": "education",
    "(특별강좌) 우리가족 초록추억: 다육이 화분만들기(도우아트)": "experience",
    "(특별강좌) 자식보다 필요한 노인장기요양보험": "education",
    "(특별강좌) 슬기로운 자산관리 상속 vs 증여": "education",
    "선착순추가모집★ (특별강좌) AI를 활용한 이력서 코칭 및 자기소개서 작성법": "education",
    "(정규강좌) 방과후 학교지도사": "education",
    "(특별강좌) 향으로 쉬는 날: 아로마": "experience",
    "(특별강좌) 힐링타임 : 수경식물 가꾸기": "experience",
    "(특별강좌) 감성느낌 가죽공예": "experience",
    "(특별강좌) 행복한 정리수납": "education",
    "(특별강좌) 나를 돋보이는 퍼스널컬러": "experience",
}

SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"[0-9a-f]{32}\Z")
_COUNTER_RE = re.compile(
    r"총\s*강좌\s*수\s*:\s*([\d,]+)\s*건\s*"
    r"\(\s*총\s*(\d+)\s*페이지\s*중\s*(\d+)\s*페이지\s*\)"
)
_FULL_DATE_RE = re.compile(
    r"(20\d{2})-(\d{1,2})-(\d{1,2})"
    r"(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?\Z"
)
_MONTH_DAY_RE = re.compile(
    r"(\d{1,2})-(\d{1,2})(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?\Z"
)
_DAY_RE = re.compile(r"(\d{1,2})(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?\Z")
_INTEGER_RE = re.compile(r"[\d,]+")

_SOURCE_STATUS_MAP: Mapping[str, str] = {
    "준비중": "SCHEDULED",
    "접수예정": "SCHEDULED",
    "접수중": "OPEN",
    "접수마감": "CLOSED",
    "접수종료": "CLOSED",
}
_DETAIL_REQUIRED_LABELS = frozenset(
    {
        "권역 / 읍면동",
        "기관명",
        "강좌명",
        "접수방식",
        "교육 기간",
        "접수 기간",
        "정원",
        "선발방식",
        "우선접수대상",
        "모집연령",
        "수업료",
        "교육장",
        "교육장주소",
    }
)
_FORBIDDEN_FETCH_TOKENS = (
    "action=write",
    "action=check",
    "/login",
    "/member",
    "/applicant",
    "/identity",
    "/file/preview",
    "/file/download",
    "attachment",
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(
        " ", html.unescape(str(value or "")).replace("\xa0", " ")
    ).strip()


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def is_chungju_women_target(target: Any) -> bool:
    parsed = urlparse(_clean(_target_value(target, "url")))
    return bool(
        _clean(_target_value(target, "provider")) == CHUNGJU_WOMEN_PROVIDER
        and parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == CHUNGJU_WOMEN_HOST
        and parsed.port is None
        and parsed.path == CHUNGJU_WOMEN_PATH
        and not parsed.query
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


is_target = is_chungju_women_target


def chungju_women_list_url(page: Any) -> str:
    raw = _clean(page)
    if not raw.isdigit() or int(raw) < 1:
        return ""
    if int(raw) == 1:
        return CHUNGJU_WOMEN_URL
    return f"{CHUNGJU_WOMEN_URL}?{urlencode({'page': int(raw)})}"


def chungju_women_detail_url(identity: Any) -> str:
    raw = _clean(identity)
    if not _IDENTITY_RE.fullmatch(raw):
        return ""
    return f"{CHUNGJU_WOMEN_URL}?{urlencode({'action': 'read', 'action-value': raw})}"


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    return session


def _safe_fetch_shape(url: str) -> bool:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != CHUNGJU_WOMEN_HOST
        or parsed.port is not None
        or parsed.path != CHUNGJU_WOMEN_PATH
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        return False
    lowered = url.lower()
    if any(token in lowered for token in _FORBIDDEN_FETCH_TOKENS):
        return False
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if not pairs:
        return url == CHUNGJU_WOMEN_URL
    if len(pairs) == 1 and pairs[0][0] == "page":
        return pairs[0][1].isdigit() and int(pairs[0][1]) >= 2
    if [key for key, _ in pairs] == ["action", "action-value"]:
        return pairs[0][1] == "read" and bool(
            _IDENTITY_RE.fullmatch(pairs[1][1])
        )
    return False


def _response_soup(response: Any, requested_url: str) -> BeautifulSoup:
    try:
        status = int(getattr(response, "status_code", 0))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise ValueError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise ValueError("HTTP redirects are not accepted")
    final_url = _clean(getattr(response, "url", ""))
    if final_url and final_url != requested_url:
        raise ValueError("final response URL changed")
    content = getattr(response, "content", None)
    if content is None:
        content = getattr(response, "text", None)
    if not content:
        raise ValueError("empty HTML response")
    if isinstance(content, (bytes, bytearray)) and len(content) > 3_000_000:
        raise ValueError("HTML response exceeds size limit")
    return BeautifulSoup(content, "lxml")


def _fetch_soup(session: Any, url: str, timeout: int) -> BeautifulSoup:
    if not _safe_fetch_shape(url):
        raise ValueError("unsafe Chungju women-culture fetch route")
    response = session.get(url, timeout=timeout, allow_redirects=False)
    return _response_soup(response, url)


def _counter(soup: BeautifulSoup) -> Optional[tuple[int, int, int]]:
    nodes = soup.select(".modules_lecture .count")
    if len(nodes) != 1:
        return None
    match = _COUNTER_RE.fullmatch(_clean(nodes[0].get_text(" ", strip=True)))
    if not match:
        return None
    return (
        int(match.group(1).replace(",", "")),
        int(match.group(3)),
        int(match.group(2)),
    )


def _parse_date_range(value: Any) -> tuple[date, date]:
    parts = [_clean(part) for part in _clean(value).split("~")]
    if len(parts) != 2 or not all(parts):
        raise ValueError("date range must have two endpoints")
    start_match = _FULL_DATE_RE.fullmatch(parts[0])
    if not start_match:
        raise ValueError("date range start must include a year")
    start_year, start_month, start_day = map(int, start_match.groups())
    start = date(start_year, start_month, start_day)

    full_end = _FULL_DATE_RE.fullmatch(parts[1])
    if full_end:
        return start, date(*map(int, full_end.groups()))
    month_end = _MONTH_DAY_RE.fullmatch(parts[1])
    if month_end:
        month, day = map(int, month_end.groups())
        end = date(start_year, month, day)
        if end < start:
            end = date(start_year + 1, month, day)
        return start, end
    day_end = _DAY_RE.fullmatch(parts[1])
    if day_end:
        end = date(start_year, start_month, int(day_end.group(1)))
        if end < start:
            month = 1 if start_month == 12 else start_month + 1
            year = start_year + (1 if start_month == 12 else 0)
            end = date(year, month, int(day_end.group(1)))
        return start, end
    raise ValueError("date range end has an unknown shape")


def _definition_value(root: Any, class_name: str) -> str:
    node = root.select_one(f"dd.{class_name}")
    return _clean(node.get_text(" ", strip=True)) if node is not None else ""


def _integer(value: Any) -> Optional[int]:
    raw = _clean(value).replace(",", "")
    return int(raw) if raw.isdigit() else None


def _first_integer(value: Any) -> Optional[int]:
    match = _INTEGER_RE.search(_clean(value))
    return int(match.group(0).replace(",", "")) if match else None


def _course_route(href: Any, expected_action: str) -> tuple[str, str]:
    parsed = urlparse(urljoin(CHUNGJU_WOMEN_URL, _clean(href)))
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != CHUNGJU_WOMEN_HOST
        or parsed.port is not None
        or parsed.path != CHUNGJU_WOMEN_PATH
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        return "", "unexpected Chungju women-culture route"
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if [key for key, _ in pairs] != ["action", "action-value"]:
        return "", "unexpected Chungju women-culture query"
    if pairs[0][1] != expected_action:
        return "", "unexpected course action"
    identity = pairs[1][1]
    if not _IDENTITY_RE.fullmatch(identity):
        return "", "malformed course identity"
    return identity, ""


def _classification(title: str) -> tuple[str, str]:
    family = CHUNGJU_WOMEN_CLASSIFICATIONS.get(title, "")
    if not family:
        return "", "unknown exact course classification"
    return family, ""


def _parse_list_page(
    soup: BeautifulSoup, *, page: int
) -> tuple[list[dict[str, Any]], int, list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    empty_markers = 0
    roots = soup.select(".modules_lecture .list > ul")
    if len(roots) != 1:
        return rows, empty_markers, [f"page {page}: expected one course list"]
    for item in roots[0].find_all("li", recursive=False):
        links = item.select("a[href*='action-value']")
        text = _clean(item.get_text(" ", strip=True))
        if not links:
            if text == "등록/검색된 정보가 없습니다.":
                empty_markers += 1
            else:
                errors.append(f"page {page}: non-course list item")
            continue
        if len(links) != 1:
            errors.append(f"page {page}: ambiguous course link")
            continue
        identity, route_error = _course_route(links[0].get("href"), "read")
        if route_error:
            errors.append(f"page {page}: {route_error}")
            continue
        number_raw = _definition_value(item, "no").replace(",", "")
        title = _definition_value(item, "title")
        status_raw = _definition_value(item, "regist")
        branch = _definition_value(item, "center")
        education_raw = _definition_value(item, "lecture_date")
        application_raw = _definition_value(item, "regist_date")
        capacity_raw = _definition_value(item, "capacity")
        applicants_raw = _definition_value(item, "count_regist")
        item_errors: list[str] = []
        if not number_raw.isdigit():
            item_errors.append("invalid source sequence")
        if not title:
            item_errors.append("empty title")
        family, classification_error = _classification(title)
        if classification_error:
            item_errors.append(classification_error)
        if status_raw not in _SOURCE_STATUS_MAP:
            item_errors.append("unknown registration status")
        if branch != CHUNGJU_WOMEN_BRANCH:
            item_errors.append("unexpected institution")
        capacity = _integer(capacity_raw)
        applicants = _integer(applicants_raw)
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
            errors.extend(f"{identity or '?'}: {message}" for message in item_errors)
            continue
        is_experience = family == "experience"
        label = "체험" if is_experience else "교육"
        rows.append(
            {
                "provider": CHUNGJU_WOMEN_PROVIDER,
                "provider_course_id": f"{CHUNGJU_WOMEN_PROVIDER}:lecture:{identity}",
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "branch": branch,
                "branch_code": CHUNGJU_WOMEN_BRANCH_CODE,
                "preserve_branch": True,
                "provider_organizer": branch,
                "category": f"여성문화회관 {label}",
                "category_raw": "여성문화회관강좌",
                "program_type": label,
                "raw_url": chungju_women_detail_url(identity),
                "application_url": "",
                "application_type": "INFO_ONLY",
                "status": _SOURCE_STATUS_MAP[status_raw],
                "fee": "",
                "period": f"{education_start.isoformat()} ~ {education_end.isoformat()}",
                "start_date": education_start.isoformat(),
                "end_date": education_end.isoformat(),
                "apply_period": f"{apply_start.isoformat()} ~ {apply_end.isoformat()}",
                "apply_start": apply_start.isoformat(),
                "apply_end": apply_end.isoformat(),
                "capacity": capacity,
                "capacity_total": capacity,
                "capacity_current": applicants,
                "description": title,
                "source_group": "municipal_reservation",
                "collection_category": "공공예약",
                "domain_category": "체험·견학" if is_experience else "교육·강좌",
                "service_group": "체험" if is_experience else "공공강좌",
                "service_group_policy": "locked",
                "service_family": family,
                "operator_type": "지자체/공공기관",
                "collection_type": CHUNGJU_WOMEN_PARSER,
                "raw_fields": {
                    "identity": identity,
                    "list_page": page,
                    "source_sequence": int(number_raw),
                    "source_status": status_raw,
                    "source_education_period": education_raw,
                    "source_application_period": application_raw,
                    "service_family": family,
                    "classification_locked": True,
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
        current = _clean(value.get_text(" ", strip=True))
        if not key:
            continue
        if key in pairs and pairs[key] != current:
            errors.append(f"duplicate detail label {key}")
        else:
            pairs[key] = current
    return pairs, errors


def _validate_disabled_control(control: Any) -> bool:
    return bool(
        _clean(control.get("href")) == "#"
        and "로그인" in _clean(control.get("onclick"))
        and "alert" in _clean(control.get("onclick"))
        and "return false" in _clean(control.get("onclick"))
    )


def _validate_attachment_control(control: Any) -> bool:
    parsed = urlparse(urljoin(CHUNGJU_WOMEN_URL, _clean(control.get("href"))))
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == CHUNGJU_WOMEN_HOST
        and parsed.port is None
        and parsed.path.startswith(("/rev/File/Preview/", "/rev/File/Download/"))
        and not parsed.query
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


def _validate_detail(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    raw = row.setdefault("raw_fields", {})
    identity = _clean(raw.get("identity"))
    errors: list[str] = []
    roots = soup.select(".modules_lecture .proc_read")
    if len(roots) != 1:
        return [f"{identity}: expected one course detail root"]
    root = roots[0]
    if len(root.select(":scope > table")) != 1:
        errors.append(f"{identity}: expected one primary detail table")
    if root.select("form"):
        errors.append(f"{identity}: unexpected detail form")
    pairs, pair_errors = _table_pairs(root)
    errors.extend(f"{identity}: {message}" for message in pair_errors)
    missing = sorted(_DETAIL_REQUIRED_LABELS - set(pairs))
    if missing:
        errors.append(f"{identity}: missing detail labels {','.join(missing)}")
        return errors
    if pairs["강좌명"] != _clean(row.get("title")):
        errors.append(f"{identity}: detail/list title mismatch")
    if pairs["기관명"] != CHUNGJU_WOMEN_BRANCH:
        errors.append(f"{identity}: detail/list institution mismatch")
    family, classification_error = _classification(pairs["강좌명"])
    if classification_error or family != _clean(row.get("service_family")):
        errors.append(f"{identity}: detail classification mismatch")
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
    if _first_integer(pairs["정원"]) != row.get("capacity_total"):
        errors.append(f"{identity}: detail/list capacity mismatch")
    if pairs["접수방식"] != "온라인":
        errors.append(f"{identity}: unexpected application method")

    write_controls = root.select("a.action_write[href]")
    check_controls = root.select("a.action_check[href]")
    if len(write_controls) != 2 or len(check_controls) != 2:
        errors.append(f"{identity}: application control count changed")
    direct_controls = 0
    disabled_controls = 0
    for control in write_controls:
        if _validate_disabled_control(control):
            disabled_controls += 1
            continue
        direct_identity, route_error = _course_route(control.get("href"), "write")
        if route_error or direct_identity != identity:
            errors.append(f"{identity}: unsafe application control")
        else:
            direct_controls += 1
    for control in check_controls:
        if not _validate_disabled_control(control):
            errors.append(f"{identity}: unsafe application-check control")
    if _clean(raw.get("source_status")) != "접수중" and direct_controls:
        errors.append(f"{identity}: non-open course exposes direct application")
    attachments = root.select("a.action_preview[href], a.action_download[href]")
    if any(not _validate_attachment_control(control) for control in attachments):
        errors.append(f"{identity}: unsafe attachment control")

    row.update(
        {
            "application_url": "",
            "application_type": "INFO_ONLY",
            "application_method_raw": "온라인",
            "target": pairs["모집연령"],
            "eligibility_raw": " · ".join(
                value
                for value in (pairs["우선접수대상"], pairs["모집연령"])
                if value
            ),
            "fee": pairs["수업료"],
            "venue_name": pairs["교육장"],
            "address": pairs["교육장주소"],
            "venue_address": pairs["교육장주소"],
            "reservation_available": False,
        }
    )
    raw.update(
        {
            "detail_verified": not errors,
            "application_control_count": len(write_controls),
            "application_check_control_count": len(check_controls),
            "disabled_login_control_count": disabled_controls,
            "direct_application_control_count": direct_controls,
            "attachment_control_count": len(attachments),
            "application_endpoint_fetched": False,
            "application_check_endpoint_fetched": False,
            "attachment_endpoint_fetched": False,
            "pii_endpoint_fetched": False,
        }
    )
    return errors


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
        "list_requests": 0,
        "data_pages": 0,
        "sentinel_pages": 0,
        "stable_rechecks": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "source_total": 0,
        "source_rows": 0,
        "current_count": 0,
        "returned_count": 0,
        "expired_count": 0,
        "education_count": 0,
        "experience_count": 0,
        "pagination_complete": False,
        "details_complete": False,
        "classification_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": source_cap_reached,
        "application_urls": 0,
        "unsafe_endpoint_calls": 0,
        "pii_payload_persisted": False,
        "no_current_data": False,
        "configured_collection_error": message,
        "ownership_scope": CHUNGJU_WOMEN_OWNERSHIP_SCOPE,
        "ownership_disjoint_from": [
            CHUNGJU_GOODEDU_PROVIDER,
            CHUNGJU_RESIDENT_PROVIDER,
        ],
    }


def _fingerprint(rows: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    return [
        (
            row.get("provider_course_id"),
            row.get("title"),
            row.get("start_date"),
            row.get("end_date"),
            row.get("apply_start"),
            row.get("apply_end"),
            row.get("status"),
            row.get("capacity_total"),
            row.get("capacity_current"),
            row.get("service_family"),
        )
        for row in rows
    ]


def collect_chungju_women_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 20,
    detail_limit: int = 50,
    *,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return an atomic current/future mixed education/experience snapshot."""

    if not is_chungju_women_target(target):
        return [], CHUNGJU_WOMEN_PARSER, _failure(
            "target does not match the exact Chungju women-culture owner"
        )
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], CHUNGJU_WOMEN_PARSER, _failure(
                "managed session_factory injection is required"
            )
        session_factory = _default_session_factory
    try:
        allowed_pages = max(0, int(max_pages))
        allowed_details = max(0, int(detail_limit))
        cutoff = _today(today)
    except (TypeError, ValueError):
        return [], CHUNGJU_WOMEN_PARSER, _failure(
            "max_pages/detail_limit/today are invalid"
        )

    errors: list[str] = []
    source_cap_reached = False
    session = session_factory()
    list_soups: dict[int, BeautifulSoup] = {}
    recheck_soup: Optional[BeautifulSoup] = None
    list_requests = 0
    detail_attempts = 0
    detail_pages = 0
    detail_errors: list[str] = []
    try:
        try:
            list_soups[1] = _fetch_soup(session, CHUNGJU_WOMEN_URL, timeout)
            list_requests += 1
        except Exception as exc:
            errors.append(f"page 1 fetch failed: {type(exc).__name__}")

        total = 0
        data_pages = 0
        required_list_requests = 0
        if not errors:
            contract = _counter(list_soups[1])
            if contract is None:
                errors.append("missing source counter")
            else:
                total, displayed_page, advertised_last = contract
                data_pages = max(1, math.ceil(total / CHUNGJU_WOMEN_PAGE_SIZE))
                if displayed_page != 1 or advertised_last != data_pages:
                    errors.append("inconsistent first-page counter")
                required_list_requests = data_pages + 2
                if required_list_requests > allowed_pages:
                    source_cap_reached = True
                    errors.append(
                        f"max_pages cap allows {allowed_pages} of "
                        f"{required_list_requests} required list requests"
                    )

        if not errors:
            for page in range(2, data_pages + 2):
                try:
                    list_soups[page] = _fetch_soup(
                        session, chungju_women_list_url(page), timeout
                    )
                    list_requests += 1
                except Exception as exc:
                    errors.append(f"page {page} fetch failed: {type(exc).__name__}")
                    break
        if not errors:
            try:
                recheck_soup = _fetch_soup(session, CHUNGJU_WOMEN_URL, timeout)
                list_requests += 1
            except Exception as exc:
                errors.append(f"page 1 recheck failed: {type(exc).__name__}")

        listed_rows: list[dict[str, Any]] = []
        page_counts: dict[int, int] = {}
        sentinel_markers = 0
        if not errors:
            for page in range(1, data_pages + 2):
                soup = list_soups[page]
                if _counter(soup) != (total, page, data_pages):
                    errors.append(f"page {page}: source counter changed")
                parsed, empty_markers, page_errors = _parse_list_page(
                    soup, page=page
                )
                errors.extend(page_errors)
                page_counts[page] = len(parsed)
                if page <= data_pages:
                    if empty_markers != (1 if total == 0 and page == 1 else 0):
                        errors.append(f"page {page}: unexpected empty marker")
                    listed_rows.extend(parsed)
                else:
                    sentinel_markers = empty_markers
                    if parsed or empty_markers != 1:
                        errors.append("immediate sentinel is not exactly empty")
            for page in range(1, data_pages):
                if page_counts.get(page) != CHUNGJU_WOMEN_PAGE_SIZE:
                    errors.append(f"page {page}: non-terminal page is not full")
            terminal_expected = total - CHUNGJU_WOMEN_PAGE_SIZE * (data_pages - 1)
            if total == 0:
                terminal_expected = 0
            if page_counts.get(data_pages) != terminal_expected:
                errors.append("terminal page row count mismatch")
            sequences = [
                int(row["raw_fields"]["source_sequence"]) for row in listed_rows
            ]
            if sequences != list(range(total, 0, -1)):
                errors.append("source sequence is not a complete descending range")
            if len(listed_rows) != total:
                errors.append(f"declared total {total} != parsed {len(listed_rows)}")

        if not errors and recheck_soup is not None:
            if _counter(recheck_soup) != (total, 1, data_pages):
                errors.append("page 1 recheck counter changed")
            recheck_rows, recheck_empty, recheck_errors = _parse_list_page(
                recheck_soup, page=1
            )
            errors.extend(recheck_errors)
            if recheck_empty or _fingerprint(recheck_rows) != _fingerprint(
                listed_rows[: len(recheck_rows)]
            ):
                errors.append("page 1 changed during collection")

        identities = [
            _clean(row.get("raw_fields", {}).get("identity")) for row in listed_rows
        ]
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count:
            errors.append(f"{duplicate_count} duplicate source identities")
        raw_urls = [_clean(row.get("raw_url")) for row in listed_rows]
        duplicate_url_count = len(raw_urls) - len(set(raw_urls))
        if duplicate_url_count:
            errors.append(f"{duplicate_url_count} duplicate detail URLs")

        current_rows = [
            row
            for row in listed_rows
            if date.fromisoformat(_clean(row.get("end_date"))) >= cutoff
        ]
        expired_count = len(listed_rows) - len(current_rows)
        classification_complete = bool(
            len(listed_rows) == total
            and all(
                _clean(row.get("service_family")) in {"education", "experience"}
                for row in listed_rows
            )
        )
        list_complete = bool(
            not errors
            and list_requests == required_list_requests
            and len(list_soups) == data_pages + 1
            and recheck_soup is not None
            and sentinel_markers == 1
            and len(listed_rows) == total
            and classification_complete
        )

        if len(current_rows) > allowed_details:
            source_cap_reached = True
            errors.append(
                f"detail_limit cap allows {allowed_details} of "
                f"{len(current_rows)} required current details"
            )
        elif list_complete:
            for row in current_rows:
                detail_attempts += 1
                try:
                    soup = _fetch_soup(session, _clean(row.get("raw_url")), timeout)
                    item_errors = _validate_detail(row, soup)
                    if item_errors:
                        detail_errors.extend(item_errors)
                    else:
                        detail_pages += 1
                except Exception as exc:
                    detail_errors.append(
                        f"{_clean(row.get('provider_course_id'))}: "
                        f"detail fetch failed {type(exc).__name__}"
                    )
        errors.extend(detail_errors)
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    details_complete = bool(
        not detail_errors
        and detail_attempts == len(current_rows)
        and detail_pages == len(current_rows)
    )
    result: list[dict[str, Any]] = []
    if list_complete and details_complete and not errors:
        deduper = dedupe_rows or _dedupe_default
        result = list(deduper(current_rows))
        if len(result) != len(current_rows):
            errors.append(
                f"dedupe_rows changed complete cardinality {len(current_rows)} to {len(result)}"
            )
            result = []
    snapshot_complete = bool(list_complete and details_complete and not errors)
    if not snapshot_complete:
        result = []

    family_counts = Counter(_clean(row.get("service_family")) for row in result)
    source_family_counts = Counter(
        _clean(row.get("service_family")) for row in listed_rows
    )
    status_counts = Counter(_clean(row.get("status")) for row in result)
    application_controls = sum(
        int(row.get("raw_fields", {}).get("application_control_count", 0))
        for row in current_rows
    )
    attachment_controls = sum(
        int(row.get("raw_fields", {}).get("attachment_control_count", 0))
        for row in current_rows
    )
    direct_controls = sum(
        int(row.get("raw_fields", {}).get("direct_application_control_count", 0))
        for row in current_rows
    )
    meta = {
        "pages": list_requests,
        "list_requests": list_requests,
        "data_pages": data_pages,
        "sentinel_pages": 1 if sentinel_markers == 1 else 0,
        "stable_rechecks": 1 if recheck_soup is not None else 0,
        "required_list_requests": required_list_requests,
        "page_counts": page_counts,
        "detail_attempts": detail_attempts,
        "detail_pages": detail_pages,
        "detail_errors": len(detail_errors),
        "source_total": total,
        "source_rows": len(listed_rows),
        "expired_count": expired_count,
        "current_count": len(current_rows),
        "returned_count": len(result),
        "source_family_counts": dict(source_family_counts),
        "family_counts": dict(family_counts),
        "education_count": family_counts.get("education", 0),
        "experience_count": family_counts.get("experience", 0),
        "current_status_counts": dict(status_counts),
        "duplicate_count": duplicate_count,
        "duplicate_url_count": duplicate_url_count,
        "application_control_count": application_controls,
        "direct_application_control_count": direct_controls,
        "attachment_control_count": attachment_controls,
        "application_urls": sum(bool(row.get("application_url")) for row in result),
        "unsafe_endpoint_calls": 0,
        "application_endpoint_calls": 0,
        "application_check_endpoint_calls": 0,
        "attachment_endpoint_calls": 0,
        "download_endpoint_calls": 0,
        "pii_endpoint_calls": 0,
        "pii_payload_persisted": False,
        "pagination_complete": list_complete,
        "details_complete": details_complete,
        "classification_complete": classification_complete,
        "snapshot_complete": snapshot_complete,
        "source_cap_reached": source_cap_reached,
        "no_current_data": bool(snapshot_complete and not current_rows),
        "no_current_reason": (
            "the complete Chungju women-culture ledger has no current/future courses"
            if snapshot_complete and not current_rows
            else ""
        ),
        "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        "ownership_scope": CHUNGJU_WOMEN_OWNERSHIP_SCOPE,
        "ownership_disjoint_from": [
            CHUNGJU_GOODEDU_PROVIDER,
            CHUNGJU_RESIDENT_PROVIDER,
        ],
    }
    return result, CHUNGJU_WOMEN_PARSER, meta


collect = collect_chungju_women_courses


__all__ = [
    "CHUNGJU_GOODEDU_PROVIDER",
    "CHUNGJU_RESIDENT_PROVIDER",
    "CHUNGJU_WOMEN_BRANCH",
    "CHUNGJU_WOMEN_BRANCH_CODE",
    "CHUNGJU_WOMEN_CLASSIFICATIONS",
    "CHUNGJU_WOMEN_HOST",
    "CHUNGJU_WOMEN_MUNICIPALITY_CODE",
    "CHUNGJU_WOMEN_MUNICIPALITY_NAME",
    "CHUNGJU_WOMEN_OWNERSHIP_SCOPE",
    "CHUNGJU_WOMEN_PAGE_SIZE",
    "CHUNGJU_WOMEN_PARSER",
    "CHUNGJU_WOMEN_PATH",
    "CHUNGJU_WOMEN_PROVIDER",
    "CHUNGJU_WOMEN_URL",
    "chungju_women_detail_url",
    "chungju_women_list_url",
    "collect",
    "collect_chungju_women_courses",
    "is_chungju_women_target",
    "is_target",
]
