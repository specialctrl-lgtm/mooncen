"""Fail-closed collector for Siheung City's branded GSEEK catalogue.

The former ``siheung.go.kr/reservation`` education table is a frozen 2024
archive with disabled detail links.  Current city, lifelong-learning,
resident-centre, health and public-sports education is owned by the official
Siheung-branded GSEEK tenant.  The tenant landing page declares the complete
region-filtered offline total.  Its JSON endpoint accepts one-based,
end-exclusive ranges and returns nine rows per request.  A snapshot is
published only after the declared catalogue has been read in full, the exact
post-total range is empty, the first and last range signatures remain stable,
and every current/future non-test detail page passes the public course and
application-control contracts.

The Siheung Urban Corporation FMCS catalogue is a separate sports-course
owner (different centres and zero current normalized-title overlap) and is
therefore exposed as a boundary constant, not merged into this provider.

The anonymous collector never invokes the identity-verification or course
application endpoints.  Detail free text is PII-screened, while instructor
career/education blocks and unneeded source payload fields are never stored.
Production callers must inject the shared managed session factory; raw
requests are available only through the explicit test switch.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import hashlib
import html
import json
import math
import re
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


SIHEUNG_PROVIDER = "MUNI_WWW_SIHEUNG_GO_KR_0A4570AD"
SIHEUNG_CANDIDATE_ID = "MUNI_IR_94BB80AE44E6"
SIHEUNG_URL = "https://siheung.gseek.kr/user/course/offline/list"
SIHEUNG_HOST = "siheung.gseek.kr"
SIHEUNG_LIST_PATH = "/user/course/offline/list"
SIHEUNG_DETAIL_PATH = "/user/course/offline/view"
SIHEUNG_API_URL = "https://siheung.gseek.kr/user/course/offline/list/search"
SIHEUNG_PARENT_PROVIDER = "GYEONGGI_GSEEK"
SIHEUNG_PARENT_URL = "https://www.gseek.kr/user/course/offline/list"
SIHEUNG_PARENT_API_URL = "https://www.gseek.kr/user/course/offline/list/search"
SIHEUNG_REGION_CODE = "4139000000"
SIHEUNG_CO_SPONSOR_ID = "G000002"
SIHEUNG_SOURCE_REGIONS = frozenset(
    {
        "거북섬동",
        "군자동",
        "능곡동",
        "대야동",
        "매화동",
        "목감동",
        "배곧1동",
        "배곧2동",
        "시흥시",
        "신현동",
        "연성동",
        "월곶동",
        "은행동",
        "장곡동",
        "정왕1동",
        "정왕2동",
        "정왕3동",
        "정왕4동",
        "정왕본동",
    }
)
SIHEUNG_AUDITED_BRANCHES = frozenset(
    {
        "거북섬동 주민자치회",
        "능곡동 주민자치회",
        "대야동 주민자치회",
        "매화동 주민자치회",
        "목감동 주민자치회",
        "배곧1동 주민자치회",
        "배곧2동 주민자치회",
        "시흥시 건강돌봄과",
        "시흥시 건강증진과",
        "시흥시 공원조성과",
        "시흥시 교육자치과",
        "시흥시 교육자치과(서울대)",
        "시흥시 대야평생학습관",
        "시흥시 보건정책과",
        "시흥시 아동돌봄과",
        "시흥시 여성보육과",
        "시흥시 일자리경제과",
        "시흥시 정왕보건지소",
        "시흥시 정왕평생학습관",
        "시흥시 질병관리과",
        "시흥시 질병관리과_",
        "시흥시 평생학습과",
        "시흥시 평생학습과(한마음관)",
        "시흥시체육회",
        "월곶동 주민자치회",
        "은행동 주민자치회",
        "장곡동 주민자치회",
        "정왕2동 주민자치회",
        "학부모진로아카데미",
    }
)
SIHEUNG_EXCLUDED_TEST_ROWS: Mapping[tuple[str, str], str] = {
    ("62076", "1"): "[시흥시체육관] 테스트",
    ("53542", "5"): "테스트강좌(신청불가!)20250609",
    ("52405", "3"): "마을교사 역량강화 연수 TEST",
    ("52670", "1"): "동네언니 직무연수 TEST",
    ("52461", "1"): "(연습)은행동 문화센터 노래교실",
}
SIHEUNG_LEGACY_URL = "https://www.siheung.go.kr/reservation/edu/program/list.do"
SIHEUNG_STALE_CONTENTS_URL = "https://www.siheung.go.kr/main/contents.do?mId=0401070000"
SIHEUNG_SEPARATE_SPORTS_OWNER_URL = "https://sportsapp.shsi.or.kr/fmcs/3"
SIHEUNG_MUNICIPALITY_CODE = "4139000000"
SIHEUNG_MUNICIPALITY_NAME = "경기도 시흥시"
SIHEUNG_PAGE_SIZE = 9
SIHEUNG_DEFAULT_MAX_PAGES = 230
SIHEUNG_DEFAULT_DETAIL_LIMIT = 400
SIHEUNG_DEFAULT_MAX_REQUESTS = 750
SIHEUNG_SESSION_REQUEST_LIMIT = 90
SIHEUNG_PARSER = "siheung_gseek_region_post_census+exact_sentinel+stable_edges+current_detail"

SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]
Sleeper = Callable[[float], None]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"[1-9]\d*")
_LANDING_TOTAL_RE = re.compile(r"총\s*([\d,]+)\s*개의\s*강좌")
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)")
_GENERIC_NON_COURSE_RE = re.compile(
    r"^(?:(?:test|sample|테스트|샘플)(?:\s*[-_#]?\s*\d+)?|"
    r"(?:교육\s*)?(?:안내|공지)(?:사항)?|(?:강좌|교육)?\s*(?:등록|없음))$",
    re.IGNORECASE,
)
_TEST_MARKER_RE = re.compile(r"(?:test|테스트)", re.IGNORECASE)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2}[- .]?\d{3,4}[- .]?\d{4}|"
    r"01[016789][- .]?\d{3,4}[- .]?\d{4})(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RESIDENT_ID_RE = re.compile(r"(?<!\d)\d{6}\s*[- ]\s*[1-4]\d{6}(?!\d)")

_OPEN_SOURCE_STATUSES = frozenset({"모집중", "마감임박", "대기접수", "추가접수"})
_STATUS_MAP: Mapping[str, str] = {
    "모집중": "OPEN",
    "마감임박": "OPEN",
    "대기접수": "OPEN",
    "추가접수": "OPEN",
    "모집예정": "SCHEDULED",
    "마감": "CLOSED",
}
_CONTROL_TEXT: Mapping[str, str] = {
    "모집중": "수강신청",
    "마감임박": "수강신청",
    "대기접수": "대기신청",
    "추가접수": "수강신청",
    "모집예정": "모집예정",
    "마감": "신청마감",
}
_PUBLIC_DETAIL_LABELS = frozenset(
    {
        "신청기간",
        "일반신청기간",
        "우선신청기간",
        "추가신청기간",
        "대기현황",
        "학습기간",
        "학습일자",
        "교육시간",
        "교육대상",
        "모집인원",
        "수강료",
        "재료비",
        "교육장소",
    }
)


class SiheungContractError(ValueError):
    """Raised when the official source no longer satisfies its contract."""


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", html.unescape(str(value or "")).replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return "".join(char.lower() for char in _clean(value) if char.isalnum())


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _target_provider(target: Any) -> str:
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


def _positive_int(value: Any, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SiheungContractError(f"{label} must be a positive integer") from exc
    if parsed < 1:
        raise SiheungContractError(f"{label} must be a positive integer")
    return parsed


def is_siheung_education_target(target: Any) -> bool:
    """Accept only the exact provider-owned HTTPS catalogue landing page."""

    parsed = urlparse(_target_url(target))
    return (
        _target_provider(target) == SIHEUNG_PROVIDER
        and parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == SIHEUNG_HOST
        and parsed.port is None
        and parsed.path == SIHEUNG_LIST_PATH
        and not parsed.query
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


is_target = is_siheung_education_target


def siheung_detail_url(subject_id: Any, cycle_id: Any) -> str:
    subject = _clean(subject_id)
    cycle = _clean(cycle_id)
    if not _IDENTITY_RE.fullmatch(subject) or not _IDENTITY_RE.fullmatch(cycle):
        return ""
    return f"https://{SIHEUNG_HOST}{SIHEUNG_DETAIL_PATH}?" + urlencode(
        {"s_sbjct_sn": subject, "s_sbjct_cycl_sn": cycle}
    )


def siheung_api_range(page: Any) -> tuple[int, int]:
    raw = _clean(page)
    if not _IDENTITY_RE.fullmatch(raw):
        return (0, 0)
    start = (int(raw) - 1) * SIHEUNG_PAGE_SIZE + 1
    return start, start + SIHEUNG_PAGE_SIZE


def siheung_sentinel_range(total: Any) -> tuple[int, int]:
    raw = _clean(total).replace(",", "")
    if not raw.isdigit() or int(raw) < 1:
        return (0, 0)
    start = int(raw) + 1
    return start, start + SIHEUNG_PAGE_SIZE


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    return session


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _response_status(response: Any) -> int:
    try:
        return int(getattr(response, "status_code", 200))
    except (TypeError, ValueError):
        return 0


def _validate_response(response: Any, expected_url: str) -> None:
    status = _response_status(response)
    if status != 200:
        raise SiheungContractError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise SiheungContractError("HTTP redirects are not accepted")
    final_url = _clean(getattr(response, "url", ""))
    if final_url and final_url != expected_url:
        raise SiheungContractError("response URL escaped the canonical route")


def _response_soup(response: Any, expected_url: str) -> BeautifulSoup:
    _validate_response(response, expected_url)
    content = getattr(response, "content", None)
    if content is None:
        content = getattr(response, "text", None)
    if not content:
        raise SiheungContractError("empty HTML response")
    return BeautifulSoup(content, "lxml")


def _response_json(response: Any, expected_url: str) -> list[Any]:
    _validate_response(response, expected_url)
    payload = response.json()
    if not isinstance(payload, list):
        raise SiheungContractError("GSEEK API response is not a JSON list")
    return payload


class _Runner:
    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        timeout: int,
        max_requests: int,
        sleeper: Sleeper,
    ) -> None:
        self._session_factory = session_factory
        self._timeout = timeout
        self._max_requests = max_requests
        self._sleeper = sleeper
        self._session: Any = None
        self._session_requests = SIHEUNG_SESSION_REQUEST_LIMIT
        self.physical_requests = 0
        self.retry_count = 0
        self.sessions_created = 0

    def close(self) -> None:
        _close_quietly(self._session)
        self._session = None

    def _new_session(self) -> None:
        self.close()
        self._session = self._session_factory()
        self.sessions_created += 1
        self._session_requests = 0
        headers = getattr(self._session, "headers", None)
        if headers is not None and hasattr(headers, "update"):
            headers.update(
                {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
                }
            )

    def _ensure_session(self) -> None:
        if self._session is None or self._session_requests >= SIHEUNG_SESSION_REQUEST_LIMIT:
            self._new_session()

    def _attempt(self, operation: Callable[[Any], Any]) -> Any:
        last_error: Optional[Exception] = None
        for attempt in range(2):
            if self.physical_requests >= self._max_requests:
                raise SiheungContractError(f"max_requests cap {self._max_requests} exhausted")
            self._ensure_session()
            self._session_requests += 1
            self.physical_requests += 1
            try:
                return operation(self._session)
            except Exception as exc:
                last_error = exc
                if attempt == 0:
                    self.retry_count += 1
                    self._new_session()
                    self._sleeper(0.1)
        assert last_error is not None
        raise last_error

    def get_soup(self, url: str) -> BeautifulSoup:
        return self._attempt(
            lambda session: _response_soup(
                session.get(url, timeout=self._timeout, allow_redirects=False),
                url,
            )
        )

    def post_range(self, start: int, end: int) -> list[Any]:
        return self._attempt(
            lambda session: _response_json(
                session.post(
                    SIHEUNG_API_URL,
                    data={
                        "s_sort_by": "1",
                        "s_row_start": str(start),
                        "s_row_end": str(end),
                        "resion": SIHEUNG_REGION_CODE,
                    },
                    timeout=self._timeout,
                    allow_redirects=False,
                    headers={
                        "Referer": SIHEUNG_URL,
                        "X-Requested-With": "XMLHttpRequest",
                    },
                ),
                SIHEUNG_API_URL,
            )
        )


def _landing_total(soup: BeautifulSoup) -> Optional[int]:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != "시흥교육캠퍼스 쏙(SSOC)":
        return None
    values = {
        int(value.replace(",", "")) for value in _LANDING_TOTAL_RE.findall(_clean(soup.get_text(" ", strip=True)))
    }
    region = soup.select_one("input#s_resion_cd1[name='s_resion_cd1']")
    sponsor = soup.select_one("input[name='ARK_CO_SPRVSN_ID']")
    markup = str(soup)
    if (
        len(values) != 1
        or region is None
        or _clean(region.get("value")) != SIHEUNG_REGION_CODE
        or sponsor is None
        or _clean(sponsor.get("value")) != SIHEUNG_CO_SPONSOR_ID
        or SIHEUNG_LIST_PATH + "/search" not in markup
    ):
        return None
    return values.pop()


def _integer(value: Any) -> Optional[int]:
    raw = _clean(value).replace(",", "")
    if not raw.isdigit():
        return None
    return int(raw)


def _source_date(value: Any) -> Optional[date]:
    match = _DATE_RE.fullmatch(_clean(value))
    if not match:
        return None
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None


def _date_tokens(value: Any) -> list[date]:
    result: list[date] = []
    for parts in _DATE_RE.findall(_clean(value)):
        try:
            result.append(date(*(int(part) for part in parts)))
        except ValueError:
            continue
    return result


def _date_range(value: Any) -> tuple[str, str, str]:
    values = _date_tokens(value)
    if len(values) != 2 or values[1] < values[0]:
        return "", "", ""
    start, end = values
    return start.isoformat(), end.isoformat(), f"{start.isoformat()} ~ {end.isoformat()}"


def _single_date_range(value: Any) -> tuple[str, str, str]:
    values = _date_tokens(value)
    if len(values) != 1:
        return "", "", ""
    current = values[0].isoformat()
    return current, current, f"{current} ~ {current}"


def _money(value: Any) -> str:
    amount = _integer(value)
    if amount is None:
        return _clean(value)
    return "무료" if amount == 0 else f"{amount:,}원"


def _branch_code(branch: Any) -> str:
    digest = hashlib.sha1(_clean(branch).encode("utf-8")).hexdigest()[:12].upper()
    return f"SIHEUNG_GSEEK_BRANCH_{digest}"


def _contains_pii(value: Any) -> bool:
    text = _clean(value)
    return bool(_PHONE_RE.search(text) or _EMAIL_RE.search(text) or _RESIDENT_ID_RE.search(text))


def _public_description(value: Any, fallback: Any) -> tuple[str, bool]:
    text = _clean(value)
    if text and not _contains_pii(text):
        return text, False
    return _clean(fallback), bool(text)


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in (None, "", [], {})}


def _api_row(item: Mapping[str, Any]) -> tuple[dict[str, Any], list[str], bool]:
    errors: list[str] = []
    subject = _clean(item.get("d_sbjct_sn"))
    cycle = _clean(item.get("d_sbjct_cycl_sn"))
    identity = f"{subject}:{cycle}"
    title = _clean(item.get("d_sbjct_nm"))
    source_branch = _clean(item.get("d_edu_gvmnfc"))
    source_region = _clean(item.get("d_rgn"))
    source_status = _clean(item.get("d_recrut_stts_nm"))
    start = _source_date(item.get("d_edu_bgng_dt"))
    end = _source_date(item.get("d_edu_end_dt"))
    single_day = _clean(item.get("d_is_single_day_course"))
    excluded_title = SIHEUNG_EXCLUDED_TEST_ROWS.get((subject, cycle), "")
    explicit_test = bool(excluded_title)
    test_or_notice = bool(
        _GENERIC_NON_COURSE_RE.fullmatch(title)
        or _TEST_MARKER_RE.search(title)
        or title.startswith("(연습)")
    )

    if not _IDENTITY_RE.fullmatch(subject) or not _IDENTITY_RE.fullmatch(cycle):
        errors.append("non-numeric subject/cycle identity")
    if not title:
        errors.append(f"course {identity}: empty title")
    elif explicit_test and title != excluded_title:
        errors.append(f"course {identity}: audited test-row title changed")
    elif test_or_notice and not explicit_test:
        errors.append(f"course {identity}: test/notice row is not publishable")
    if not explicit_test and source_branch not in SIHEUNG_AUDITED_BRANCHES:
        errors.append(f"course {identity}: unaudited education institution")
    if source_region not in SIHEUNG_SOURCE_REGIONS:
        errors.append(f"course {identity}: non-Siheung source region")
    if _clean(item.get("d_co_sprvsn_id")) != SIHEUNG_CO_SPONSOR_ID:
        errors.append(f"course {identity}: non-Siheung site supervision")
    if not explicit_test and _clean(item.get("d_sbjct_type_cd_id")) != "OF":
        errors.append(f"course {identity}: not an offline course")
    if explicit_test and _clean(item.get("d_sbjct_type_cd_id")) not in {"OF", "NO"}:
        errors.append(f"course {identity}: audited test-row type changed")
    if source_status not in _STATUS_MAP:
        errors.append(f"course {identity}: unknown recruitment status")
    if start is None or end is None or end < start:
        errors.append(f"course {identity}: invalid education date range")
    if single_day not in {"Y", "N"}:
        errors.append(f"course {identity}: invalid single-day flag")
    elif start and end and ((start == end) != (single_day == "Y")):
        errors.append(f"course {identity}: single-day flag/date mismatch")

    start_time = _clean(item.get("d_edu_start_time"))
    end_time = _clean(item.get("d_edu_end_time"))
    weekday = _clean(item.get("d_edu_wday_cd_nm"))
    schedule = " ".join(
        value
        for value in (
            f"매주 {weekday}" if weekday and single_day != "Y" else "",
            f"{start_time} ~ {end_time}" if start_time and end_time else start_time or end_time,
        )
        if value
    )
    category = " > ".join(
        value
        for value in (
            _clean(item.get("d_clsf_depth1_nm")),
            _clean(item.get("d_clsf_depth2_nm")),
            _clean(item.get("d_clsf_depth3_nm")),
        )
        if value
    )
    capacity_total = _integer(item.get("d_edu_nope"))
    capacity_current = _integer(item.get("d_aply_cnt"))
    description, redacted = _public_description(item.get("d_sbjct_intrd_cn"), title)
    raw_url = siheung_detail_url(subject, cycle)
    period = f"{start.isoformat()} ~ {end.isoformat()}" if start and end else ""
    raw_fields: dict[str, Any] = {
        "parser": SIHEUNG_PARSER,
        "subject_id": subject,
        "cycle_id": cycle,
        "source_status": source_status,
        "source_branch": source_branch,
        "source_region": source_region,
        "co_sponsor_id": _clean(item.get("d_co_sprvsn_id")),
        "single_day": single_day,
        "source_start_time": start_time,
        "source_end_time": end_time,
        "source_description_redacted": redacted,
        "explicit_source_test_course": explicit_test,
    }
    row: dict[str, Any] = {
        "provider": SIHEUNG_PROVIDER,
        "provider_course_id": f"{SIHEUNG_PROVIDER}:course:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "branch": source_branch,
        "branch_code": _branch_code(source_branch),
        "provider_organizer": source_branch,
        "category": category or "평생학습",
        "raw_url": raw_url,
        "status": _STATUS_MAP.get(source_status, ""),
        "period": period,
        "start_date": start.isoformat() if start else "",
        "end_date": end.isoformat() if end else "",
        "schedule_raw": schedule,
        "target": _clean(item.get("d_sbjct_trgt_nm_1")),
        "fee": _money(item.get("d_sbjct_amt")),
        "material_fee": _money(item.get("d_prepar_cmdty_amt")),
        "capacity": capacity_total,
        "capacity_total": capacity_total,
        "capacity_current": capacity_current,
        "description": description,
        "instructor": _clean(item.get("d_instr_nm")),
        "application_method_raw": _clean(item.get("d_stdnt_chice_mthd_cd_nm")),
        "collection_category": "교육",
        "domain_category": "평생학습",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_integrated_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": "json_api+detail_html",
        "program_type": "강좌",
        "region": SIHEUNG_MUNICIPALITY_NAME,
        "municipality_code": SIHEUNG_MUNICIPALITY_CODE,
        "municipality_full_name": SIHEUNG_MUNICIPALITY_NAME,
        "reservation_available": False,
        "raw_fields": raw_fields,
    }
    return _clean_row(row), errors, test_or_notice


def _pairs(container: Any) -> tuple[dict[str, str], list[str]]:
    result: dict[str, str] = {}
    conflicts: list[str] = []
    if container is None:
        return result, conflicts
    for dl in container.select("dl"):
        pending = ""
        for node in dl.find_all(["dt", "dd"], recursive=False):
            if node.name == "dt":
                pending = _clean(node.get_text(" ", strip=True))
            elif pending:
                value = _clean(node.get_text(" ", strip=True))
                if pending in _PUBLIC_DETAIL_LABELS:
                    if pending in result and result[pending] != value:
                        conflicts.append(pending)
                    result[pending] = value
                pending = ""
    return result, conflicts


def _input_value(soup: BeautifulSoup, name: str) -> str:
    node = soup.select_one(f"input[name='{name}']")
    return _clean(node.get("value")) if node is not None else ""


def _detail_contract(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    errors: list[str] = []
    raw_fields = row.get("raw_fields") or {}
    subject = _clean(raw_fields.get("subject_id"))
    cycle = _clean(raw_fields.get("cycle_id"))
    identity = f"{subject}:{cycle}"
    container = soup.select_one("div.course-detail-container")
    if container is None:
        return [f"course {identity}: missing detail container"]

    if _input_value(soup, "s_sbjct_sn") != subject or _input_value(soup, "s_sbjct_cycl_sn") != cycle:
        errors.append(f"course {identity}: detail identity mismatch")
    title_node = container.select_one("h2.course-title")
    detail_title = _clean(title_node.get_text(" ", strip=True)) if title_node else ""
    if _normalized(detail_title) != _normalized(row.get("title")):
        errors.append(f"course {identity}: detail/list title mismatch")

    branch_nodes = container.select("section.key-course-info span.tag-field")
    branches = [_clean(node.get_text(" ", strip=True)) for node in branch_nodes]
    if branches != [_clean(raw_fields.get("source_branch"))]:
        errors.append(f"course {identity}: detail/list branch mismatch")
    region_node = container.select_one("section.key-course-info span.tag-type.offline-type")
    detail_region = _clean(region_node.get_text(" ", strip=True)) if region_node else ""
    if detail_region != _clean(raw_fields.get("source_region")):
        errors.append(f"course {identity}: detail/list source region mismatch")
    status_node = container.select_one("section.key-course-info .tag-item-xs")
    detail_status = _clean(status_node.get_text(" ", strip=True)) if status_node else ""
    source_status = _clean(raw_fields.get("source_status"))
    if detail_status != source_status:
        errors.append(f"course {identity}: detail/list status mismatch")

    pairs, conflicts = _pairs(container)
    if conflicts:
        errors.append(f"course {identity}: conflicting duplicate detail labels " + ",".join(sorted(set(conflicts))))
    if _clean(raw_fields.get("single_day")) == "Y":
        detail_start, detail_end, detail_period = _single_date_range(pairs.get("학습일자"))
    else:
        detail_start, detail_end, detail_period = _date_range(pairs.get("학습기간"))
    if detail_period != _clean(row.get("period")):
        errors.append(f"course {identity}: detail/list education period mismatch")

    application_ranges: dict[str, tuple[str, str, str]] = {}
    for label in ("신청기간", "일반신청기간", "우선신청기간", "추가신청기간"):
        if label not in pairs:
            continue
        parsed = _date_range(pairs.get(label))
        if not parsed[2]:
            errors.append(f"course {identity}: malformed {label}")
        application_ranges[label] = parsed
    if not application_ranges:
        errors.append(f"course {identity}: missing application period")
    canonical_label = next(
        (label for label in ("추가신청기간", "신청기간", "일반신청기간") if label in application_ranges),
        "",
    )
    if not canonical_label:
        errors.append(f"course {identity}: no canonical application period")
    apply_start, apply_end, apply_period = application_ranges.get(canonical_label, ("", "", ""))

    schedule = _clean(pairs.get("교육시간"))
    if not schedule:
        errors.append(f"course {identity}: missing education time")
    for token in (
        _clean(raw_fields.get("source_start_time")),
        _clean(raw_fields.get("source_end_time")),
    ):
        if token and token not in schedule:
            errors.append(f"course {identity}: detail/list education time mismatch")
            break
    target = _clean(pairs.get("교육대상"))
    if not target:
        errors.append(f"course {identity}: missing education target")
    detail_capacity = _integer(re.sub(r"[^\d,]", "", pairs.get("모집인원", "")))
    if detail_capacity is None:
        errors.append(f"course {identity}: missing detail capacity")
    elif detail_capacity != row.get("capacity_total"):
        errors.append(f"course {identity}: detail/list capacity mismatch")
    venue = _clean(pairs.get("교육장소"))
    if not venue:
        errors.append(f"course {identity}: missing education venue")

    expected_return = urlparse(_clean(row.get("raw_url"))).path + "?" + urlparse(_clean(row.get("raw_url"))).query
    if _input_value(soup, "p_return_url") != expected_return:
        errors.append(f"course {identity}: malformed login return URL")
    markup = str(soup)
    if "/user/course/cert/checkCi" not in markup or "/user/course/aply" not in markup:
        errors.append(f"course {identity}: missing canonical application flow")
    controls = container.select(".btn-course-box .btn-course-apply")
    if len(controls) != 1:
        errors.append(f"course {identity}: expected one application control")
        control = controls[0] if controls else None
    else:
        control = controls[0]
    classes = control.get("class") or [] if control is not None else []
    disabled = bool(
        control is not None
        and (
            control.name == "span"
            or "disabled" in classes
            or control.has_attr("disabled")
            or _clean(control.get("aria-disabled")).lower() == "true"
        )
    )
    control_text = _clean(control.get_text(" ", strip=True)) if control else ""
    if control_text != _CONTROL_TEXT.get(source_status, ""):
        errors.append(f"course {identity}: status/application label mismatch")
    is_open = source_status in _OPEN_SOURCE_STATUSES
    if disabled == is_open:
        errors.append(f"course {identity}: status/application control mismatch")

    detail_description_node = container.select_one(".course-desc")
    detail_description = (
        _clean(detail_description_node.get_text(" ", strip=True)) if detail_description_node is not None else ""
    )
    public_description, detail_redacted = _public_description(
        detail_description, row.get("description") or row.get("title")
    )
    row.update(
        {
            "apply_period": apply_period,
            "apply_start_date": apply_start,
            "apply_end_date": apply_end,
            "schedule_raw": schedule or row.get("schedule_raw"),
            "target": target or row.get("target"),
            "venue_name": venue,
            "venue_address": venue,
            "room": venue,
            "description": public_description,
            "reservation_available": bool(is_open and not disabled),
        }
    )
    for label, prefix in (
        ("일반신청기간", "general_apply"),
        ("우선신청기간", "priority_apply"),
        ("추가신청기간", "additional_apply"),
    ):
        value_start, value_end, value_period = application_ranges.get(label, ("", "", ""))
        if value_period:
            row[f"{prefix}_period"] = value_period
            row[f"{prefix}_start_date"] = value_start
            row[f"{prefix}_end_date"] = value_end
    if is_open and not disabled:
        row["application_url"] = _clean(row.get("raw_url"))
        row["application_type"] = "ONLINE_RESERVATION"
    else:
        row.pop("application_url", None)
        raw_fields["clear_application_url"] = True
    raw_fields.update(
        {
            "detail_status": detail_status,
            "detail_region": detail_region,
            "detail_labels": sorted(pairs),
            "detail_start": detail_start,
            "detail_end": detail_end,
            "canonical_application_period_label": canonical_label,
            "detail_application_control": bool(control),
            "detail_application_control_text": control_text,
            "detail_application_disabled": disabled,
            "detail_description_redacted": detail_redacted,
            "application_endpoints_audited_not_called": True,
        }
    )
    return errors


def _payload_signature(payload: list[Any]) -> str:
    signature_rows: list[list[str]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            raise SiheungContractError("range signature encountered a non-object row")
        signature_rows.append(
            [
                _clean(item.get("d_sbjct_sn")),
                _clean(item.get("d_sbjct_cycl_sn")),
                _clean(item.get("d_sbjct_nm")),
                _clean(item.get("d_edu_gvmnfc")),
                _clean(item.get("d_rgn")),
                _clean(item.get("d_edu_bgng_dt")),
                _clean(item.get("d_edu_end_dt")),
                _clean(item.get("d_edu_start_time")),
                _clean(item.get("d_edu_end_time")),
                _clean(item.get("d_total_cnt")),
            ]
        )
    encoded = json.dumps(signature_rows, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _semantic_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        _normalized(row.get("title")),
        _normalized(row.get("branch")),
        _clean(row.get("period")),
        _normalized(row.get("schedule_raw")),
        _normalized(row.get("venue_name")),
    )


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _base_meta(error: str = "") -> dict[str, Any]:
    return {
        "pages": 0,
        "main_discovery_pages": 0,
        "list_requests": 0,
        "physical_requests": 0,
        "retry_count": 0,
        "sessions_created": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "source_total": 0,
        "source_rows": 0,
        "current_count": 0,
        "returned_count": 0,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "configured_collection_error": error,
    }


def collect_siheung_education_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = SIHEUNG_DEFAULT_MAX_PAGES,
    detail_limit: int = SIHEUNG_DEFAULT_DETAIL_LIMIT,
    *,
    max_requests: int = SIHEUNG_DEFAULT_MAX_REQUESTS,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    sleeper: Sleeper = time.sleep,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one atomic current/future Siheung education snapshot.

    max_pages caps the data ranges plus the exact post-total sentinel.
    detail_limit must cover every current/future course.  max_requests caps
    physical attempts, including retries and the two edge-stability reads.
    """

    meta = _base_meta()
    if not is_siheung_education_target(target):
        meta["configured_collection_error"] = "target does not match the canonical Siheung GSEEK route"
        return [], SIHEUNG_PARSER, meta
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta["configured_collection_error"] = "managed session_factory injection is required"
            return [], SIHEUNG_PARSER, meta
        session_factory = _default_session_factory
    try:
        timeout = _positive_int(timeout, "timeout")
        max_pages = _positive_int(max_pages, "max_pages")
        detail_limit = _positive_int(detail_limit, "detail_limit")
        max_requests = _positive_int(max_requests, "max_requests")
        cutoff = _today(today)
    except Exception as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], SIHEUNG_PARSER, meta

    runner = _Runner(
        session_factory=session_factory,
        timeout=timeout,
        max_requests=max_requests,
        sleeper=sleeper,
    )
    errors: list[str] = []
    page_payloads: dict[int, list[Any]] = {}
    rows: list[dict[str, Any]] = []
    current_rows: list[dict[str, Any]] = []
    result: list[dict[str, Any]] = []
    source_total = 0
    data_pages = 0
    required_range_requests = 0
    sentinel_start = 0
    sentinel_count = -1
    stability_rechecks = 0
    edge_signatures: dict[str, str] = {}
    detail_attempts = 0
    detail_pages = 0
    detail_errors = 0
    malformed_count = 0
    test_or_notice_count = 0
    explicit_test_count = 0
    current_source_count = 0
    current_explicit_test_count = 0
    semantic_duplicate_count = 0
    duplicate_count = 0
    duplicate_url_count = 0
    expired_count = 0
    required_logical_requests = 0
    source_cap_reached = False
    landing_soup: Optional[BeautifulSoup] = None

    try:
        try:
            landing_soup = runner.get_soup(SIHEUNG_URL)
        except Exception as exc:
            errors.append(f"landing page: fetch {type(exc).__name__}")
        if landing_soup is not None:
            declared = _landing_total(landing_soup)
            if declared is None or declared < 1:
                errors.append("landing page: missing unambiguous Siheung catalogue contract")
            else:
                source_total = declared
                data_pages = math.ceil(source_total / SIHEUNG_PAGE_SIZE)
                required_range_requests = data_pages + 1
                sentinel_start = source_total + 1
                edge_page_count = 1 if data_pages == 1 else 2
                minimum_requests = 1 + required_range_requests + edge_page_count
                if required_range_requests > max_pages:
                    source_cap_reached = True
                    errors.append(
                        f"max_pages cap allows {max_pages} of {required_range_requests} required API range requests"
                    )
                if minimum_requests > max_requests:
                    source_cap_reached = True
                    errors.append(
                        f"max_requests cap allows {max_requests} of at least "
                        f"{minimum_requests} required logical requests"
                    )

        if not errors:
            for page in range(1, data_pages + 1):
                start, end = siheung_api_range(page)
                try:
                    page_payloads[page] = runner.post_range(start, end)
                except Exception as exc:
                    errors.append(f"API range {page}: fetch {type(exc).__name__}")
                    break
            if not errors:
                start, end = siheung_sentinel_range(source_total)
                try:
                    page_payloads[data_pages + 1] = runner.post_range(start, end)
                except Exception as exc:
                    errors.append(f"API exact sentinel: fetch {type(exc).__name__}")

        if not errors:
            for page in range(1, data_pages + 1):
                payload = page_payloads.get(page, [])
                expected = min(
                    SIHEUNG_PAGE_SIZE,
                    source_total - (page - 1) * SIHEUNG_PAGE_SIZE,
                )
                if len(payload) != expected:
                    errors.append(f"API range {page}: expected {expected} rows, got {len(payload)}")
                for item in payload:
                    if not isinstance(item, Mapping):
                        malformed_count += 1
                        errors.append(f"API range {page}: non-object course row")
                        continue
                    if _integer(item.get("d_total_cnt")) != source_total:
                        errors.append(f"API range {page}: catalogue total changed")
                    row, row_errors, test_or_notice = _api_row(item)
                    rows.append(row)
                    malformed_count += len(row_errors)
                    test_or_notice_count += int(test_or_notice)
                    explicit_test_count += int(
                        bool(row.get("raw_fields", {}).get("explicit_source_test_course"))
                    )
                    errors.extend(row_errors)
            sentinel_payload = page_payloads.get(data_pages + 1, [])
            sentinel_count = len(sentinel_payload)
            if sentinel_payload:
                errors.append("API exact post-total sentinel range is not empty")
            if len(rows) != source_total:
                errors.append(f"declared total {source_total} != parsed rows {len(rows)}")

        if not errors:
            edge_pages = [1] if data_pages == 1 else [1, data_pages]
            for page in edge_pages:
                start, end = siheung_api_range(page)
                original = page_payloads[page]
                original_signature = _payload_signature(original)
                try:
                    repeated = runner.post_range(start, end)
                    stability_rechecks += 1
                except Exception as exc:
                    errors.append(f"API stability range {page}: fetch {type(exc).__name__}")
                    break
                repeated_signature = _payload_signature(repeated)
                label = "first" if page == 1 else "last"
                edge_signatures[label] = original_signature
                if len(repeated) != len(original) or repeated_signature != original_signature:
                    errors.append(f"API {label} range signature changed during census")

        identities = [_clean(row.get("provider_course_id")) for row in rows]
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count:
            errors.append(f"{duplicate_count} duplicate source course identities")
        urls = [_clean(row.get("raw_url")) for row in rows]
        duplicate_url_count = len(urls) - len(set(urls))
        if duplicate_url_count:
            errors.append(f"{duplicate_url_count} duplicate canonical detail URLs")

        for row in rows:
            try:
                end = date.fromisoformat(_clean(row.get("end_date")))
            except ValueError:
                errors.append(f"{_clean(row.get('provider_course_id'))}: invalid end date")
                continue
            if end < cutoff:
                expired_count += 1
            else:
                current_source_count += 1
                if row.get("raw_fields", {}).get("explicit_source_test_course"):
                    current_explicit_test_count += 1
                else:
                    current_rows.append(row)
        if len(current_rows) > detail_limit:
            source_cap_reached = True
            errors.append(
                f"detail_limit cap allows {detail_limit} of {len(current_rows)} required current/future details"
            )
        edge_page_count = 0 if data_pages < 1 else (1 if data_pages == 1 else 2)
        required_logical_requests = (
            1 + required_range_requests + edge_page_count + len(current_rows) if data_pages else 0
        )
        if required_logical_requests > max_requests:
            source_cap_reached = True
            errors.append(
                f"max_requests cap allows {max_requests} of {required_logical_requests} required logical requests"
            )

        if not errors:
            for row in current_rows:
                detail_attempts += 1
                try:
                    detail_soup = runner.get_soup(_clean(row.get("raw_url")))
                    row_errors = _detail_contract(row, detail_soup)
                    if row_errors:
                        detail_errors += len(row_errors)
                        errors.extend(row_errors)
                    else:
                        detail_pages += 1
                except Exception as exc:
                    detail_errors += 1
                    errors.append(f"{_clean(row.get('provider_course_id'))}: detail fetch {type(exc).__name__}")

        if not errors:
            semantic = [_semantic_key(row) for row in current_rows]
            semantic_duplicate_count = len(semantic) - len(set(semantic))
            if semantic_duplicate_count:
                errors.append(f"{semantic_duplicate_count} duplicate current semantic signatures")
        if not errors:
            deduper = dedupe_rows or _dedupe_default
            result = list(deduper([_clean_row(row) for row in current_rows]))
            if len(result) != len(current_rows):
                errors.append(f"dedupe changed complete row count {len(current_rows)} to {len(result)}")
                result = []

        snapshot_complete = not errors
        pagination_complete = bool(
            snapshot_complete
            and len(page_payloads) == required_range_requests
            and sentinel_count == 0
            and len(rows) == source_total
            and stability_rechecks == edge_page_count
        )
        details_complete = bool(
            snapshot_complete
            and detail_attempts == len(current_rows)
            and detail_pages == len(current_rows)
            and detail_errors == 0
        )
        source_status_counts = Counter(_clean(row.get("raw_fields", {}).get("source_status")) for row in rows)
        current_source_status_counts = Counter(
            _clean(row.get("raw_fields", {}).get("source_status")) for row in current_rows
        )
        source_region_counts = Counter(_clean(row.get("raw_fields", {}).get("source_region")) for row in rows)
        current_source_region_counts = Counter(
            _clean(row.get("raw_fields", {}).get("source_region")) for row in current_rows
        )
        current_branch_counts = Counter(_clean(row.get("branch")) for row in current_rows)
        status_counts = Counter(_clean(row.get("status")) for row in result)
        meta.update(
            {
                "pages": len(page_payloads),
                "main_discovery_pages": 1 if landing_soup is not None else 0,
                "list_requests": len(page_payloads) + stability_rechecks,
                "required_list_requests": required_range_requests + edge_page_count,
                "physical_requests": runner.physical_requests,
                "retry_count": runner.retry_count,
                "sessions_created": runner.sessions_created,
                "detail_attempts": detail_attempts,
                "detail_pages": detail_pages,
                "detail_errors": detail_errors,
                "source_total": source_total,
                "source_rows": len(rows),
                "data_pages": data_pages,
                "required_range_requests": required_range_requests,
                "page_counts": {page: len(payload) for page, payload in page_payloads.items()},
                "sentinel_page": data_pages + 1 if data_pages else 0,
                "sentinel_start": sentinel_start,
                "sentinel_end": (sentinel_start + SIHEUNG_PAGE_SIZE if sentinel_start else 0),
                "sentinel_count": sentinel_count,
                "sentinel_kind": "exact_post_total_empty",
                "stability_rechecks": stability_rechecks,
                "first_page_signature": edge_signatures.get("first", ""),
                "last_page_signature": edge_signatures.get("last", edge_signatures.get("first", "")),
                "first_identity": (
                    _clean(rows[0].get("raw_fields", {}).get("subject_id"))
                    + ":"
                    + _clean(rows[0].get("raw_fields", {}).get("cycle_id"))
                    if rows
                    else ""
                ),
                "last_identity": (
                    _clean(rows[-1].get("raw_fields", {}).get("subject_id"))
                    + ":"
                    + _clean(rows[-1].get("raw_fields", {}).get("cycle_id"))
                    if rows
                    else ""
                ),
                "malformed_count": malformed_count,
                "test_or_notice_row_count": test_or_notice_count,
                "explicit_test_excluded_count": explicit_test_count,
                "current_explicit_test_excluded_count": current_explicit_test_count,
                "eligible_source_rows": len(rows) - explicit_test_count,
                "expired_count": expired_count,
                "source_current_count": current_source_count,
                "current_count": len(current_rows),
                "returned_count": len(result),
                "source_status_counts": dict(source_status_counts),
                "current_source_status_counts": dict(current_source_status_counts),
                "status_counts": dict(status_counts),
                "source_region_counts": dict(source_region_counts),
                "current_source_region_counts": dict(current_source_region_counts),
                "branch_count": len(current_branch_counts),
                "branch_counts": dict(current_branch_counts),
                "venue_count": len({_clean(row.get("venue_name")) for row in result}),
                "duplicate_count": duplicate_count,
                "duplicate_url_count": duplicate_url_count,
                "semantic_duplicate_count": semantic_duplicate_count,
                "pii_redaction_count": sum(
                    bool(row.get("raw_fields", {}).get("source_description_redacted"))
                    or bool(row.get("raw_fields", {}).get("detail_description_redacted"))
                    for row in current_rows
                ),
                "application_control_count": sum(
                    bool(row.get("raw_fields", {}).get("detail_application_control")) for row in current_rows
                ),
                "reservation_discovery_links": sum(bool(row.get("application_url")) for row in result),
                "discovered_links": len(rows),
                "required_logical_requests": required_logical_requests,
                "max_requests": max_requests,
                "session_request_limit": SIHEUNG_SESSION_REQUEST_LIMIT,
                "pagination_detected": data_pages > 1,
                "pagination_complete": pagination_complete,
                "details_complete": details_complete,
                "snapshot_complete": snapshot_complete,
                "source_cap_reached": source_cap_reached,
                "no_current_data": bool(snapshot_complete and not current_rows),
                "no_current_reason": (
                    "all complete Siheung GSEEK catalogue courses have ended"
                    if snapshot_complete and not current_rows
                    else ""
                ),
                "configured_collection_error": "; ".join(errors),
                "candidate_id": SIHEUNG_CANDIDATE_ID,
                "ownership_region_code": SIHEUNG_REGION_CODE,
                "ownership_co_sponsor_id": SIHEUNG_CO_SPONSOR_ID,
                "ownership_branches": sorted(SIHEUNG_AUDITED_BRANCHES),
                "parent_aggregate_provider": SIHEUNG_PARENT_PROVIDER,
                "parent_aggregate_exclusion_required": True,
                "parent_aggregate_exclusion_field": "d_co_sprvsn_id",
                "parent_aggregate_exclusion_value": SIHEUNG_CO_SPONSOR_ID,
                "parent_aggregate_overlap_identity": "d_sbjct_sn+d_sbjct_cycl_sn",
                "legacy_frozen_archive_url": SIHEUNG_LEGACY_URL,
                "stale_contents_redirect_url": SIHEUNG_STALE_CONTENTS_URL,
                "separate_sports_owner_url": SIHEUNG_SEPARATE_SPORTS_OWNER_URL,
                "application_endpoints_called": 0,
            }
        )
        if errors:
            return [], SIHEUNG_PARSER, meta
        return result, SIHEUNG_PARSER, meta
    finally:
        runner.close()


