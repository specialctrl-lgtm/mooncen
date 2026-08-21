"""Fail-closed collector for Chuncheon's official Baewobom education ledger.

The public ``/enrollment/category/`` catalogue owns a large archive.  The
three official states ``receive``, ``standBy`` and ``edu`` form the complete
current/future partition.  This collector reconciles their declared totals,
walks every combined page, checks an empty post-last boundary and replays the
first and final pages before publishing an atomic snapshot.

Only the public list and public course-detail GET endpoints are callable.
Login, application, applicant, payment, download and other PII-bearing routes
are rejected before a network request is made.  External application links
are recorded from the official list but are never fetched.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import hashlib
import html
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence
from urllib.parse import parse_qs, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag


BWB_CHUNCHEON_PROVIDER = "MUNI_BWB_CHUNCHEON_GO_KR_1CCE214F"
BWB_CHUNCHEON_CANDIDATE_ID = "MUNI_IR_F4DAE31AD205"
BWB_CHUNCHEON_URL = "https://bwb.chuncheon.go.kr/enrollment/category/"
BWB_CHUNCHEON_LIST_ENDPOINT = BWB_CHUNCHEON_URL
BWB_CHUNCHEON_DETAIL_ENDPOINT = (
    "https://bwb.chuncheon.go.kr/enrollment/detail-view/"
)
BWB_CHUNCHEON_MUNICIPALITY_CODE = "5111000000"
BWB_CHUNCHEON_MUNICIPALITY_NAME = "강원특별자치도 춘천시"
BWB_CHUNCHEON_DEFAULT_ADDRESS = "강원특별자치도 춘천시 퇴계농공로 40"
BWB_CHUNCHEON_PAGE_SIZE = 9
BWB_CHUNCHEON_DEFAULT_MAX_PAGES = 30
BWB_CHUNCHEON_DEFAULT_DETAIL_LIMIT = 100
BWB_CHUNCHEON_REQUEST_LIMIT = 120
BWB_CHUNCHEON_CURRENT_FILTERS = ("receive", "standBy", "edu")
BWB_CHUNCHEON_PARSER = (
    "chuncheon_bwb_official_current_status_partitions+declared_totals+"
    "all_pages+empty_post_last+stable_first_final+all_public_get_details+"
    "external_list_only+notice_test_exclusion+locked_education+"
    "no_application_login_pii_fetch+atomic_snapshot"
)
BWB_CHUNCHEON_OWNERSHIP_SCOPE = (
    "chuncheon_baewobom_official_current_future_education"
)

BWB_CHUNCHEON_LIVE_AUDIT_BASELINE: Mapping[str, Any] = {
    "checked_at": "2026-08-05",
    "archive_total": 1749,
    "partition_totals": {"receive": 31, "standBy": 7, "edu": 48},
    "source_total": 86,
    "data_pages": 10,
    "page_size": 9,
    "sentinel_page": 11,
    "internal_detail_rows": 72,
    "external_list_only_rows": 14,
}

SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"
)
_ID_RE = re.compile(r"[1-9]\d*")
_VIEW_RE = re.compile(r"\bfn_view\(\s*['\"]?([1-9]\d*)['\"]?\s*\)")
_APPLY_RE = re.compile(r"\bfn_apply\(\s*['\"]?([1-9]\d*)['\"]?\s*\)")
_TOTAL_RE = re.compile(r"강좌수\s*총\s*([\d,]+)\s*건")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2}[- .]?\d{3,4}[- .]?\d{4}|"
    r"01[016789][- .]?\d{3,4}[- .]?\d{4})(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_FILTER_SOURCE_STATUS: Mapping[str, str] = {
    "receive": "접수중",
    "standBy": "대기",
    "edu": "교육중",
}
_SOURCE_STATUS_FILTER = {value: key for key, value in _FILTER_SOURCE_STATUS.items()}
_NORMALIZED_STATUS: Mapping[str, str] = {
    "접수중": "OPEN",
    "대기": "SCHEDULED",
    # The course is current, but registration is no longer available.
    "교육중": "CLOSED",
}
_ALL_FORM_STATUS_VALUES = {
    "receive",
    "standBy",
    "complete",
    "edu",
    "completeEdu",
}
_REQUIRED_LIST_FIELDS = {
    "교육기관",
    "교육기간",
    "교육시간",
    "접수기간",
    "모집인원",
    "대상자",
    "강사",
}
_NOTICE_PREFIXES = ("공지", "[공지", "(공지", "【공지", "공지사항")
_TEST_PREFIXES = ("[테스트", "(테스트", "테스트 강좌", "TEST 강좌", "test 강좌")
_TEST_MARKERS = ("신청하지 마세요", "예약하지 마세요", "샘플 강좌")


class ChuncheonBwbContractError(ValueError):
    """Raised whenever the audited public-source contract changes."""


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(
        " ", html.unescape(str(value or "")).replace("\xa0", " ")
    ).strip()


def _redact(value: Any) -> str:
    return _clean(_EMAIL_RE.sub(" ", _PHONE_RE.sub(" ", _clean(value))))


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _positive(value: Any, field: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ChuncheonBwbContractError(
            f"{field} must be a positive integer"
        ) from exc
    if result < 1:
        raise ChuncheonBwbContractError(f"{field} must be a positive integer")
    return result


def _exact_target_url(value: Any) -> bool:
    parsed = urlparse(_clean(value))
    wanted = urlparse(BWB_CHUNCHEON_URL)
    return bool(
        parsed.scheme == "https"
        and parsed.hostname == wanted.hostname
        and parsed.port is None
        and parsed.path == wanted.path
        and parse_qs(parsed.query, keep_blank_values=True) == {}
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


def is_chuncheon_bwb_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")) == BWB_CHUNCHEON_PROVIDER
        and _exact_target_url(_target_value(target, "url"))
    )


is_target = is_chuncheon_bwb_target


def _default_session_factory() -> requests.Session:
    value = requests.Session()
    value.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    return value


def _close(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query, keep_blank_values=True)


def _assert_safe_public_url(url: str) -> None:
    parsed = urlparse(_clean(url))
    if not (
        parsed.scheme == "https"
        and parsed.hostname == "bwb.chuncheon.go.kr"
        and parsed.port is None
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    ):
        raise ChuncheonBwbContractError("URL escaped the audited Chuncheon host")

    query = _query(url)
    if parsed.path == "/enrollment/category/":
        if set(query) - {"status[]", "pageIndex", "backFlag"}:
            raise ChuncheonBwbContractError("invalid public list query")
        statuses = tuple(query.get("status[]", ()))
        allowed_statuses = {
            (),
            *(tuple([value]) for value in BWB_CHUNCHEON_CURRENT_FILTERS),
            BWB_CHUNCHEON_CURRENT_FILTERS,
        }
        if statuses not in allowed_statuses:
            raise ChuncheonBwbContractError("invalid current-status partition")
        pages = query.get("pageIndex", ["1"])
        if len(pages) != 1 or not _ID_RE.fullmatch(pages[0]):
            raise ChuncheonBwbContractError("invalid public list page")
        back_flags = query.get("backFlag", ["category"])
        if back_flags != ["category"]:
            raise ChuncheonBwbContractError("invalid list ownership flag")
        return

    if parsed.path == "/enrollment/detail-view/":
        if set(query) != {"lifelongGisuLectureId", "backFlag"}:
            raise ChuncheonBwbContractError("invalid public detail query")
        identities = query.get("lifelongGisuLectureId", [])
        if len(identities) != 1 or not _ID_RE.fullmatch(identities[0]):
            raise ChuncheonBwbContractError("invalid public detail identity")
        if query.get("backFlag") != ["category"]:
            raise ChuncheonBwbContractError("invalid detail ownership flag")
        return

    raise ChuncheonBwbContractError(
        "application/login/applicant/payment/download/PII endpoint refused"
    )


def _response_soup(response: Any, expected_url: str) -> BeautifulSoup:
    try:
        status = int(getattr(response, "status_code", 200))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise ChuncheonBwbContractError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise ChuncheonBwbContractError("redirects are not accepted")
    final_url = _clean(getattr(response, "url", ""))
    if final_url:
        final = urlparse(final_url)
        wanted = urlparse(expected_url)
        if (
            final.scheme != "https"
            or final.hostname != wanted.hostname
            or final.path != wanted.path
        ):
            raise ChuncheonBwbContractError("response escaped the audited endpoint")
    text = str(getattr(response, "text", "") or "")
    if not text and getattr(response, "content", None):
        text = bytes(response.content).decode("utf-8", errors="replace")
    if not text:
        raise ChuncheonBwbContractError("empty public response")
    soup = BeautifulSoup(text, "lxml")
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if "배워봄" not in title or "로그인" in title:
        raise ChuncheonBwbContractError("response left the public Baewobom ledger")
    return soup


class _Runner:
    def __init__(self, factory: SessionFactory, timeout: int) -> None:
        self.factory = factory
        self.timeout = timeout
        self.session: Any = None
        self.requests = 0
        self.sessions_created = 0

    def __enter__(self) -> "_Runner":
        self.session = self.factory()
        self.sessions_created = 1
        headers = getattr(self.session, "headers", None)
        if headers is not None and hasattr(headers, "update"):
            headers.update(
                {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
                    "Referer": BWB_CHUNCHEON_URL,
                }
            )
        return self

    def __exit__(self, *_: Any) -> None:
        _close(self.session)

    def soup(self, url: str) -> BeautifulSoup:
        _assert_safe_public_url(url)
        if self.requests >= BWB_CHUNCHEON_REQUEST_LIMIT:
            raise ChuncheonBwbContractError("audited session request budget exceeded")
        self.requests += 1
        response = self.session.get(
            url,
            timeout=self.timeout,
            allow_redirects=False,
        )
        return _response_soup(response, url)


def bwb_chuncheon_list_url(
    page: int,
    statuses: Sequence[str] = BWB_CHUNCHEON_CURRENT_FILTERS,
) -> str:
    page = _positive(page, "page")
    status_tuple = tuple(_clean(value) for value in statuses)
    allowed = {
        (),
        *(tuple([value]) for value in BWB_CHUNCHEON_CURRENT_FILTERS),
        BWB_CHUNCHEON_CURRENT_FILTERS,
    }
    if status_tuple not in allowed:
        raise ChuncheonBwbContractError("unsupported current-status partition")
    query: list[tuple[str, str]] = [
        ("status[]", value) for value in status_tuple
    ]
    query.extend((('pageIndex', str(page)), ('backFlag', 'category')))
    return BWB_CHUNCHEON_LIST_ENDPOINT + "?" + urlencode(query)


def bwb_chuncheon_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _ID_RE.fullmatch(value):
        return ""
    return BWB_CHUNCHEON_DETAIL_ENDPOINT + "?" + urlencode(
        {"lifelongGisuLectureId": value, "backFlag": "category"}
    )


def _form_contract(
    soup: BeautifulSoup,
    *,
    expected_statuses: Sequence[str],
    expected_page: int,
) -> None:
    form = soup.select_one("form#frm")
    if form is None:
        raise ChuncheonBwbContractError("missing official catalogue form")
    method = _clean(form.get("method")).lower()
    action = _clean(form.get("action"))
    if method not in {"", "get"}:
        raise ChuncheonBwbContractError("catalogue is no longer a public GET form")
    if action not in {"", "/enrollment/category/", BWB_CHUNCHEON_URL}:
        raise ChuncheonBwbContractError("catalogue form action changed")

    hidden: dict[str, str] = {}
    for node in form.select("input[type='hidden'][name]"):
        key = _clean(node.get("name"))
        if key and key not in hidden:
            hidden[key] = _clean(node.get("value"))
    if hidden.get("backFlag") != "category":
        raise ChuncheonBwbContractError("catalogue ownership flag changed")
    if hidden.get("pageIndex") != str(expected_page):
        raise ChuncheonBwbContractError("catalogue page identity changed")

    city_values = {
        _clean(node.get("value"))
        for node in form.select("input[name='city[]']")
        if _clean(node.get("value"))
    }
    if city_values != {"32010"}:
        raise ChuncheonBwbContractError("catalogue left Chuncheon ownership")

    status_nodes = form.select("input[name='status[]'][value]")
    status_values = {_clean(node.get("value")) for node in status_nodes}
    if status_values != _ALL_FORM_STATUS_VALUES:
        raise ChuncheonBwbContractError("official status registry changed")
    checked = tuple(
        _clean(node.get("value"))
        for node in status_nodes
        if node.has_attr("checked")
    )
    if checked != tuple(expected_statuses):
        raise ChuncheonBwbContractError("server did not preserve requested statuses")


def _declared_total(soup: BeautifulSoup) -> int:
    candidates: set[int] = set()
    for node in soup.select(".search-result"):
        match = _TOTAL_RE.search(_clean(node.get_text(" ", strip=True)))
        if match:
            candidates.add(int(match.group(1).replace(",", "")))
    if len(candidates) != 1:
        raise ChuncheonBwbContractError("missing unambiguous declared source total")
    return candidates.pop()


def _date_range(value: Any, field: str) -> tuple[str, str]:
    values = [
        f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
        for year, month, day in _DATE_RE.findall(_clean(value))
    ]
    if len(values) < 2:
        raise ChuncheonBwbContractError(f"missing complete {field} date range")
    for raw in (values[0], values[-1]):
        date.fromisoformat(raw)
    return values[0], values[-1]


def _definition_pairs(root: Tag, selector: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for node in root.select(selector):
        term = node.select_one("dt")
        description = node.select_one("dd")
        if term is None or description is None:
            raise ChuncheonBwbContractError("malformed definition-list field")
        key = _clean(term.get_text(" ", strip=True))
        if not key or key in result:
            raise ChuncheonBwbContractError("duplicate or empty definition-list field")
        result[key] = _clean(description.get_text(" ", strip=True))
    return result


def _capacity(value: Any) -> tuple[Optional[int], Optional[int]]:
    numbers = [int(raw.replace(",", "")) for raw in re.findall(r"\d[\d,]*", _clean(value))]
    if len(numbers) >= 2:
        return numbers[0], numbers[1]
    if len(numbers) == 1:
        return None, numbers[0]
    return None, None


def _explicit_non_program(title: str) -> tuple[bool, str]:
    value = _clean(title)
    if value.startswith(_NOTICE_PREFIXES):
        return True, "notice"
    if value.startswith(_TEST_PREFIXES) or any(marker in value for marker in _TEST_MARKERS):
        return True, "test"
    return False, ""


def _external_url(value: Any) -> str:
    url = _clean(value)
    parsed = urlparse(url)
    if not (
        parsed.scheme == "https"
        and parsed.hostname
        and parsed.port is None
        and not parsed.username
        and not parsed.password
        and not parsed.fragment
    ):
        raise ChuncheonBwbContractError("unsafe external course link")
    return url


def _row_identity(box: Tag, title: str, institution: str) -> tuple[str, str, str]:
    internal_ids = {
        match.group(1)
        for node in box.select("[onclick]")
        if (match := _VIEW_RE.search(_clean(node.get("onclick"))))
    }
    external_links = {
        _external_url(node.get("href"))
        for node in box.select(".btn-wrap a.out-class[href]")
    }
    if len(internal_ids) == 1 and not external_links:
        identity = next(iter(internal_ids))
        return "internal", identity, bwb_chuncheon_detail_url(identity)
    if len(external_links) == 1 and not internal_ids:
        external = next(iter(external_links))
        digest = hashlib.sha256(
            f"{external}|{title}|{institution}".encode("utf-8")
        ).hexdigest()[:24]
        return "external", digest, external
    raise ChuncheonBwbContractError(
        "course card must own exactly one internal detail or external course link"
    )


def _parse_card(box: Tag, expected_statuses: Sequence[str]) -> dict[str, Any]:
    headings = box.select(".tit h3")
    if len(headings) != 1:
        raise ChuncheonBwbContractError("malformed course-card heading")
    title = _clean(headings[0].get_text(" ", strip=True))
    if not title:
        raise ChuncheonBwbContractError("empty course-card title")

    source_statuses = [
        value
        for node in box.select(".tit .label")
        if (value := _clean(node.get_text(" ", strip=True)))
        in _SOURCE_STATUS_FILTER
    ]
    if len(source_statuses) != 1:
        raise ChuncheonBwbContractError("missing unambiguous current source status")
    source_status = source_statuses[0]
    status_filter = _SOURCE_STATUS_FILTER[source_status]
    if status_filter not in set(expected_statuses):
        raise ChuncheonBwbContractError("course escaped requested status partition")

    explicit, non_program_reason = _explicit_non_program(title)
    pairs = _definition_pairs(box, ".info > dl")
    institution = _clean(pairs.get("교육기관"))
    owner_kind, identity, raw_url = _row_identity(box, title, institution)
    provider_course_id = (
        f"{BWB_CHUNCHEON_PROVIDER}:bwb:{owner_kind}:{identity}"
    )
    if explicit:
        return {
            "provider": BWB_CHUNCHEON_PROVIDER,
            "provider_course_id": provider_course_id,
            "title": title,
            "raw_url": raw_url,
            "source_url": BWB_CHUNCHEON_URL,
            "raw_fields": {
                "source_status": source_status,
                "status_filter": status_filter,
                "owner_kind": owner_kind,
                "explicit_non_program": True,
                "non_program_reason": non_program_reason,
                "source_contact_omitted": True,
            },
        }

    missing = sorted(_REQUIRED_LIST_FIELDS - set(pairs))
    if missing or any(not pairs.get(key) for key in _REQUIRED_LIST_FIELDS):
        raise ChuncheonBwbContractError(
            "course card lost required list fields: " + ", ".join(missing)
        )
    event_range = _date_range(pairs["교육기간"], "education")
    registration_range = _date_range(pairs["접수기간"], "registration")
    capacity_current, capacity_total = _capacity(pairs["모집인원"])
    normalized_status = _NORMALIZED_STATUS[source_status]
    external_application = raw_url if owner_kind == "external" else ""
    visible_course_url = raw_url
    application_url = (
        visible_course_url if normalized_status == "OPEN" else ""
    )
    branch_code = "chuncheon-bwb:" + hashlib.sha1(
        institution.encode("utf-8")
    ).hexdigest()[:16]
    return {
        "provider": BWB_CHUNCHEON_PROVIDER,
        "provider_course_id": provider_course_id,
        "title": title,
        "description": title,
        "branch": institution,
        "branch_code": branch_code,
        "preserve_branch": True,
        "raw_url": raw_url,
        "source_url": BWB_CHUNCHEON_URL,
        "application_url": application_url,
        "application_type": (
            "ONLINE_RESERVATION" if normalized_status == "OPEN" else "INFO_ONLY"
        ),
        "reservation_available": normalized_status == "OPEN",
        "status": normalized_status,
        "course_status": normalized_status,
        "registration_start_date": registration_range[0],
        "registration_end_date": registration_range[1],
        "start_date": event_range[0],
        "end_date": event_range[1],
        "period": pairs["교육기간"],
        "apply_period": pairs["접수기간"],
        "schedule": pairs["교육시간"],
        "schedule_raw": pairs["교육시간"],
        "capacity": pairs["모집인원"],
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "target": pairs["대상자"],
        "target_audience": pairs["대상자"],
        "instructor": pairs["강사"],
        "venue_name": institution,
        "region": BWB_CHUNCHEON_MUNICIPALITY_NAME,
        "municipality_code": BWB_CHUNCHEON_MUNICIPALITY_CODE,
        "municipality_full_name": BWB_CHUNCHEON_MUNICIPALITY_NAME,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "category": "교육",
        "program_type": "교육",
        "program_type_source": "official_course_application_menu",
        "classification_locked": True,
        "operator_type": "지자체/공공기관",
        "facility_type": "공공기관",
        "collection_type": BWB_CHUNCHEON_PARSER,
        "raw_fields": {
            "source_status": source_status,
            "status_filter": status_filter,
            "owner_kind": owner_kind,
            "external_application_link": bool(external_application),
            "explicit_non_program": False,
            "non_program_reason": "",
            "source_contact_omitted": True,
        },
    }


def _parse_page(
    soup: BeautifulSoup,
    *,
    expected_statuses: Sequence[str],
    expected_page: int,
) -> tuple[int, list[dict[str, Any]]]:
    _form_contract(
        soup,
        expected_statuses=expected_statuses,
        expected_page=expected_page,
    )
    total = _declared_total(soup)
    wrappers = soup.select(".list-wrap")
    if len(wrappers) != 1:
        raise ChuncheonBwbContractError("missing unambiguous official course list")
    boxes = wrappers[0].select(":scope > .box")
    rows = [_parse_card(box, expected_statuses) for box in boxes]
    return total, rows


def _detail_sections(root: Tag) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for wrapper in root.select(".accordion-wrap"):
        heading = wrapper.select_one("h4")
        content = wrapper.select_one(".accordion-content")
        if heading is None or content is None:
            raise ChuncheonBwbContractError("malformed public detail section")
        name = _clean(heading.get_text(" ", strip=True))
        if not name or name in result:
            raise ChuncheonBwbContractError("duplicate public detail section")
        result[name] = _definition_pairs(content, ":scope > dl")
    return result


def _clean_address(value: Any) -> str:
    result = _clean(value).rstrip(" /")
    result = re.sub(r"^\(\d{5}\)\s*", "", result)
    result = re.sub(r"\s*\(\d{5}\)\s*$", "", result)
    return _clean(result)


def _parse_detail(soup: BeautifulSoup, row: dict[str, Any]) -> None:
    root = soup.select_one(".view-wrap")
    heading = root.select_one(".view-tit h3") if root is not None else None
    if root is None or heading is None:
        raise ChuncheonBwbContractError("missing public course-detail contract")
    if _clean(heading.get_text(" ", strip=True)) != _clean(row.get("title")):
        raise ChuncheonBwbContractError("course detail title mismatch")

    identity = (_query(_clean(row.get("raw_url"))).get("lifelongGisuLectureId") or [""])[0]
    if not _ID_RE.fullmatch(identity):
        raise ChuncheonBwbContractError("row lost internal detail identity")
    detail_statuses = [
        value
        for node in root.select(".view-top .label")
        if (value := _clean(node.get_text(" ", strip=True)))
        in _SOURCE_STATUS_FILTER
    ]
    if detail_statuses != [_clean(row["raw_fields"].get("source_status"))]:
        raise ChuncheonBwbContractError("course detail status mismatch")

    apply_ids = {
        match.group(1)
        for node in root.select("[onclick]")
        if (match := _APPLY_RE.search(_clean(node.get("onclick"))))
    }
    if apply_ids and apply_ids != {identity}:
        raise ChuncheonBwbContractError("application control is not course-bound")
    if row["status"] == "OPEN" and apply_ids != {identity}:
        raise ChuncheonBwbContractError("open course lost its identity-bound control")
    if row["status"] != "OPEN" and apply_ids:
        raise ChuncheonBwbContractError("non-open course exposed an application control")

    category_node = root.select_one(".view-top > span")
    category = _clean(
        category_node.get_text(" ", strip=True) if category_node else ""
    )
    if not category:
        raise ChuncheonBwbContractError("course detail lost official category")

    sections = _detail_sections(root)
    basic = sections.get("기본정보", {})
    course = sections.get("강좌정보", {})
    application = sections.get("신청정보", {})
    place = sections.get("교육장소", {})
    if not all((basic, course, application, place)):
        raise ChuncheonBwbContractError("course detail lost required public sections")

    if _date_range(course.get("교육기간"), "detail education") != (
        row["start_date"],
        row["end_date"],
    ):
        raise ChuncheonBwbContractError("course detail education dates changed")
    if _date_range(
        basic.get("일반접수 모집기간") or basic.get("모집기간"),
        "detail registration",
    ) != (row["registration_start_date"], row["registration_end_date"]):
        raise ChuncheonBwbContractError("course detail registration dates changed")
    institution = _clean(course.get("교육기관"))
    if not institution or institution != _clean(row.get("branch")):
        raise ChuncheonBwbContractError("course detail institution changed")
    if _capacity(basic.get("접수인원")) != (
        row.get("capacity_current"),
        row.get("capacity_total"),
    ):
        raise ChuncheonBwbContractError("course detail capacity changed")

    description = _redact(
        " | ".join(
            value
            for value in (
                _clean(course.get("강의소개")),
                _clean(course.get("강의목표")),
                _clean(course.get("교육방법")),
            )
            if value
        )
    )
    address = _clean_address(place.get("주소"))
    row.update(
        {
            "category": category,
            "fee": _clean(application.get("수강료")) or "별도 안내",
            "material_fee": _clean(application.get("재료비")),
            "room": _clean(course.get("교육장소")),
            "venue_name": _clean(course.get("교육장소")) or institution,
            "venue_address": address or BWB_CHUNCHEON_DEFAULT_ADDRESS,
            "address": address or BWB_CHUNCHEON_DEFAULT_ADDRESS,
            "education_type": _clean(course.get("교육유형")),
            "application_method_raw": _clean(basic.get("모집방식")),
            "description": description[:1500] or _clean(row.get("title")),
        }
    )
    target = _clean(basic.get("대상자"))
    if target:
        row["target"] = target
        row["target_audience"] = target
    schedule = _clean(course.get("교육시간"))
    if schedule:
        row["schedule"] = schedule
        row["schedule_raw"] = schedule
    instructor = _clean(course.get("강사"))
    if instructor:
        row["instructor"] = instructor
    row["raw_fields"]["detail_public_contract"] = True
    row["raw_fields"]["application_control_visible"] = bool(apply_ids)
    row["raw_fields"]["pii_sections_omitted"] = True


def _fingerprint(row: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        _clean(row.get(key))
        for key in (
            "provider_course_id",
            "title",
            "branch",
            "registration_start_date",
            "registration_end_date",
            "start_date",
            "end_date",
        )
    ) + (_clean((row.get("raw_fields") or {}).get("source_status")),)


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _failure(message: str) -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "physical_requests": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "archive_total": 0,
        "partition_totals": {},
        "source_total": 0,
        "source_rows": 0,
        "current_count": 0,
        "returned_count": 0,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "application_endpoints_called": 0,
        "pii_payload_persisted": False,
        "configured_collection_error": message,
        "ownership_scope": BWB_CHUNCHEON_OWNERSHIP_SCOPE,
        "municipality_code": BWB_CHUNCHEON_MUNICIPALITY_CODE,
    }


def _collect(
    target: Any,
    *,
    timeout: int,
    max_pages: int,
    detail_limit: int,
    session_factory: SessionFactory,
    today: Optional[date | datetime | str],
    dedupe_rows: Optional[DedupeRows],
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    cutoff = _today(today)
    allowed_list_requests = _positive(max_pages, "max_pages")
    allowed_details = _positive(detail_limit, "detail_limit")
    archive_total = 0
    partition_totals: dict[str, int] = {}
    source_total = 0
    data_pages = 0
    page_rows: dict[int, list[dict[str, Any]]] = {}
    sentinel_rows: list[dict[str, Any]] = []
    stable_first = False
    stable_final = False
    detail_attempts = 0
    detail_pages = 0
    source_cap_reached = False
    runner: Optional[_Runner] = None

    try:
        with _Runner(session_factory, timeout) as active_runner:
            runner = active_runner
            archive_soup = runner.soup(BWB_CHUNCHEON_URL)
            _form_contract(archive_soup, expected_statuses=(), expected_page=1)
            archive_total = _declared_total(archive_soup)

            for status_filter in BWB_CHUNCHEON_CURRENT_FILTERS:
                soup = runner.soup(bwb_chuncheon_list_url(1, (status_filter,)))
                total, rows = _parse_page(
                    soup,
                    expected_statuses=(status_filter,),
                    expected_page=1,
                )
                expected_first_count = min(BWB_CHUNCHEON_PAGE_SIZE, total)
                if len(rows) != expected_first_count:
                    raise ChuncheonBwbContractError(
                        f"{status_filter} first page expected "
                        f"{expected_first_count} rows, got {len(rows)}"
                    )
                partition_totals[status_filter] = total

            first_soup = runner.soup(bwb_chuncheon_list_url(1))
            source_total, page_rows[1] = _parse_page(
                first_soup,
                expected_statuses=BWB_CHUNCHEON_CURRENT_FILTERS,
                expected_page=1,
            )
            if source_total != sum(partition_totals.values()):
                raise ChuncheonBwbContractError(
                    "combined declared total differs from status-partition sum"
                )
            if archive_total < source_total:
                raise ChuncheonBwbContractError(
                    "archive declared total is smaller than current partition"
                )
            data_pages = max(1, math.ceil(source_total / BWB_CHUNCHEON_PAGE_SIZE))
            required_list_requests = 1 + len(BWB_CHUNCHEON_CURRENT_FILTERS) + data_pages + 3
            if required_list_requests > allowed_list_requests:
                source_cap_reached = True
                raise ChuncheonBwbContractError(
                    f"max_pages cap allows {allowed_list_requests} of "
                    f"{required_list_requests} required list requests"
                )

            for page in range(2, data_pages + 1):
                total, page_rows[page] = _parse_page(
                    runner.soup(bwb_chuncheon_list_url(page)),
                    expected_statuses=BWB_CHUNCHEON_CURRENT_FILTERS,
                    expected_page=page,
                )
                if total != source_total:
                    raise ChuncheonBwbContractError("declared total changed across pages")

            sentinel_page = data_pages + 1
            sentinel_total, sentinel_rows = _parse_page(
                runner.soup(bwb_chuncheon_list_url(sentinel_page)),
                expected_statuses=BWB_CHUNCHEON_CURRENT_FILTERS,
                expected_page=sentinel_page,
            )
            if sentinel_total != source_total or sentinel_rows:
                raise ChuncheonBwbContractError(
                    "post-last sentinel is no longer exactly empty"
                )

            rows = [row for page in range(1, data_pages + 1) for row in page_rows[page]]
            for page in range(1, data_pages + 1):
                expected = min(
                    BWB_CHUNCHEON_PAGE_SIZE,
                    max(0, source_total - (page - 1) * BWB_CHUNCHEON_PAGE_SIZE),
                )
                if len(page_rows[page]) != expected:
                    raise ChuncheonBwbContractError(
                        f"page {page} expected {expected} rows, got {len(page_rows[page])}"
                    )
            if len(rows) != source_total:
                raise ChuncheonBwbContractError(
                    f"declared total {source_total} != parsed rows {len(rows)}"
                )
            identities = [_clean(row.get("provider_course_id")) for row in rows]
            if not all(identities) or len(identities) != len(set(identities)):
                raise ChuncheonBwbContractError("duplicate or empty source identities")

            explicit_non_program = [
                row
                for row in rows
                if bool((row.get("raw_fields") or {}).get("explicit_non_program"))
            ]
            current_rows = [row for row in rows if row not in explicit_non_program]
            expired = [
                row
                for row in current_rows
                if date.fromisoformat(_clean(row.get("end_date"))) < cutoff
            ]
            if expired:
                raise ChuncheonBwbContractError(
                    "official current-status partition contains expired courses"
                )
            internal_rows = [
                row
                for row in current_rows
                if (row.get("raw_fields") or {}).get("owner_kind") == "internal"
            ]
            external_rows = [
                row
                for row in current_rows
                if (row.get("raw_fields") or {}).get("owner_kind") == "external"
            ]
            if len(internal_rows) > allowed_details:
                source_cap_reached = True
                raise ChuncheonBwbContractError(
                    f"detail_limit cap allows {allowed_details} of "
                    f"{len(internal_rows)} required public details"
                )

            for row in internal_rows:
                detail_attempts += 1
                _parse_detail(runner.soup(_clean(row.get("raw_url"))), row)
                detail_pages += 1

            first_total, verify_first = _parse_page(
                runner.soup(bwb_chuncheon_list_url(1)),
                expected_statuses=BWB_CHUNCHEON_CURRENT_FILTERS,
                expected_page=1,
            )
            final_total, verify_final = _parse_page(
                runner.soup(bwb_chuncheon_list_url(data_pages)),
                expected_statuses=BWB_CHUNCHEON_CURRENT_FILTERS,
                expected_page=data_pages,
            )
            stable_first = (
                first_total == source_total
                and [_fingerprint(row) for row in verify_first]
                == [_fingerprint(row) for row in page_rows[1]]
            )
            stable_final = (
                final_total == source_total
                and [_fingerprint(row) for row in verify_final]
                == [_fingerprint(row) for row in page_rows[data_pages]]
            )
            if not stable_first or not stable_final:
                raise ChuncheonBwbContractError(
                    "first/final list boundary changed during snapshot"
                )

            result = list((dedupe_rows or _dedupe_default)(current_rows))
            if len(result) != len(current_rows):
                raise ChuncheonBwbContractError(
                    "dedupe changed a complete current/future snapshot"
                )
            meta = {
                "pages": data_pages + 1,
                "list_requests": required_list_requests,
                "physical_requests": runner.requests,
                "sessions_created": runner.sessions_created,
                "detail_attempts": detail_attempts,
                "detail_pages": detail_pages,
                "detail_errors": 0,
                "archive_total": archive_total,
                "partition_totals": dict(partition_totals),
                "source_total": source_total,
                "source_rows": len(rows),
                "data_pages": data_pages,
                "page_size": BWB_CHUNCHEON_PAGE_SIZE,
                "page_counts": {page: len(value) for page, value in page_rows.items()},
                "required_list_requests": required_list_requests,
                "sentinel_page": data_pages + 1,
                "sentinel_rows": len(sentinel_rows),
                "stable_first_page": stable_first,
                "stable_final_page": stable_final,
                "explicit_non_program_count": len(explicit_non_program),
                "notice_count": sum(
                    (row.get("raw_fields") or {}).get("non_program_reason") == "notice"
                    for row in explicit_non_program
                ),
                "test_count": sum(
                    (row.get("raw_fields") or {}).get("non_program_reason") == "test"
                    for row in explicit_non_program
                ),
                "current_count": len(current_rows),
                "returned_count": len(result),
                "internal_detail_rows": len(internal_rows),
                "external_list_only_rows": len(external_rows),
                "branch_count": len({_clean(row.get("branch")) for row in result}),
                "branch_counts": dict(Counter(_clean(row.get("branch")) for row in result)),
                "source_status_counts": dict(
                    Counter(
                        _clean((row.get("raw_fields") or {}).get("source_status"))
                        for row in result
                    )
                ),
                "status_counts": dict(Counter(_clean(row.get("status")) for row in result)),
                "pagination_complete": True,
                "details_complete": detail_pages == len(internal_rows),
                "snapshot_complete": True,
                "source_cap_reached": False,
                "no_current_data": not current_rows,
                "application_endpoints_called": 0,
                "external_endpoints_called": 0,
                "pii_payload_persisted": False,
                "configured_collection_error": "",
                "ownership_scope": BWB_CHUNCHEON_OWNERSHIP_SCOPE,
                "municipality_code": BWB_CHUNCHEON_MUNICIPALITY_CODE,
            }
            return result, BWB_CHUNCHEON_PARSER, meta
    except Exception as exc:
        meta = _failure(f"{type(exc).__name__}: {_redact(exc)}")
        meta.update(
            {
                "physical_requests": runner.requests if runner is not None else 0,
                "sessions_created": runner.sessions_created if runner is not None else 0,
                "archive_total": archive_total,
                "partition_totals": dict(partition_totals),
                "source_total": source_total,
                "source_rows": sum(len(value) for value in page_rows.values()),
                "data_pages": data_pages,
                "pages": len(page_rows) + (1 if sentinel_rows else 0),
                "detail_attempts": detail_attempts,
                "detail_pages": detail_pages,
                "detail_errors": int(detail_attempts > detail_pages),
                "stable_first_page": stable_first,
                "stable_final_page": stable_final,
                "source_cap_reached": source_cap_reached,
            }
        )
        return [], BWB_CHUNCHEON_PARSER, meta


def collect_chuncheon_bwb_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = BWB_CHUNCHEON_DEFAULT_MAX_PAGES,
    detail_limit: int = BWB_CHUNCHEON_DEFAULT_DETAIL_LIMIT,
    *,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    if not is_chuncheon_bwb_target(target):
        return [], BWB_CHUNCHEON_PARSER, _failure(
            "target does not match the audited Chuncheon Baewobom owner"
        )
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], BWB_CHUNCHEON_PARSER, _failure(
                "managed session_factory injection is required"
            )
        session_factory = _default_session_factory
    try:
        return _collect(
            target,
            timeout=_positive(timeout, "timeout"),
            max_pages=max_pages,
            detail_limit=detail_limit,
            session_factory=session_factory,
            today=today,
            dedupe_rows=dedupe_rows,
        )
    except Exception as exc:
        return [], BWB_CHUNCHEON_PARSER, _failure(
            f"{type(exc).__name__}: {_redact(exc)}"
        )


collect = collect_chuncheon_bwb_courses


__all__ = [
    "BWB_CHUNCHEON_CANDIDATE_ID",
    "BWB_CHUNCHEON_CURRENT_FILTERS",
    "BWB_CHUNCHEON_DETAIL_ENDPOINT",
    "BWB_CHUNCHEON_LIVE_AUDIT_BASELINE",
    "BWB_CHUNCHEON_MUNICIPALITY_CODE",
    "BWB_CHUNCHEON_MUNICIPALITY_NAME",
    "BWB_CHUNCHEON_PARSER",
    "BWB_CHUNCHEON_PROVIDER",
    "BWB_CHUNCHEON_URL",
    "ChuncheonBwbContractError",
    "bwb_chuncheon_detail_url",
    "bwb_chuncheon_list_url",
    "collect",
    "collect_chuncheon_bwb_courses",
    "is_chuncheon_bwb_target",
]
