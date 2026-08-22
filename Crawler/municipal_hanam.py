"""Fail-closed education collectors for Hanam City (4145000000).

Hanam has five independent public course ledgers.  They intentionally remain
separate provider identities: the branded GSEEK tenant, resident-centre
programmes, the youth training centre, Hanam Education Foundation (HDream),
and the eight-branch public-library catalogue.  This module reads only public
list/detail resources.  Login, identity verification, application, view-count
mutation, download and applicant endpoints are never requested.

Production callers must inject the shared managed ``session_factory``.  Raw
``requests`` sessions are available only behind an explicit test/live-audit
switch.
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
from urllib.parse import parse_qs, unquote_plus, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


HANAM_MUNICIPALITY_CODE = "4145000000"
HANAM_MUNICIPALITY_NAME = "경기도 하남시"

HANAM_GSEEK_PROVIDER = "MUNI_WWW_HANAM_GO_KR_553EE539"
HANAM_GSEEK_CANDIDATE_ID = "MUNI_IR_F57BE6FE7683"
HANAM_GSEEK_URL = "https://hanam.gseek.kr/user/course/offline/list"
HANAM_GSEEK_API_URL = HANAM_GSEEK_URL + "/search"
HANAM_GSEEK_PARENT_URL = "https://www.gseek.kr/user/course/offline/list"
HANAM_GSEEK_REGION_CODE = HANAM_MUNICIPALITY_CODE
HANAM_GSEEK_CO_SPONSOR_ID = "G000013"
HANAM_GSEEK_PAGE_SIZE = 9

HANAM_RESIDENT_PROVIDER = "MUNI_WWW_HANAM_GO_KR_04578639"
HANAM_RESIDENT_CANDIDATE_ID = "MUNI_IR_6B2938E32126"
HANAM_RESIDENT_URL = (
    "https://www.hanam.go.kr/www/selectInhbtntProgrmWebList.do?key=125"
)
HANAM_RESIDENT_BRANCHES = (
    "천현동", "신장1동", "신장2동", "덕풍1동", "덕풍2동", "덕풍3동",
    "미사1동", "미사2동", "미사3동", "감북동", "춘궁동", "초이동",
    "위례동", "감일동",
)
HANAM_RESIDENT_EXCLUDED_TITLES = frozenset(
    {"수강신청 테스트(관리자용)", "테스트(test)", "테스트(신청x)"}
)

HANAM_YOUTH_PROVIDER = "MUNI_ONLINE_HNYOUTH_KR_6F390C33"
HANAM_YOUTH_DEPRECATED_PROVIDER = "MUNI_ONLINE_HNYOUTH_KR_A03457AE"
HANAM_YOUTH_URL = "https://online.hnyouth.kr/HnYouth/"
HANAM_YOUTH_LIST_URL = (
    "https://online.hnyouth.kr/HnYouth/s_center/jLecture_Search_List_202406.ajax.php"
)
HANAM_YOUTH_DETAIL_URL = "https://online.hnyouth.kr/HnYouth/s_center/pro_view.php"
HANAM_YOUTH_BRANCH = "하남시청소년수련관"
HANAM_YOUTH_FORM_KEYS = (
    "sales_code", "event_name", "g_code", "s_code", "b_code", "center_id",
    "page", "ntitle", "cx_id", "month_qty", "unit_price", "tot", "target_code",
)

HANAM_HDREAM_PROVIDER = "MUNI_WWW_HDREAM_OR_KR_064EE411"
HANAM_HDREAM_CANDIDATE_ID = "MUNI_IR_704D7C8E2BCF"
HANAM_HDREAM_URL = "https://www.hdream.or.kr/experience-program/program"
HANAM_HDREAM_API_URL = "https://hnon-api.hdream.or.kr/experience-program/user/program"
HANAM_HDREAM_DETAIL_API_URL = (
    "https://hnon-api.hdream.or.kr/experience-program/program/detail/load"
)
HANAM_HDREAM_LEGACY_ALIAS = "https://hlearn.or.kr/"
HANAM_HDREAM_DEVELOPMENT_ALIAS = "https://hdream.wbsoft.kr/"
HANAM_HDREAM_FROZEN_ARCHIVE_URL = "https://www.hedu.or.kr/eduBiz/eduApp/edu_list.asp"
HANAM_HDREAM_PAGE_SIZE = 10

HANAM_LIBRARY_PROVIDER = "MUNI_WWW_HANAMLIB_GO_KR_EE810F0A"
HANAM_LIBRARY_CANDIDATE_ID = "MUNI_IR_CAA8D8AA5532"
HANAM_LIBRARY_URL = (
    "https://www.hanamlib.go.kr/silib/selectWebEdcLctreList.do?key=163"
)
HANAM_LIBRARY_BRANCHES: tuple[tuple[str, str, str], ...] = (
    ("신장도서관", "silib", "163"),
    ("나룰도서관", "nalib", "72"),
    ("미사도서관", "mslib", "689"),
    ("덕풍도서관", "dulib", "231"),
    ("위례도서관", "wilib", "975"),
    ("일가도서관", "iglib", "1047"),
    ("세미도서관", "selib", "453"),
    ("디지털도서관", "dilib", "553"),
)

HANAM_SCHOOL_ONLY_OWNER_URL = "https://www.hanam.go.kr/hnedu/"
HANAM_SEPARATE_SPORTS_OWNER_URL = "https://www.hanam.go.kr/www/contents.do?key=3319"
HANAM_NATIONAL_DIGITAL_LEARNING_URL = "https://www.디지털배움터.kr"

HANAM_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    "gseek": {
        "provider": HANAM_GSEEK_PROVIDER,
        "url": HANAM_GSEEK_URL,
        "decision": "retarget_incumbent_static_city_provider_to_current_gseek_owner",
    },
    "gseek_parent": {
        "url": HANAM_GSEEK_PARENT_URL,
        "decision": "exclude_rows_where_d_co_sprvsn_id_is_G000013",
    },
    "resident_centres": {
        "provider": HANAM_RESIDENT_PROVIDER,
        "url": HANAM_RESIDENT_URL,
        "decision": "new_fourteen_branch_owner_including_chungung",
    },
    "youth": {
        "provider": HANAM_YOUTH_PROVIDER,
        "url": HANAM_YOUTH_URL,
        "decision": "retain_incumbent_owner_and_disable_deprecated_guide_alias",
    },
    "hdream": {
        "provider": HANAM_HDREAM_PROVIDER,
        "url": HANAM_HDREAM_URL,
        "decision": "new_education_foundation_owner",
    },
    "hdream_old_foundation_site": {
        "url": HANAM_HDREAM_FROZEN_ARCHIVE_URL,
        "decision": "disable_frozen_2025_archive_replaced_by_hdream",
    },
    "libraries": {
        "provider": HANAM_LIBRARY_PROVIDER,
        "url": HANAM_LIBRARY_URL,
        "decision": "one_owner_for_eight_official_branch_ledgers",
    },
    "future_education_district": {
        "url": HANAM_SCHOOL_ONLY_OWNER_URL,
        "decision": "exclude_teacher_and_school_only_application_ledger",
    },
    "sports": {
        "url": HANAM_SEPARATE_SPORTS_OWNER_URL,
        "decision": "keep_as_separate_sports_owner",
    },
    "digital_learning": {
        "url": HANAM_NATIONAL_DIGITAL_LEARNING_URL,
        "decision": "separate_national_platform_link_not_hanam_owned_ledger",
    },
}

HANAM_LIVE_AUDIT_BASELINE: Mapping[str, Mapping[str, Any]] = {
    "gseek": {
        "checked_at": "2026-07-23", "source_total": 139, "current_count": 70,
        "sentinel_start": 140,
        "source_identity_sha256": "a3f852d1ae0e48771dbee974d731605678c197eb8c0274ff44a13f0b03dea322",
    },
    "resident": {
        "checked_at": "2026-07-23", "source_total": 297, "returned_count": 294,
        "sentinel_page": 31,
        "source_identity_sha256": "d1819b01e31f8114228bd561419d00b5b8ae3606233339b965f3bbcfae86fbff",
    },
    "youth": {
        "checked_at": "2026-07-23", "source_total": 8, "current_count": 8,
        "sentinel_page": 2,
    },
    "hdream": {
        "checked_at": "2026-07-23", "source_total": 31, "current_count": 7,
        "sentinel_start": 31,
        "source_identity_sha256": "89d1a4b562201acf8fb2c57b6a49b5f34c8c06c97293727260f22396c8b891d5",
    },
    "library": {
        "checked_at": "2026-07-23", "source_total": 58, "current_count": 58,
        "branch_count": 8,
    },
}

HANAM_PARSER = (
    "hanam_owner_dispatch+complete_pagination+empty_sentinels+stable_edges+"
    "all_current_safe_details+official_branches+pii_minimized+no_application_calls"
)
HANAM_DEFAULT_MAX_PAGES = 240
HANAM_DEFAULT_DETAIL_LIMIT = 650
HANAM_DEFAULT_MAX_REQUESTS = 1_200
HANAM_SESSION_REQUEST_LIMIT = 90

SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]
Sleeper = Callable[[float], None]

_SPACE_RE = re.compile(r"\s+")
_ID_RE = re.compile(r"[1-9]\d*")
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)")
_DATE8_RE = re.compile(r"20\d{6}")
_PHONE_RE = re.compile(r"(?<!\d)(?:0\d{1,2}[- .]?\d{3,4}[- .]?\d{4}|01[016789][- .]?\d{3,4}[- .]?\d{4})(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RESIDENT_ID_RE = re.compile(r"(?<!\d)\d{6}\s*[- ]\s*[1-4]\d{6}(?!\d)")

_GSEEK_STATUS = {
    "모집중": "OPEN", "마감임박": "OPEN", "대기접수": "OPEN", "추가접수": "OPEN",
    "모집예정": "SCHEDULED", "마감": "CLOSED",
}
_LIBRARY_STATUS = {
    "접수중": "OPEN", "접수대기": "SCHEDULED", "접수마감": "CLOSED", "교육중": "CLOSED",
}
_HDREAM_STATUS = {
    "open": "OPEN", "wait": "SCHEDULED", "ready": "SCHEDULED", "end": "CLOSED",
}


class HanamContractError(ValueError):
    """Raised when an audited public source contract changes."""


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", html.unescape(str(value or "")).replace("\xa0", " ")).strip()


def _norm(value: Any) -> str:
    return "".join(c.lower() for c in _clean(value) if c.isalnum())


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


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


def _positive(value: Any, name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise HanamContractError(f"{name} must be a positive integer") from exc
    if result < 1:
        raise HanamContractError(f"{name} must be a positive integer")
    return result


def _exact_target(url: str, canonical: str) -> bool:
    parsed, wanted = urlparse(url), urlparse(canonical)
    return bool(
        parsed.scheme == "https"
        and parsed.hostname == wanted.hostname
        and parsed.port is None
        and parsed.path == wanted.path
        and parse_qs(parsed.query, keep_blank_values=True)
        == parse_qs(wanted.query, keep_blank_values=True)
        and not parsed.params and not parsed.fragment and not parsed.username and not parsed.password
    )


_TARGETS = {
    HANAM_GSEEK_PROVIDER: HANAM_GSEEK_URL,
    HANAM_RESIDENT_PROVIDER: HANAM_RESIDENT_URL,
    HANAM_YOUTH_PROVIDER: HANAM_YOUTH_URL,
    HANAM_HDREAM_PROVIDER: HANAM_HDREAM_URL,
    HANAM_LIBRARY_PROVIDER: HANAM_LIBRARY_URL,
}


def is_hanam_education_target(target: Any) -> bool:
    canonical = _TARGETS.get(_provider(target))
    return bool(canonical and _exact_target(_target_url(target), canonical))


is_target = is_hanam_education_target


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    })
    return session


def _close(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _validate_response(response: Any, expected_url: str, *, parameterized: bool = False) -> None:
    try:
        status = int(getattr(response, "status_code", 200))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise HanamContractError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise HanamContractError("redirects are not accepted")
    final = _clean(getattr(response, "url", ""))
    if not final:
        return
    if parameterized:
        got, wanted = urlparse(final), urlparse(expected_url)
        if got.scheme != "https" or got.hostname != wanted.hostname or got.path != wanted.path:
            raise HanamContractError("response escaped the audited endpoint")
    elif final != expected_url:
        raise HanamContractError("response escaped the canonical URL")


class _Runner:
    def __init__(self, factory: SessionFactory, timeout: int, max_requests: int, sleeper: Sleeper) -> None:
        self.factory, self.timeout, self.max_requests, self.sleeper = factory, timeout, max_requests, sleeper
        self.session: Any = None
        self.session_requests = HANAM_SESSION_REQUEST_LIMIT
        self.physical_requests = 0
        self.retry_count = 0
        self.sessions_created = 0

    def close(self) -> None:
        _close(self.session)
        self.session = None

    def _new(self) -> None:
        self.close()
        self.session = self.factory()
        self.sessions_created += 1
        self.session_requests = 0
        headers = getattr(self.session, "headers", None)
        if hasattr(headers, "update"):
            headers.update({"Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"})

    def request(self, method: str, url: str, *, parameterized: bool = False, **kwargs: Any) -> Any:
        last: Optional[Exception] = None
        for attempt in range(2):
            if self.physical_requests >= self.max_requests:
                raise HanamContractError(f"max_requests cap {self.max_requests} exhausted")
            if self.session is None or self.session_requests >= HANAM_SESSION_REQUEST_LIMIT:
                self._new()
            self.physical_requests += 1
            self.session_requests += 1
            try:
                operation = getattr(self.session, method)
                response = operation(url, timeout=self.timeout, allow_redirects=False, **kwargs)
                _validate_response(response, url, parameterized=parameterized)
                return response
            except Exception as exc:
                last = exc
                if attempt == 0:
                    self.retry_count += 1
                    self._new()
                    self.sleeper(0.1)
        assert last is not None
        raise last

    def soup(self, method: str, url: str, *, parameterized: bool = False, **kwargs: Any) -> BeautifulSoup:
        response = self.request(method, url, parameterized=parameterized, **kwargs)
        content = getattr(response, "content", None)
        if content is None:
            content = getattr(response, "text", "")
        if not content:
            raise HanamContractError("empty HTML response")
        return BeautifulSoup(content, "lxml")

    def json(self, method: str, url: str, *, parameterized: bool = False, **kwargs: Any) -> Any:
        response = self.request(method, url, parameterized=parameterized, **kwargs)
        try:
            return response.json()
        except Exception:
            content = getattr(response, "content", b"")
            if isinstance(content, bytes):
                content = content.decode("utf-8")
            return json.loads(content)


def _date_tokens(value: Any) -> list[date]:
    result: list[date] = []
    for parts in _DATE_RE.findall(_clean(value)):
        try:
            result.append(date(*(int(v) for v in parts)))
        except ValueError:
            pass
    return result


def _range(value: Any) -> tuple[str, str, str]:
    values = _date_tokens(value)
    if len(values) < 2 or values[1] < values[0]:
        return "", "", ""
    start, end = values[0], values[1]
    return start.isoformat(), end.isoformat(), f"{start.isoformat()} ~ {end.isoformat()}"


def _date8(value: Any) -> str:
    raw = _clean(value).replace("-", "").replace(".", "")
    if not _DATE8_RE.fullmatch(raw):
        return ""
    try:
        return date(int(raw[:4]), int(raw[4:6]), int(raw[6:])).isoformat()
    except ValueError:
        return ""


def _integer(value: Any) -> Optional[int]:
    raw = _clean(value).replace(",", "")
    return int(raw) if raw.isdigit() else None


def _branch_code(owner: str, branch: Any) -> str:
    digest = hashlib.sha1(_clean(branch).encode("utf-8")).hexdigest()[:12].upper()
    return f"HANAM_{owner}_{digest}"


def _safe_description(value: Any, fallback: Any) -> tuple[str, bool]:
    source = BeautifulSoup(str(value or ""), "lxml").get_text(" ", strip=True)
    source = _clean(source)
    unsafe = bool(_PHONE_RE.search(source) or _EMAIL_RE.search(source) or _RESIDENT_ID_RE.search(source))
    return (_clean(fallback) if unsafe or not source else source), unsafe


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in (None, "", [], {})}


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _signature(rows: Iterable[Any]) -> str:
    return hashlib.sha256(json.dumps(list(rows), ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def _base_meta(owner: str, error: str = "") -> dict[str, Any]:
    return {
        "owner": owner, "pages": 0, "list_requests": 0, "detail_attempts": 0,
        "detail_pages": 0, "detail_errors": 0, "source_total": 0, "source_rows": 0,
        "discovered_links": 0, "current_count": 0, "returned_count": 0,
        "pagination_detected": False, "pagination_complete": False,
        "details_complete": False, "snapshot_complete": False, "source_cap_reached": False,
        "full_snapshot_validated": False, "no_current_data": False,
        "no_current_reason": "", "configured_collection_error": error,
        "application_endpoints_called": 0,
    }


def _common_row(provider: str, identity: str, title: str, branch: str, raw_url: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "provider_course_id": f"{provider}:course:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "branch": branch,
        "branch_code": _branch_code(provider.split("_")[1], branch),
        "provider_organizer": branch,
        "raw_url": raw_url,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "program_type": "강좌",
        "region": HANAM_MUNICIPALITY_NAME,
        "municipality_code": HANAM_MUNICIPALITY_CODE,
        "municipality_full_name": HANAM_MUNICIPALITY_NAME,
    }


def _gseek_detail_url(subject: Any, cycle: Any) -> str:
    subject, cycle = _clean(subject), _clean(cycle)
    if not _ID_RE.fullmatch(subject) or not _ID_RE.fullmatch(cycle):
        return ""
    return HANAM_GSEEK_URL.removesuffix("/list") + "/view?" + urlencode(
        {"s_sbjct_sn": subject, "s_sbjct_cycl_sn": cycle}
    )


def _gseek_row(item: Mapping[str, Any]) -> dict[str, Any]:
    subject, cycle = _clean(item.get("d_sbjct_sn")), _clean(item.get("d_sbjct_cycl_sn"))
    identity = f"{subject}:{cycle}"
    title = _clean(item.get("d_sbjct_nm"))
    branch = _clean(item.get("d_edu_gvmnfc"))
    status_source = _clean(item.get("d_recrut_stts_nm"))
    start, end, period = _range(f"{item.get('d_edu_bgng_dt')} ~ {item.get('d_edu_end_dt')}")
    if not _ID_RE.fullmatch(subject) or not _ID_RE.fullmatch(cycle) or not title or not branch:
        raise HanamContractError("malformed GSEEK identity/title/branch")
    if _clean(item.get("d_co_sprvsn_id")) != HANAM_GSEEK_CO_SPONSOR_ID:
        raise HanamContractError(f"{identity}: foreign GSEEK co-sponsor")
    if status_source not in _GSEEK_STATUS or not start or not end:
        raise HanamContractError(f"{identity}: unknown status or invalid dates")
    description, redacted = _safe_description(item.get("d_sbjct_intrd_cn"), title)
    row = _common_row(HANAM_GSEEK_PROVIDER, identity, title, branch, _gseek_detail_url(subject, cycle))
    row.update({
        "category": " > ".join(filter(None, (_clean(item.get("d_clsf_depth1_nm")), _clean(item.get("d_clsf_depth2_nm")), _clean(item.get("d_clsf_depth3_nm"))))) or "평생학습",
        "status": _GSEEK_STATUS[status_source], "period": period, "start_date": start,
        "end_date": end,
        "schedule_raw": " ".join(filter(None, (_clean(item.get("d_edu_wday_cd_nm")), f"{_clean(item.get('d_edu_start_time'))} ~ {_clean(item.get('d_edu_end_time'))}"))),
        "target": _clean(item.get("d_sbjct_trgt_nm_1")),
        "fee": "무료" if _integer(item.get("d_sbjct_amt")) == 0 else _clean(item.get("d_sbjct_amt")),
        "capacity_total": _integer(item.get("d_edu_nope")),
        "capacity_current": _integer(item.get("d_aply_cnt")),
        "description": description,
        "application_method_raw": _clean(item.get("d_stdnt_chice_mthd_cd_nm")),
        "reservation_available": status_source in {"모집중", "마감임박", "대기접수", "추가접수"},
        "collection_type": "json_api+detail_html",
        "raw_fields": {"parser": HANAM_PARSER, "subject_id": subject, "cycle_id": cycle,
                       "source_status": status_source, "source_region": _clean(item.get("d_rgn")),
                       "co_sponsor_id": HANAM_GSEEK_CO_SPONSOR_ID,
                       "source_description_redacted": redacted},
    })
    return _clean_row(row)


def _gseek_detail_contract(soup: BeautifulSoup, row: Mapping[str, Any]) -> None:
    fields = row.get("raw_fields") or {}
    for name, expected in (("s_sbjct_sn", fields.get("subject_id")), ("s_sbjct_cycl_sn", fields.get("cycle_id"))):
        node = soup.select_one(f"input[name='{name}']")
        if node is None or _clean(node.get("value")) != _clean(expected):
            raise HanamContractError(f"GSEEK detail {name} mismatch")
    if _norm(row.get("title")) not in _norm(soup.get_text(" ", strip=True)):
        raise HanamContractError("GSEEK detail title mismatch")


def _collect_gseek(runner: _Runner, cutoff: date, max_pages: int, detail_limit: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    meta = _base_meta("gseek")
    landing = runner.soup("get", HANAM_GSEEK_URL)
    total_node = landing.select_one("#totSubjCnt")
    region = landing.select_one("input#s_resion_cd1[name='s_resion_cd1']")
    sponsor = landing.select_one("input[name='ARK_CO_SPRVSN_ID']")
    total = _integer(total_node.get_text(" ", strip=True) if total_node else None)
    if not total or region is None or _clean(region.get("value")) != HANAM_GSEEK_REGION_CODE:
        raise HanamContractError("GSEEK landing total/region contract changed")
    if sponsor is None or _clean(sponsor.get("value")) != HANAM_GSEEK_CO_SPONSOR_ID:
        raise HanamContractError("GSEEK landing sponsor contract changed")
    pages = math.ceil(total / HANAM_GSEEK_PAGE_SIZE)
    if pages + 1 > max_pages:
        meta["source_cap_reached"] = True
        raise HanamContractError("max_pages cannot cover GSEEK census and sentinel")
    payloads: list[list[Any]] = []
    for page in range(pages):
        start = page * HANAM_GSEEK_PAGE_SIZE + 1
        payload = runner.json("post", HANAM_GSEEK_API_URL, data={
            "s_sort_by": "1", "s_row_start": str(start),
            "s_row_end": str(start + HANAM_GSEEK_PAGE_SIZE), "resion": HANAM_GSEEK_REGION_CODE,
        }, headers={"Referer": HANAM_GSEEK_URL, "X-Requested-With": "XMLHttpRequest"})
        if not isinstance(payload, list) or len(payload) != min(HANAM_GSEEK_PAGE_SIZE, total - page * HANAM_GSEEK_PAGE_SIZE):
            raise HanamContractError(f"GSEEK range {page + 1} row count changed")
        payloads.append(payload)
    sentinel_start = total + 1
    sentinel = runner.json("post", HANAM_GSEEK_API_URL, data={
        "s_sort_by": "1", "s_row_start": str(sentinel_start),
        "s_row_end": str(sentinel_start + HANAM_GSEEK_PAGE_SIZE), "resion": HANAM_GSEEK_REGION_CODE,
    }, headers={"Referer": HANAM_GSEEK_URL, "X-Requested-With": "XMLHttpRequest"})
    if sentinel != []:
        raise HanamContractError("GSEEK exact post-total sentinel is not empty")
    for page in sorted({0, pages - 1}):
        start = page * HANAM_GSEEK_PAGE_SIZE + 1
        repeated = runner.json("post", HANAM_GSEEK_API_URL, data={
            "s_sort_by": "1", "s_row_start": str(start),
            "s_row_end": str(start + HANAM_GSEEK_PAGE_SIZE), "resion": HANAM_GSEEK_REGION_CODE,
        }, headers={"Referer": HANAM_GSEEK_URL, "X-Requested-With": "XMLHttpRequest"})
        if _signature(repeated) != _signature(payloads[page]):
            raise HanamContractError("GSEEK boundary changed during census")
    source = [_gseek_row(item) for payload in payloads for item in payload if isinstance(item, Mapping)]
    if len(source) != total or len({r["provider_course_id"] for r in source}) != total:
        raise HanamContractError("GSEEK total or identity completeness failed")
    current = [row for row in source if date.fromisoformat(row["end_date"]) >= cutoff]
    if len(current) > detail_limit:
        meta["source_cap_reached"] = True
        raise HanamContractError("detail_limit cannot cover every current GSEEK row")
    for row in current:
        _gseek_detail_contract(runner.soup("get", row["raw_url"]), row)
    meta.update({
        "pages": pages + 1, "list_requests": pages + 3, "detail_attempts": len(current),
        "detail_pages": len(current), "source_total": total, "source_rows": len(source),
        "current_count": len(current), "sentinel_start": sentinel_start, "sentinel_count": 0,
        "stability_rechecks": len({0, pages - 1}),
        "source_status_counts": dict(Counter(r["raw_fields"]["source_status"] for r in source)),
        "branch_counts": dict(Counter(r["branch"] for r in current)),
        "parent_aggregate_exclusion_required": True,
        "parent_aggregate_exclusion_field": "d_co_sprvsn_id",
        "parent_aggregate_exclusion_value": HANAM_GSEEK_CO_SPONSOR_ID,
        "pagination_complete": True, "details_complete": True,
    })
    return current, meta


def _resident_url(page: int) -> str:
    return HANAM_RESIDENT_URL if page == 1 else HANAM_RESIDENT_URL + f"&pageIndex={page}"


def _resident_rows(soup: BeautifulSoup) -> list[dict[str, Any]]:
    table = None
    expected = ["동구분", "강좌명", "대상", "교육시간", "수강료", "강사명", "접수/정원", "문의전화", "접수방법", "신청하기"]
    for candidate in soup.select("table"):
        if [_clean(x.get_text(" ", strip=True)) for x in candidate.select("thead th")] == expected:
            table = candidate
            break
    if table is None:
        raise HanamContractError("resident-centre table header changed")
    result: list[dict[str, Any]] = []
    for tr in table.select("tbody tr"):
        cells = [_clean(td.get_text(" ", strip=True)) for td in tr.select("td")]
        if len(cells) != 10:
            continue
        branch, title, target, schedule, fee, _instructor, capacity, _phone, method, action = cells
        if not title:
            continue
        if branch not in HANAM_RESIDENT_BRANCHES:
            raise HanamContractError(f"unknown resident-centre branch {branch}")
        source_key = "|".join((branch, title, schedule))
        identity = hashlib.sha256(source_key.encode()).hexdigest()[:24]
        row = _common_row(HANAM_RESIDENT_PROVIDER, identity, title, branch, HANAM_RESIDENT_URL)
        row.update({
            "category": "주민자치 프로그램", "status": "CLOSED", "target": target,
            "schedule_raw": schedule, "fee": "무료" if fee in {"없음", "0", "무료"} else fee,
            "capacity": capacity, "application_method_raw": method, "reservation_available": False,
            "description": title, "collection_type": "html_table",
            "raw_fields": {"parser": HANAM_PARSER, "source_action": action or "완료",
                           "term_snapshot_without_dates": True,
                           "explicit_source_test_course": title in HANAM_RESIDENT_EXCLUDED_TITLES},
        })
        result.append(_clean_row(row))
    return result


def _collect_resident(runner: _Runner, max_pages: int, detail_limit: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    meta = _base_meta("resident")
    page_rows: list[list[dict[str, Any]]] = []
    sentinel = 0
    for page in range(1, max_pages + 1):
        parsed = _resident_rows(runner.soup("get", _resident_url(page)))
        if not parsed:
            sentinel = page
            break
        page_rows.append(parsed)
    if not sentinel:
        meta["source_cap_reached"] = True
        raise HanamContractError("max_pages reached before resident-centre empty sentinel")
    for index in sorted({0, len(page_rows) - 1}):
        repeated = _resident_rows(runner.soup("get", _resident_url(index + 1)))
        if _signature(repeated) != _signature(page_rows[index]):
            raise HanamContractError("resident-centre boundary changed during census")
    source = [row for page in page_rows for row in page]
    identities = [row["provider_course_id"] for row in source]
    if len(identities) != len(set(identities)):
        raise HanamContractError("resident-centre semantic identity collision")
    if set(r["branch"] for r in source) != set(HANAM_RESIDENT_BRANCHES):
        raise HanamContractError("resident-centre official branch registry incomplete")
    result = [r for r in source if not r["raw_fields"]["explicit_source_test_course"]]
    if len(result) > detail_limit:
        meta["source_cap_reached"] = True
        raise HanamContractError("detail_limit lower than resident-centre publishable census")
    meta.update({
        "pages": len(page_rows) + 1, "list_requests": len(page_rows) + 3,
        "source_total": len(source), "source_rows": len(source), "current_count": len(result),
        "sentinel_page": sentinel, "sentinel_count": 0, "stability_rechecks": len({0, len(page_rows) - 1}),
        "excluded_test_count": len(source) - len(result), "branch_count": len(HANAM_RESIDENT_BRANCHES),
        "branch_counts": dict(Counter(r["branch"] for r in result)),
        "pagination_complete": True, "details_complete": True,
        "detail_policy": "source_has_no_detail_or_dates; publish_current_term_table_rows",
    })
    return result, meta


def _youth_form(onclick: Any) -> dict[str, str]:
    values = [unquote_plus(v) for v in re.findall(r"'([^']*)'", _clean(onclick))]
    return {key: values[i] for i, key in enumerate(HANAM_YOUTH_FORM_KEYS) if i < len(values)}


def _youth_hidden(soup: BeautifulSoup) -> list[dict[str, str]]:
    values = [_clean(node.get("value")) for node in soup.select("input[type='hidden']")
              if not _clean(node.get("name")) and _clean(node.get("value"))]
    result: list[dict[str, str]] = []
    for index in range(0, len(values), 9):
        group = values[index:index + 9]
        if len(group) >= 8 and _DATE8_RE.fullmatch(group[2]):
            result.append({"capacity_total": group[0], "capacity_current": group[1],
                           "apply_start": _date8(group[2]), "apply_end": _date8(group[3]),
                           "class_start": _date8(group[6])})
    return result


def _youth_page(soup: BeautifulSoup) -> list[dict[str, Any]]:
    hidden = _youth_hidden(soup)
    result: list[dict[str, Any]] = []
    for index, tr in enumerate(soup.select("tr.jone, tr.tt, tr.cc:not(.xx)")):
        cells = [_clean(td.get_text(" ", strip=True)) for td in tr.find_all("td", recursive=False)]
        link = tr.find("a", onclick=True)
        form = _youth_form(link.get("onclick") if link else "")
        if len(cells) < 10 or not _clean(form.get("sales_code")).isdigit():
            continue
        info = hidden[index] if index < len(hidden) else {}
        title, identity = _clean(form.get("ntitle")), _clean(form.get("sales_code"))
        if not title or not info.get("class_start"):
            raise HanamContractError(f"youth course {identity}: missing title/date metadata")
        action_status = cells[9]
        status = "CLOSED" if "마감" in action_status else "OPEN" if "대기" in action_status else "SCHEDULED"
        row = _common_row(HANAM_YOUTH_PROVIDER, identity, title, HANAM_YOUTH_BRANCH, HANAM_YOUTH_DETAIL_URL)
        row.update({
            "category": _clean(form.get("event_name")) or cells[1], "status": status,
            "schedule_raw": cells[4], "target": cells[3], "fee": cells[6], "capacity": cells[7],
            "capacity_total": _integer(info.get("capacity_total")),
            "capacity_current": _integer(info.get("capacity_current")),
            "apply_period": f"{info.get('apply_start')} ~ {info.get('apply_end')}",
            "application_method_raw": "온라인 수강신청", "reservation_available": False,
            "description": title, "collection_type": "ajax_html+safe_detail_post",
            "raw_fields": {"parser": HANAM_PARSER, "form_params": form, "source_status": action_status,
                           "source_action": cells[8], "class_start": info.get("class_start")},
        })
        result.append(_clean_row(row))
    return result


def _youth_detail(runner: _Runner, row: dict[str, Any]) -> None:
    form = dict(row["raw_fields"]["form_params"])
    payload = {key: form.get(key, "") for key in HANAM_YOUTH_FORM_KEYS}
    payload.update({"xtype": "J", "n_type": "program"})
    soup = runner.soup("post", HANAM_YOUTH_DETAIL_URL, data=payload, headers={"Referer": HANAM_YOUTH_LIST_URL})
    identity = row["provider_course_id"].rsplit(":", 1)[-1]
    sales = soup.select_one("input[name='sales_code']")
    start = soup.select_one("input[name='Special_Start_Date']")
    end = soup.select_one("input[name='Special_End_Date']")
    if sales is None or _clean(sales.get("value")) != identity or start is None or end is None:
        raise HanamContractError(f"youth course {identity}: safe detail contract changed")
    start_date, end_date = _clean(start.get("value")), _clean(end.get("value"))
    if not _date_tokens(start_date) or not _date_tokens(end_date) or end_date < start_date:
        raise HanamContractError(f"youth course {identity}: invalid detail dates")
    row.update({"start_date": start_date, "end_date": end_date,
                "period": f"{start_date} ~ {end_date}"})


def _collect_youth(runner: _Runner, cutoff: date, max_pages: int, detail_limit: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    meta = _base_meta("youth")
    landing = runner.soup("get", HANAM_YOUTH_URL)
    if "하남시청소년수련관" not in _clean(landing.get_text(" ", strip=True)):
        raise HanamContractError("youth landing owner contract changed")
    pages: list[list[dict[str, Any]]] = []
    sentinel = 0
    for page in range(1, max_pages + 1):
        soup = runner.soup("post", HANAM_YOUTH_LIST_URL, data={
            "item1": "02", "item2": "", "target": "", "area": "02", "page": str(page), "tot": "T",
        }, headers={"Referer": HANAM_YOUTH_URL, "Origin": "https://online.hnyouth.kr"})
        parsed = _youth_page(soup)
        if not parsed:
            sentinel = page
            break
        pages.append(parsed)
    if not sentinel:
        meta["source_cap_reached"] = True
        raise HanamContractError("max_pages reached before youth empty sentinel")
    repeated = runner.soup("post", HANAM_YOUTH_LIST_URL, data={
        "item1": "02", "item2": "", "target": "", "area": "02", "page": "1", "tot": "T",
    }, headers={"Referer": HANAM_YOUTH_URL, "Origin": "https://online.hnyouth.kr"})
    if _signature(_youth_page(repeated)) != _signature(pages[0]):
        raise HanamContractError("youth first page changed during census")
    source = [row for page in pages for row in page]
    if len({r["provider_course_id"] for r in source}) != len(source):
        raise HanamContractError("duplicate youth course identity")
    if len(source) > detail_limit:
        meta["source_cap_reached"] = True
        raise HanamContractError("detail_limit cannot cover youth source rows")
    for row in source:
        _youth_detail(runner, row)
    current = [row for row in source if date.fromisoformat(row["end_date"]) >= cutoff]
    meta.update({
        "pages": len(pages) + 1, "list_requests": len(pages) + 2,
        "detail_attempts": len(source), "detail_pages": len(source),
        "source_total": len(source), "source_rows": len(source), "current_count": len(current),
        "sentinel_page": sentinel, "sentinel_count": 0, "stability_rechecks": 1,
        "source_status_counts": dict(Counter(r["raw_fields"]["source_status"] for r in source)),
        "branch_counts": {HANAM_YOUTH_BRANCH: len(current)},
        "pagination_complete": True, "details_complete": True,
    })
    return current, meta


def _hdream_params(start: int) -> dict[str, Any]:
    return {"searchProgram": "", "searchType": "information", "payWay": "", "applyYn": "",
            "experienceType": "", "programTarget": "", "startRow": start,
            "pagingGap": HANAM_HDREAM_PAGE_SIZE, "sortOrder": "latest"}


def _hdream_data(payload: Any) -> list[Mapping[str, Any]]:
    if not isinstance(payload, Mapping) or _clean(payload.get("code")) != "response.ok":
        raise HanamContractError("HDream API envelope changed")
    rows = payload.get("data")
    if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
        raise HanamContractError("HDream API data is not a list")
    return list(rows)


def _hdream_row(item: Mapping[str, Any]) -> dict[str, Any]:
    identity, title = _clean(item.get("PROGRAM_ID")), _clean(item.get("PROGRAM_TITLE"))
    status_source = _clean(item.get("PROGRAM_APPLY_STATUS"))
    start, end, period = _range(item.get("PRGROM_PERIOD"))
    if not _ID_RE.fullmatch(identity) or not title or status_source not in _HDREAM_STATUS or not start:
        raise HanamContractError("malformed HDream course row")
    branch = _clean(item.get("SHOW_PLACE_NAME")) or _clean(item.get("PLACE_NAME")) or "하남교육재단"
    public_url = f"{HANAM_HDREAM_URL}/{identity}"
    description, redacted = _safe_description(item.get("INTRODUCTION_MARKDOWN"), title)
    apply_start, apply_end, apply_period = _range(item.get("APPLY_PERIOD"))
    row = _common_row(HANAM_HDREAM_PROVIDER, identity, title, branch, public_url)
    row.update({
        "category": _clean(item.get("EXPERIENCE_TYPE")) or "진로·체험교육",
        "program_type": "진로체험",
        "domain_category": "체험·견학",
        "service_group": "체험",
        "status": _HDREAM_STATUS[status_source], "period": period, "start_date": start,
        "end_date": end, "apply_period": apply_period, "apply_start_date": apply_start,
        "apply_end_date": apply_end, "target": _clean(item.get("EXPERIENCE_TARGET")),
        "fee": _clean(item.get("COST_INFO")) or ("무료" if _clean(item.get("COST_YN")) == "N" else _clean(item.get("COST"))),
        "capacity_total": _integer(item.get("MAX_APPLY")),
        "capacity_current": _integer(item.get("APPLY_COUNT")),
        "description": description, "reservation_available": status_source == "open",
        "collection_type": "safe_json_api+safe_detail_json",
        "raw_fields": {"parser": HANAM_PARSER, "program_id": identity,
                       "source_status": status_source, "source_apply_status": _clean(item.get("APPLY_STATUS")),
                       "source_description_redacted": redacted},
    })
    return _clean_row(row)


def _hdream_detail(runner: _Runner, row: dict[str, Any]) -> None:
    identity = row["raw_fields"]["program_id"]
    payload = runner.json("get", HANAM_HDREAM_DETAIL_API_URL, parameterized=True,
                          params={"programId": identity, "user": "Y"},
                          headers={"Referer": row["raw_url"]})
    if not isinstance(payload, Mapping) or _clean(payload.get("code")) != "response.ok" or not isinstance(payload.get("data"), Mapping):
        raise HanamContractError(f"HDream {identity}: detail envelope changed")
    detail = payload["data"]
    if _clean(detail.get("PROGRAM_ID")) != identity or _norm(detail.get("PROGRAM_TITLE")) != _norm(row["title"]):
        raise HanamContractError(f"HDream {identity}: detail identity/title mismatch")
    if _date8(detail.get("EXPERIENCE_START_DATE")) != row["start_date"] or _date8(detail.get("EXPERIENCE_END_DATE")) != row["end_date"]:
        raise HanamContractError(f"HDream {identity}: detail date mismatch")


def _collect_hdream(runner: _Runner, cutoff: date, max_pages: int, detail_limit: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    meta = _base_meta("hdream")
    landing = runner.soup("get", HANAM_HDREAM_URL)
    if "하남" not in _clean(landing.get_text(" ", strip=True)):
        raise HanamContractError("HDream landing owner contract changed")
    first = _hdream_data(runner.json("get", HANAM_HDREAM_API_URL, parameterized=True,
                                    params=_hdream_params(0), headers={"Referer": HANAM_HDREAM_URL}))
    if not first:
        raise HanamContractError("HDream first page unexpectedly empty")
    totals = {_integer(row.get("TOTAL_COUNT")) for row in first}
    if len(totals) != 1 or None in totals or 0 in totals:
        raise HanamContractError("HDream declared total changed")
    total = totals.pop()
    assert total is not None
    pages = math.ceil(total / HANAM_HDREAM_PAGE_SIZE)
    if pages + 1 > max_pages:
        meta["source_cap_reached"] = True
        raise HanamContractError("max_pages cannot cover HDream census and sentinel")
    payloads = [first]
    for start in range(HANAM_HDREAM_PAGE_SIZE, total, HANAM_HDREAM_PAGE_SIZE):
        payloads.append(_hdream_data(runner.json("get", HANAM_HDREAM_API_URL, parameterized=True,
                                                params=_hdream_params(start), headers={"Referer": HANAM_HDREAM_URL})))
    if any(len(rows) != min(HANAM_HDREAM_PAGE_SIZE, total - i * HANAM_HDREAM_PAGE_SIZE)
           for i, rows in enumerate(payloads)):
        raise HanamContractError("HDream page size contract changed")
    sentinel = _hdream_data(runner.json("get", HANAM_HDREAM_API_URL, parameterized=True,
                                       params=_hdream_params(total), headers={"Referer": HANAM_HDREAM_URL}))
    if sentinel:
        raise HanamContractError("HDream exact post-total sentinel is not empty")
    for index in sorted({0, len(payloads) - 1}):
        repeated = _hdream_data(runner.json("get", HANAM_HDREAM_API_URL, parameterized=True,
                                           params=_hdream_params(index * HANAM_HDREAM_PAGE_SIZE),
                                           headers={"Referer": HANAM_HDREAM_URL}))
        if _signature(repeated) != _signature(payloads[index]):
            raise HanamContractError("HDream boundary changed during census")
    source = [_hdream_row(item) for payload in payloads for item in payload]
    if len(source) != total or len({r["provider_course_id"] for r in source}) != total:
        raise HanamContractError("HDream total or identity completeness failed")
    current = [row for row in source if date.fromisoformat(row["end_date"]) >= cutoff]
    if len(current) > detail_limit:
        meta["source_cap_reached"] = True
        raise HanamContractError("detail_limit cannot cover every current HDream row")
    for row in current:
        _hdream_detail(runner, row)
    meta.update({
        "pages": pages + 1, "list_requests": pages + 3, "detail_attempts": len(current),
        "detail_pages": len(current), "source_total": total, "source_rows": len(source),
        "current_count": len(current), "sentinel_start": total, "sentinel_count": 0,
        "stability_rechecks": len({0, len(payloads) - 1}),
        "source_status_counts": dict(Counter(r["raw_fields"]["source_status"] for r in source)),
        "branch_counts": dict(Counter(r["branch"] for r in current)),
        "mutation_view_endpoint_called": 0, "pagination_complete": True, "details_complete": True,
    })
    return current, meta


def _library_list_url(site: str, key: str, page: int) -> str:
    base = f"https://www.hanamlib.go.kr/{site}/selectWebEdcLctreList.do?key={key}"
    return base if page == 1 else base + f"&pageUnit=10&searchCnd=all&pageIndex={page}"


def _library_page(soup: BeautifulSoup, branch: str, site: str, key: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for tr in soup.select("table tbody tr"):
        cells = [_clean(td.get_text(" ", strip=True)) for td in tr.select("td")]
        link = tr.select_one("a[href*='selectWebEdcLctreView.do']")
        if len(cells) != 7 or link is None:
            continue
        candidate = urljoin(_library_list_url(site, key, 1), _clean(link.get("href")))
        parsed = urlparse(candidate)
        query = parse_qs(parsed.query)
        identity = _clean((query.get("edcLctreNo") or [""])[0])
        if (parsed.scheme, parsed.hostname, parsed.path) != ("https", "www.hanamlib.go.kr", f"/{site}/selectWebEdcLctreView.do"):
            raise HanamContractError("library detail escaped official route")
        if query.get("key") != [key] or not _ID_RE.fullmatch(identity):
            raise HanamContractError("library detail identity/key changed")
        start, end, period = _range(cells[3])
        apply_start, apply_end, apply_period = _range(cells[4])
        if not start or cells[6] not in _LIBRARY_STATUS:
            raise HanamContractError(f"library {identity}: invalid dates/status")
        row = _common_row(HANAM_LIBRARY_PROVIDER, identity, cells[2], branch, candidate)
        row.update({
            "category": cells[1] or "독서문화프로그램", "status": _LIBRARY_STATUS[cells[6]],
            "period": period, "start_date": start, "end_date": end,
            "apply_period": apply_period, "apply_start_date": apply_start, "apply_end_date": apply_end,
            "capacity": cells[5], "description": cells[2],
            "reservation_available": cells[6] == "접수중",
            "application_method_raw": "온라인 신청", "collection_type": "html_table+detail_html",
            "raw_fields": {"parser": HANAM_PARSER, "lecture_id": identity, "site": site,
                           "key": key, "source_status": cells[6]},
        })
        result.append(_clean_row(row))
    return result


def _detail_pairs(soup: BeautifulSoup) -> dict[str, str]:
    result: dict[str, str] = {}
    for tr in soup.select("table tr"):
        cells = tr.select("th,td")
        for index in range(0, len(cells) - 1, 2):
            if cells[index].name == "th":
                key, value = _clean(cells[index].get_text(" ", strip=True)), _clean(cells[index + 1].get_text(" ", strip=True))
                if key and key not in result:
                    result[key] = value
    return result


def _library_detail(runner: _Runner, row: dict[str, Any]) -> int:
    soup = runner.soup("get", row["raw_url"])
    pairs = _detail_pairs(soup)
    identity = row["raw_fields"]["lecture_id"]
    if _norm(pairs.get("강좌명")) != _norm(row["title"]):
        raise HanamContractError(f"library {identity}: detail/list title mismatch")
    start, end, _ = _range(pairs.get("일정"))
    if start != row["start_date"] or end != row["end_date"]:
        raise HanamContractError(f"library {identity}: detail/list date mismatch")
    application = ""
    expected_path = f"/{row['raw_fields']['site']}/addEdcLctreReqstView.do"
    for link in soup.select("a[href*='addEdcLctreReqstView.do']"):
        candidate = urljoin(row["raw_url"], _clean(link.get("href")))
        parsed, query = urlparse(candidate), parse_qs(urlparse(candidate).query)
        if parsed.scheme == "https" and parsed.hostname == "www.hanamlib.go.kr" and parsed.path == expected_path \
                and query.get("key") == [row["raw_fields"]["key"]] and query.get("edcLctreNo") == [identity]:
            application = candidate
            break
    if application and row["status"] == "OPEN":
        row["application_url"] = application
    return int(bool(application))


def _collect_library(runner: _Runner, cutoff: date, max_pages: int, detail_limit: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    meta = _base_meta("library")
    all_rows: list[dict[str, Any]] = []
    sentinels: dict[str, int] = {}
    page_counts: dict[str, list[int]] = {}
    stability = 0
    list_requests = 0
    for branch, site, key in HANAM_LIBRARY_BRANCHES:
        pages: list[list[dict[str, Any]]] = []
        for page in range(1, max_pages + 1):
            parsed = _library_page(runner.soup("get", _library_list_url(site, key, page)), branch, site, key)
            list_requests += 1
            if not parsed:
                sentinels[branch] = page
                break
            pages.append(parsed)
        if branch not in sentinels:
            meta["source_cap_reached"] = True
            raise HanamContractError(f"max_pages reached before {branch} empty sentinel")
        page_counts[branch] = [len(page) for page in pages] + [0]
        for index in sorted({0, len(pages) - 1}):
            repeated = _library_page(runner.soup("get", _library_list_url(site, key, index + 1)), branch, site, key)
            list_requests += 1
            stability += 1
            if _signature(repeated) != _signature(pages[index]):
                raise HanamContractError(f"{branch} boundary changed during census")
        all_rows.extend(row for page in pages for row in page)
    if len({r["provider_course_id"] for r in all_rows}) != len(all_rows):
        raise HanamContractError("duplicate cross-branch library identity")
    current = [row for row in all_rows if date.fromisoformat(row["end_date"]) >= cutoff]
    if len(current) > detail_limit:
        meta["source_cap_reached"] = True
        raise HanamContractError("detail_limit cannot cover every current library row")
    application_controls = sum(_library_detail(runner, row) for row in current)
    meta.update({
        "pages": sum(len(v) for v in page_counts.values()), "list_requests": list_requests,
        "detail_attempts": len(current), "detail_pages": len(current),
        "source_total": len(all_rows), "source_rows": len(all_rows), "current_count": len(current),
        "sentinel_pages": sentinels, "sentinel_count": 0, "page_counts": page_counts,
        "stability_rechecks": stability, "branch_count": len(HANAM_LIBRARY_BRANCHES),
        "branch_counts": dict(Counter(r["branch"] for r in current)),
        "source_status_counts": dict(Counter(r["raw_fields"]["source_status"] for r in all_rows)),
        "application_control_count": application_controls,
        "pagination_complete": True, "details_complete": True,
    })
    return current, meta


def collect_hanam_education_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = HANAM_DEFAULT_MAX_PAGES,
    detail_limit: int = HANAM_DEFAULT_DETAIL_LIMIT,
    *,
    max_requests: int = HANAM_DEFAULT_MAX_REQUESTS,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    sleeper: Sleeper = time.sleep,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete, atomic Hanam owner snapshot."""

    owner = {
        HANAM_GSEEK_PROVIDER: "gseek", HANAM_RESIDENT_PROVIDER: "resident",
        HANAM_YOUTH_PROVIDER: "youth", HANAM_HDREAM_PROVIDER: "hdream",
        HANAM_LIBRARY_PROVIDER: "library",
    }.get(_provider(target), "unknown")
    meta = _base_meta(owner)
    if not is_hanam_education_target(target):
        meta["configured_collection_error"] = "target does not match an exact canonical Hanam owner route"
        return [], HANAM_PARSER, meta
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta["configured_collection_error"] = "managed session_factory injection is required"
            return [], HANAM_PARSER, meta
        session_factory = _default_session_factory
    try:
        timeout = _positive(timeout, "timeout")
        max_pages = _positive(max_pages, "max_pages")
        detail_limit = _positive(detail_limit, "detail_limit")
        max_requests = _positive(max_requests, "max_requests")
        cutoff = _today(today)
    except Exception as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], HANAM_PARSER, meta

    runner = _Runner(session_factory, timeout, max_requests, sleeper)
    try:
        try:
            if owner == "gseek":
                rows, meta = _collect_gseek(runner, cutoff, max_pages, detail_limit)
            elif owner == "resident":
                rows, meta = _collect_resident(runner, max_pages, detail_limit)
            elif owner == "youth":
                rows, meta = _collect_youth(runner, cutoff, max_pages, detail_limit)
            elif owner == "hdream":
                rows, meta = _collect_hdream(runner, cutoff, max_pages, detail_limit)
            else:
                rows, meta = _collect_library(runner, cutoff, max_pages, detail_limit)
            deduper = dedupe_rows or _dedupe_default
            result = list(deduper([_clean_row(row) for row in rows]))
            if len(result) != len(rows):
                raise HanamContractError(f"dedupe changed complete row count {len(rows)} to {len(result)}")
            meta.update({
                "returned_count": len(result), "snapshot_complete": True,
                "full_snapshot_validated": True,
                "discovered_links": int(meta.get("source_rows") or 0),
                "pagination_detected": int(meta.get("pages") or 0) > 1,
                "no_current_data": not result,
                "no_current_reason": "complete owner ledger has no current/future courses" if not result else "",
                "physical_requests": runner.physical_requests, "retry_count": runner.retry_count,
                "sessions_created": runner.sessions_created, "max_requests": max_requests,
                "configured_collection_error": "", "application_endpoints_called": 0,
            })
            return result, HANAM_PARSER, meta
        except Exception as exc:
            meta.update({
                "physical_requests": runner.physical_requests, "retry_count": runner.retry_count,
                "sessions_created": runner.sessions_created, "max_requests": max_requests,
                "configured_collection_error": _clean(exc), "snapshot_complete": False,
                "returned_count": 0, "application_endpoints_called": 0,
            })
            return [], HANAM_PARSER, meta
    finally:
        runner.close()


collect = collect_hanam_education_courses


__all__ = [name for name in globals() if name.startswith("HANAM_")] + [
    "HanamContractError", "collect", "collect_hanam_education_courses",
    "is_hanam_education_target", "is_target",
]
