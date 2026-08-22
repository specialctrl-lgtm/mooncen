"""Fail-closed collector for Jinan-gun's complete lifelong-learning ledger.

The registered provider used to point at a static Jeonbuk Citizens College
landing page and the generated crawler consequently retained one incidental
detail.  The actual owner is board ``BBS_0000018``.  Its catalogue is split
into four first-class category branches (A-D); category A alone is not a
complete snapshot.

Every advertised page in every category is read, followed by the immediate
out-of-range page.  Jinan does not return an empty sentinel: it clamps that
request to the exact last page.  The collector verifies that behaviour,
global ``dataSid`` uniqueness, branch directories, descending per-category
sequences, all current/future details, and post-detail boundary stability.

The public detail can expose an identity-bound ``peopleCountAjax`` control.
Its capacity probe and PII-bearing application form are deliberately never
requested.  Instructor names, contacts, attachments, descriptions, and raw
HTML are not retained.  Eleven audited records currently return an empty
detail body from the official server; only those exact identities may use a
safe list-field fallback, and the fallback is explicit in metadata.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


JINAN_PROVIDER = "MUNI_WWW_JINAN_GO_KR_3DF1AE69"
JINAN_LEGACY_CANDIDATE_ID = "MUNI_IR_6D5FA6516C37"
JINAN_CANONICAL_CANDIDATE_ID = "MUNI_IR_F003B1D5FD98"
JINAN_MUNICIPALITY_CODE = "5272000000"
JINAN_MUNICIPALITY_NAME = "전북특별자치도 진안군"

JINAN_HOST = "www.jinan.go.kr"
JINAN_LIST_PATH = "/edu/board/list.jinan"
JINAN_DETAIL_PATH = "/edu/board/view.jinan"
JINAN_APPLICATION_CAPACITY_PATH = "/index.jinan"
JINAN_APPLICATION_WRITE_PATH = "/board/write.jinan"
JINAN_BOARD_ID = "BBS_0000018"
JINAN_MENU_CODE = "DOM_000000502003000000"
JINAN_PAGE_SIZE = 10
JINAN_RECOMMENDED_MAX_PAGES = 100
JINAN_RECOMMENDED_DETAIL_LIMIT = 200
JINAN_MAX_HTML_BYTES = 2_000_000

JINAN_LEGACY_URL = (
    "https://www.jinan.go.kr/edu/index.jinan?"
    "menuCd=DOM_000000508000000000"
)
JINAN_CANONICAL_URL = (
    "https://www.jinan.go.kr/edu/board/list.jinan?"
    "boardId=BBS_0000018&menuCd=DOM_000000502003000000&paging=ok"
)
JINAN_LEGACY_NORMALIZED_SHA1 = (
    "3df1ae696787264d101214d38009e6d1d9d368ea"
)
JINAN_LEGACY_NORMALIZED_SHA256 = (
    "6d5fa6516c37828a17365e917d52a1a042085f4a6e49847ef29a8dcf5be9effd"
)
JINAN_CANONICAL_NORMALIZED_SHA1 = (
    "2e2fcb8858c64df122cd42b90b84efc91a8b045c"
)
JINAN_CANONICAL_NORMALIZED_SHA256 = (
    "f003b1d5fd98440909221545b531105b3c9d637f26e488741446df2f77a04b5f"
)

JINAN_PARSER = (
    "jinan_bbs_0000018_four_category_get_pages+advertised_totals+"
    "exact_last_page_clamp+global_datasid_uniqueness+official_branch_"
    "directories+all_current_future_detail_attempts+known_empty_detail_"
    "allowlist+application_script_binding+current_page_boundary_rechecks+"
    "no_capacity_probe_or_application_form_fetch+pii_allowlist"
)

JINAN_CATEGORIES: Mapping[str, str] = {
    "A": "평생학습 기관",
    "B": "주민자치센터",
    "C": "평생학습 유관기관",
    "D": "전북시민대학",
}

# Exact branch selectors emitted by the four unfiltered category forms.
JINAN_BRANCHES: Mapping[str, Mapping[str, str]] = {
    "A": {
        "A_01": "진안군 평생학습센터",
        "A_02": "진안군청 평생학습관",
        "A_03": "진안군 자원봉사센터",
        "A_04": "진안군 여성일자리지원센터",
        "A_05": "진안군 건강가정다문화지원센터",
        "A_06": "진안군 청소년수련관",
        "A_07": "진안군 장애인종합복지관",
        "A_08": "대한노인회 진안군지회",
        "A_09": "진안군 복합노인복지타운",
        "A_10": "진안 문화원",
        "A_11": "진안 문화의집",
        "A_12": "진안 역사박물관",
        "A_13": "진안 공공도서관",
        "A_14": "온생명평생교육원",
        "A_15": "진안군 마이종합학습장",
    },
    "B": {
        "B_01": "진안읍 주민자치센터",
        "B_02": "용담면 주민자치센터",
        "B_03": "안천면 주민자치센터",
        "B_04": "동향면 주민자치센터",
        "B_05": "상전면 주민자치센터",
        "B_06": "백운면 주민자치센터",
        "B_07": "성수면 주민자치센터",
        "B_08": "마령면 주민자치센터",
        "B_09": "부귀면 주민자치센터",
        "B_10": "정천면 주민자치센터",
        "B_11": "주천면 주민자치센터",
    },
    "C": {
        "C_01": "진안 문화의집",
        "C_02": "진안군 청소년수련관",
        "C_03": "대한노인회 진안군지회",
        "C_04": "진안군 일자리센터",
        "C_05": "진안군 여성일자리센터",
        "C_06": "보듬",
        "C_07": "진안군 장애인종합복지관",
        "C_08": "(사)진안군 자원봉사센터",
        "C_09": "진안군 작은도서관협회",
        "C_10": "진안군 귀농귀촌협의체",
        "C_11": "진안군 사회적경제지원센터",
    },
    "D": {},
}

# These public detail URLs return the exact official shell but no course
# table.  New identities are not allowed to fall back silently.
JINAN_KNOWN_EMPTY_DETAIL_IDS = frozenset(
    {
        "204752",
        "204751",
        "204750",
        "204749",
        "204748",
        "204747",
        "203363",
        "203351",
        "203344",
        "184740",
        "178309",
    }
)

JINAN_KNOWN_REVERSED_EDUCATION_IDS = frozenset({"178305", "97144"})
JINAN_KNOWN_REVERSED_APPLICATION_IDS = frozenset(
    {"164034", "165710", "192539", "97144", "97173"}
)
JINAN_KNOWN_RANGE_CAPACITY_IDS = frozenset({"188071"})
JINAN_KNOWN_NONSTANDARD_EDUCATION_IDS = frozenset(
    {
        "100311",
        "164041",
        "164042",
        "164044",
        "164045",
        "164047",
        "164048",
        "164049",
        "165696",
        "165697",
        "165698",
        "165699",
        "165702",
        "165709",
        "165710",
        "172451",
        "172452",
        "172455",
        "172456",
        "172457",
        "172458",
    }
)

JINAN_CANDIDATE_AUDIT: Mapping[str, Mapping[str, Any]] = {
    JINAN_LEGACY_CANDIDATE_ID: {
        "decision": "retain_provider_retarget_static_landing",
        "provider": JINAN_PROVIDER,
        "url": JINAN_LEGACY_URL,
        "owner": JINAN_PROVIDER,
        "reason": "official_static_landing_without_course_identity_rows",
    },
    JINAN_CANONICAL_CANDIDATE_ID: {
        "decision": "canonical_complete_four_category_owner",
        "provider": JINAN_PROVIDER,
        "derived_provider_not_used": "MUNI_WWW_JINAN_GO_KR_2E2FCB88",
        "url": JINAN_CANONICAL_URL,
        "owner": JINAN_PROVIDER,
        "reason": "same_owner_retarget_preserves_existing_provider_course_ids",
    },
}

JINAN_NON_EXECUTING_ALIASES: tuple[Mapping[str, Any], ...] = (
    {
        "url": JINAN_LEGACY_URL,
        "reason": "static_jeonbuk_citizens_college_landing_without_rows",
        "owner": JINAN_PROVIDER,
    },
    {
        "url": (
            "https://www.jinan.go.kr/edu/index.jinan?"
            "menuCd=DOM_000000508002000000"
        ),
        "reason": "redirect_alias_to_category_A_of_canonical_board",
        "owner": JINAN_PROVIDER,
    },
    {
        "url": (
            "https://www.jinan.go.kr/edu/board/list.jinan?"
            "boardId=BBS_0000018&menuCd=DOM_000000502004000000"
        ),
        "reason": "one_certificate_menu_is_41_datasid_subset_of_category_A",
        "owner": JINAN_PROVIDER,
    },
    {
        "url": (
            "https://www.jinan.go.kr/edu/board/list.jinan?"
            "boardId=BBS_0000018&menuCd=DOM_000000502005000000"
        ),
        "reason": "education_forum_menu_is_4_datasid_subset_of_category_A",
        "owner": JINAN_PROVIDER,
    },
    {
        "url_pattern": JINAN_CANONICAL_URL + "&categoryCode1=<A-D>&categoryCode2=<branch>",
        "reason": "category_and_center_filters_are_subsets_of_four_canonical_ledgers",
        "owner": JINAN_PROVIDER,
    },
    {
        "url": (
            "https://www.jinan.go.kr/edu/index.jinan?"
            "menuCd=DOM_000000508003000000"
        ),
        "reason": "static_application_information_without_course_identities",
        "owner": JINAN_PROVIDER,
    },
    {
        "url": (
            "https://www.jinan.go.kr/edu/index.jinan?"
            "menuCd=DOM_000000506004000000"
        ),
        "reason": "three_row_informational_talk_schedule_without_application_identity",
        "owner": "",
    },
)

JINAN_SEPARATE_OWNER_BOUNDARIES: tuple[Mapping[str, Any], ...] = (
    {
        "provider": "CULTURE_PUBLIC_LIBRARY_FCEB8068F5",
        "name": "전북특별자치도교육청진안도서관",
        "url": "https://lib.jbe.go.kr/jinanplib/index.do",
        "reason": "separate_education_office_library_identity_and_application_ledger",
    },
    {
        "provider": "MUNI_WWW_JINAN_GO_KR_F429346A",
        "name": "진안군 평생학습 공지 첨부파일",
        "url_pattern": "https://www.jinan.go.kr/edu/board/download.jinan?...",
        "reason": "attachment_guideline_only_not_a_course_owner",
    },
)

JINAN_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-23",
    "canonical_url": JINAN_CANONICAL_URL,
    "category_totals": {"A": 243, "B": 589, "C": 81, "D": 34},
    "category_pages": {"A": 25, "B": 59, "C": 9, "D": 4},
    "historical_rows": 947,
    "globally_unique_datasids": 947,
    "current_or_future_rows": 103,
    "detail_tables_verified": 92,
    "known_empty_detail_fallbacks": 11,
    "online_application_controls_verified": 2,
    "source_status_counts": {
        "강좌종료": 784,
        "강좌중": 86,
        "폐강": 62,
        "접수중": 14,
        "준비중": 1,
    },
    "current_source_status_counts": {
        "강좌중": 86,
        "접수중": 14,
        "폐강": 2,
        "준비중": 1,
    },
    "category_A_filtered_subsets": {
        "one_certificate": 41,
        "education_forum": 4,
    },
    "immediate_out_of_range_contract": "exact_last_page_clamp_per_category",
    "audited_historical_source_exceptions": {
        "nonstandard_education_period_ids": 21,
        "reversed_education_period_ids": 2,
        "reversed_application_period_ids": 5,
        "range_capacity_ids": 1,
    },
}

JINAN_RECOMMENDED_OVERRIDE: Mapping[str, Any] = {
    "code": JINAN_MUNICIPALITY_CODE,
    "full_name": JINAN_MUNICIPALITY_NAME,
    "provider": JINAN_PROVIDER,
    "provider_decision": "retain_existing_provider_and_retarget_url",
    "candidates": (
        {
            "status": "candidate",
            "score": 100,
            "candidate_id": JINAN_CANONICAL_CANDIDATE_ID,
            "title": "진안군 평생학습관 전체 교육·강좌",
            "url": JINAN_CANONICAL_URL,
            "evidence_urls": (JINAN_LEGACY_URL, JINAN_CANONICAL_URL),
        },
        {
            "status": "alias",
            "candidate_id": JINAN_LEGACY_CANDIDATE_ID,
            "url": JINAN_LEGACY_URL,
            "reason": "static_landing_retarget_to_complete_board",
        },
    ),
}


SessionFactory = Callable[[], Any]
Getter = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class JinanContractError(ValueError):
    """Raised when the official source no longer matches the audited contract."""


_SPACE = re.compile(r"\s+")
_IDENTITY = re.compile(r"^[1-9]\d*$")
_DATE_PART = re.compile(r"^(20\d{2})[-]?(\d{1,2})[-]?(\d{1,2})$")
_STANDARD_EDUCATION_PERIOD = re.compile(
    r"^20\d{2}-\d{2}-\d{2}\s*~\s*20\d{2}-\d{2}-\d{2}$"
)
_APPLY_PERIOD = re.compile(
    r"^(20\d{2})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})\s*~\s*"
    r"(20\d{2})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})$"
)
_CAPACITY = re.compile(r"^(\d+)\s*/\s*(\d+)(?:\s*~\s*(\d+))?\s*명?$")
_DETAIL_CAPACITY = re.compile(r"^(\d+)\s*명?$")
_PAGE_MARKER = re.compile(r"^(\d+)\s*/\s*(\d+)$")
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_CONTROL_DATA_ID = re.compile(
    r"data\s*:\s*\{\s*[\"']?up_dataid[\"']?\s*:\s*[\"']([1-9]\d*)[\"']\s*\}"
)
_CONTROL_LOCATION = re.compile(r"location\.href\s*=\s*[\"']([^\"']+)[\"']")

_LIST_HEADERS = (
    "순번",
    "강좌명",
    "학습센터 교육장소",
    "강의기간",
    "정원",
    "강사",
    "신청기간",
    "진행상태",
)
_SOURCE_STATUS: Mapping[str, str] = {
    "접수중": "OPEN",
    "준비중": "SCHEDULED",
    "강좌중": "CLOSED",
    "강좌종료": "CLOSED",
    "폐강": "CANCELLED",
}
_EXPECTED_INSTITUTION = dict(JINAN_CATEGORIES)
_DETAIL_REQUIRED = frozenset(
    {
        "강좌명",
        "기관구분",
        "학습센터",
        "교육대상",
        "교육장소",
        "강좌기간",
        "신청기간",
        "수강시간",
        "정원",
        "접수방법",
    }
)
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "source_category_code",
        "source_category_name",
        "source_page",
        "source_sequence",
        "source_status",
        "source_apply_period",
        "source_education_period",
        "source_schedule",
        "source_venue",
        "official_branch_code",
        "capacity_current",
        "capacity_total",
        "detail_verified",
        "detail_unavailable",
        "detail_unavailable_reason",
        "application_control_present",
        "application_control_contract",
        "application_control_verified",
        "application_capacity_probe_fetched",
        "application_form_fetched",
        "pii_form_fetched",
        "service_family",
    }
)
_FORBIDDEN_ROW_KEYS = frozenset(
    {
        "phone",
        "email",
        "contact",
        "contacts",
        "instructor",
        "instructor_name",
        "attachments",
        "attachment_urls",
        "detail_description",
        "course_content",
        "source_html",
        "raw_html",
        "notice",
        "applicant_name",
        "applicant_phone",
        "password",
    }
)


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _query(url: str) -> list[tuple[str, str]]:
    return parse_qsl(urlparse(url).query, keep_blank_values=True, strict_parsing=True)


def is_jinan_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != JINAN_PROVIDER:
        return False
    value = _clean(_target_value(target, "url"))
    return value in {JINAN_LEGACY_URL, JINAN_CANONICAL_URL}


is_target = is_jinan_education_target


def _list_url(category: str, page: int) -> str:
    return "https://www.jinan.go.kr/edu/board/list.jinan?" + urlencode(
        (
            ("boardId", JINAN_BOARD_ID),
            ("menuCd", JINAN_MENU_CODE),
            ("paging", "ok"),
            ("categoryCode1", category),
            ("startPage", str(page)),
        )
    )


def _detail_url(category: str, page: int, identity: str) -> str:
    return "https://www.jinan.go.kr/edu/board/view.jinan?" + urlencode(
        (
            ("boardId", JINAN_BOARD_ID),
            ("menuCd", JINAN_MENU_CODE),
            ("paging", "ok"),
            ("startPage", str(page)),
            ("categoryCode1", category),
            ("dataSid", identity),
        )
    )


def _allowed_get_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        port = parsed.port
        query = _query(url)
    except ValueError:
        return False
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == JINAN_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
    ):
        return False
    if parsed.path == JINAN_LIST_PATH:
        if len(query) != 5:
            return False
        expected = {
            "boardId": JINAN_BOARD_ID,
            "menuCd": JINAN_MENU_CODE,
            "paging": "ok",
        }
        values = dict(query)
        return bool(
            len(values) == 5
            and all(values.get(key) == value for key, value in expected.items())
            and values.get("categoryCode1") in JINAN_CATEGORIES
            and _clean(values.get("startPage")).isdigit()
            and int(values["startPage"]) >= 1
        )
    if parsed.path == JINAN_DETAIL_PATH:
        if len(query) != 6:
            return False
        values = dict(query)
        return bool(
            len(values) == 6
            and values.get("boardId") == JINAN_BOARD_ID
            and values.get("menuCd") == JINAN_MENU_CODE
            and values.get("paging") == "ok"
            and values.get("categoryCode1") in JINAN_CATEGORIES
            and _clean(values.get("startPage")).isdigit()
            and int(values["startPage"]) >= 1
            and _IDENTITY.fullmatch(_clean(values.get("dataSid")))
        )
    return False


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MooncenMunicipalCrawler/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        }
    )
    return session


def _validated_response(response: Any, requested_url: str) -> BeautifulSoup:
    response.raise_for_status()
    final_url = _clean(getattr(response, "url", requested_url))
    if final_url != requested_url or not _allowed_get_url(final_url):
        raise JinanContractError("response left the exact official HTTPS endpoint")
    content_type = _clean(response.headers.get("Content-Type")).lower()
    if "html" not in content_type:
        raise JinanContractError("response is not HTML")
    content = response.content
    if len(content) > JINAN_MAX_HTML_BYTES:
        raise JinanContractError("HTML response exceeded the bounded size limit")
    return BeautifulSoup(content, "html.parser")


def _default_getter(session: Any, url: str, timeout: int) -> BeautifulSoup:
    if not _allowed_get_url(url):
        raise JinanContractError("refused non-list/detail GET endpoint")
    return _validated_response(
        session.get(url, timeout=timeout, allow_redirects=True),
        url,
    )


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, bytes):
        if len(value) > JINAN_MAX_HTML_BYTES:
            raise JinanContractError("fixture HTML exceeded the bounded size limit")
        return BeautifulSoup(value, "html.parser")
    if isinstance(value, str):
        if len(value.encode("utf-8")) > JINAN_MAX_HTML_BYTES:
            raise JinanContractError("fixture HTML exceeded the bounded size limit")
        return BeautifulSoup(value, "html.parser")
    content = getattr(value, "content", None)
    if isinstance(content, (bytes, bytearray)):
        return _coerce_soup(bytes(content))
    raise TypeError("getter must return HTML, bytes, a response, or BeautifulSoup")


def _get_soup(session: Any, getter: Getter, url: str, timeout: int) -> BeautifulSoup:
    if not _allowed_get_url(url):
        raise JinanContractError("refused unsafe GET endpoint")
    return _coerce_soup(getter(session, url, timeout))


def _close_quietly(value: Any) -> None:
    try:
        close = getattr(value, "close", None)
        if callable(close):
            close()
    except Exception:
        pass


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        return (
            value.astimezone(ZoneInfo("Asia/Seoul")).date()
            if value.tzinfo
            else value.date()
        )
    if isinstance(value, date):
        return value
    return date.fromisoformat(_clean(value))


def _calendar_date(value: str, field: str) -> date:
    match = _DATE_PART.fullmatch(_clean(value))
    if match is None:
        raise JinanContractError(f"unsupported {field} date: {value!r}")
    try:
        return date(*(int(item) for item in match.groups()))
    except ValueError as exc:
        raise JinanContractError(f"invalid {field} calendar date") from exc


def _education_period(
    value: Any, field: str, *, allow_reversed_source: bool = False
) -> tuple[Optional[date], date, str]:
    text = _clean(value)
    parts = re.split(r"\s*~\s*", text)
    if len(parts) != 2:
        raise JinanContractError(f"unsupported {field} period: {text!r}")
    start = None if parts[0] == "미정" else _calendar_date(parts[0], field)
    end = _calendar_date(parts[1], field)
    if start is not None and end < start and not allow_reversed_source:
        raise JinanContractError(f"reversed {field} period")
    normalized_start = start.isoformat() if start is not None else "미정"
    return start, end, f"{normalized_start} ~ {end.isoformat()}"


def _application_period(
    value: Any, *, allow_reversed_source: bool = False
) -> tuple[datetime, datetime, str]:
    text = _clean(value)
    match = _APPLY_PERIOD.fullmatch(text)
    if match is None:
        raise JinanContractError(f"unsupported application period: {text!r}")
    values = [int(item) for item in match.groups()]
    try:
        start = datetime(*values[:5])
        end = datetime(*values[5:])
    except ValueError as exc:
        raise JinanContractError("invalid application calendar date/time") from exc
    if end < start and not allow_reversed_source:
        raise JinanContractError("reversed application period")
    normalized = (
        f"{start.strftime('%Y-%m-%d %H:%M')} ~ "
        f"{end.strftime('%Y-%m-%d %H:%M')}"
    )
    return start, end, normalized


def _hidden(form: Any, name: str, context: str) -> str:
    values = form.select(f"input[type='hidden'][name='{name}']")
    if len(values) != 1:
        raise JinanContractError(f"{context}: hidden {name} drift")
    return _clean(values[0].get("value"))


def _tab_directory(soup: BeautifulSoup, context: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for anchor in soup.select("a[href*='categoryCode1']"):
        href = re.sub(r"\s+", "", _clean(anchor.get("href")))
        parsed = urlparse(href)
        query = parse_qsl(parsed.query, keep_blank_values=True)
        values = dict(query)
        if (
            parsed.path == "/board/list.jinan"
            and values.get("boardId") == JINAN_BOARD_ID
            and values.get("menuCd") == JINAN_MENU_CODE
            and "categoryCode2" in values
            and values.get("categoryCode1") in JINAN_CATEGORIES
        ):
            code = str(values["categoryCode1"])
            name = _clean(anchor.get_text(" ", strip=True))
            if code in found and found[code] != name:
                raise JinanContractError(f"{context}: duplicate category tab")
            found[code] = name
    if found != dict(JINAN_CATEGORIES):
        raise JinanContractError(f"{context}: four-category directory drift")
    return found


def _branch_directory(form: Any, category: str, context: str) -> dict[str, str]:
    selects = form.select("select[name='categoryCode2']")
    if len(selects) != 1:
        raise JinanContractError(f"{context}: branch selector drift")
    options = [
        (_clean(option.get("value")), _clean(option.get_text(" ", strip=True)))
        for option in selects[0].select("option")
    ]
    expected = [("", "학습센터"), *JINAN_BRANCHES[category].items()]
    if options != expected:
        raise JinanContractError(f"{context}: official branch directory drift")
    return dict(JINAN_BRANCHES[category])


def _list_form(
    soup: BeautifulSoup, category: str, rendered_page: int, context: str
) -> dict[str, str]:
    forms = soup.select("form.rfc_bbs_searchForm[name='rfc_bbs_searchForm']")
    if len(forms) != 1:
        raise JinanContractError(f"{context}: exact search form missing")
    form = forms[0]
    if (
        _clean(form.get("method")).lower() != "get"
        or _clean(form.get("action")) != "/board/list.jinan"
        or _hidden(form, "boardId", context) != JINAN_BOARD_ID
        or _hidden(form, "menuCd", context) != JINAN_MENU_CODE
        or _hidden(form, "contentsSid", context) != "454"
        or _hidden(form, "categoryCode1", context) != category
        # A new search always resets pagination; the live form therefore
        # keeps this value at 1 even while pages 2..N are being rendered.
        or _hidden(form, "startPage", context) != "1"
    ):
        raise JinanContractError(f"{context}: search form state drift")
    if len(form.select("input[type='text'][name='keyword']")) != 1:
        raise JinanContractError(f"{context}: search keyword control drift")
    return _branch_directory(form, category, context)


def _split_center_venue(cell: Any, context: str) -> tuple[str, str]:
    parts: list[list[str]] = [[]]
    for child in cell.children:
        if getattr(child, "name", None) == "br":
            parts.append([])
            continue
        text = child.get_text(" ", strip=True) if hasattr(child, "get_text") else str(child)
        if _clean(text):
            parts[-1].append(_clean(text))
    cleaned = [_clean(" ".join(part)) for part in parts]
    if len(cleaned) != 2 or not cleaned[1]:
        raise JinanContractError(f"{context}: center/venue line shape drift")
    return cleaned[0], cleaned[1]


def _parse_detail_link(
    anchor: Any, category: str, rendered_page: int, context: str
) -> tuple[str, str]:
    href = _clean(anchor.get("href"))
    absolute = urljoin("https://www.jinan.go.kr", href)
    try:
        query = _query(absolute)
    except ValueError as exc:
        raise JinanContractError(f"{context}: malformed detail URL") from exc
    values = dict(query)
    identity = _clean(values.get("dataSid"))
    if not (
        urlparse(absolute).path == JINAN_DETAIL_PATH
        and len(query) == 6
        and len(values) == 6
        and values.get("boardId") == JINAN_BOARD_ID
        and values.get("menuCd") == JINAN_MENU_CODE
        and values.get("paging") == "ok"
        and values.get("startPage") == str(rendered_page)
        and values.get("categoryCode1") == category
        and _IDENTITY.fullmatch(identity)
    ):
        raise JinanContractError(f"{context}: detail identity URL drift")
    expected = _detail_url(category, rendered_page, identity)
    if absolute != expected:
        raise JinanContractError(f"{context}: detail query ordering/encoding drift")
    return identity, absolute


def _parse_list_page(
    soup: BeautifulSoup, category: str, requested_page: int
) -> dict[str, Any]:
    context = f"category {category} request page {requested_page}"
    totals = soup.select("p.page .count-1")
    markers = soup.select("p.page .count-2")
    if (
        len(totals) != 1
        or not _clean(totals[0].get_text()).isdigit()
        or len(markers) != 1
    ):
        raise JinanContractError(f"{context}: advertised pagination missing")
    total = int(_clean(totals[0].get_text()))
    if total < 1:
        raise JinanContractError(f"{context}: audited category became empty")
    marker = _PAGE_MARKER.fullmatch(_clean(markers[0].get_text(" ", strip=True)))
    if marker is None:
        raise JinanContractError(f"{context}: page marker drift")
    rendered_page, last = map(int, marker.groups())
    expected_last = math.ceil(total / JINAN_PAGE_SIZE)
    if last != expected_last:
        raise JinanContractError(f"{context}: last page disagrees with total")
    if requested_page <= last:
        if rendered_page != requested_page:
            raise JinanContractError(f"{context}: advertised page unexpectedly clamped")
    elif requested_page == last + 1:
        if rendered_page != last:
            raise JinanContractError(f"{context}: exact final-page clamp missing")
    else:
        raise JinanContractError(f"{context}: request is beyond audited clamp boundary")

    tabs = _tab_directory(soup, context)
    branches = _list_form(soup, category, rendered_page, context)
    tables = soup.select("table.basicList")
    if len(tables) != 1:
        raise JinanContractError(f"{context}: exact course table missing")
    table = tables[0]
    caption = _clean(table.caption.get_text(" ", strip=True) if table.caption else "")
    headers = tuple(_clean(item.get_text(" ", strip=True)) for item in table.select("thead th"))
    if caption != "강좌개설 리스트" or headers != _LIST_HEADERS:
        raise JinanContractError(f"{context}: course table contract drift")
    bodies = table.find_all("tbody", recursive=False)
    if len(bodies) != 1:
        raise JinanContractError(f"{context}: course tbody drift")
    nodes = bodies[0].find_all("tr", recursive=False)
    if not 1 <= len(nodes) <= JINAN_PAGE_SIZE:
        raise JinanContractError(f"{context}: live course row count drift")

    name_to_code = {name: code for code, name in branches.items()}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in nodes:
        cells = node.find_all("td", recursive=False)
        if len(cells) != len(_LIST_HEADERS):
            raise JinanContractError(f"{context}: course column count drift")
        sequence_text = _clean(cells[0].get_text(" ", strip=True))
        links = cells[1].select("a[href][title]")
        if len(links) != 1 or not sequence_text.isdigit():
            raise JinanContractError(f"{context}: sequence/title control drift")
        title = _clean(links[0].get("title"))
        displayed_title = _clean(links[0].get_text(" ", strip=True))
        identity, detail_url = _parse_detail_link(
            links[0], category, rendered_page, context
        )
        if not title or not displayed_title or identity in seen:
            raise JinanContractError(f"{context}: required/unique identity drift")
        seen.add(identity)

        center, venue = _split_center_venue(cells[2], context)
        if category == "D":
            if center:
                raise JinanContractError(f"{context}: citizens-college center drift")
            branch = JINAN_CATEGORIES[category]
            branch_code = category
        else:
            if center not in name_to_code:
                raise JinanContractError(f"{context}: row uses an unknown official branch")
            branch = center
            branch_code = name_to_code[center]

        education_source = _clean(cells[3].get_text(" ", strip=True))
        if (
            _STANDARD_EDUCATION_PERIOD.fullmatch(education_source) is None
            and identity not in JINAN_KNOWN_NONSTANDARD_EDUCATION_IDS
        ):
            raise JinanContractError(
                f"{context}: new nonstandard education period is not allowlisted"
            )
        start, end, period = _education_period(
            education_source,
            "education",
            allow_reversed_source=(
                identity in JINAN_KNOWN_REVERSED_EDUCATION_IDS
            ),
        )
        capacity_match = _CAPACITY.fullmatch(_clean(cells[4].get_text(" ", strip=True)))
        if capacity_match is None:
            raise JinanContractError(f"{context}: capacity shape drift")
        if (
            capacity_match.group(3) is not None
            and identity not in JINAN_KNOWN_RANGE_CAPACITY_IDS
        ):
            raise JinanContractError(
                f"{context}: new range capacity is not allowlisted"
            )
        capacity_current = int(capacity_match.group(1))
        capacity_total = int(capacity_match.group(3) or capacity_match.group(2))
        # The historical ledger contains legacy data-entry typos with a
        # reversed application range.  Preserve those rows for pagination
        # integrity; current/open detail validation remains strict below.
        apply_start, apply_end, apply_period = _application_period(
            cells[6].get_text(" ", strip=True),
            allow_reversed_source=(
                identity in JINAN_KNOWN_REVERSED_APPLICATION_IDS
            ),
        )
        source_status = _clean(cells[7].get_text(" ", strip=True))
        if source_status not in _SOURCE_STATUS:
            raise JinanContractError(
                f"{context}: unsupported source status {source_status!r}"
            )
        rows.append(
            {
                "identity": identity,
                "category": category,
                "category_name": JINAN_CATEGORIES[category],
                "page": rendered_page,
                "sequence": int(sequence_text),
                "title": title,
                "branch": branch,
                "branch_code": branch_code,
                "center": center,
                "venue": venue,
                "start": start,
                "end": end,
                "period": period,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "apply_period": apply_period,
                "capacity_current": capacity_current,
                "capacity_total": capacity_total,
                "capacity_source": _clean(cells[4].get_text(" ", strip=True)),
                "source_status": source_status,
                "status": _SOURCE_STATUS[source_status],
                "detail_url": detail_url,
            }
        )
    return {
        "category": category,
        "requested_page": requested_page,
        "rendered_page": rendered_page,
        "total": total,
        "last": last,
        "tabs": tabs,
        "branches": branches,
        "rows": rows,
    }


def _page_signature(page: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        str(page["category"]),
        int(page["rendered_page"]),
        int(page["total"]),
        int(page["last"]),
        tuple(page["tabs"].items()),
        tuple(page["branches"].items()),
        tuple(
            (
                row["identity"],
                row["sequence"],
                row["title"],
                row["branch"],
                row["branch_code"],
                row["venue"],
                row["period"],
                row["apply_period"],
                row["capacity_current"],
                row["capacity_total"],
                row["capacity_source"],
                row["source_status"],
            )
            for row in page["rows"]
        ),
    )


def _detail_fields(table: Any, identity: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for row in table.select("tbody > tr"):
        items = row.find_all(["th", "td"], recursive=False)
        if len(items) == 1 and items[0].name == "td" and not _clean(items[0].get_text()):
            continue
        position = 0
        while position < len(items):
            if (
                items[position].name == "th"
                and position + 1 < len(items)
                and items[position + 1].name == "td"
            ):
                label = _clean(items[position].get_text(" ", strip=True))
                value = _clean(items[position + 1].get_text(" ", strip=True))
                if not label or label in fields:
                    raise JinanContractError(
                        f"course {identity}: duplicate/empty detail label"
                    )
                fields[label] = value
                position += 2
            else:
                raise JinanContractError(
                    f"course {identity}: detail label/value shape drift"
                )
    if not _DETAIL_REQUIRED <= set(fields):
        missing = sorted(_DETAIL_REQUIRED - set(fields))
        raise JinanContractError(
            f"course {identity}: required detail fields missing: {missing}"
        )
    return fields


def _fee(value: Any) -> tuple[str, Optional[int]]:
    text = _clean(value)
    if not text:
        return "정보없음", None
    if text == "무료":
        return "무료", 0
    match = re.fullmatch(r"([0-9][0-9,]*)\s*원?", text)
    if match:
        amount = int(match.group(1).replace(",", ""))
        return f"{amount:,}원", amount
    if len(text) > 50 or _PHONE.search(text) or _EMAIL.search(text):
        raise JinanContractError("unsafe/unsupported tuition value")
    return text, None


def _application_methods(value: Any, identity: str) -> tuple[list[str], list[str]]:
    source = [_clean(item) for item in _clean(value).split(",") if _clean(item)]
    if not source or not set(source) <= {"방문", "전화", "인터넷"}:
        raise JinanContractError(f"course {identity}: reception-method drift")
    mapped = ["온라인" if item == "인터넷" else item for item in source]
    return source, mapped


def _application_control(
    soup: BeautifulSoup,
    listed: Mapping[str, Any],
    methods: list[str],
    cutoff: date,
) -> tuple[bool, str]:
    identity = str(listed["identity"])
    scripts = [
        _clean(script.get_text())
        for script in soup.select("script")
        if "peopleCountAjax" in script.get_text()
    ]
    anchors = [
        anchor
        for anchor in soup.select("a[onclick]")
        if "peopleCountAjax" in _clean(anchor.get("onclick"))
    ]
    online_expected = listed["status"] == "OPEN" and "인터넷" in methods
    if listed["status"] == "OPEN" and not (
        listed["apply_start"].date() <= cutoff <= listed["apply_end"].date()
    ):
        raise JinanContractError(
            f"course {identity}: open source status disagrees with application period"
        )
    if not online_expected:
        if scripts or anchors:
            raise JinanContractError(
                f"course {identity}: unexpected online application control"
            )
        return False, ""
    if len(scripts) != 1 or len(anchors) != 1:
        raise JinanContractError(
            f"course {identity}: open online detail lacks one application control"
        )
    script = scripts[0]
    anchor = anchors[0]
    data_match = _CONTROL_DATA_ID.search(script)
    location_match = _CONTROL_LOCATION.search(script)
    if (
        "url:'/index.jinan?contentsSid=372'" not in script.replace(" ", "")
        and 'url:"/index.jinan?contentsSid=372"' not in script.replace(" ", "")
    ):
        raise JinanContractError(f"course {identity}: capacity probe endpoint drift")
    if data_match is None or data_match.group(1) != identity or location_match is None:
        raise JinanContractError(f"course {identity}: application script identity drift")
    write_url = urljoin("https://www.jinan.go.kr", location_match.group(1))
    parsed = urlparse(write_url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    values = dict(query)
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == JINAN_HOST
        and parsed.path == JINAN_APPLICATION_WRITE_PATH
        and len(query) == 6
        and len(values) == 6
        and values.get("boardId") == "BBS_0000019"
        and values.get("menuCd") == "DOM_000000502003001000"
        and values.get("startPage") == "1"
        and values.get("studyno") == identity
        and values.get("tNum1") == str(listed["capacity_total"])
        and values.get("title1") == listed["title"]
        and _clean(anchor.get_text(" ", strip=True)) == "강좌신청"
        and _clean(anchor.get("href")) == "#n"
        and _clean(anchor.get("onclick")) == "peopleCountAjax();"
    ):
        raise JinanContractError(f"course {identity}: application form binding drift")
    return True, (
        f"GET {JINAN_APPLICATION_CAPACITY_PATH}?contentsSid=372 "
        f"up_dataid={identity} -> {JINAN_APPLICATION_WRITE_PATH} studyno={identity}"
    )


def _base_row(listed: Mapping[str, Any]) -> dict[str, Any]:
    identity = str(listed["identity"])
    if listed["start"] is None:
        raise JinanContractError(f"course {identity}: current course has unknown start")
    return {
        "provider": JINAN_PROVIDER,
        "provider_course_id": f"{JINAN_PROVIDER}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": str(listed["title"]),
        "description": str(listed["title"]),
        "branch": str(listed["branch"]),
        "branch_code": str(listed["branch_code"]),
        "preserve_branch": True,
        "category": "공공교육",
        "program_type": "교육",
        "raw_url": str(listed["detail_url"]),
        "application_url": str(listed["detail_url"]),
        "status": str(listed["status"]),
        "period": str(listed["period"]),
        "start_date": listed["start"].isoformat(),
        "end_date": listed["end"].isoformat(),
        "apply_period": str(listed["apply_period"]),
        "apply_start": listed["apply_start"].strftime("%Y-%m-%d %H:%M"),
        "apply_end": listed["apply_end"].strftime("%Y-%m-%d %H:%M"),
        "capacity": f"{listed['capacity_total']}명",
        "capacity_current": int(listed["capacity_current"]),
        "capacity_total": int(listed["capacity_total"]),
        "venue": str(listed["venue"]),
        "venue_name": str(listed["venue"]),
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": JINAN_PARSER,
        "municipality_code": JINAN_MUNICIPALITY_CODE,
        "municipality_full_name": JINAN_MUNICIPALITY_NAME,
    }


def _fallback_detail(listed: Mapping[str, Any], soup: BeautifulSoup) -> dict[str, Any]:
    identity = str(listed["identity"])
    if identity not in JINAN_KNOWN_EMPTY_DETAIL_IDS:
        raise JinanContractError(
            f"course {identity}: unexpected empty public detail is not allowlisted"
        )
    if soup.select("table") or any(
        "peopleCountAjax" in script.get_text() for script in soup.select("script")
    ):
        raise JinanContractError(f"course {identity}: empty-detail shell shape drift")
    row = _base_row(listed)
    row.update(
        {
            "application_type": "INFO_ONLY",
            "application_method": "정보확인",
            "application_methods": ["정보확인"],
            "reservation_available": listed["status"] == "OPEN",
            "fee": "정보없음",
            "fee_amount": None,
            "schedule_raw": "",
            "target": "",
            "raw_fields": {
                "identity": identity,
                "source_category_code": str(listed["category"]),
                "source_category_name": str(listed["category_name"]),
                "source_page": int(listed["page"]),
                "source_sequence": int(listed["sequence"]),
                "source_status": str(listed["source_status"]),
                "source_apply_period": str(listed["apply_period"]),
                "source_education_period": str(listed["period"]),
                "source_schedule": "",
                "source_venue": str(listed["venue"]),
                "official_branch_code": str(listed["branch_code"]),
                "capacity_current": int(listed["capacity_current"]),
                "capacity_total": int(listed["capacity_total"]),
                "detail_verified": False,
                "detail_unavailable": True,
                "detail_unavailable_reason": "official_200_response_without_course_table",
                "application_control_present": False,
                "application_control_contract": "",
                "application_control_verified": False,
                "application_capacity_probe_fetched": False,
                "application_form_fetched": False,
                "pii_form_fetched": False,
                "service_family": "education",
            },
        }
    )
    return row


def _parse_detail(
    listed: Mapping[str, Any], soup: BeautifulSoup, cutoff: date
) -> dict[str, Any]:
    identity = str(listed["identity"])
    tables = soup.select("table.bbs_list.basicWrite")
    if not tables:
        return _fallback_detail(listed, soup)
    if len(tables) != 1:
        raise JinanContractError(f"course {identity}: exact detail table drift")
    table = tables[0]
    caption = _clean(table.caption.get_text(" ", strip=True) if table.caption else "")
    if caption != "게시판 리스트":
        raise JinanContractError(f"course {identity}: detail caption drift")
    fields = _detail_fields(table, identity)
    if fields["강좌명"] != listed["title"]:
        raise JinanContractError(f"course {identity}: detail title drift")
    if fields["기관구분"] != _EXPECTED_INSTITUTION[listed["category"]]:
        raise JinanContractError(f"course {identity}: institution category drift")
    expected_center = "" if listed["category"] == "D" else listed["branch"]
    if fields["학습센터"] != expected_center:
        raise JinanContractError(f"course {identity}: official branch drift")
    detail_start, detail_end, detail_period = _education_period(
        fields["강좌기간"], "detail education"
    )
    if (
        detail_start != listed["start"]
        or detail_end != listed["end"]
        or detail_period != listed["period"]
        or detail_end < cutoff
    ):
        raise JinanContractError(f"course {identity}: detail education period drift")
    apply_start, apply_end, apply_period = _application_period(fields["신청기간"])
    if (
        apply_start != listed["apply_start"]
        or apply_end != listed["apply_end"]
        or apply_period != listed["apply_period"]
    ):
        raise JinanContractError(f"course {identity}: detail application period drift")
    venue = _clean(fields["교육장소"])
    target = _clean(fields["교육대상"])
    schedule = _clean(fields["수강시간"])
    if not target or not schedule or venue != listed["venue"]:
        raise JinanContractError(f"course {identity}: detail target/schedule/venue drift")
    if any(_PHONE.search(value) or _EMAIL.search(value) for value in (target, venue, schedule)):
        raise JinanContractError(f"course {identity}: retained detail contains contact data")
    capacity = _DETAIL_CAPACITY.fullmatch(fields["정원"])
    if capacity is None or int(capacity.group(1)) != listed["capacity_total"]:
        raise JinanContractError(f"course {identity}: detail capacity drift")
    source_methods, methods = _application_methods(fields["접수방법"], identity)
    control_present, control_contract = _application_control(
        soup, listed, source_methods, cutoff
    )
    fee, fee_amount = _fee(fields.get("수강료", ""))

    row = _base_row(listed)
    row.update(
        {
            "application_type": (
                "ONLINE_RESERVATION" if control_present else "INFO_ONLY"
            ),
            "application_method": ",".join(methods),
            "application_methods": methods,
            "reservation_available": listed["status"] == "OPEN",
            "fee": fee,
            "fee_amount": fee_amount,
            "schedule_raw": schedule,
            "target": target,
            "raw_fields": {
                "identity": identity,
                "source_category_code": str(listed["category"]),
                "source_category_name": str(listed["category_name"]),
                "source_page": int(listed["page"]),
                "source_sequence": int(listed["sequence"]),
                "source_status": str(listed["source_status"]),
                "source_apply_period": apply_period,
                "source_education_period": detail_period,
                "source_schedule": schedule,
                "source_venue": venue,
                "official_branch_code": str(listed["branch_code"]),
                "capacity_current": int(listed["capacity_current"]),
                "capacity_total": int(listed["capacity_total"]),
                "detail_verified": True,
                "detail_unavailable": False,
                "detail_unavailable_reason": "",
                "application_control_present": control_present,
                "application_control_contract": control_contract,
                "application_control_verified": True,
                "application_capacity_probe_fetched": False,
                "application_form_fetched": False,
                "pii_form_fetched": False,
                "service_family": "education",
            },
        }
    )
    return row


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_ROW_KEYS:
        errors.append("forbidden detail/PII key")
    raw_fields = row.get("raw_fields")
    if not isinstance(raw_fields, Mapping) or not set(raw_fields) <= _SAFE_RAW_FIELDS:
        errors.append("raw field allowlist exceeded")
    payload = repr(
        {
            key: value
            for key, value in row.items()
            if key not in {"raw_url", "application_url"}
        }
    )
    if _PHONE.search(payload) or _EMAIL.search(payload):
        errors.append("contact data persisted")
    if row.get("description") != row.get("title"):
        errors.append("free-form detail persisted")
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


def _base_meta() -> dict[str, Any]:
    return {
        "municipality_code": JINAN_MUNICIPALITY_CODE,
        "municipality_full_name": JINAN_MUNICIPALITY_NAME,
        "owner_provider": JINAN_PROVIDER,
        "provider_decision": "retain_existing_provider_and_retarget_url",
        "legacy_candidate_id": JINAN_LEGACY_CANDIDATE_ID,
        "canonical_candidate_id": JINAN_CANONICAL_CANDIDATE_ID,
        "legacy_url": JINAN_LEGACY_URL,
        "canonical_url": JINAN_CANONICAL_URL,
        "legacy_normalized_sha1": JINAN_LEGACY_NORMALIZED_SHA1,
        "legacy_normalized_sha256": JINAN_LEGACY_NORMALIZED_SHA256,
        "canonical_normalized_sha1": JINAN_CANONICAL_NORMALIZED_SHA1,
        "canonical_normalized_sha256": JINAN_CANONICAL_NORMALIZED_SHA256,
        "parser": JINAN_PARSER,
        "ownership_scope": "jinan_bbs_0000018_categories_A_B_C_D_datasid_set",
        "official_categories": dict(JINAN_CATEGORIES),
        "official_branches": {
            key: dict(value) for key, value in JINAN_BRANCHES.items()
        },
        "candidate_audit": {
            key: dict(value) for key, value in JINAN_CANDIDATE_AUDIT.items()
        },
        "non_executing_aliases": [dict(item) for item in JINAN_NON_EXECUTING_ALIASES],
        "separate_owner_boundaries": [
            dict(item) for item in JINAN_SEPARATE_OWNER_BOUNDARIES
        ],
        "recommended_override": dict(JINAN_RECOMMENDED_OVERRIDE),
        "recommended_max_pages": JINAN_RECOMMENDED_MAX_PAGES,
        "recommended_detail_limit": JINAN_RECOMMENDED_DETAIL_LIMIT,
        "source_requests": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "detail_verified_count": 0,
        "detail_fallback_count": 0,
        "advertised_pages": 0,
        "source_rows": 0,
        "current_source_count": 0,
        "returned_count": 0,
        "boundary_rechecks": 0,
        "current_list_pages_rechecked": {},
        "clamp_pages": {},
        "clamp_rechecked": False,
        "application_capacity_probes_called": 0,
        "application_form_endpoints_called": 0,
        "pii_form_endpoints_called": 0,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "configured_collection_error": "",
    }


def collect_jinan_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = JINAN_RECOMMENDED_MAX_PAGES,
    detail_limit: int = JINAN_RECOMMENDED_DETAIL_LIMIT,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    getter: Optional[Getter] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one atomic current/future snapshot across all four categories."""

    meta = _base_meta()
    if not is_jinan_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match the exact retained/canonical Jinan education owner"
        )
        return [], JINAN_PARSER, meta
    try:
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int)
            or timeout < 1
            or isinstance(max_pages, bool)
            or not isinstance(max_pages, int)
            or max_pages < 2
            or isinstance(detail_limit, bool)
            or not isinstance(detail_limit, int)
            or detail_limit < 0
        ):
            raise ValueError("timeout/max_pages/detail_limit caps are invalid")
        cutoff = _today(today)
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], JINAN_PARSER, meta

    factory = session_factory or _default_session_factory
    current_getter = getter or _default_getter
    session = factory()

    def get_list(category: str, page: int) -> dict[str, Any]:
        parsed = _parse_list_page(
            _get_soup(session, current_getter, _list_url(category, page), timeout),
            category,
            page,
        )
        meta["source_requests"] += 1
        meta["list_requests"] += 1
        return parsed

    try:
        pages: dict[tuple[str, int], dict[str, Any]] = {}
        clamps: dict[str, dict[str, Any]] = {}
        category_totals: dict[str, int] = {}
        category_pages: dict[str, int] = {}
        for category in JINAN_CATEGORIES:
            first = get_list(category, 1)
            total = int(first["total"])
            last = int(first["last"])
            if last + 1 > max_pages:
                meta["source_cap_reached"] = True
                raise JinanContractError(
                    f"category {category}: max_pages {max_pages} below clamp page {last + 1}"
                )
            pages[(category, 1)] = first
            for page_number in range(2, last + 1):
                parsed = get_list(category, page_number)
                if (
                    parsed["total"] != total
                    or parsed["last"] != last
                    or parsed["tabs"] != first["tabs"]
                    or parsed["branches"] != first["branches"]
                ):
                    raise JinanContractError(
                        f"category {category} page {page_number}: ledger contract drift"
                    )
                pages[(category, page_number)] = parsed
            clamp = get_list(category, last + 1)
            if _page_signature(clamp) != _page_signature(pages[(category, last)]):
                raise JinanContractError(
                    f"category {category}: immediate page is not exact final-page clamp"
                )
            clamps[category] = clamp
            category_totals[category] = total
            category_pages[category] = last

            for page_number in range(1, last):
                if len(pages[(category, page_number)]["rows"]) != JINAN_PAGE_SIZE:
                    raise JinanContractError(
                        f"category {category} page {page_number}: non-final page is not full"
                    )
            expected_final = total - JINAN_PAGE_SIZE * (last - 1)
            if len(pages[(category, last)]["rows"]) != expected_final:
                raise JinanContractError(
                    f"category {category}: final row count disagrees with total"
                )

        listed: list[dict[str, Any]] = []
        for category in JINAN_CATEGORIES:
            category_rows = [
                row
                for page_number in range(1, category_pages[category] + 1)
                for row in pages[(category, page_number)]["rows"]
            ]
            if len(category_rows) != category_totals[category]:
                raise JinanContractError(
                    f"category {category}: all-page row count disagrees with total"
                )
            sequences = [int(row["sequence"]) for row in category_rows]
            if sequences != list(range(category_totals[category], 0, -1)):
                raise JinanContractError(
                    f"category {category}: display sequence is not complete/descending"
                )
            listed.extend(category_rows)
        identities = [str(row["identity"]) for row in listed]
        if len(identities) != len(set(identities)):
            raise JinanContractError("dataSid repeated across category ledgers")

        current = [row for row in listed if row["end"] >= cutoff]
        meta.update(
            {
                "cutoff": cutoff.isoformat(),
                "category_totals": category_totals,
                "category_pages": category_pages,
                "clamp_pages": {
                    category: category_pages[category] + 1
                    for category in JINAN_CATEGORIES
                },
                "advertised_pages": sum(category_pages.values()),
                "source_rows": len(listed),
                "current_source_count": len(current),
                "expired_source_count": len(listed) - len(current),
                "source_status_counts": dict(
                    Counter(str(row["source_status"]) for row in listed)
                ),
                "current_source_status_counts": dict(
                    Counter(str(row["source_status"]) for row in current)
                ),
                "source_category_counts": dict(
                    Counter(str(row["category"]) for row in listed)
                ),
                "current_category_counts": dict(
                    Counter(str(row["category"]) for row in current)
                ),
                "source_branch_counts": dict(
                    Counter(str(row["branch"]) for row in listed)
                ),
                "pagination_complete": True,
            }
        )
        if len(current) > detail_limit:
            meta["source_cap_reached"] = True
            raise JinanContractError(
                f"detail_limit {detail_limit} below required {len(current)}"
            )

        rows: list[dict[str, Any]] = []
        for listed_row in current:
            soup = _get_soup(
                session,
                current_getter,
                str(listed_row["detail_url"]),
                timeout,
            )
            meta["source_requests"] += 1
            meta["detail_pages"] += 1
            parsed = _parse_detail(listed_row, soup, cutoff)
            if parsed["raw_fields"]["detail_verified"]:
                meta["detail_verified_count"] += 1
            else:
                meta["detail_fallback_count"] += 1
            rows.append(parsed)

        rechecked: dict[str, list[int]] = {}
        for category in JINAN_CATEGORIES:
            stability_pages = sorted(
                {1, category_pages[category]}
                | {
                    int(row["page"])
                    for row in current
                    if row["category"] == category
                }
            )
            rechecked[category] = stability_pages
            for page_number in stability_pages:
                parsed = get_list(category, page_number)
                meta["boundary_rechecks"] += 1
                if _page_signature(parsed) != _page_signature(
                    pages[(category, page_number)]
                ):
                    raise JinanContractError(
                        f"category {category} page {page_number}: stability recheck failed"
                    )
            clamp_number = category_pages[category] + 1
            clamp_recheck = get_list(category, clamp_number)
            meta["boundary_rechecks"] += 1
            if _page_signature(clamp_recheck) != _page_signature(clamps[category]):
                raise JinanContractError(
                    f"category {category}: final-page clamp stability failed"
                )
        meta["current_list_pages_rechecked"] = rechecked
        meta["clamp_rechecked"] = True

        rows.sort(key=lambda row: (row["start_date"], row["provider_course_id"]))
        rows = list((dedupe_rows or _dedupe)(rows))
        privacy_errors = [error for row in rows for error in _privacy_errors(row)]
        if privacy_errors:
            raise JinanContractError("; ".join(privacy_errors[:5]))
        if len(rows) != len(current):
            raise JinanContractError("dedupe changed the complete current dataSid set")
        meta.update(
            {
                "returned_count": len(rows),
                "status_counts": dict(Counter(str(row["status"]) for row in rows)),
                "branch_counts": dict(Counter(str(row["branch"]) for row in rows)),
                "application_control_count": sum(
                    bool(row["raw_fields"]["application_control_present"])
                    for row in rows
                ),
                "offline_open_count": sum(
                    row["status"] == "OPEN"
                    and not row["raw_fields"]["application_control_present"]
                    for row in rows
                ),
                "details_complete": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
                "no_current_data": not rows,
            }
        )
        return rows, JINAN_PARSER, meta
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], JINAN_PARSER, meta
    finally:
        _close_quietly(session)