collect = collect_siheung_education_courses


__all__ = [
    "SIHEUNG_API_URL",
    "SIHEUNG_AUDITED_BRANCHES",
    "SIHEUNG_CANDIDATE_ID",
    "SIHEUNG_CO_SPONSOR_ID",
    "SIHEUNG_DEFAULT_DETAIL_LIMIT",
    "SIHEUNG_DEFAULT_MAX_PAGES",
    "SIHEUNG_DEFAULT_MAX_REQUESTS",
    "SIHEUNG_MUNICIPALITY_CODE",
    "SIHEUNG_MUNICIPALITY_NAME",
    "SIHEUNG_EXCLUDED_TEST_ROWS",
    "SIHEUNG_LEGACY_URL",
    "SIHEUNG_PAGE_SIZE",
    "SIHEUNG_PARENT_API_URL",
    "SIHEUNG_PARENT_PROVIDER",
    "SIHEUNG_PARENT_URL",
    "SIHEUNG_PARSER",
    "SIHEUNG_PROVIDER",
    "SIHEUNG_REGION_CODE",
    "SIHEUNG_SEPARATE_SPORTS_OWNER_URL",
    "SIHEUNG_SESSION_REQUEST_LIMIT",
    "SIHEUNG_SOURCE_REGIONS",
    "SIHEUNG_STALE_CONTENTS_URL",
    "SIHEUNG_URL",
    "SiheungContractError",
    "collect",
    "collect_siheung_education_courses",
    "siheung_api_range",
    "siheung_detail_url",
    "siheung_sentinel_range",
    "is_siheung_education_target",
    "is_target",
]
