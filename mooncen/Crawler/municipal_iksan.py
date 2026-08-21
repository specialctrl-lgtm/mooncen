"""Fail-closed collector for Iksan's complete public education ledger.

The canonical owner is the Iksan integrated-reservation service.  Its global
``rsvtType=EDUCATION`` API is the only source-of-truth: the lifelong-learning,
Baekje Royal Palace Museum and Global Culture Center pages are alternate
presentations of subsets from that same backend, not independent owners.

Every advertised API page, the immediate empty sentinel, and stable first/last
boundary rechecks are required.  Current/future rows are then bound to the
official detail menu for their exact facility code.  Applicant forms are never
requested.  Free-form descriptions, instructors, contacts, payment/account
details, attachments and source HTML are deliberately not persisted.  Any
pagination, identity, detail, application-control or privacy drift makes the
whole snapshot atomically empty.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import json
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


IKSAN_PROVIDER = "MUNI_WWW_IKSAN_GO_KR_05CBD6EA"
IKSAN_CANDIDATE_ID = "MUNI_IR_AB9D4FA82479"
IKSAN_MUNICIPALITY_CODE = "5214000000"
IKSAN_MUNICIPALITY_NAME = "전북특별자치도 익산시"
IKSAN_HOST = "www.iksan.go.kr"
IKSAN_CANONICAL_URL = "https://www.iksan.go.kr/reserve"
IKSAN_API_PATH = "/reserve/integr/rsvt/fclt/item/api/items.do"
IKSAN_API_URL = f"https://{IKSAN_HOST}{IKSAN_API_PATH}"
IKSAN_PAGE_SIZE = 100
IKSAN_MAX_WORKERS = 12
IKSAN_MAX_RESPONSE_BYTES = 5_000_000
IKSAN_PARSER = (
    "iksan_global_rsvt_type_education+all_declared_pages+page_after_last_empty+"
    "stable_first_last+exact_facility_menu_routes+current_details+"
    "identity_bound_application_controls+exact_branches+pii_allowlist"
)

IKSAN_LLL_PROVIDER_ALIAS = "MUNI_WWW_IKSAN_GO_KR_ED1E8256"
IKSAN_LLL_CANDIDATE_ALIAS = "MUNI_IR_6E63781EF1F8"
IKSAN_LLL_ALIAS_URL = "https://www.iksan.go.kr/lll"
IKSAN_LLL_INST_UID = "ff80808199041533019907e2297a0a07"
IKSAN_DIRECTORY_EXCLUSION_URL = (
    "https://www.iksan.go.kr/lll/board/post/list.do?"
    "boardUid=ff808081975e28f7019767640bc10682&"
    "menuUid=ff80808197387a4f01975340a08606bc"
)


@dataclass(frozen=True)
class IksanFacility:
    code: str
    api_name: str
    branch: str
    category: str
    site: str
    menu_uid: str
    fclt_uid: str
    inst_uid: str


# The menu UIDs below are the detail UIDs emitted by each official list page's
# viewProgram/detail script, rather than the parent/navigation UIDs.
IKSAN_FACILITIES: tuple[IksanFacility, ...] = (
    IksanFacility(
        "MOHYEON",
        "모현동행정복지센터",
        "모현동행정복지센터",
        "주민자치",
        "reserve",
        "ff8080819979df3a019979e72970002c",
        "ff8080819a39930e019a48680ce60507",
        "ff8080819a39930e019a485ca1cd04e5",
    ),
    IksanFacility(
        "WOMEN_CENTER",
        "여성회관",
        "익산시 여성회관",
        "여성회관",
        "reserve",
        "ff8080819979df3a019979f59eb2007b",
        "ff8080819904153301990937e79a1404",
        "ff80808197c01f910197d2dbec411cd6",
    ),
    IksanFacility(
        "INFO_EDU",
        "정보화교육",
        "익산시 정보화교육",
        "정보화교육",
        "reserve",
        "ff8080819979df3a019979fa1aff0099",
        "ff8080819978ebf801997988c48f0292",
        "ff80808197c01f910197d2dbec411cd6",
    ),
    IksanFacility(
        "CITIZEN_RECODER",
        "시민기록가",
        "익산시 시민기록가",
        "시민기록가",
        "reserve",
        "ff8080819979df3a019979fb857700ab",
        "ff8080819978ebf8019979893fca0295",
        "ff80808197c01f910197d2dbec411cd6",
    ),
    IksanFacility(
        "ART_CENTER",
        "프로그램 예약",
        "익산예술의전당",
        "예술의전당",
        "reserve",
        "ff8080819979df3a019979fdeae000c8",
        "ff8080819978ebf80199798a4b880296",
        "ff8080819a818f6e019a81abcd7101ac",
    ),
    IksanFacility(
        "ONEDAY02",
        "원데이 클래스",
        "익산시 음식·식품교육문화원",
        "원데이 클래스",
        "reserve",
        "4028a6109b82364f019b8269fb7101ae",
        "4028a6109b82364f019b8277fe68023c",
        "ff8080819978ebf8019979a40d7a02e8",
    ),
    IksanFacility(
        "LIFE_EDU",
        "교육수강 신청",
        "익산시평생학습관",
        "평생학습",
        "lll",
        "ff80808198a70b8d0198ea9a78fa6e88",
        "ff8080819956f25d01995c0fd6900b8f",
        IKSAN_LLL_INST_UID,
    ),
    IksanFacility(
        "wg02",
        "교육·행사",
        "백제왕궁박물관",
        "박물관 교육·행사",
        "wg",
        "ff8080819917cf24019918f1fd1a04e1",
        "ff80808197c01f910197c53e9e2d0ca0",
        "ff80808197c01f910197c020c2410001",
    ),
    IksanFacility(
        "global02",
        "특별기획체험",
        "익산글로벌문화관",
        "특별기획체험",
        "global",
        "ff8080819956f25d01995c003b2a0af2",
        "ff808081995c3fc401995f56146e03ca",
        "ff80808199551de8019955545891018f",
    ),
    IksanFacility(
        "gm01",
        "글로벌문화강좌",
        "익산글로벌문화관",
        "글로벌문화강좌",
        "global",
        "ff808081996e9b9c01997050a83e07b7",
        "ff808081996e9b9c019970459434072c",
        "ff80808199551de8019955545891018f",
    ),
)
IKSAN_FACILITY_BY_CODE = {item.code: item for item in IKSAN_FACILITIES}

IKSAN_DISCOVERY_AUDIT: dict[str, Any] = {
    "canonical_owner": {
        "decision": "include_global_education_ledger",
        "url": IKSAN_CANONICAL_URL,
        "provider": IKSAN_PROVIDER,
        "candidate_id": IKSAN_CANDIDATE_ID,
    },
    "reserve_index_alias": {
        "decision": "same_owner_byte_identical_alias",
        "url": "https://www.iksan.go.kr/reserve/index.do",
    },
    "lifelong_alias": {
        "decision": "same_backend_subset_alias_not_separate_owner",
        "url": IKSAN_LLL_ALIAS_URL,
        "provider": IKSAN_LLL_PROVIDER_ALIAS,
        "candidate_id": IKSAN_LLL_CANDIDATE_ALIAS,
        "filter": {"instUid": IKSAN_LLL_INST_UID, "fcltCode": "LIFE_EDU"},
    },
    "lifelong_external_directory": {
        "decision": "exclude_directory_without_identity_bound_application",
        "url": IKSAN_DIRECTORY_EXCLUSION_URL,
        "audited_total_2026_07_22": 1181,
    },
}

IKSAN_PII_FIELDS_NEVER_PERSISTED = (
    "신청자명",
    "생년월일",
    "주소",
    "전화번호",
    "이메일",
    "강사명",
    "문의처",
    "입금정보",
    "계좌번호",
    "결제정보",
    "첨부파일",
    "강의내용",
    "상세설명",
    "원문 HTML",
)

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class IksanContractError(ValueError):
    """Raised when the audited official Iksan contract changes."""


@dataclass(frozen=True)
class _ApiPage:
    requested: int
    number: int
    total_pages: int
    total_elements: int
    rows: tuple[dict[str, Any], ...]
    first: bool
    last: bool
    empty: bool


_SPACE = re.compile(r"\s+")
_UID = re.compile(r"^[0-9a-f]{32}$")
_ISO_DATE = re.compile(r"^(20\d{2})-(\d{2})-(\d{2})$")
_ISO_DATETIME = re.compile(r"^(20\d{2})-(\d{2})-(\d{2}) (\d{2}):(\d{2})$")
_DISPLAY_DATE = re.compile(r"(?<!\d)(20\d{2})[./-](\d{1,2})[./-](\d{1,2})(?!\d)")
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_GLOBAL_APPLICATION = re.compile(
    r"\s*alert\([^;]+\);\s*fn_reserv_auth\(\s*['\"]([0-9a-f]{32})['\"]\s*,\s*"
    r"['\"]([0-9a-f]{32})['\"]\s*\);\s*return\s+false;\s*"
)
_PROGRESS_STATUS = {
    "PROCEEDING": "OPEN",
    "ADVANCE": "OPEN",
    "SCHEDULED": "SCHEDULED",
    "DEADLINE": "CLOSED",
}
_USAGE_PROGRESS = {"UPCOMING", "ONGOING", "FINISHED"}
_BOOKING_TYPES = {"ONLINE", "OFFLINE"}
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "source_page",
        "source_facility_code",
        "source_facility_name",
        "source_progress",
        "source_usage_progress",
        "source_booking_type",
        "source_apply_period",
        "source_education_period",
        "source_schedule",
        "source_venue",
        "source_target",
        "detail_site",
        "detail_menu_uid",
        "detail_verified",
        "application_control_present",
        "insecure_external_control_blocked",
        "service_family",
    }
)
_FORBIDDEN_PERSISTED_KEYS = frozenset(
    {
        "phone",
        "email",
        "contact",
        "instructor",
        "staff",
        "payment_info",
        "bank_account",
        "attachments",
        "attachment_urls",
        "detail_description",
        "source_html",
        "raw_html",
        "explanation",
    }
)


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def is_iksan_education_target(target: Any) -> bool:
    if _clean(_value(target, "provider")) != IKSAN_PROVIDER:
        return False
    parsed = urlparse(_clean(_value(target, "url")))
    try:
        port = parsed.port
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == IKSAN_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == "/reserve"
        and not query
        and not parsed.params
        and not parsed.fragment
    )


is_target = is_iksan_education_target


def iksan_api_url(page: Any) -> str:
    raw = _clean(page)
    if not raw.isdigit() or int(raw) < 1:
        return ""
    return f"{IKSAN_API_URL}?" + urlencode(
        {
            "rsvtType": "EDUCATION",
            "page": int(raw),
            "size": IKSAN_PAGE_SIZE,
            "sort": "registerDt,desc",
        }
    )


def iksan_detail_url(facility_code: Any, identity: Any) -> str:
    code, uid = _clean(facility_code), _clean(identity)
    facility = IKSAN_FACILITY_BY_CODE.get(code)
    if facility is None or not _UID.fullmatch(uid):
        return ""
    return f"https://{IKSAN_HOST}/{facility.site}/index.do?" + urlencode(
        {"menuUid": facility.menu_uid, "itemUid": uid}
    )


def _default_session_factory() -> requests.Session:
    value = requests.Session()
    value.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MooncenMunicipalCrawler/1.0)",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return value


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=False)


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _official_url(value: str) -> bool:
    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == IKSAN_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
        and any(
            parsed.path.startswith(prefix)
            for prefix in ("/reserve/", "/lll/", "/wg/", "/global/")
        )
    )


def _response_bytes(
    url: str,
    timeout: int,
    session_factory: SessionFactory,
    fetcher: Fetcher,
) -> bytes:
    if not _official_url(url):
        raise IksanContractError("non-canonical request URL refused")
    session = session_factory()
    try:
        response = fetcher(session, url, timeout)
        status = int(getattr(response, "status_code", 200))
        if status < 200 or status >= 300:
            raise IksanContractError(f"HTTP {status} is not a successful response")
        final_url = _clean(getattr(response, "url", url)) or url
        if not _official_url(final_url):
            raise IksanContractError("redirect outside the official Iksan host")
        content = getattr(response, "content", None)
        if content is None:
            content = _clean(getattr(response, "text", response)).encode("utf-8")
        if not isinstance(content, (bytes, bytearray)) or not content:
            raise IksanContractError("empty response")
        if len(content) > IKSAN_MAX_RESPONSE_BYTES:
            raise IksanContractError("response size cap exceeded")
        return bytes(content)
    finally:
        _close_quietly(session)


def _json_response(
    url: str,
    timeout: int,
    session_factory: SessionFactory,
    fetcher: Fetcher,
) -> Mapping[str, Any]:
    content = _response_bytes(url, timeout, session_factory, fetcher)
    try:
        payload = json.loads(content.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise IksanContractError("invalid API JSON") from exc
    if not isinstance(payload, Mapping):
        raise IksanContractError("API top level is not an object")
    return payload


def _html_response(
    url: str,
    timeout: int,
    session_factory: SessionFactory,
    fetcher: Fetcher,
) -> BeautifulSoup:
    return BeautifulSoup(
        _response_bytes(url, timeout, session_factory, fetcher), "html.parser"
    )


def _date_value(value: Any, *, label: str) -> date:
    raw = _clean(value)
    if not _ISO_DATE.fullmatch(raw):
        raise IksanContractError(f"invalid {label}")
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise IksanContractError(f"invalid {label}") from exc


def _datetime_value(value: Any, *, label: str, optional: bool = False) -> str:
    raw = _clean(value)
    if optional and not raw:
        return ""
    match = _ISO_DATETIME.fullmatch(raw)
    if match is None:
        raise IksanContractError(f"invalid {label}")
    try:
        datetime.strptime(raw, "%Y-%m-%d %H:%M")
    except ValueError as exc:
        raise IksanContractError(f"invalid {label}") from exc
    return raw


def _safe_public_text(value: Any, *, label: str, required: bool = False) -> str:
    result = _clean(value)
    if required and not result:
        raise IksanContractError(f"empty {label}")
    if _PHONE.search(result) or _EMAIL.search(result):
        raise IksanContractError(f"PII/contact pattern in {label}")
    return result


def _nonnegative_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise IksanContractError(f"invalid {label}")
    return value


def _parse_item(raw: Any, *, page: int) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise IksanContractError(f"page {page}: item is not an object")
    identity = _clean(raw.get("itemUid"))
    if not _UID.fullmatch(identity):
        raise IksanContractError(f"page {page}: invalid item identity")
    title = _safe_public_text(raw.get("itemTitle"), label="title", required=True)
    facility_raw = raw.get("facilityInfo")
    if not isinstance(facility_raw, Mapping):
        raise IksanContractError(f"item {identity}: facility object missing")
    code = _clean(facility_raw.get("fcltCode"))
    facility = IKSAN_FACILITY_BY_CODE.get(code)
    if facility is None:
        raise IksanContractError(f"item {identity}: unmapped EDUCATION facility {code!r}")
    expected_facility = {
        "fcltCode": facility.code,
        "fcltName": facility.api_name,
        "fcltUid": facility.fclt_uid,
        "instUid": facility.inst_uid,
        "rsvtType": "EDUCATION",
    }
    for key, expected in expected_facility.items():
        if _clean(facility_raw.get(key)) != expected:
            raise IksanContractError(
                f"item {identity}: facility {key} drift for {facility.code}"
            )
    if (
        _clean(raw.get("fcltUid")) != facility.fclt_uid
        or _clean(raw.get("instUid")) != facility.inst_uid
    ):
        raise IksanContractError(f"item {identity}: item/facility identity mismatch")
    start = _date_value(raw.get("beginDate"), label="education start")
    end = _date_value(raw.get("endDate"), label="education end")
    if end < start:
        raise IksanContractError(f"item {identity}: reversed education period")
    apply_start = _datetime_value(
        raw.get("applyBeginDate"), label="application start", optional=True
    )
    apply_end = _datetime_value(
        raw.get("applyEndDate"), label="application end", optional=True
    )
    if bool(apply_start) != bool(apply_end):
        raise IksanContractError(f"item {identity}: partial application period")
    if apply_start and apply_end and apply_end < apply_start:
        raise IksanContractError(f"item {identity}: reversed application period")
    progress = _clean(raw.get("itemProgress"))
    usage_progress = _clean(raw.get("usageProgress"))
    booking_type = _clean(raw.get("bookingType"))
    if progress not in _PROGRESS_STATUS:
        raise IksanContractError(f"item {identity}: unknown reservation progress")
    if usage_progress not in _USAGE_PROGRESS:
        raise IksanContractError(f"item {identity}: unknown usage progress")
    if booking_type not in _BOOKING_TYPES:
        raise IksanContractError(f"item {identity}: unknown booking type")
    maximum = _nonnegative_int(raw.get("maxCapacity"), label="maximum capacity")
    applied = _nonnegative_int(raw.get("applyCount"), label="application count")
    wait_capacity = _nonnegative_int(raw.get("waitCapacity"), label="wait capacity")
    wait_count = _nonnegative_int(raw.get("waitCount"), label="wait count")
    fee = _nonnegative_int(raw.get("baseFee"), label="base fee")
    venue_hint = _safe_public_text(raw.get("itemInfo3"), label="API venue")
    target_hint = _safe_public_text(raw.get("itemInfo4"), label="API target")
    schedule = _safe_public_text(raw.get("timeInfo"), label="API schedule")
    external_url = _clean(raw.get("externalUrl"))
    if external_url:
        external = urlparse(external_url)
        try:
            external_port = external.port
        except ValueError as exc:
            raise IksanContractError(f"item {identity}: malformed external URL") from exc
        if (
            external.scheme not in {"http", "https"}
            or not external.hostname
            or external.username
            or external.password
            or external_port not in {None, 80, 443}
        ):
            raise IksanContractError(f"item {identity}: unsafe external URL")
    return {
        "identity": identity,
        "title": title,
        "facility_code": code,
        "facility_name": facility.api_name,
        "source_page": page,
        "start": start,
        "end": end,
        "apply_start": apply_start,
        "apply_end": apply_end,
        "progress": progress,
        "usage_progress": usage_progress,
        "booking_type": booking_type,
        "maximum": maximum,
        "applied": applied,
        "wait_capacity": wait_capacity,
        "wait_count": wait_count,
        "fee": fee,
        "venue_hint": venue_hint,
        "target_hint": target_hint,
        "schedule": schedule,
        # This value is retained only until the matching public control is
        # checked.  It is never copied into raw_fields or persisted rows.
        "external_url": external_url,
    }


def _parse_api_page(payload: Mapping[str, Any], *, requested: int) -> _ApiPage:
    result = payload.get("result")
    if not isinstance(result, Mapping):
        raise IksanContractError(f"page {requested}: result object missing")
    content = result.get("content")
    if not isinstance(content, list):
        raise IksanContractError(f"page {requested}: content is not a list")
    integer_fields: dict[str, int] = {}
    for key in ("number", "size", "numberOfElements", "totalPages", "totalElements"):
        value = result.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise IksanContractError(f"page {requested}: invalid {key}")
        integer_fields[key] = value
    if (
        integer_fields["number"] != requested - 1
        or integer_fields["size"] != IKSAN_PAGE_SIZE
        or integer_fields["numberOfElements"] != len(content)
    ):
        raise IksanContractError(f"page {requested}: page-number/size contract drift")
    expected_pages = (
        (integer_fields["totalElements"] + IKSAN_PAGE_SIZE - 1) // IKSAN_PAGE_SIZE
        if integer_fields["totalElements"]
        else 0
    )
    if integer_fields["totalPages"] != expected_pages:
        raise IksanContractError(f"page {requested}: total-pages arithmetic drift")
    first, last, empty = result.get("first"), result.get("last"), result.get("empty")
    if not all(isinstance(value, bool) for value in (first, last, empty)):
        raise IksanContractError(f"page {requested}: boolean page markers missing")
    if (
        first != (requested == 1)
        or last != (requested >= max(1, integer_fields["totalPages"]))
        or empty != (len(content) == 0)
    ):
        raise IksanContractError(f"page {requested}: boolean page-marker drift")
    rows = tuple(_parse_item(item, page=requested) for item in content)
    if len({row["identity"] for row in rows}) != len(rows):
        raise IksanContractError(f"page {requested}: duplicate identity")
    return _ApiPage(
        requested=requested,
        number=integer_fields["number"],
        total_pages=integer_fields["totalPages"],
        total_elements=integer_fields["totalElements"],
        rows=rows,
        first=first,
        last=last,
        empty=empty,
    )


def _page_signature(page: _ApiPage) -> tuple[Any, ...]:
    return (
        page.total_pages,
        page.total_elements,
        tuple(
            (
                row["identity"],
                row["title"],
                row["facility_code"],
                row["progress"],
                row["start"],
                row["end"],
            )
            for row in page.rows
        ),
    )


def _display_dates(value: Any) -> list[date]:
    result: list[date] = []
    for match in _DISPLAY_DATE.finditer(_clean(value)):
        try:
            result.append(date(int(match.group(1)), int(match.group(2)), int(match.group(3))))
        except ValueError as exc:
            raise IksanContractError("invalid date on detail page") from exc
    return result


def _same_period(observed: list[date], start: date, end: date) -> bool:
    return observed == [start, end] or (start == end and observed == [start])


def _detail_pairs(root: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for dl in root.select(".view_top ul.info_list li dl"):
        dt, dd = dl.select_one("dt"), dl.select_one("dd")
        if dt is None or dd is None:
            continue
        key = _clean(dt.get_text(" ", strip=True))
        value = _clean(dd.get_text(" ", strip=True))
        if key in result and result[key] != value:
            raise IksanContractError(f"duplicate conflicting detail field {key!r}")
        result[key] = value
    return result


def _primary_controls(root: Any) -> list[Any]:
    return list(root.select(".view_top .txt_area > .btn_area .button"))


def _control_text(control: Any) -> str:
    return _clean(control.get_text(" ", strip=True))


def _validate_common_application_control(
    source: Mapping[str, Any],
    soup: BeautifulSoup,
    root: Any,
    detail_url: str,
) -> tuple[bool, str, str, bool]:
    """Return present, application URL, application type, HTTP-blocked."""

    identity = str(source["identity"])
    facility = IKSAN_FACILITY_BY_CODE[str(source["facility_code"])]
    progress, booking = str(source["progress"]), str(source["booking_type"])
    controls = _primary_controls(root)
    labels = [_control_text(control) for control in controls]
    if len(controls) > 1:
        raise IksanContractError(f"item {identity}: multiple primary controls")
    control = controls[0] if controls else None

    if progress == "PROCEEDING" and booking == "ONLINE":
        if control is None or _control_text(control) != "신청하기":
            raise IksanContractError(f"item {identity}: active online control missing")
        if facility.site == "global":
            match = _GLOBAL_APPLICATION.fullmatch(_clean(control.get("onclick")))
            if match is None or match.group(2) != identity:
                raise IksanContractError(
                    f"item {identity}: global application identity mismatch"
                )
        elif facility.site == "reserve":
            if (
                _clean(control.get("id")) != "btn_pass"
                or _clean(control.get("href")) != "javascript:void(0);"
            ):
                raise IksanContractError(
                    f"item {identity}: integrated application control malformed"
                )
            login_links = soup.select("li[data-util='login'] a[href*='returnUrl=']")
            bound = False
            for link in login_links:
                values = parse_qs(urlparse(_clean(link.get("href"))).query).get(
                    "returnUrl", []
                )
                if values == [detail_url]:
                    bound = True
                    break
            if not bound:
                raise IksanContractError(
                    f"item {identity}: login/application return identity mismatch"
                )
        else:
            raise IksanContractError(
                f"item {identity}: unaudited active online detail family"
            )
        return True, detail_url, "ONLINE_RESERVATION_LOGIN_REQUIRED", False

    if progress == "ADVANCE" and booking == "ONLINE":
        external_url = _clean(source.get("external_url"))
        if (
            facility.site != "wg"
            or control is None
            or _control_text(control) != "바로가기"
            or _clean(control.get("href")) != external_url
        ):
            raise IksanContractError(f"item {identity}: advance control mismatch")
        parsed = urlparse(external_url)
        if parsed.scheme == "https":
            return True, external_url, "EXTERNAL_ONLINE_RESERVATION", False
        # The museum currently emits an HTTP-only third-party control.  Keep
        # the official HTTPS detail, but do not expose the insecure target.
        return True, "", "EXTERNAL_HTTP_INFO_ONLY", True

    if booking == "OFFLINE" and progress in {"PROCEEDING", "ADVANCE"}:
        if control is None or _control_text(control) != "오프라인":
            raise IksanContractError(f"item {identity}: offline marker missing")
        return False, "", "OFFLINE_APPLY", False

    if progress == "SCHEDULED":
        if control is None or _control_text(control) not in {
            "진행예정",
            "접수예정",
            "신청예정",
        }:
            raise IksanContractError(f"item {identity}: scheduled marker missing")
        return False, "", "INFO_ONLY", False

    if progress == "DEADLINE":
        if facility.site == "lll":
            marker = root.select_one("ul.app_class_list .state")
            expected_marker = (
                "교육중" if source["usage_progress"] == "ONGOING" else "접수마감"
            )
            if (
                marker is None
                or _clean(marker.get_text(" ", strip=True)) != expected_marker
            ):
                raise IksanContractError(f"item {identity}: closed marker missing")
            if controls:
                raise IksanContractError(
                    f"item {identity}: closed lifelong item has active control"
                )
        elif booking == "OFFLINE":
            if control is None or _control_text(control) != "오프라인":
                raise IksanContractError(f"item {identity}: offline marker missing")
        elif control is None or _control_text(control) not in {"접수마감", "신청마감"}:
            raise IksanContractError(f"item {identity}: closed marker missing")
        return False, "", "INFO_ONLY", False

    raise IksanContractError(
        f"item {identity}: unsupported progress/booking control combination {labels!r}"
    )


def _detail_row(source: Mapping[str, Any], soup: BeautifulSoup) -> dict[str, Any]:
    identity = str(source["identity"])
    facility = IKSAN_FACILITY_BY_CODE[str(source["facility_code"])]
    detail_url = iksan_detail_url(facility.code, identity)
    if facility.site == "lll":
        root = soup.select_one("#boardWrap")
        title_node = (
            root.select_one("ul.app_class_list > li > strong.tit") if root else None
        )
        period_node = (
            root.select_one("ul.app_class_list > li > dl.medium > dd") if root else None
        )
        apply_node = (
            root.select_one("ul.app_class_list > li > dl.period > dd") if root else None
        )
        venue_node = (
            root.select_one("ul.app_class_list > li > dl:not(.period):not(.medium) + dl dd")
            if root
            else None
        )
        # Select by audited display classes first, then the public basics table.
        venue = ""
        target = ""
        if root is not None:
            for item in root.select(".view_basics_list > li"):
                key_node, value_node = item.select_one("strong.tit"), item.select_one("p")
                if key_node is None or value_node is None:
                    continue
                key = _clean(key_node.get_text(" ", strip=True))
                value = _clean(value_node.get_text(" ", strip=True))
                if key == "교육장소":
                    venue = value
                elif key == "교육대상":
                    target = value
        if venue_node is not None and not venue:
            venue = _clean(venue_node.get_text(" ", strip=True))
        title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
        period_text = _clean(period_node.get_text(" ", strip=True) if period_node else "")
        apply_text = _clean(apply_node.get_text(" ", strip=True) if apply_node else "")
    else:
        root = soup.select_one("article[data-subarea='system_view']")
        title_node = root.select_one("h3.tit_area") if root else None
        title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
        fields = _detail_pairs(root) if root else {}
        period_text = fields.get("교육기간") or fields.get("체험기간") or ""
        apply_text = fields.get("접수기간", "")
        venue = fields.get("교육장소") or fields.get("체험장소") or ""
        target = fields.get("교육대상") or fields.get("모집대상") or ""
    if root is None or not title or title != source["title"]:
        raise IksanContractError(f"item {identity}: detail title identity drift")
    if not _same_period(_display_dates(period_text), source["start"], source["end"]):
        raise IksanContractError(f"item {identity}: detail education period drift")
    if not source["apply_start"] or not source["apply_end"]:
        raise IksanContractError(f"item {identity}: current item application period missing")
    apply_start_date = date.fromisoformat(str(source["apply_start"])[:10])
    apply_end_date = date.fromisoformat(str(source["apply_end"])[:10])
    if not _same_period(_display_dates(apply_text), apply_start_date, apply_end_date):
        raise IksanContractError(f"item {identity}: detail application period drift")
    venue = _safe_public_text(venue, label="detail venue")
    target = _safe_public_text(target, label="detail target")
    venue_hint = _clean(source.get("venue_hint"))
    target_hint = _clean(source.get("target_hint"))
    if venue_hint and venue != venue_hint:
        raise IksanContractError(f"item {identity}: API/detail venue drift")
    if target_hint and target and target_hint not in target and target not in target_hint:
        raise IksanContractError(f"item {identity}: API/detail target drift")
    control, application_url, application_type, http_blocked = (
        _validate_common_application_control(source, soup, root, detail_url)
    )
    normalized_status = _PROGRESS_STATUS[str(source["progress"])]
    branch_code = "IKSAN_EDU_" + re.sub(
        r"[^A-Z0-9]+", "_", facility.code.upper()
    ).strip("_")
    row = {
        "provider": IKSAN_PROVIDER,
        "provider_course_id": f"{IKSAN_PROVIDER}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": source["title"],
        "description": source["title"],
        "branch": facility.branch,
        "branch_code": branch_code,
        "preserve_branch": True,
        "category": facility.category,
        "program_type": "교육",
        "raw_url": detail_url,
        "application_url": application_url,
        "application_type": application_type,
        "application_method": "온라인" if source["booking_type"] == "ONLINE" else "오프라인",
        "application_methods": [
            "온라인" if source["booking_type"] == "ONLINE" else "오프라인"
        ],
        "reservation_available": bool(application_url and normalized_status == "OPEN"),
        "status": normalized_status,
        "fee": "무료" if source["fee"] == 0 else f"{source['fee']:,}원",
        "period": f"{source['start'].isoformat()} ~ {source['end'].isoformat()}",
        "start_date": source["start"].isoformat(),
        "end_date": source["end"].isoformat(),
        "apply_period": f"{source['apply_start']} ~ {source['apply_end']}",
        "apply_start": source["apply_start"],
        "apply_end": source["apply_end"],
        "schedule_raw": source["schedule"],
        "capacity": f"{source['maximum']}명",
        "capacity_current": source["applied"],
        "capacity_total": source["maximum"],
        "waitlist_current": source["wait_count"],
        "waitlist_capacity": source["wait_capacity"],
        "target": target or target_hint,
        "venue": venue or facility.branch,
        "venue_name": venue or facility.branch,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": IKSAN_PARSER,
        "municipality_code": IKSAN_MUNICIPALITY_CODE,
        "municipality_full_name": IKSAN_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "source_page": source["source_page"],
            "source_facility_code": facility.code,
            "source_facility_name": facility.api_name,
            "source_progress": source["progress"],
            "source_usage_progress": source["usage_progress"],
            "source_booking_type": source["booking_type"],
            "source_apply_period": f"{source['apply_start']} ~ {source['apply_end']}",
            "source_education_period": (
                f"{source['start'].isoformat()} ~ {source['end'].isoformat()}"
            ),
            "source_schedule": source["schedule"],
            "source_venue": venue or facility.branch,
            "source_target": target or target_hint,
            "detail_site": facility.site,
            "detail_menu_uid": facility.menu_uid,
            "detail_verified": True,
            "application_control_present": control,
            "insecure_external_control_blocked": http_blocked,
            "service_family": "education",
        },
    }
    return row


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_PERSISTED_KEYS:
        errors.append("forbidden detail/PII key")
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping) or not set(raw) <= _SAFE_RAW_FIELDS:
        errors.append("raw field allowlist exceeded")
    # Opaque hexadecimal item/menu IDs can accidentally resemble a local
    # telephone number.  Scan only persisted human-readable content while the
    # structural allowlist above separately constrains identity fields.
    payload = repr(
        {
            key: row.get(key)
            for key in (
                "title",
                "description",
                "branch",
                "category",
                "fee",
                "schedule_raw",
                "target",
                "venue",
                "venue_name",
            )
        }
    )
    if _PHONE.search(payload) or _EMAIL.search(payload):
        errors.append("contact data persisted")
    return errors


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = str(row["provider_course_id"])
        if identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(_clean(value))


def _failure_meta() -> dict[str, Any]:
    return {
        "municipality_code": IKSAN_MUNICIPALITY_CODE,
        "municipality_full_name": IKSAN_MUNICIPALITY_NAME,
        "owner_provider": IKSAN_PROVIDER,
        "candidate_id": IKSAN_CANDIDATE_ID,
        "canonical_url": IKSAN_CANONICAL_URL,
        "parser": IKSAN_PARSER,
        "list_requests": 0,
        "detail_pages": 0,
        "source_rows": 0,
        "current_source_count": 0,
        "returned_count": 0,
        "pagination_complete": False,
        "boundary_rechecks_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "forbidden_application_endpoint_requests": 0,
        "configured_collection_error": "",
    }


def collect_iksan_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 20,
    detail_limit: int = 500,
    today: Optional[date | datetime | str] = None,
    max_workers: int = IKSAN_MAX_WORKERS,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    meta = _failure_meta()
    if not is_iksan_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match canonical Iksan education owner"
        )
        return [], IKSAN_PARSER, meta
    try:
        cutoff = _today(today)
        if any(
            isinstance(value, bool) or int(value) < 1
            for value in (timeout, max_pages, max_workers)
        ):
            raise ValueError("timeout/max_pages/max_workers must be positive integers")
        if isinstance(detail_limit, bool) or int(detail_limit) < 0:
            raise ValueError("detail_limit must be a non-negative integer")
    except Exception as exc:
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": _clean(exc),
            }
        )
        return [], IKSAN_PARSER, meta
    factory = session_factory or _default_session_factory
    current_fetcher = fetcher or _default_fetcher
    workers = min(int(max_workers), IKSAN_MAX_WORKERS)

    def load_page(page: int) -> _ApiPage:
        return _parse_api_page(
            _json_response(
                iksan_api_url(page), int(timeout), factory, current_fetcher
            ),
            requested=page,
        )

    try:
        first = load_page(1)
        meta["list_requests"] = 1
        total_pages = first.total_pages
        if total_pages < 1:
            raise IksanContractError("global EDUCATION ledger unexpectedly has no pages")
        if total_pages + 1 > int(max_pages):
            raise IksanContractError(
                f"max_pages {max_pages} below required sentinel page {total_pages + 1}"
            )
        jobs = [("data", page) for page in range(2, total_pages + 1)]
        jobs.extend(
            [
                ("sentinel", total_pages + 1),
                ("first_recheck", 1),
                ("last_recheck", total_pages),
            ]
        )
        pages: dict[int, _ApiPage] = {1: first}
        checks: dict[str, _ApiPage] = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(load_page, page): (kind, page) for kind, page in jobs
            }
            for future in as_completed(futures):
                kind, page = futures[future]
                parsed = future.result()
                meta["list_requests"] += 1
                if kind == "data":
                    pages[page] = parsed
                else:
                    checks[kind] = parsed
        if set(pages) != set(range(1, total_pages + 1)):
            raise IksanContractError("one or more advertised data pages are missing")
        if any(
            page.total_pages != total_pages
            or page.total_elements != first.total_elements
            for page in pages.values()
        ):
            raise IksanContractError("declared page boundary changed during collection")
        if any(
            len(pages[page].rows) != IKSAN_PAGE_SIZE
            for page in range(1, total_pages)
        ):
            raise IksanContractError("short non-final API page")
        listed = [
            row
            for page in range(1, total_pages + 1)
            for row in pages[page].rows
        ]
        if (
            len(listed) != first.total_elements
            or len({row["identity"] for row in listed}) != first.total_elements
        ):
            raise IksanContractError(
                "declared total does not equal unique EDUCATION identities"
            )
        sentinel = checks.get("sentinel")
        if (
            sentinel is None
            or sentinel.requested != total_pages + 1
            or sentinel.total_pages != total_pages
            or sentinel.total_elements != first.total_elements
            or sentinel.rows
            or not sentinel.empty
            or not sentinel.last
        ):
            raise IksanContractError("immediate empty page-after-last sentinel missing")
        if (
            checks.get("first_recheck") is None
            or _page_signature(checks["first_recheck"]) != _page_signature(first)
        ):
            raise IksanContractError("first-page stability recheck failed")
        if (
            checks.get("last_recheck") is None
            or _page_signature(checks["last_recheck"])
            != _page_signature(pages[total_pages])
        ):
            raise IksanContractError("last-page stability recheck failed")
    except Exception as exc:
        meta["configured_collection_error"] = (
            f"{type(exc).__name__}: {_clean(exc)}"
        )
        meta["source_cap_reached"] = "max_pages" in meta[
            "configured_collection_error"
        ]
        return [], IKSAN_PARSER, meta

    current = [row for row in listed if row["end"] >= cutoff]
    source_counts = Counter(row["facility_code"] for row in listed)
    current_counts = Counter(row["facility_code"] for row in current)
    meta.update(
        {
            "cutoff": cutoff.isoformat(),
            "source_rows": len(listed),
            "source_total": first.total_elements,
            "data_pages": total_pages,
            "page_sizes": [len(pages[page].rows) for page in range(1, total_pages + 1)],
            "empty_sentinel_page": total_pages + 1,
            "current_source_count": len(current),
            "expired_count": len(listed) - len(current),
            "source_facility_counts": dict(source_counts),
            "current_facility_counts": dict(current_counts),
            "facility_menu_uids": {
                facility.code: facility.menu_uid for facility in IKSAN_FACILITIES
            },
            "pagination_complete": True,
            "boundary_rechecks_complete": True,
            "alias_urls": [
                "https://www.iksan.go.kr/reserve/index.do",
                IKSAN_LLL_ALIAS_URL,
            ],
            "excluded_directory_url": IKSAN_DIRECTORY_EXCLUSION_URL,
        }
    )
    if len(current) > int(detail_limit):
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": (
                    f"detail_limit {detail_limit} below required {len(current)}"
                ),
            }
        )
        return [], IKSAN_PARSER, meta

    rows: list[dict[str, Any]] = []
    detail_errors: list[str] = []

    def load_detail(source: Mapping[str, Any]) -> dict[str, Any]:
        # Keep the network request inside the submitted callable.  Evaluating
        # _html_response as a submit() argument would serialize all details.
        soup = _html_response(
            iksan_detail_url(source["facility_code"], source["identity"]),
            int(timeout),
            factory,
            current_fetcher,
        )
        return _detail_row(source, soup)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(load_detail, source): source["identity"]
            for source in current
        }
        for future in as_completed(futures):
            identity = futures[future]
            try:
                rows.append(future.result())
                meta["detail_pages"] += 1
            except Exception as exc:
                detail_errors.append(
                    f"{identity}: {type(exc).__name__}: {_clean(exc)}"
                )
    if detail_errors:
        meta["configured_collection_error"] = "; ".join(detail_errors[:5])
        return [], IKSAN_PARSER, meta

    rows.sort(key=lambda row: (row["start_date"], row["provider_course_id"]))
    rows = list((dedupe_rows or _dedupe)(rows))
    privacy_errors = [error for row in rows for error in _privacy_errors(row)]
    if privacy_errors or len(rows) != len(current):
        meta["configured_collection_error"] = (
            "; ".join(privacy_errors[:5])
            or "dedupe changed official EDUCATION identity cardinality"
        )
        return [], IKSAN_PARSER, meta
    meta.update(
        {
            "returned_count": len(rows),
            "status_counts": dict(Counter(row["status"] for row in rows)),
            "branch_counts": dict(Counter(row["branch"] for row in rows)),
            "application_control_count": sum(
                bool(row["raw_fields"]["application_control_present"])
                for row in rows
            ),
            "insecure_external_controls_blocked": sum(
                bool(row["raw_fields"]["insecure_external_control_blocked"])
                for row in rows
            ),
            "snapshot_complete": True,
            "full_snapshot_validated": True,
            "no_current_data": not rows,
        }
    )
    return rows, IKSAN_PARSER, meta


collect = collect_iksan_education