collect = collect_jinan_education


__all__ = [
    "JINAN_BRANCHES",
    "JINAN_CANONICAL_CANDIDATE_ID",
    "JINAN_CANONICAL_NORMALIZED_SHA1",
    "JINAN_CANONICAL_NORMALIZED_SHA256",
    "JINAN_CANONICAL_URL",
    "JINAN_CANDIDATE_AUDIT",
    "JINAN_CATEGORIES",
    "JINAN_DISCOVERY_AUDIT",
    "JINAN_KNOWN_EMPTY_DETAIL_IDS",
    "JINAN_KNOWN_NONSTANDARD_EDUCATION_IDS",
    "JINAN_KNOWN_RANGE_CAPACITY_IDS",
    "JINAN_KNOWN_REVERSED_APPLICATION_IDS",
    "JINAN_KNOWN_REVERSED_EDUCATION_IDS",
    "JINAN_LEGACY_CANDIDATE_ID",
    "JINAN_LEGACY_URL",
    "JINAN_NON_EXECUTING_ALIASES",
    "JINAN_PARSER",
    "JINAN_PROVIDER",
    "JINAN_RECOMMENDED_DETAIL_LIMIT",
    "JINAN_RECOMMENDED_MAX_PAGES",
    "JINAN_RECOMMENDED_OVERRIDE",
    "JINAN_SEPARATE_OWNER_BOUNDARIES",
    "JinanContractError",
    "collect",
    "collect_jinan_education",
    "is_jinan_education_target",
    "is_target",
]
