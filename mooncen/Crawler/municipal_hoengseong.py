"""Fail-closed collectors for Hoengseong's public programme catalogues.

The audited boundary contains six independent owners: Hoengseong integrated
reservation, Hoengseong County Library, Hoengseong Education Library, the
county Youth Center, Hoengseong Cultural Center's culture school, and the
Hoengseong Family Center.  The integrated reservation catalogue also audits
its two currently-empty experience ledgers.

Only exact public list/detail routes are reachable.  Login, application,
applicant/result, attachment, payment and member routes are outside the URL
allowlist.  A missing page, unstable sentinel, partial detail set, access
restriction, owner mismatch, or source-contract drift invalidates the whole
owner snapshot.
"""

from __future__ import annotations

import calendar
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import re
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


HOENGSEONG_MUNICIPALITY_CODE = "5173000000"
HOENGSEONG_MUNICIPALITY_NAME = "강원특별자치도 횡성군"

# Keep the incumbent county provider and retarget its homepage-only entry to
# the complete integrated reservation education ledger.
HOENGSEONG_RESERVATION_PROVIDER = "MUNI_WWW_HSG_GO_KR_7452F27B"
HOENGSEONG_LIBRARY_PROVIDER = "MUNI_LIB_HSG_GO_KR_F84FF98D"
HOENGSEONG_GWE_PROVIDER = "MUNI_LIB_GWE_GO_KR_5CEF7967"
HOENGSEONG_YOUTH_PROVIDER = "MUNI_HSYOUTHCENTER_HSG_GO_KR_46DEDE77"
HOENGSEONG_CULTURE_PROVIDER = "MUNI_HS_CULTURE_OR_KR_B2E1E14F"
HOENGSEONG_FAMILY_PROVIDER = "MUNI_HSG_FAMILYNET_OR_KR_4676E082"

HOENGSEONG_RESERVATION_URL = (
    "https://www.hsg.go.kr/reserve/"
    "selectEdcCourseLctreRcritListU.do?key=1668"
)
HOENGSEONG_LIBRARY_URL = (
    "https://lib.hsg.go.kr/library/index.php?g_page=culture&m_page=culture01"
)
HOENGSEONG_LIBRARY_FESTIVAL_URL = (
    "https://lib.hsg.go.kr/library/index.php?g_page=culture&m_page=culture08"
)
HOENGSEONG_GWE_URL = (
    "https://lib.gwe.go.kr/hslib/menu/2958/lecture-event/list/all"
)
HOENGSEONG_YOUTH_URL = (
    "https://hsyouthcenter.hsg.go.kr/bbs/board.php?bo_table=center"
)
HOENGSEONG_CULTURE_URL = "https://hs-culture.or.kr/page/doc.php?m_id=21"
HOENGSEONG_FAMILY_URL = (
    "https://hsg.familynet.or.kr/center/lay1/program/"
    "S295T322C451/recruitReceipt/list.do"
)
HOENGSEONG_FAMILY_DETAIL_PATH = (
    "/center/lay1/program/S295T322C451/recruitReceipt/view.do"
)
HOENGSEONG_FAMILY_VIEW_API_URL = (
    "https://hsg.familynet.or.kr/recruitReceipt/getView.do"
)

HOENGSEONG_RESERVATION_CANDIDATE_ID = "MUNI_IR_A58263A42478"
HOENGSEONG_LIBRARY_CANDIDATE_ID = "MUNI_IR_2A7E6DAC433C"
HOENGSEONG_GWE_CANDIDATE_ID = "MUNI_IR_857489E2A9AA"
HOENGSEONG_YOUTH_CANDIDATE_ID = "MUNI_IR_4B0739F9B619"
HOENGSEONG_CULTURE_CANDIDATE_ID = "MUNI_IR_9FFED2EC71CF"
HOENGSEONG_FAMILY_CANDIDATE_ID = "MUNI_IR_B2F71DF8F7CB"

HOENGSEONG_OWNERS: Mapping[str, Mapping[str, str]] = {
    "reservation": {
        "provider": HOENGSEONG_RESERVATION_PROVIDER,
        "url": HOENGSEONG_RESERVATION_URL,
        "candidate_id": HOENGSEONG_RESERVATION_CANDIDATE_ID,
        "branch": "횡성군 통합예약",
    },
    "municipal_library": {
        "provider": HOENGSEONG_LIBRARY_PROVIDER,
        "url": HOENGSEONG_LIBRARY_URL,
        "candidate_id": HOENGSEONG_LIBRARY_CANDIDATE_ID,
        "branch": "횡성군립도서관",
    },
    "education_library": {
        "provider": HOENGSEONG_GWE_PROVIDER,
        "url": HOENGSEONG_GWE_URL,
        "candidate_id": HOENGSEONG_GWE_CANDIDATE_ID,
        "branch": "횡성교육도서관",
    },
    "youth_center": {
        "provider": HOENGSEONG_YOUTH_PROVIDER,
        "url": HOENGSEONG_YOUTH_URL,
        "candidate_id": HOENGSEONG_YOUTH_CANDIDATE_ID,
        "branch": "횡성군 청년센터",
    },
    "culture_school": {
        "provider": HOENGSEONG_CULTURE_PROVIDER,
        "url": HOENGSEONG_CULTURE_URL,
        "candidate_id": HOENGSEONG_CULTURE_CANDIDATE_ID,
        "branch": "횡성문화원 문화학교",
    },
    "family_center": {
        "provider": HOENGSEONG_FAMILY_PROVIDER,
        "url": HOENGSEONG_FAMILY_URL,
        "candidate_id": HOENGSEONG_FAMILY_CANDIDATE_ID,
        "branch": "횡성군 가족센터",
    },
}


@dataclass(frozen=True)
class _HsgCategory:
    category_id: int
    menu_key: int
    page_title: str
    branch: str


HOENGSEONG_RESERVATION_CATEGORIES = (
    _HsgCategory(60, 1670, "평생학습", "횡성군평생학습관"),
    _HsgCategory(62, 1672, "여성회관", "횡성군여성회관"),
    _HsgCategory(59, 1669, "횡성군립도서관", "횡성군립도서관"),
    _HsgCategory(79, 2068, "둔내태성도서관", "둔내태성도서관"),
    _HsgCategory(63, 1673, "청소년수련관", "횡성군청소년수련관"),
    _HsgCategory(66, 1677, "횡성읍", "횡성읍 주민자치센터"),
    _HsgCategory(67, 1678, "우천면", "우천면 주민자치센터"),
    _HsgCategory(68, 1679, "안흥면", "안흥면 주민자치센터"),
    _HsgCategory(69, 1680, "둔내면", "둔내면 주민자치센터"),
    _HsgCategory(70, 1681, "갑천면", "갑천면 주민자치센터"),
    _HsgCategory(71, 1682, "청일면", "청일면 주민자치센터"),
    _HsgCategory(72, 1683, "공근면", "공근면 주민자치센터"),
    _HsgCategory(73, 1684, "서원면", "서원면 주민자치센터"),
    _HsgCategory(74, 1685, "강림면", "강림면 주민자치센터"),
)

HOENGSEONG_EXPERIENCE_LEDGERS = (
    (
        "한우체험관",
        "횡성한우체험관",
        "https://www.hsg.go.kr/reserve/"
        "selectUserExprnTourBasicInfoList.do?key=1687&searchExprnKey=4",
        "한우체험관 예약하기 - 통합예약",
    ),
    (
        "안흥찐빵",
        "안흥찐빵 모락모락마을",
        "https://www.hsg.go.kr/reserve/"
        "selectUserExprnTourBasicInfoList.do?key=1688&searchExprnKey=5",
        "안흥찐빵 모락모락마을 - 통합예약",
    ),
)

HOENGSEONG_CANDIDATE_AUDIT: Mapping[str, Mapping[str, str]] = {
    HOENGSEONG_RESERVATION_CANDIDATE_ID: {
        "url": HOENGSEONG_RESERVATION_URL,
        "provider": HOENGSEONG_RESERVATION_PROVIDER,
        "decision": "canonical_complete_integrated_reservation_owner",
    },
    HOENGSEONG_LIBRARY_CANDIDATE_ID: {
        "url": HOENGSEONG_LIBRARY_URL,
        "provider": HOENGSEONG_LIBRARY_PROVIDER,
        "decision": "independent_municipal_library_programme_owner",
    },
    HOENGSEONG_GWE_CANDIDATE_ID: {
        "url": HOENGSEONG_GWE_URL,
        "provider": HOENGSEONG_GWE_PROVIDER,
        "decision": "independent_provincial_education_library_owner",
    },
    HOENGSEONG_YOUTH_CANDIDATE_ID: {
        "url": HOENGSEONG_YOUTH_URL,
        "provider": HOENGSEONG_YOUTH_PROVIDER,
        "decision": "independent_youth_center_programme_owner",
    },
    HOENGSEONG_CULTURE_CANDIDATE_ID: {
        "url": HOENGSEONG_CULTURE_URL,
        "provider": HOENGSEONG_CULTURE_PROVIDER,
        "decision": "independent_culture_school_roster",
    },
    HOENGSEONG_FAMILY_CANDIDATE_ID: {
        "url": HOENGSEONG_FAMILY_URL,
        "provider": HOENGSEONG_FAMILY_PROVIDER,
        "decision": "independent_family_center_programme_owner",
    },
    "MUNI_IR_90310635DC35": {
        "url": "https://www.hsg.go.kr/",
        "provider": HOENGSEONG_RESERVATION_PROVIDER,
        "decision": "retarget_incumbent_homepage_to_complete_reservation_ledger",
    },
    "MUNI_IR_A72756E021AA": {
        "url": "https://www.hsg.go.kr/www/downloadBbsFile.do?atchmnflNo=531422",
        "provider": "MUNI_WWW_HSG_GO_KR_9EA598FA",
        "decision": "exclude_attachment_not_course_ledger",
    },
}

HOENGSEONG_EXCLUDED_BOUNDARIES: Mapping[str, str] = {
    "https://www.hsg.go.kr/": (
        "homepage shell; retain its incumbent provider but retarget to the complete "
        "integrated reservation catalogue"
    ),
    "https://www.hsg.go.kr/www/downloadBbsFile.do?atchmnflNo=531422": (
        "attachment route, not a programme ledger"
    ),
    "https://hsyouthcenter.hsg.go.kr/bbs/board.php?bo_table=backup": (
        "heterogeneous support, employment and subsidy schemes; not the center "
        "programme ledger"
    ),
    "https://lib.hsg.go.kr/library/toy_group_visit": (
        "group-booking calendar exposes applicant institution names and is outside "
        "the public course boundary"
    ),
    "https://www.hsg.go.kr/agri/selectSchdulManageCalDateU.do?key=1659&searchSchdulBassKey=2": (
        "mixed staff, association and event schedule without a public-enrolment schema"
    ),
    "https://www.hsg.go.kr/agri/selectBbsNttList.do?bbsNo=13&key=1658": (
        "heterogeneous notice board with attachment/form recruitment, not a stable "
        "course ledger"
    ),
    "https://luge.hsg.go.kr/kor/": "admission/on-site attraction, not a course ledger",
    "https://hscf.or.kr/": (
        "official foundation host currently returns an empty body and exposes no "
        "verifiable programme catalogue"
    ),
    "https://burak.or.kr/": (
        "welfare notice/service surface without a stable public course catalogue"
    ),
}

HOENGSEONG_PARSER = (
    "hoengseong_six_disjoint_public_owners+integrated_category_union_and_test_"
    "quarantine+complete_pagination_and_empty_sentinels+current_public_details+"
    "official_branch_registry+family_public_view_api+classification_locked+"
    "no_login_application_attachment_or_applicant_routes"
)
HOENGSEONG_MAX_PAGES = 100
HOENGSEONG_MAX_DETAILS = 100
HOENGSEONG_MAX_WORKERS = 4
HOENGSEONG_MAX_HTML_BYTES = 5_000_000
HOENGSEONG_MAX_JSON_BYTES = 1_000_000
HOENGSEONG_FAMILY_PAGE_SIZE = 5


class HoengseongContractError(RuntimeError):
    """Raised when an audited Hoengseong public-source contract changes."""


Fetcher = Callable[..., Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE = re.compile(r"\s+")
_POSITIVE = re.compile(r"^[1-9]\d*$")
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+[.][A-Za-z]{2,}")
_DATE_TOKEN = re.compile(
    r"(?:(?P<year>(?:20)?\d{2})\s*(?:년|[./-])\s*)?"
    r"(?P<month>\d{1,2})\s*(?:월|[./-])\s*"
    r"(?P<day>\d{1,2})\s*(?:일)?"
)
_MONTH_RANGE = re.compile(r"(?P<start>\d{1,2})\s*월\s*~\s*(?P<end>\d{1,2})\s*월")
_FAMILY_SEND = re.compile(
    r"^\s*send\(\s*'(?P<identity>[1-9]\d*)'\s*,.*,"
    r"\s*'(?P<fork>web|center)'\s*\)\s*;?\s*$",
    re.DOTALL,
)
_CSRF = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_FORBIDDEN_OUTPUT_KEYS = frozenset(
    {
        "phone",
        "email",
        "contact",
        "manager",
        "staff",
        "member",
        "instructor",
        "teacher",
        "description",
        "content",
        "attachment",
        "attachments",
        "image_url",
        "csrf",
        "session",
        "applicant",
        "application_payload",
        "raw_html",
        "raw_json",
    }
)


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def owner_for_target(target: Any) -> str:
    provider = _clean(_target_value(target, "provider"))
    url = _clean(_target_value(target, "url"))
    for owner, config in HOENGSEONG_OWNERS.items():
        if provider == config["provider"] and url == config["url"]:
            return owner
    return ""


def is_hoengseong_target(target: Any) -> bool:
    return bool(owner_for_target(target))


is_target = is_hoengseong_target


def reservation_list_url(page: int = 1) -> str:
    if not isinstance(page, int) or page < 1:
        raise HoengseongContractError("invalid reservation list page")
    query: list[tuple[str, Any]] = [("key", 1668)]
    if page > 1:
        query.append(("pageIndex", page))
    return "https://www.hsg.go.kr/reserve/selectEdcCourseLctreRcritListU.do?" + urlencode(query)


def reservation_category_url(category: _HsgCategory, page: int = 1) -> str:
    if category not in HOENGSEONG_RESERVATION_CATEGORIES or page < 1:
        raise HoengseongContractError("invalid reservation category page")
    query: list[tuple[str, Any]] = [
        ("key", category.menu_key),
        ("searchEdcKey", category.category_id),
    ]
    if page > 1:
        query.append(("pageIndex", page))
    return "https://www.hsg.go.kr/reserve/selectEdcCourseLctreRcritListU.do?" + urlencode(query)


def reservation_detail_url(identity: Any, page: int = 1) -> str:
    identity = _clean(identity)
    if not _POSITIVE.fullmatch(identity) or not isinstance(page, int) or page < 1:
        raise HoengseongContractError("invalid reservation detail identity")
    return (
        "https://www.hsg.go.kr/reserve/selectEdcCourseLctreRcritViewU.do?"
        + urlencode(
            [
                ("key", 1668),
                ("searchLctreRcritKey", identity),
                ("pageUnit", 10),
                ("searchCnd", "all"),
                ("pageIndex", page),
            ]
        )
    )


def library_detail_url(identity: Any) -> str:
    identity = _clean(identity)
    if not _POSITIVE.fullmatch(identity):
        raise HoengseongContractError("invalid municipal-library identity")
    return HOENGSEONG_LIBRARY_URL + "&" + urlencode(
        [("act", "lecture_view"), ("lgCode", 9), ("leCode", identity), ("cate", "")]
    )


def gwe_list_url(page: int = 1) -> str:
    if not isinstance(page, int) or page < 1:
        raise HoengseongContractError("invalid GWE page")
    return HOENGSEONG_GWE_URL if page == 1 else f"{HOENGSEONG_GWE_URL}?page={page}"


def gwe_detail_url(identity: Any) -> str:
    identity = _clean(identity)
    if not _POSITIVE.fullmatch(identity):
        raise HoengseongContractError("invalid GWE identity")
    return f"https://lib.gwe.go.kr/hslib/menu/2958/lecture-event/{identity}"


def youth_list_url(page: int = 1) -> str:
    if not isinstance(page, int) or page < 1:
        raise HoengseongContractError("invalid youth page")
    return HOENGSEONG_YOUTH_URL if page == 1 else f"{HOENGSEONG_YOUTH_URL}&page={page}"


def youth_detail_url(identity: Any) -> str:
    identity = _clean(identity)
    if not _POSITIVE.fullmatch(identity):
        raise HoengseongContractError("invalid youth identity")
    return f"{HOENGSEONG_YOUTH_URL}&wr_id={identity}"


def family_list_url(page: int = 1) -> str:
    if not isinstance(page, int) or page < 1:
        raise HoengseongContractError("invalid family-center page")
    return HOENGSEONG_FAMILY_URL + "?" + urlencode(
        [("rows", HOENGSEONG_FAMILY_PAGE_SIZE), ("cpage", page)]
    )


def family_detail_url(identity: Any) -> str:
    identity = _clean(identity)
    if not _POSITIVE.fullmatch(identity):
        raise HoengseongContractError("invalid family-center identity")
    return "https://hsg.familynet.or.kr" + HOENGSEONG_FAMILY_DETAIL_PATH + "?" + urlencode(
        {"seq": identity}
    )


def _query(url: str) -> tuple[Any, dict[str, str]]:
    parsed = urlparse(_clean(url))
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    values = dict(pairs)
    if len(pairs) != len(values):
        raise HoengseongContractError("duplicate query key")
    if parsed.username or parsed.password or parsed.fragment or parsed.params:
        raise HoengseongContractError("unsafe URL authority or fragment")
    try:
        if parsed.port is not None:
            raise HoengseongContractError("explicit ports are forbidden")
    except ValueError as exc:
        raise HoengseongContractError("invalid URL port") from exc
    return parsed, values


def _classify_url(owner: str, method: str, url: str) -> str:
    parsed, query = _query(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        raise HoengseongContractError("only HTTPS is allowlisted")
    if owner == "reservation":
        if method != "GET" or host != "www.hsg.go.kr":
            raise HoengseongContractError("reservation request boundary changed")
        if parsed.path == "/reserve/selectEdcCourseLctreRcritListU.do":
            allowed = {"key", "pageIndex"} if "pageIndex" in query else {"key"}
            category_allowed = (
                {"key", "searchEdcKey", "pageIndex"}
                if "pageIndex" in query
                else {"key", "searchEdcKey"}
            )
            if set(query) == allowed and query.get("key") == "1668" and (
                "pageIndex" not in query or _POSITIVE.fullmatch(query["pageIndex"])
            ):
                return "list"
            if set(query) == category_allowed:
                match = next(
                    (
                        item
                        for item in HOENGSEONG_RESERVATION_CATEGORIES
                        if str(item.menu_key) == query.get("key")
                        and str(item.category_id) == query.get("searchEdcKey")
                    ),
                    None,
                )
                if match and (
                    "pageIndex" not in query or _POSITIVE.fullmatch(query["pageIndex"])
                ):
                    return "list"
            raise HoengseongContractError("reservation list query is not allowlisted")
        if parsed.path == "/reserve/selectEdcCourseLctreRcritViewU.do" and set(query) == {
            "key",
            "searchLctreRcritKey",
            "pageUnit",
            "searchCnd",
            "pageIndex",
        }:
            if (
                query.get("key") == "1668"
                and query.get("pageUnit") == "10"
                and query.get("searchCnd") == "all"
                and _POSITIVE.fullmatch(query.get("searchLctreRcritKey", ""))
                and _POSITIVE.fullmatch(query.get("pageIndex", ""))
            ):
                return "detail"
        if parsed.path == "/reserve/selectUserExprnTourBasicInfoList.do" and set(query) == {
            "key",
            "searchExprnKey",
        }:
            if (query.get("key"), query.get("searchExprnKey")) in {("1687", "4"), ("1688", "5")}:
                return "list"
        raise HoengseongContractError("reservation route is not allowlisted")
    if owner == "municipal_library":
        if method != "GET" or host != "lib.hsg.go.kr" or parsed.path != "/library/index.php":
            raise HoengseongContractError("municipal-library route changed")
        if query in (
            {"g_page": "culture", "m_page": "culture01"},
            {"g_page": "culture", "m_page": "culture08"},
        ):
            return "list"
        if set(query) == {"g_page", "m_page", "act", "lgCode", "leCode", "cate"} and (
            query.get("g_page") == "culture"
            and query.get("m_page") == "culture01"
            and query.get("act") == "lecture_view"
            and query.get("lgCode") == "9"
            and _POSITIVE.fullmatch(query.get("leCode", ""))
        ):
            return "detail"
        raise HoengseongContractError("municipal-library query is not allowlisted")
    if owner == "education_library":
        if method != "GET" or host != "lib.gwe.go.kr":
            raise HoengseongContractError("GWE route changed")
        if parsed.path == "/hslib/menu/2958/lecture-event/list/all" and (
            not query or (set(query) == {"page"} and _POSITIVE.fullmatch(query["page"]))
        ):
            return "list"
        if re.fullmatch(r"/hslib/menu/2958/lecture-event/[1-9]\d*", parsed.path) and not query:
            return "detail"
        raise HoengseongContractError("GWE query is not allowlisted")
    if owner == "youth_center":
        if method != "GET" or host != "hsyouthcenter.hsg.go.kr" or parsed.path != "/bbs/board.php":
            raise HoengseongContractError("youth-center route changed")
        if query.get("bo_table") != "center":
            raise HoengseongContractError("support-scheme board is forbidden")
        if set(query) == {"bo_table"}:
            return "list"
        if set(query) == {"bo_table", "page"} and _POSITIVE.fullmatch(query.get("page", "")):
            return "list"
        if set(query) == {"bo_table", "wr_id"} and _POSITIVE.fullmatch(query.get("wr_id", "")):
            return "detail"
        raise HoengseongContractError("youth-center query is not allowlisted")
    if owner == "culture_school":
        if method == "GET" and url == HOENGSEONG_CULTURE_URL:
            return "list"
        raise HoengseongContractError("culture-school route is not allowlisted")
    if owner == "family_center":
        if host != "hsg.familynet.or.kr":
            raise HoengseongContractError("family-center host changed")
        if method == "GET" and parsed.path.endswith("/recruitReceipt/list.do"):
            if not query or (
                set(query) == {"rows", "cpage"}
                and query.get("rows") == str(HOENGSEONG_FAMILY_PAGE_SIZE)
                and _POSITIVE.fullmatch(query.get("cpage", ""))
            ):
                return "list"
        if method == "GET" and parsed.path == HOENGSEONG_FAMILY_DETAIL_PATH and set(query) == {"seq"} and _POSITIVE.fullmatch(query["seq"]):
            return "detail"
        if method == "POST" and url == HOENGSEONG_FAMILY_VIEW_API_URL:
            return "detail_api"
        raise HoengseongContractError("family-center route is not allowlisted")
    raise HoengseongContractError("unknown Hoengseong owner")


def _raw_session() -> requests.Session:
    current = requests.Session()
    current.trust_env = False
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/140 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return current


def _default_fetcher(
    session: Any,
    method: str,
    url: str,
    *,
    timeout: int,
    json_payload: Optional[Mapping[str, Any]] = None,
    headers: Optional[Mapping[str, str]] = None,
) -> Any:
    if method == "GET":
        return session.get(url, timeout=timeout, allow_redirects=False, headers=headers)
    if method == "POST":
        return session.post(
            url,
            json=dict(json_payload or {}),
            headers=dict(headers or {}),
            timeout=timeout,
            allow_redirects=False,
        )
    raise HoengseongContractError("unsupported HTTP method")


def _response_bytes(response: Any, requested_url: str, maximum: int) -> bytes:
    status = int(getattr(response, "status_code", 200) or 0)
    if status != 200:
        raise HoengseongContractError(f"HTTP {status}")
    if tuple(getattr(response, "history", ()) or ()):
        raise HoengseongContractError("redirect history is forbidden")
    headers = getattr(response, "headers", {}) or {}
    if any(str(key).lower() == "location" and value for key, value in headers.items()):
        raise HoengseongContractError("redirect location is forbidden")
    final_url = _clean(getattr(response, "url", ""))
    if final_url and final_url != requested_url:
        raise HoengseongContractError(f"response URL changed to {final_url!r}")
    content = getattr(response, "content", None)
    if content is None:
        text = getattr(response, "text", response)
        content = str(text).encode("utf-8")
    if not isinstance(content, (bytes, bytearray)):
        content = bytes(content)
    body = bytes(content)
    if not body or len(body) > maximum:
        raise HoengseongContractError(f"empty or oversized response ({len(body)} bytes)")
    return body


def _guard_access(soup: BeautifulSoup) -> None:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    text = _clean(soup.get_text(" ", strip=True))[:5000]
    lowered = f"{title} {text}".lower()
    tokens = (
        "access denied",
        "request rejected",
        "captcha",
        "cloudflare ray id",
        "접근이 제한",
        "서비스 이용이 제한",
        "비정상적인 접근",
    )
    if any(token in lowered for token in tokens):
        raise HoengseongContractError("source access restriction detected")


class _Requester:
    def __init__(
        self,
        owner: str,
        session_factory: SessionFactory,
        fetcher: Fetcher,
        timeout: int,
        meta: dict[str, Any],
    ) -> None:
        self.owner = owner
        self.session_factory = session_factory
        self.fetcher = fetcher
        self.timeout = timeout
        self.meta = meta
        self.local = threading.local()
        self.lock = threading.Lock()
        self.sessions: list[Any] = []

    def _session(self) -> Any:
        current = getattr(self.local, "session", None)
        if current is None:
            current = self.session_factory()
            self.local.session = current
            with self.lock:
                self.sessions.append(current)
        return current

    def _reset(self, attempt: int) -> None:
        with self.lock:
            self.meta["request_retry_count"] += 1
        current = getattr(self.local, "session", None)
        if current is not None:
            close = getattr(current, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        self.local.session = None
        time.sleep(min(1.0, 0.2 * (2**attempt)))

    def _request(
        self,
        method: str,
        url: str,
        *,
        json_payload: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        maximum: int,
    ) -> bytes:
        kind = _classify_url(self.owner, method, url)
        with self.lock:
            self.meta["logical_requests"] += 1
            key = {
                "list": "list_requests",
                "detail": "detail_requests",
                "detail_api": "detail_api_requests",
            }[kind]
            self.meta[key] += 1
        last: Optional[Exception] = None
        for attempt in range(3):
            with self.lock:
                self.meta["physical_requests"] += 1
            try:
                response = self.fetcher(
                    self._session(),
                    method,
                    url,
                    timeout=self.timeout,
                    json_payload=json_payload,
                    headers=headers,
                )
                status = int(getattr(response, "status_code", 200) or 0)
                if status in {408, 429, 500, 502, 503, 504} and attempt < 2:
                    self._reset(attempt)
                    continue
                return _response_bytes(response, url, maximum)
            except requests.RequestException as exc:
                last = exc
                if attempt < 2:
                    self._reset(attempt)
                    continue
                raise
        if last is not None:
            raise last
        raise HoengseongContractError("request retries exhausted")

    def soup(self, url: str) -> BeautifulSoup:
        soup = BeautifulSoup(
            self._request("GET", url, maximum=HOENGSEONG_MAX_HTML_BYTES),
            "html.parser",
        )
        _guard_access(soup)
        return soup

    def json(
        self,
        url: str,
        payload: Mapping[str, Any],
        headers: Mapping[str, str],
    ) -> Mapping[str, Any]:
        body = self._request(
            "POST",
            url,
            json_payload=payload,
            headers=headers,
            maximum=HOENGSEONG_MAX_JSON_BYTES,
        )
        try:
            parsed = json.loads(body.decode("utf-8-sig", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HoengseongContractError("invalid public-detail JSON") from exc
        if not isinstance(parsed, Mapping):
            raise HoengseongContractError("public-detail JSON root is not an object")
        return parsed

    def close(self) -> None:
        seen: set[int] = set()
        for current in self.sessions:
            if id(current) in seen:
                continue
            seen.add(id(current))
            close = getattr(current, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass


def _parallel_map(values: Sequence[Any], function: Callable[[Any], Any], workers: int) -> list[Any]:
    if not values:
        return []
    if workers <= 1 or len(values) == 1:
        return [function(value) for value in values]
    with ThreadPoolExecutor(max_workers=min(workers, len(values))) as pool:
        return list(pool.map(function, values))


def _date_pair(
    value: Any,
    field: str,
    *,
    default_year: Optional[int] = None,
    required: bool = True,
) -> Optional[tuple[date, date]]:
    text = _clean(value)
    found: list[date] = []
    active_year = default_year
    for match in _DATE_TOKEN.finditer(text):
        raw_year = match.group("year")
        if raw_year:
            year = int(raw_year)
            active_year = year + 2000 if year < 100 else year
        if active_year is None:
            continue
        try:
            found.append(
                date(active_year, int(match.group("month")), int(match.group("day")))
            )
        except ValueError as exc:
            raise HoengseongContractError(f"invalid {field} date") from exc
    if found:
        start, end = found[0], found[-1]
    else:
        month_match = _MONTH_RANGE.search(text)
        if month_match and default_year:
            start_month = int(month_match.group("start"))
            end_month = int(month_match.group("end"))
            if not (1 <= start_month <= end_month <= 12):
                raise HoengseongContractError(f"invalid {field} month range")
            start = date(default_year, start_month, 1)
            end = date(
                default_year,
                end_month,
                calendar.monthrange(default_year, end_month)[1],
            )
        elif required:
            raise HoengseongContractError(f"missing {field} date")
        else:
            return None
    if start > end:
        raise HoengseongContractError(f"reversed {field} period")
    return start, end


def _pairs(root: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for container in root.select("dl"):
        children = [
            node
            for node in container.find_all(["dt", "dd"], recursive=False)
        ]
        for index in range(0, len(children) - 1):
            if children[index].name != "dt" or children[index + 1].name != "dd":
                continue
            label = _clean(children[index].get_text(" ", strip=True)).rstrip(":")
            value = _clean(children[index + 1].get_text(" ", strip=True))
            if label in result:
                raise HoengseongContractError(f"duplicate detail field {label!r}")
            result[label] = value
    return result


def _table_pairs(table: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for tr in table.select("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if len(cells) < 2:
            continue
        for index in range(0, len(cells) - 1, 2):
            if cells[index].name != "th" or cells[index + 1].name != "td":
                continue
            label = _clean(cells[index].get_text(" ", strip=True)).rstrip(":")
            value = _clean(cells[index + 1].get_text(" ", strip=True))
            if label in result:
                raise HoengseongContractError(f"duplicate table field {label!r}")
            result[label] = value
    return result


def _capacity_numbers(value: Any) -> tuple[Optional[int], Optional[int], Optional[int]]:
    numbers = [int(item) for item in re.findall(r"\d+", _clean(value))]
    if len(numbers) < 2:
        return None, None, None
    return numbers[0], numbers[1], numbers[3] if len(numbers) >= 4 else None


def _status(value: Any) -> str:
    text = _clean(value)
    if any(token in text for token in ("대기자접수", "접수중", "모집중", "신청가능")):
        return "OPEN"
    if any(token in text for token in ("접수예정", "모집예정", "대기중")):
        return "SCHEDULED"
    if any(token in text for token in ("마감", "종료", "완료", "폐강", "취소")):
        return "CLOSED"
    return "PUBLISHED"


def _domain(title: str) -> tuple[str, str]:
    text = _clean(title).lower()
    experiential = (
        "체험",
        "만들기",
        "공예",
        "원데이",
        "키링",
        "팔찌",
        "케이크",
        "바디워시",
        "드립백",
        "티라미수",
        "업사이클",
        "메타버스",
        "vr",
        "3d art",
        "특수분장",
        "과학수사",
        "일기예보관",
        "인체해부",
        "반려동물전문가",
    )
    if any(token in text for token in experiential):
        return "체험·견학", "체험"
    return "교육·강좌", "공공강좌"


def _base_row(owner: str, identity: str, title: str, branch: str) -> dict[str, Any]:
    provider = HOENGSEONG_OWNERS[owner]["provider"]
    domain, service = _domain(title)
    return {
        "provider": provider,
        "municipality_code": HOENGSEONG_MUNICIPALITY_CODE,
        "municipality_name": HOENGSEONG_MUNICIPALITY_NAME,
        "provider_course_id": f"{provider}:{identity}",
        "source_course_id": identity,
        "title": title,
        "branch": branch,
        "preserve_branch": True,
        "application_url": "",
        "collection_category": "공공예약",
        "domain_category": domain,
        "source_group": "municipal_reservation",
        "service_group": service,
        "collection_type": "locked",
        "service_group_policy": "locked",
        "classification_locked": True,
        "operator_type": "지자체/공공기관",
    }


def _identity_hash(identities: Iterable[Any]) -> str:
    values = sorted(_clean(value) for value in identities)
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                key_text = str(key).lower()
                child_path = f"{path}.{key}" if path else str(key)
                if key_text in _FORBIDDEN_OUTPUT_KEYS:
                    errors.append(f"forbidden key {child_path}")
                walk(child, child_path)
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")
        elif isinstance(value, str) and (_PHONE.search(value) or _EMAIL.search(value)):
            errors.append(f"PII value in {path}")

    walk(row, "")
    return errors


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            output.append(row)
    return output


@dataclass(frozen=True)
class _Page:
    page: int
    rows: tuple[dict[str, Any], ...]
    declared_last: int
    empty: bool


def _page_signature(page: _Page) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            row.get("source_identity"),
            row.get("title"),
            row.get("apply_start"),
            row.get("apply_end"),
            row.get("start"),
            row.get("end"),
            row.get("source_status"),
            row.get("branch"),
        )
        for row in page.rows
    )


def _hsg_list_page(
    soup: BeautifulSoup,
    page: int,
    title_prefix: str,
    branch: str,
) -> _Page:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != f"{title_prefix} - 통합예약":
        raise HoengseongContractError(f"reservation page {page}: wrong title {title!r}")
    expected = (
        "No.",
        "강좌명",
        "접수기간/교육기간",
        "교육요일/시간",
        "선발방법",
        "신청/모집(대기자)",
        "신청방법",
        "접수상태",
    )
    matches: list[Any] = []
    for table in soup.select("table"):
        headers = tuple(
            re.sub(r"\s+", "", _clean(cell.get_text(" ", strip=True)))
            for cell in table.select("thead th")
        )
        if headers == expected:
            matches.append(table)
    if len(matches) != 1:
        raise HoengseongContractError(
            f"reservation page {page}: expected one course table, found {len(matches)}"
        )
    rows: list[dict[str, Any]] = []
    for tr in matches[0].select("tbody tr"):
        cells = tr.find_all("td", recursive=False)
        link = tr.select_one("a[href*='searchLctreRcritKey=']")
        if link is None:
            if len(cells) == 1 or not _clean(tr.get_text(" ", strip=True)):
                continue
            raise HoengseongContractError(f"reservation page {page}: ambiguous row")
        if len(cells) != 8:
            raise HoengseongContractError(f"reservation page {page}: row width changed")
        href = urljoin(HOENGSEONG_RESERVATION_URL, _clean(link.get("href")))
        parsed, query = _query(href)
        identity = query.get("searchLctreRcritKey", "")
        if (
            parsed.path != "/reserve/selectEdcCourseLctreRcritViewU.do"
            or not _POSITIVE.fullmatch(identity)
        ):
            raise HoengseongContractError(f"reservation page {page}: bad detail link")
        number = _clean(cells[0].get_text(" ", strip=True))
        if not _POSITIVE.fullmatch(number):
            raise HoengseongContractError(f"reservation page {page}: invalid row number")
        title_text = _clean(link.get_text(" ", strip=True))
        if not title_text or _PHONE.search(title_text) or _EMAIL.search(title_text):
            raise HoengseongContractError(f"reservation course {identity}: invalid title")
        apply_node = cells[2].select_one(".js")
        period_node = cells[2].select_one(".ky")
        if apply_node is None or period_node is None:
            raise HoengseongContractError(f"reservation course {identity}: period cells changed")
        apply_start, apply_end = _date_pair(
            apply_node.get_text(" ", strip=True), "application"
        ) or (None, None)
        start, end = _date_pair(period_node.get_text(" ", strip=True), "education") or (
            None,
            None,
        )
        source_status = _clean(cells[7].get_text(" ", strip=True))
        if source_status not in {"접수중", "대기자접수", "접수마감", "접수예정"}:
            raise HoengseongContractError(
                f"reservation course {identity}: unknown status {source_status!r}"
            )
        rows.append(
            {
                "source_identity": identity,
                "number": int(number),
                "title": title_text,
                "page": page,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "start": start,
                "end": end,
                "schedule": _clean(cells[3].get_text(" ", strip=True)),
                "selection": _clean(cells[4].get_text(" ", strip=True)),
                "capacity": _clean(cells[5].get_text(" ", strip=True)),
                "application_method": _clean(cells[6].get_text(" ", strip=True)),
                "source_status": source_status,
                "branch": branch,
            }
        )
    declared: list[int] = []
    for anchor in soup.select(".p-page__link[href]"):
        href = urljoin(HOENGSEONG_RESERVATION_URL, _clean(anchor.get("href")))
        parsed, query = _query(href)
        if parsed.path.endswith("selectEdcCourseLctreRcritListU.do"):
            value = query.get("pageIndex", "")
            if _POSITIVE.fullmatch(value):
                declared.append(int(value))
    declared_last = max(declared, default=(1 if rows else 0))
    active = soup.select_one(".p-page__link.active")
    if rows and active is not None:
        current_match = re.search(r"\d+", _clean(active.get_text(" ", strip=True)))
        if not current_match or int(current_match.group()) != page:
            raise HoengseongContractError(
                f"reservation page {page}: current-page marker mismatch"
            )
    return _Page(page, tuple(rows), declared_last, not rows)


def _read_hsg_ledger(
    requester: _Requester,
    url_builder: Callable[[int], str],
    parser: Callable[[BeautifulSoup, int], _Page],
    max_pages: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    first = parser(requester.soup(url_builder(1)), 1)
    if first.empty:
        recheck = parser(requester.soup(url_builder(1)), 1)
        if not recheck.empty or _page_signature(recheck) != _page_signature(first):
            raise HoengseongContractError("empty reservation category changed on recheck")
        return [], {
            "data_pages": 0,
            "page_counts": {},
            "empty_sentinel_page": 1,
            "stability_rechecks": 1,
        }
    last_page = first.declared_last
    if last_page < 1 or last_page + 1 > max_pages:
        raise HoengseongContractError(
            f"max_pages cap {max_pages} reached before reservation sentinel"
        )
    pages = {1: first}
    for page_number in range(2, last_page + 1):
        parsed = parser(requester.soup(url_builder(page_number)), page_number)
        if parsed.empty:
            raise HoengseongContractError("reservation pagination ended before declared last")
        pages[page_number] = parsed
    sentinel = parser(requester.soup(url_builder(last_page + 1)), last_page + 1)
    if not sentinel.empty:
        raise HoengseongContractError("reservation post-last sentinel is not empty")
    first_recheck = parser(requester.soup(url_builder(1)), 1)
    last_recheck = parser(requester.soup(url_builder(last_page)), last_page)
    sentinel_recheck = parser(
        requester.soup(url_builder(last_page + 1)), last_page + 1
    )
    if (
        _page_signature(first_recheck) != _page_signature(first)
        or _page_signature(last_recheck) != _page_signature(pages[last_page])
        or not sentinel_recheck.empty
    ):
        raise HoengseongContractError("reservation first/last/sentinel stability changed")
    for page_number, parsed in pages.items():
        if page_number < last_page and len(parsed.rows) != 10:
            raise HoengseongContractError("reservation page has premature short result")
        if not (1 <= len(parsed.rows) <= 10):
            raise HoengseongContractError("reservation page size changed")
    rows = [row for page in sorted(pages) for row in pages[page].rows]
    identities = [_clean(row["source_identity"]) for row in rows]
    if len(identities) != len(set(identities)):
        raise HoengseongContractError("duplicate reservation identity across pages")
    return rows, {
        "data_pages": len(pages),
        "page_counts": {page: len(value.rows) for page, value in sorted(pages.items())},
        "empty_sentinel_page": sentinel.page,
        "stability_rechecks": 3,
    }


def _hsg_detail(listed: Mapping[str, Any], soup: BeautifulSoup) -> dict[str, Any]:
    identity = _clean(listed.get("source_identity"))
    heading = soup.select_one("div.text_wrap > span.title")
    title = _clean(heading.get_text(" ", strip=True) if heading else "")
    if title != _clean(listed.get("title")):
        raise HoengseongContractError(f"reservation detail {identity}: title mismatch")
    status_node = soup.select_one("div.text_wrap > em[data-state]")
    source_status = _clean(status_node.get_text(" ", strip=True) if status_node else "")
    if source_status != _clean(listed.get("source_status")):
        raise HoengseongContractError(f"reservation detail {identity}: status mismatch")
    tables = soup.select("table.table.type2.responsive")
    if len(tables) != 1:
        raise HoengseongContractError(f"reservation detail {identity}: detail table changed")
    pairs = _table_pairs(tables[0])
    required = {
        "접수기간",
        "접수현황",
        "선발방법",
        "신청방법",
        "교육대상",
        "교육기간",
        "교육시간",
        "교육장",
        "수강료",
    }
    if not required.issubset(pairs):
        raise HoengseongContractError(f"reservation detail {identity}: fields incomplete")
    apply_start, apply_end = _date_pair(pairs["접수기간"], "application") or (None, None)
    start, end = _date_pair(pairs["교육기간"], "education") or (None, None)
    if (
        (apply_start, apply_end) != (listed.get("apply_start"), listed.get("apply_end"))
        or (start, end) != (listed.get("start"), listed.get("end"))
        or _clean(pairs["교육시간"]) != _clean(listed.get("schedule"))
        or _clean(pairs["신청방법"]) != _clean(listed.get("application_method"))
    ):
        raise HoengseongContractError(f"reservation detail {identity}: list/detail mismatch")
    venue_node = tables[0].select_one(".hsg_form_text")
    venue = _clean(
        venue_node.get_text(" ", strip=True) if venue_node else pairs.get("교육장")
    )
    if not venue or _PHONE.search(venue) or _EMAIL.search(venue):
        raise HoengseongContractError(f"reservation detail {identity}: invalid venue")
    current, capacity, waiting = _capacity_numbers(pairs["접수현황"])
    row = _base_row(
        "reservation",
        f"education:{identity}",
        title,
        _clean(listed.get("branch")),
    )
    row.update(
        {
            "raw_url": reservation_detail_url(identity, int(listed.get("page") or 1)),
            "program_type": "통합예약 교육강좌",
            "source_status": source_status,
            "status": _status(source_status),
            "reservation_available": _status(source_status) == "OPEN",
            "period": f"{start.isoformat()} ~ {end.isoformat()}",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "apply_period": f"{apply_start.isoformat()} ~ {apply_end.isoformat()}",
            "apply_start_date": apply_start.isoformat(),
            "apply_end_date": apply_end.isoformat(),
            "schedule_raw": _clean(pairs["교육시간"]),
            "target": _clean(pairs["교육대상"]),
            "venue_name": venue,
            "fee": _clean(pairs["수강료"]),
            "application_method_raw": _clean(pairs["신청방법"]),
            "capacity_current": current,
            "capacity_total": capacity,
            "capacity_wait_total": waiting,
        }
    )
    return row


def _verify_hsg_experience_empty(requester: _Requester) -> dict[str, Any]:
    expected_headers = (
        "No.",
        "카테고리",
        "체험견학명",
        "체험신청인원",
        "체험비(원)",
        "예약신청",
    )
    branches: dict[str, int] = {}
    for key, branch, url, expected_title in HOENGSEONG_EXPERIENCE_LEDGERS:
        signatures: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []
        for _ in range(2):
            soup = requester.soup(url)
            title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
            tables = []
            for table in soup.select("table"):
                headers = tuple(
                    re.sub(r"\s+", "", _clean(node.get_text(" ", strip=True)))
                    for node in table.select("thead th")
                )
                if headers == expected_headers:
                    tables.append(table)
            if title != expected_title or len(tables) != 1:
                raise HoengseongContractError(f"experience ledger {branch}: contract changed")
            body_rows = tuple(
                _clean(tr.get_text(" ", strip=True)) for tr in tables[0].select("tbody tr")
            )
            if body_rows != ("검색결과가 없습니다.",):
                raise HoengseongContractError(
                    f"experience ledger {branch}: non-empty rows require detail audit"
                )
            signatures.append((title, expected_headers, body_rows))
        if signatures[0] != signatures[1]:
            raise HoengseongContractError(f"experience ledger {branch}: sentinel changed")
        branches[key] = 0
    return {
        "experience_source_count": 0,
        "experience_current_count": 0,
        "experience_branch_counts": branches,
        "experience_empty_sentinels_verified": len(HOENGSEONG_EXPERIENCE_LEDGERS),
    }


def _collect_reservation(
    requester: _Requester,
    cutoff: date,
    max_pages: int,
    detail_limit: int,
    workers: int,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    all_rows, all_audit = _read_hsg_ledger(
        requester,
        reservation_list_url,
        lambda soup, page: _hsg_list_page(soup, page, "전체", ""),
        max_pages,
    )
    if not all_rows:
        raise HoengseongContractError("integrated reservation all-list unexpectedly empty")
    numbers = [int(row["number"]) for row in all_rows]
    if numbers != list(range(numbers[0], 0, -1)) or numbers[0] != len(all_rows):
        raise HoengseongContractError("integrated reservation row numbering is incomplete")

    def read_category(
        category: _HsgCategory,
    ) -> tuple[_HsgCategory, list[dict[str, Any]], dict[str, Any]]:
        category_rows, category_audit = _read_hsg_ledger(
            requester,
            lambda page, category=category: reservation_category_url(category, page),
            lambda soup, page, category=category: _hsg_list_page(
                soup,
                page,
                category.page_title,
                category.branch,
            ),
            max_pages,
        )
        return category, category_rows, category_audit

    union_rows: list[dict[str, Any]] = []
    category_audits: dict[str, Any] = {}
    for category, category_rows, category_audit in _parallel_map(
        HOENGSEONG_RESERVATION_CATEGORIES,
        read_category,
        workers,
    ):
        category_audits[category.branch] = {
            **category_audit,
            "source_count": len(category_rows),
        }
        union_rows.extend(category_rows)

    union_ids = [_clean(row["source_identity"]) for row in union_rows]
    if len(union_ids) != len(set(union_ids)):
        raise HoengseongContractError("course appears in more than one official category")
    all_by_id = {_clean(row["source_identity"]): row for row in all_rows}
    union_by_id = {_clean(row["source_identity"]): row for row in union_rows}
    missing = set(union_by_id) - set(all_by_id)
    if missing:
        raise HoengseongContractError("category union contains courses missing from all-list")
    extras = set(all_by_id) - set(union_by_id)
    expected_test_ids = {"225", "572"}
    if not extras.issubset(expected_test_ids) or any(
        _clean(all_by_id[identity]["title"]) != "강좌1" for identity in extras
    ):
        raise HoengseongContractError("unregistered category rows escaped exact test quarantine")
    comparison_keys = (
        "title",
        "apply_start",
        "apply_end",
        "start",
        "end",
        "schedule",
        "source_status",
    )
    for identity, category_row in union_by_id.items():
        if any(all_by_id[identity].get(key) != category_row.get(key) for key in comparison_keys):
            raise HoengseongContractError(
                f"reservation course {identity}: all/category replica mismatch"
            )
        category_row["page"] = all_by_id[identity]["page"]

    current = [row for row in union_rows if row["end"] >= cutoff]
    if len(current) > detail_limit:
        raise HoengseongContractError(
            f"detail_limit cap allows {detail_limit} of {len(current)} reservation details"
        )

    def fetch_detail(listed: Mapping[str, Any]) -> dict[str, Any]:
        identity = _clean(listed.get("source_identity"))
        return _hsg_detail(
            listed,
            requester.soup(reservation_detail_url(identity, int(listed.get("page") or 1))),
        )

    rows = _parallel_map(current, fetch_detail, workers)
    experience_audit = _verify_hsg_experience_empty(requester)
    source_statuses = Counter(_clean(row["source_status"]) for row in union_rows)
    branch_counts = Counter(_clean(row["branch"]) for row in union_rows)
    audit = {
        "source_rows": len(union_rows),
        "all_list_source_rows": len(all_rows),
        "quarantined_test_rows": len(extras),
        "quarantined_test_ids": sorted(extras, key=int),
        "current_source_count": len(current),
        "expired_source_count": len(union_rows) - len(current),
        "source_status_counts": dict(source_statuses),
        "source_branch_counts": dict(branch_counts),
        "category_audits": category_audits,
        "data_pages": all_audit["data_pages"],
        "page_counts": all_audit["page_counts"],
        "empty_sentinel_page": all_audit["empty_sentinel_page"],
        "empty_sentinel_verified": True,
        "stability_rechecks": all_audit["stability_rechecks"]
        + sum(item["stability_rechecks"] for item in category_audits.values()),
        "detail_verified": len(rows),
        "detail_transport": "public_detail_pages",
        **experience_audit,
    }
    return rows, union_ids, audit


def _library_list_page(soup: BeautifulSoup, *, festival: bool = False) -> list[dict[str, Any]]:
    expected_title = (
        "횡성군립도서관 - 책축제 프로그램 신청"
        if festival
        else "횡성군립도서관 - 프로그램 신청"
    )
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != expected_title:
        raise HoengseongContractError(f"municipal-library wrong title {title!r}")
    matches: list[Any] = []
    expected_headers = ("강좌명", "모집인원/대상", "접수일/수강일", "접수현황")
    for table in soup.select("table.tstyle.responsive"):
        headers = tuple(_clean(th.get_text(" ", strip=True)) for th in table.select("thead th"))
        if headers == expected_headers:
            matches.append(table)
    if len(matches) != 1:
        raise HoengseongContractError("municipal-library programme table changed")
    rows: list[dict[str, Any]] = []
    for tr in matches[0].select("tbody tr"):
        cells = tr.find_all("td", recursive=False)
        if not cells:
            continue
        if len(cells) != 4:
            raise HoengseongContractError("municipal-library row width changed")
        link = cells[0].select_one("a[href*='act=lecture_view']")
        if link is None:
            raise HoengseongContractError("municipal-library detail link missing")
        href = urljoin(HOENGSEONG_LIBRARY_URL, _clean(link.get("href")))
        parsed, query = _query(href)
        identity = query.get("leCode", "")
        if (
            parsed.hostname != "lib.hsg.go.kr"
            or query.get("act") != "lecture_view"
            or query.get("lgCode") != "9"
            or not _POSITIVE.fullmatch(identity)
        ):
            raise HoengseongContractError("municipal-library detail identity changed")
        title_text = _clean(link.get_text(" ", strip=True))
        if not title_text or _PHONE.search(title_text) or _EMAIL.search(title_text):
            raise HoengseongContractError(f"municipal-library {identity}: invalid title")
        apply_node = cells[2].select_one(".red.fb")
        period_node = cells[2].select_one(".blue.fb")
        if apply_node is None or period_node is None:
            raise HoengseongContractError(f"municipal-library {identity}: period cells changed")
        apply_start, apply_end = _date_pair(
            apply_node.get_text(" ", strip=True), "library application"
        ) or (None, None)
        start, end = _date_pair(
            period_node.get_text(" ", strip=True), "library course"
        ) or (None, None)
        state = cells[3].select_one("span.type")
        source_status = _clean(state.get_text(" ", strip=True) if state else "")
        if source_status not in {
            "대기중",
            "접수중",
            "접수마감",
            "신청대기",
            "신청가능",
        }:
            raise HoengseongContractError(
                f"municipal-library {identity}: unknown status {source_status!r}"
            )
        total_text = _clean(cells[1].get_text(" ", strip=True))
        capacity_match = re.search(r"(\d+)명\s*모집", total_text)
        current_match = re.search(r"(\d+)명\s*신청", total_text)
        rows.append(
            {
                "source_identity": identity,
                "title": title_text,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "start": start,
                "end": end,
                "source_status": source_status,
                "capacity_current": int(current_match.group(1)) if current_match else None,
                "capacity_total": int(capacity_match.group(1)) if capacity_match else None,
            }
        )
    pager = soup.select_one("div.paging")
    if pager is None or _clean(pager.get_text(" ", strip=True)) != "1" or pager.select("a[href]"):
        raise HoengseongContractError("municipal-library single-page declaration changed")
    return rows


def _library_detail(listed: Mapping[str, Any], soup: BeautifulSoup) -> dict[str, Any]:
    identity = _clean(listed.get("source_identity"))
    root = soup.select_one(".lecture_view")
    if root is None:
        raise HoengseongContractError(f"municipal-library detail {identity}: shell missing")
    heading = root.select_one("h4")
    title = _clean(heading.get_text(" ", strip=True) if heading else "")
    if title != _clean(listed.get("title")):
        raise HoengseongContractError(f"municipal-library detail {identity}: title mismatch")
    pairs = _pairs(root)
    required = {
        "대상",
        "인터넷 모집인원",
        "현재신청자수",
        "대기자 모집인원",
        "수강료",
        "접수 기간",
        "강좌 기간",
        "강좌 일시",
        "강좌 장소",
    }
    if not required.issubset(pairs):
        raise HoengseongContractError(f"municipal-library detail {identity}: fields incomplete")
    apply_start, apply_end = _date_pair(pairs["접수 기간"], "library application") or (
        None,
        None,
    )
    start, end = _date_pair(pairs["강좌 기간"], "library course") or (None, None)
    if (
        (apply_start, apply_end) != (listed.get("apply_start"), listed.get("apply_end"))
        or (start, end) != (listed.get("start"), listed.get("end"))
    ):
        raise HoengseongContractError(f"municipal-library detail {identity}: date mismatch")
    capacity = int(re.search(r"\d+", pairs["인터넷 모집인원"]).group())
    current = int(re.search(r"\d+", pairs["현재신청자수"]).group())
    if capacity != listed.get("capacity_total") or current != listed.get("capacity_current"):
        raise HoengseongContractError(f"municipal-library detail {identity}: capacity mismatch")
    venue = _clean(pairs["강좌 장소"])
    if venue in {"", "0", "-"}:
        venue = "횡성군립도서관"
    row = _base_row(
        "municipal_library",
        f"library:{identity}",
        title,
        "횡성군립도서관",
    )
    row.update(
        {
            "raw_url": library_detail_url(identity),
            "program_type": "도서관 프로그램",
            "source_status": _clean(listed.get("source_status")),
            "status": _status(listed.get("source_status")),
            "reservation_available": _status(listed.get("source_status")) == "OPEN",
            "period": f"{start.isoformat()} ~ {end.isoformat()}",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "apply_period": f"{apply_start.isoformat()} ~ {apply_end.isoformat()}",
            "apply_start_date": apply_start.isoformat(),
            "apply_end_date": apply_end.isoformat(),
            "schedule_raw": _clean(pairs["강좌 일시"]),
            "target": _clean(pairs["대상"]),
            "venue_name": venue,
            "fee": _clean(pairs["수강료"]),
            "application_method_raw": "온라인",
            "capacity_current": current,
            "capacity_total": capacity,
            "capacity_wait_total": int(re.search(r"\d+", pairs["대기자 모집인원"]).group()),
        }
    )
    return row


def _collect_municipal_library(
    requester: _Requester,
    cutoff: date,
    max_pages: int,
    detail_limit: int,
    workers: int,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    if max_pages < 2:
        raise HoengseongContractError("max_pages cap prevents library sentinel audit")
    first = _library_list_page(requester.soup(HOENGSEONG_LIBRARY_URL))
    sentinel = _library_list_page(
        requester.soup(HOENGSEONG_LIBRARY_FESTIVAL_URL), festival=True
    )
    first_recheck = _library_list_page(requester.soup(HOENGSEONG_LIBRARY_URL))
    sentinel_recheck = _library_list_page(
        requester.soup(HOENGSEONG_LIBRARY_FESTIVAL_URL), festival=True
    )
    first_signature = tuple(
        (row["source_identity"], row["title"], row["start"], row["end"])
        for row in first
    )
    if (
        first_signature
        != tuple(
            (row["source_identity"], row["title"], row["start"], row["end"])
            for row in first_recheck
        )
        or sentinel
        or sentinel_recheck
    ):
        raise HoengseongContractError("municipal-library first/sentinel stability changed")
    identities = [_clean(row["source_identity"]) for row in first]
    if len(identities) != len(set(identities)):
        raise HoengseongContractError("duplicate municipal-library identity")
    if identities and [int(value) for value in identities] != sorted(
        (int(value) for value in identities), reverse=True
    ):
        raise HoengseongContractError("municipal-library identities lost descending order")
    current = [row for row in first if row["end"] >= cutoff]
    if len(current) > detail_limit:
        raise HoengseongContractError(
            f"detail_limit cap allows {detail_limit} of {len(current)} library details"
        )

    def fetch_detail(listed: Mapping[str, Any]) -> dict[str, Any]:
        identity = _clean(listed.get("source_identity"))
        return _library_detail(listed, requester.soup(library_detail_url(identity)))

    rows = _parallel_map(current, fetch_detail, workers)
    return rows, identities, {
        "source_rows": len(first),
        "current_source_count": len(current),
        "expired_source_count": len(first) - len(current),
        "data_pages": 1,
        "page_counts": {1: len(first)},
        "empty_sentinel_page": "book_festival_subledger",
        "empty_sentinel_verified": True,
        "stability_rechecks": 2,
        "detail_verified": len(rows),
        "detail_transport": "public_detail_pages",
        "result_registration_links_ignored": len(first),
        "source_branch_counts": {"횡성군립도서관": len(first)},
    }


def _gwe_page(soup: BeautifulSoup, page: int) -> _Page:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != "프로그램신청":
        raise HoengseongContractError(f"GWE page {page}: wrong title {title!r}")
    root = soup.select_one("ul.lecture_result_list")
    if root is None:
        raise HoengseongContractError(f"GWE page {page}: result list missing")
    items = root.select(":scope > li.lecture_item")
    no_data = root.select(":scope > li.no_data")
    if not items:
        marker = _clean(no_data[0].get_text(" ", strip=True)) if len(no_data) == 1 else ""
        if marker != "조회되는 문화강좌가 없습니다.":
            raise HoengseongContractError(f"GWE page {page}: ambiguous empty result")
        return _Page(page, tuple(), 1, True)
    if no_data:
        raise HoengseongContractError(f"GWE page {page}: rows and empty marker coexist")
    rows: list[dict[str, Any]] = []
    for item in items:
        link = item.select_one(".lecture_item__title a[href]")
        if link is None:
            raise HoengseongContractError(f"GWE page {page}: detail link missing")
        href = urljoin(HOENGSEONG_GWE_URL, _clean(link.get("href")))
        match = re.fullmatch(
            r"https://lib[.]gwe[.]go[.]kr/hslib/menu/2958/lecture-event/([1-9]\d*)",
            href,
        )
        if not match:
            raise HoengseongContractError(f"GWE page {page}: detail route changed")
        identity = match.group(1)
        title_text = _clean(link.get_text(" ", strip=True))
        branch_node = item.select_one(".lecture_item__library")
        branch = _clean(branch_node.get_text(" ", strip=True) if branch_node else "")
        if branch != "횡성교육도서관":
            raise HoengseongContractError(f"GWE course {identity}: wrong library {branch!r}")
        pairs = _pairs(item)
        if not {"신청기간", "운영기간", "신청대상", "모집방법", "모집인원"}.issubset(pairs):
            raise HoengseongContractError(f"GWE course {identity}: fields incomplete")
        apply_start, apply_end = _date_pair(pairs["신청기간"], "GWE application") or (
            None,
            None,
        )
        start, end = _date_pair(pairs["운영기간"], "GWE operation") or (None, None)
        state = item.select_one(".lecture_item__button > button:first-of-type")
        source_status = _clean(state.get_text(" ", strip=True) if state else "")
        if source_status not in {"접수예정", "접수중", "대기자접수", "신청마감"}:
            raise HoengseongContractError(f"GWE course {identity}: unknown status")
        current_capacity, capacity, waiting = _capacity_numbers(pairs["모집인원"])
        rows.append(
            {
                "source_identity": identity,
                "title": title_text,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "start": start,
                "end": end,
                "source_status": source_status,
                "target": _clean(pairs["신청대상"]),
                "application_method": _clean(pairs["모집방법"]),
                "capacity_current": current_capacity,
                "capacity_total": capacity,
                "capacity_wait_total": waiting,
                "branch": branch,
            }
        )
    return _Page(page, tuple(rows), 1, False)


def _gwe_detail(listed: Mapping[str, Any], soup: BeautifulSoup) -> dict[str, Any]:
    identity = _clean(listed.get("source_identity"))
    root = soup.select_one(".lecture_detail")
    heading = soup.select_one(".lecture_detail__title")
    if root is None or heading is None:
        raise HoengseongContractError(f"GWE detail {identity}: shell missing")
    heading_text = _clean(heading.get_text(" ", strip=True))
    title = _clean(listed.get("title"))
    if not heading_text.startswith(title):
        raise HoengseongContractError(f"GWE detail {identity}: title mismatch")
    pairs = _pairs(root)
    required = {
        "도서관",
        "운영기간",
        "운영시간",
        "신청방법",
        "신청기간",
        "신청대상",
        "모집인원",
        "재료비",
        "참가비",
        "장소",
    }
    if not required.issubset(pairs) or _clean(pairs["도서관"]) != "횡성교육도서관":
        raise HoengseongContractError(f"GWE detail {identity}: fields/owner changed")
    apply_start, apply_end = _date_pair(pairs["신청기간"], "GWE application") or (
        None,
        None,
    )
    start, end = _date_pair(pairs["운영기간"], "GWE operation") or (None, None)
    if (
        (apply_start, apply_end) != (listed.get("apply_start"), listed.get("apply_end"))
        or (start, end) != (listed.get("start"), listed.get("end"))
        or _clean(pairs["신청대상"]) != _clean(listed.get("target"))
    ):
        raise HoengseongContractError(f"GWE detail {identity}: list/detail mismatch")
    current_capacity, capacity, waiting = _capacity_numbers(pairs["모집인원"])
    if (current_capacity, capacity, waiting) != (
        listed.get("capacity_current"),
        listed.get("capacity_total"),
        listed.get("capacity_wait_total"),
    ):
        raise HoengseongContractError(f"GWE detail {identity}: capacity mismatch")
    venue = _clean(pairs["장소"])
    if not venue or _PHONE.search(venue) or _EMAIL.search(venue):
        raise HoengseongContractError(f"GWE detail {identity}: invalid venue")
    material = _clean(pairs["재료비"])
    participation = _clean(pairs["참가비"])
    fee = "무료" if material in {"", "-", "없음", "무료"} and participation in {"", "-", "없음", "무료"} else _clean(f"재료비 {material} 참가비 {participation}")
    row = _base_row(
        "education_library",
        f"gwe:{identity}",
        title,
        "횡성교육도서관",
    )
    row.update(
        {
            "raw_url": gwe_detail_url(identity),
            "program_type": "교육도서관 프로그램",
            "source_status": _clean(listed.get("source_status")),
            "status": _status(listed.get("source_status")),
            "reservation_available": _status(listed.get("source_status")) == "OPEN",
            "period": f"{start.isoformat()} ~ {end.isoformat()}",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "apply_period": f"{apply_start.isoformat()} ~ {apply_end.isoformat()}",
            "apply_start_date": apply_start.isoformat(),
            "apply_end_date": apply_end.isoformat(),
            "schedule_raw": _clean(pairs["운영시간"]),
            "target": _clean(pairs["신청대상"]),
            "venue_name": venue,
            "fee": fee,
            "application_method_raw": _clean(pairs["신청방법"]),
            "capacity_current": capacity and current_capacity,
            "capacity_total": capacity,
            "capacity_wait_total": waiting,
        }
    )
    return row


def _collect_gwe(
    requester: _Requester,
    cutoff: date,
    max_pages: int,
    detail_limit: int,
    workers: int,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    if max_pages < 2:
        raise HoengseongContractError("max_pages cap prevents GWE sentinel audit")
    first = _gwe_page(requester.soup(gwe_list_url(1)), 1)
    sentinel = _gwe_page(requester.soup(gwe_list_url(2)), 2)
    first_recheck = _gwe_page(requester.soup(gwe_list_url(1)), 1)
    sentinel_recheck = _gwe_page(requester.soup(gwe_list_url(2)), 2)
    if (
        first.empty
        or not sentinel.empty
        or _page_signature(first) != _page_signature(first_recheck)
        or not sentinel_recheck.empty
    ):
        raise HoengseongContractError("GWE first/sentinel stability changed")
    source = list(first.rows)
    identities = [_clean(row["source_identity"]) for row in source]
    if len(identities) != len(set(identities)):
        raise HoengseongContractError("duplicate GWE identity")
    current = [row for row in source if row["end"] >= cutoff]
    if len(current) > detail_limit:
        raise HoengseongContractError(
            f"detail_limit cap allows {detail_limit} of {len(current)} GWE details"
        )

    def fetch_detail(listed: Mapping[str, Any]) -> dict[str, Any]:
        identity = _clean(listed.get("source_identity"))
        return _gwe_detail(listed, requester.soup(gwe_detail_url(identity)))

    rows = _parallel_map(current, fetch_detail, workers)
    return rows, identities, {
        "source_rows": len(source),
        "current_source_count": len(current),
        "expired_source_count": len(source) - len(current),
        "data_pages": 1,
        "page_counts": {1: len(source)},
        "empty_sentinel_page": 2,
        "empty_sentinel_verified": True,
        "stability_rechecks": 2,
        "detail_verified": len(rows),
        "detail_transport": "public_detail_pages",
        "source_branch_counts": {"횡성교육도서관": len(source)},
    }


def _youth_page(soup: BeautifulSoup, page: int) -> _Page:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != "횡성군 청년센터":
        raise HoengseongContractError(f"youth page {page}: wrong title {title!r}")
    root = soup.select_one("ul.board_gallery_list")
    form = soup.select_one("form#fboardlist")
    if root is None or form is None:
        raise HoengseongContractError(f"youth page {page}: programme list/form missing")
    page_node = form.select_one("input[name=page]")
    board_node = form.select_one("input[name=bo_table]")
    if (
        page_node is None
        or _clean(page_node.get("value")) != str(page)
        or board_node is None
        or _clean(board_node.get("value")) != "center"
    ):
        raise HoengseongContractError(f"youth page {page}: owner/page form changed")
    rows: list[dict[str, Any]] = []
    for li in root.select(":scope > li"):
        link = li.select_one("a[href*='wr_id=']")
        if link is None:
            raise HoengseongContractError(f"youth page {page}: detail link missing")
        href = urljoin(HOENGSEONG_YOUTH_URL, _clean(link.get("href")))
        parsed, query = _query(href)
        identity = query.get("wr_id", "")
        if (
            parsed.hostname != "hsyouthcenter.hsg.go.kr"
            or query.get("bo_table") != "center"
            or not _POSITIVE.fullmatch(identity)
        ):
            raise HoengseongContractError(f"youth page {page}: detail route changed")
        title_node = li.select_one(".text .tit")
        title_text = _clean(title_node.get_text(" ", strip=True) if title_node else "")
        if not title_text or _PHONE.search(title_text) or _EMAIL.search(title_text):
            raise HoengseongContractError(f"youth course {identity}: invalid title")
        fields: dict[str, str] = {}
        for block in li.select(".text .date_list"):
            label_node = block.select_one(".icon p")
            paragraphs = block.find_all("p")
            label = _clean(label_node.get_text(" ", strip=True) if label_node else "")
            value = _clean(paragraphs[-1].get_text(" ", strip=True)) if paragraphs else ""
            if label in fields:
                raise HoengseongContractError(f"youth course {identity}: duplicate {label}")
            fields[label] = value
        if set(fields) != {"모집기간", "교육일정"}:
            raise HoengseongContractError(f"youth course {identity}: list fields changed")
        apply_start, apply_end = _date_pair(fields["모집기간"], "youth application") or (
            None,
            None,
        )
        start, end = _date_pair(
            fields["교육일정"],
            "youth education",
            default_year=apply_start.year,
        ) or (None, None)
        capacity_text = _clean(
            (li.select_one(".member_list .count") or li).get_text(" ", strip=True)
        )
        numbers = [int(item) for item in re.findall(r"\d+", capacity_text)]
        current = numbers[0] if numbers else None
        capacity = numbers[1] if len(numbers) > 1 else None
        source_status = "모집마감" if "end" in set(li.get("class", ())) else "모집중"
        rows.append(
            {
                "source_identity": identity,
                "title": title_text,
                "page": page,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "start": start,
                "end": end,
                "schedule": fields["교육일정"],
                "source_status": source_status,
                "capacity_current": current,
                "capacity_total": capacity,
                "branch": "횡성군 청년센터",
            }
        )
    pager_values: list[int] = []
    for anchor in soup.select("a[href*='bo_table=center'][href*='page=']"):
        _, query = _query(urljoin(HOENGSEONG_YOUTH_URL, _clean(anchor.get("href"))))
        value = query.get("page", "")
        if _POSITIVE.fullmatch(value):
            pager_values.append(int(value))
    declared = max(pager_values, default=(1 if rows else 0))
    return _Page(page, tuple(rows), declared, not rows)


def _youth_detail(listed: Mapping[str, Any], soup: BeautifulSoup) -> dict[str, Any]:
    identity = _clean(listed.get("source_identity"))
    root = soup.select_one(".board_view")
    title_node = soup.select_one(".board_view .title_bx .tit > p")
    if root is None or title_node is None:
        raise HoengseongContractError(f"youth detail {identity}: shell missing")
    title = _clean(title_node.get_text(" ", strip=True))
    if title != _clean(listed.get("title")):
        raise HoengseongContractError(f"youth detail {identity}: title mismatch")
    info = root.select_one("dl.info_bx")
    if info is None:
        raise HoengseongContractError(f"youth detail {identity}: info block missing")
    pairs: dict[str, str] = {}
    for block in info.select(":scope > div"):
        dt = block.select_one("dt")
        dd = block.select_one("dd")
        if dt is None or dd is None:
            raise HoengseongContractError(f"youth detail {identity}: malformed field")
        label = _clean(dt.get_text(" ", strip=True))
        if label in pairs:
            raise HoengseongContractError(f"youth detail {identity}: duplicate field")
        pairs[label] = _clean(dd.get_text(" ", strip=True))
    required = {"모집기간", "교육일정", "모집인원", "신청대상", "교육장소", "수강료"}
    if not required.issubset(pairs):
        raise HoengseongContractError(f"youth detail {identity}: fields incomplete")
    apply_start, apply_end = _date_pair(pairs["모집기간"], "youth application") or (
        None,
        None,
    )
    start, end = _date_pair(
        pairs["교육일정"], "youth education", default_year=apply_start.year
    ) or (None, None)
    if (
        (apply_start, apply_end) != (listed.get("apply_start"), listed.get("apply_end"))
        or (start, end) != (listed.get("start"), listed.get("end"))
    ):
        raise HoengseongContractError(f"youth detail {identity}: list/detail mismatch")
    capacity_match = re.search(r"\d+", pairs["모집인원"])
    capacity = int(capacity_match.group()) if capacity_match else None
    if capacity != listed.get("capacity_total"):
        raise HoengseongContractError(f"youth detail {identity}: capacity mismatch")
    venue = _clean(pairs["교육장소"])
    target = _clean(pairs["신청대상"])
    if (
        not venue
        or not target
        or _PHONE.search(venue)
        or _EMAIL.search(venue)
        or _PHONE.search(target)
        or _EMAIL.search(target)
    ):
        raise HoengseongContractError(f"youth detail {identity}: invalid public fields")
    row = _base_row(
        "youth_center",
        f"youth:{identity}",
        title,
        "횡성군 청년센터",
    )
    row.update(
        {
            "raw_url": youth_detail_url(identity),
            "program_type": "청년센터 프로그램",
            "source_status": _clean(listed.get("source_status")),
            "status": _status(listed.get("source_status")),
            "reservation_available": _status(listed.get("source_status")) == "OPEN",
            "period": f"{start.isoformat()} ~ {end.isoformat()}",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "apply_period": f"{apply_start.isoformat()} ~ {apply_end.isoformat()}",
            "apply_start_date": apply_start.isoformat(),
            "apply_end_date": apply_end.isoformat(),
            "schedule_raw": _clean(pairs["교육일정"]),
            "target": target,
            "venue_name": venue,
            "fee": _clean(pairs["수강료"]),
            "application_method_raw": "온라인 신청(로그인 필요)",
            "capacity_current": listed.get("capacity_current"),
            "capacity_total": capacity,
        }
    )
    return row


def _collect_youth(
    requester: _Requester,
    cutoff: date,
    max_pages: int,
    detail_limit: int,
    workers: int,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    first = _youth_page(requester.soup(youth_list_url(1)), 1)
    if first.empty:
        raise HoengseongContractError("youth programme ledger unexpectedly empty")
    declared = max(first.declared_last, 1)
    if declared + 1 > max_pages:
        raise HoengseongContractError(
            f"max_pages cap {max_pages} reached before youth sentinel"
        )
    pages = {1: first}
    for page_number in range(2, declared + 1):
        page = _youth_page(requester.soup(youth_list_url(page_number)), page_number)
        if page.empty:
            raise HoengseongContractError("youth pagination ended before declared last")
        pages[page_number] = page
    # GnuBoard can omit pager controls when page size changes. Continue until the
    # first exact empty gallery, bounded by max_pages.
    page_number = declared + 1
    while page_number <= max_pages:
        page = _youth_page(requester.soup(youth_list_url(page_number)), page_number)
        if page.empty:
            sentinel = page
            break
        pages[page_number] = page
        page_number += 1
    else:
        raise HoengseongContractError(
            f"max_pages cap {max_pages} reached before youth sentinel"
        )
    first_recheck = _youth_page(requester.soup(youth_list_url(1)), 1)
    last_number = max(pages)
    last_recheck = _youth_page(requester.soup(youth_list_url(last_number)), last_number)
    sentinel_recheck = _youth_page(
        requester.soup(youth_list_url(sentinel.page)), sentinel.page
    )
    if (
        _page_signature(first_recheck) != _page_signature(first)
        or _page_signature(last_recheck) != _page_signature(pages[last_number])
        or not sentinel_recheck.empty
    ):
        raise HoengseongContractError("youth first/last/sentinel stability changed")
    source = [row for number in sorted(pages) for row in pages[number].rows]
    identities = [_clean(row["source_identity"]) for row in source]
    if len(identities) != len(set(identities)):
        raise HoengseongContractError("duplicate youth identity")
    if [int(value) for value in identities] != sorted(
        (int(value) for value in identities), reverse=True
    ):
        raise HoengseongContractError("youth identities lost descending order")
    current = [row for row in source if row["end"] >= cutoff]
    if len(current) > detail_limit:
        raise HoengseongContractError(
            f"detail_limit cap allows {detail_limit} of {len(current)} youth details"
        )

    def fetch_detail(listed: Mapping[str, Any]) -> dict[str, Any]:
        identity = _clean(listed.get("source_identity"))
        return _youth_detail(listed, requester.soup(youth_detail_url(identity)))

    rows = _parallel_map(current, fetch_detail, workers)
    return rows, identities, {
        "source_rows": len(source),
        "current_source_count": len(current),
        "expired_source_count": len(source) - len(current),
        "data_pages": len(pages),
        "page_counts": {number: len(page.rows) for number, page in sorted(pages.items())},
        "empty_sentinel_page": sentinel.page,
        "empty_sentinel_verified": True,
        "stability_rechecks": 3,
        "detail_verified": len(rows),
        "detail_transport": "public_detail_pages",
        "source_branch_counts": {"횡성군 청년센터": len(source)},
    }


def _next_paragraph(heading: Any) -> Any:
    node = heading.find_next_sibling() if heading is not None else None
    while node is not None and getattr(node, "name", None) is None:
        node = node.find_next_sibling()
    return node


def _culture_snapshot(soup: BeautifulSoup, cutoff: date) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != "횡성문화원":
        raise HoengseongContractError(f"culture-school wrong title {title!r}")
    headings = {
        _clean(node.get_text(" ", strip=True)): node for node in soup.select(".culWrap > h2")
    }
    required_headings = {"학교명", "모집 개요", "운영 기간", "강좌별 운영현황"}
    if not required_headings.issubset(headings):
        raise HoengseongContractError("culture-school sections changed")
    school_node = _next_paragraph(headings["학교명"])
    recruit_node = _next_paragraph(headings["모집 개요"])
    operation_node = _next_paragraph(headings["운영 기간"])
    school = _clean(school_node.get_text(" ", strip=True) if school_node else "")
    recruit = _clean(recruit_node.get_text(" ", strip=True) if recruit_node else "")
    operation = _clean(operation_node.get_text(" ", strip=True) if operation_node else "")
    cohort_match = re.fullmatch(r"횡성문화원 문화학교 제([1-9]\d*)기", school)
    year_match = re.search(r"모집기간\s*:\s*(20\d{2})년", recruit)
    declared_match = re.search(r"모집강좌\s*:\s*(\d+)개 강좌", recruit)
    if not cohort_match or not year_match or not declared_match:
        raise HoengseongContractError("culture-school cohort declaration changed")
    year = int(year_match.group(1))
    cohort = int(cohort_match.group(1))
    start, end = _date_pair(operation, "culture-school operation", default_year=year) or (
        None,
        None,
    )
    recruitment_text_match = re.search(r"모집기간\s*:\s*([^＊]+)", recruit)
    apply_pair = _date_pair(
        recruitment_text_match.group(1) if recruitment_text_match else "",
        "culture-school recruitment",
        default_year=year,
    )
    if apply_pair is None:
        raise HoengseongContractError("culture-school recruitment date missing")
    apply_start = apply_pair[0]
    target_match = re.search(r"모집대상\s*:\s*([^＊]+)", recruit)
    fee_match = re.search(r"특별회원회비\s*:\s*([^＊(]+)", recruit)
    target = _clean(target_match.group(1) if target_match else "")
    fee = _clean(fee_match.group(1) if fee_match else "")
    if not target or not fee or _PHONE.search(target) or _EMAIL.search(target):
        raise HoengseongContractError("culture-school public cohort fields changed")
    tables = soup.select(".cul_list > table")
    if len(tables) != 1:
        raise HoengseongContractError("culture-school roster table changed")
    grid = []
    for tr in tables[0].select("tr"):
        cells = [_clean(cell.get_text(" ", strip=True)) for cell in tr.find_all(["th", "td"], recursive=False)]
        if cells:
            grid.append(cells)
    expected_headers = ["순서", "프 로 그 램 명", "강 의 요 일 / 시 간", "모집 인원", "교 육 내 용", "비고"]
    if not grid or [re.sub(r"\s+", " ", item) for item in grid[0]] != expected_headers:
        raise HoengseongContractError("culture-school roster headers changed")
    source_rows = grid[1:]
    declared = int(declared_match.group(1))
    if len(source_rows) != declared:
        raise HoengseongContractError("culture-school declared roster count mismatch")
    rows: list[dict[str, Any]] = []
    identities: list[str] = []
    for expected_number, cells in enumerate(source_rows, 1):
        if len(cells) != 6 or cells[0] != str(expected_number):
            raise HoengseongContractError("culture-school sequence/row width changed")
        title_text = cells[1]
        if not title_text or _PHONE.search(title_text) or _EMAIL.search(title_text):
            raise HoengseongContractError("culture-school invalid title")
        capacity_match = re.fullmatch(r"(\d+)명", cells[3])
        if not capacity_match:
            raise HoengseongContractError("culture-school capacity changed")
        identity = f"culture_school:{year}:{expected_number}"
        identities.append(identity)
        if end < cutoff:
            continue
        row = _base_row(
            "culture_school",
            identity,
            title_text,
            "횡성문화원 문화학교",
        )
        row.update(
            {
                "raw_url": HOENGSEONG_CULTURE_URL,
                "program_type": f"횡성문화원 문화학교 제{cohort}기",
                "source_status": "운영중" if start <= cutoff <= end else "운영예정",
                "status": "OPEN" if start <= cutoff <= end else "SCHEDULED",
                "reservation_available": False,
                "period": f"{start.isoformat()} ~ {end.isoformat()}",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "apply_period": f"{apply_start.isoformat()} ~ 방문 선착순",
                "apply_start_date": apply_start.isoformat(),
                "apply_end_date": "",
                "schedule_raw": cells[2],
                "target": target,
                "venue_name": cells[5],
                "fee": fee,
                "application_method_raw": "개별 방문접수",
                "capacity_total": int(capacity_match.group(1)),
            }
        )
        rows.append(row)
    audit = {
        "source_rows": len(source_rows),
        "current_source_count": len(rows),
        "expired_source_count": len(source_rows) - len(rows),
        "data_pages": 1,
        "page_counts": {1: len(source_rows)},
        "empty_sentinel_page": "not_applicable_static_complete_table",
        "empty_sentinel_verified": True,
        "stability_rechecks": 1,
        "detail_verified": len(rows),
        "detail_transport": "inline_static_complete_roster",
        "source_branch_counts": {"횡성문화원 문화학교": len(source_rows)},
        "culture_school_year": year,
        "culture_school_cohort": cohort,
    }
    return rows, identities, audit


def _collect_culture(
    requester: _Requester,
    cutoff: date,
    max_pages: int,
    detail_limit: int,
    workers: int,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    del workers
    if max_pages < 1:
        raise HoengseongContractError("max_pages cap prevents culture-school audit")
    first = _culture_snapshot(requester.soup(HOENGSEONG_CULTURE_URL), cutoff)
    second = _culture_snapshot(requester.soup(HOENGSEONG_CULTURE_URL), cutoff)
    if first[1] != second[1] or [row["provider_course_id"] for row in first[0]] != [
        row["provider_course_id"] for row in second[0]
    ]:
        raise HoengseongContractError("culture-school complete table changed on recheck")
    if len(first[0]) > detail_limit:
        raise HoengseongContractError(
            f"detail_limit cap allows {detail_limit} of {len(first[0])} culture rows"
        )
    return first


def _family_page(soup: BeautifulSoup, page: int) -> _Page:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != "횡성군 가족센터>프로그램안내>프로그램신청":
        raise HoengseongContractError(f"family page {page}: wrong title {title!r}")
    form = soup.select_one("form#searchForm")
    if form is None:
        raise HoengseongContractError(f"family page {page}: search form missing")
    action = urljoin(HOENGSEONG_FAMILY_URL, _clean(form.get("action")))
    values = {
        _clean(node.get("name")): _clean(node.get("value"))
        for node in form.select("input[name]")
        if _clean(node.get("name"))
    }
    if (
        action != HOENGSEONG_FAMILY_URL
        or _clean(form.get("method")).lower() != "get"
        or values.get("rows") != str(HOENGSEONG_FAMILY_PAGE_SIZE)
        or values.get("cpage") != str(page)
        or values.get("area") != "A004"
        or values.get("area_detail") != "D083"
    ):
        raise HoengseongContractError(f"family page {page}: owner/pagination form changed")
    root = soup.select_one(".program_list > ul")
    if root is None:
        raise HoengseongContractError(f"family page {page}: programme list missing")
    cards = root.select(":scope > li.clearfix")
    if len(cards) > HOENGSEONG_FAMILY_PAGE_SIZE:
        raise HoengseongContractError(f"family page {page}: page-size overflow")
    rows: list[dict[str, Any]] = []
    status_map = {
        "접수중": ("OPEN", "c0"),
        "접수예정": ("SCHEDULED", "c1"),
        "접수마감": ("CLOSED", "c2"),
        "진행중": ("CLOSED", "c3"),
        "완료": ("CLOSED", "c4"),
    }
    for card in cards:
        title_link = card.select_one(".txt > .tit a[onclick]")
        title_match = _FAMILY_SEND.fullmatch(
            _clean(title_link.get("onclick")) if title_link else ""
        )
        if not title_match or title_match.group("fork") != "web":
            raise HoengseongContractError(f"family page {page}: title control changed")
        identity = title_match.group("identity")
        title_text = _clean(title_link.get_text(" ", strip=True))
        if not title_text or _PHONE.search(title_text) or _EMAIL.search(title_text):
            raise HoengseongContractError(f"family course {identity}: invalid title")
        fields: dict[str, str] = {}
        for paragraph in card.select(".txt > ul > li p"):
            label_node = paragraph.find("b")
            if label_node is None:
                continue
            label = _clean(label_node.get_text(" ", strip=True))
            whole = _clean(paragraph.get_text(" ", strip=True))
            value = _clean(whole[len(label) :]) if whole.startswith(label) else ""
            if label == "진행장소" and value.endswith("오시는길"):
                value = _clean(value[: -len("오시는길")])
            if label in fields:
                raise HoengseongContractError(f"family course {identity}: duplicate field")
            fields[label] = value
        if set(fields) != {"회차정보", "행사기간", "접수기간", "진행장소"}:
            raise HoengseongContractError(f"family course {identity}: fields incomplete")
        round_match = re.fullmatch(r"총\s*([1-9]\d*)회", fields["회차정보"])
        if not round_match:
            raise HoengseongContractError(f"family course {identity}: round count changed")
        start, end = _date_pair(fields["행사기간"], "family event") or (None, None)
        apply_start, apply_end = _date_pair(
            fields["접수기간"], "family application"
        ) or (None, None)
        region = _clean(
            card.select_one(".util > .loc").get_text(" ", strip=True)
            if card.select_one(".util > .loc")
            else ""
        )
        if region != "강원 > 횡성군":
            raise HoengseongContractError(f"family course {identity}: wrong region {region!r}")
        state = card.select_one(".util > .state")
        status_node = state.select_one("span") if state else None
        source_status = _clean(
            status_node.get_text(" ", strip=True) if status_node else ""
        )
        if source_status not in status_map or set(status_node.get("class", ())) != {
            status_map[source_status][1]
        }:
            raise HoengseongContractError(f"family course {identity}: status contract changed")
        application = state.find("a", string=lambda value: _clean(value) == "신청하기") if state else None
        application_match = _FAMILY_SEND.fullmatch(
            _clean(application.get("onclick")) if application else ""
        )
        if (
            status_map[source_status][0] == "OPEN"
            and (
                not application_match
                or application_match.group("identity") != identity
                or application_match.group("fork") != "center"
            )
        ):
            raise HoengseongContractError(f"family course {identity}: application control changed")
        venue = fields["진행장소"]
        if len(venue) > 600 or _PHONE.search(venue) or _EMAIL.search(venue):
            raise HoengseongContractError(f"family course {identity}: invalid venue")
        rows.append(
            {
                "source_identity": identity,
                "title": title_text,
                "page": page,
                "rounds": int(round_match.group(1)),
                "start": start,
                "end": end,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "source_status": source_status,
                "status": status_map[source_status][0],
                "venue": venue,
                "application_control": bool(application_match),
                "branch": "횡성군 가족센터",
            }
        )
    pager_values: list[int] = []
    for anchor in soup.select("a[href*='cpage=']"):
        href = urljoin(HOENGSEONG_FAMILY_URL, _clean(anchor.get("href")))
        parsed, query = _query(href)
        if parsed.hostname != "hsg.familynet.or.kr":
            raise HoengseongContractError(f"family page {page}: off-host pager")
        value = query.get("cpage", "")
        if _POSITIVE.fullmatch(value):
            pager_values.append(int(value))
    declared = max(pager_values, default=1)
    empty_marker = not rows and _clean(root.get_text(" ", strip=True)) == "프로그램 목록이 존재하지 않습니다."
    if not rows and not empty_marker:
        raise HoengseongContractError(f"family page {page}: ambiguous empty result")
    return _Page(page, tuple(rows), declared, empty_marker)


def _family_shell(soup: BeautifulSoup, identity: str) -> str:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != "횡성군 가족센터>프로그램안내>프로그램신청":
        raise HoengseongContractError(f"family detail {identity}: wrong title")
    if soup.select_one(".program_view") is None:
        raise HoengseongContractError(f"family detail {identity}: programme shell missing")
    seq = soup.select_one("input[name=familynet_pg_no]")
    area = soup.select_one("input[name=area]")
    area_detail = soup.select_one("input[name=area_detail]")
    csrf_node = soup.select_one("meta[name=_csrf]")
    csrf = _clean(csrf_node.get("content")) if csrf_node else ""
    if (
        seq is None
        or _clean(seq.get("value")) != identity
        or area is None
        or _clean(area.get("value")) != "A004"
        or area_detail is None
        or _clean(area_detail.get("value")) != "D083"
        or not _CSRF.fullmatch(csrf)
    ):
        raise HoengseongContractError(f"family detail {identity}: owner/session contract changed")
    scripts = "\n".join(node.get_text("\n", strip=False) for node in soup.select("script"))
    if (
        "/recruitReceipt/getView.do" not in scripts
        or "/recruitReceipt/loginCheck.do" not in scripts
        or "/recruitReceipt/modal/apply.do" not in scripts
    ):
        raise HoengseongContractError(f"family detail {identity}: public/application boundary changed")
    return csrf


def _integer(value: Any, label: str, identity: str) -> int:
    raw = _clean(value)
    if not re.fullmatch(r"\d+", raw):
        raise HoengseongContractError(f"family detail {identity}: invalid {label}")
    number = int(raw)
    if number > 1_000_000:
        raise HoengseongContractError(f"family detail {identity}: {label} exceeds cap")
    return number


def _family_payload(listed: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    identity = _clean(listed.get("source_identity"))
    view = payload.get("view")
    if not isinstance(view, Mapping) or payload.get("apply_yn") is not False:
        raise HoengseongContractError(f"family detail {identity}: public view object changed")
    if (
        _clean(view.get("familynet_pg_no")) != identity
        or _clean(view.get("title")) != _clean(listed.get("title"))
        or _clean(view.get("area")) != "A004"
        or _clean(view.get("area_detail")) != "D083"
        or _clean(view.get("area_nm")) != "강원"
        or _clean(view.get("area_detail_nm")) != "횡성군"
    ):
        raise HoengseongContractError(f"family detail {identity}: identity/owner mismatch")
    start, end = _date_pair(
        f"{_clean(view.get('program_start_date'))} ~ {_clean(view.get('program_end_date'))}",
        "family event",
    ) or (None, None)
    apply_start, apply_end = _date_pair(
        f"{_clean(view.get('reception_date_start_time'))} ~ "
        f"{_clean(view.get('reception_date_end_time'))}",
        "family application",
    ) or (None, None)
    source_status = _clean(view.get("program_status_nm"))
    listed_start = listed.get("start")
    listed_end = listed.get("end")
    # FamilyNet list cards may show the next episode while the public view API
    # exposes the complete multi-session programme period.  The episode must be
    # contained by that full period; application/status fields remain exact.
    if (
        not isinstance(listed_start, date)
        or not isinstance(listed_end, date)
        or not (start <= listed_start <= listed_end <= end)
        or (apply_start, apply_end) != (listed.get("apply_start"), listed.get("apply_end"))
        or source_status != _clean(listed.get("source_status"))
    ):
        raise HoengseongContractError(f"family detail {identity}: list/API mismatch")
    place1 = _clean(view.get("program_place1"))
    place2 = _clean(view.get("program_place2"))
    combined = _clean(f"{place1} {place2}")
    listed_venue = _clean(listed.get("venue"))
    if listed_venue not in {combined, place1, place2, "-"} and combined not in {"", "-"}:
        raise HoengseongContractError(f"family detail {identity}: venue mismatch")
    target = _clean(view.get("participation_target"))
    if len(target) > 500 or _PHONE.search(target) or _EMAIL.search(target):
        raise HoengseongContractError(f"family detail {identity}: invalid target")
    current = _integer(view.get("curr_apply_seq"), "current capacity", identity)
    capacity = _integer(view.get("recruit_personal"), "capacity", identity)
    waiting = _integer(view.get("waiting_personal"), "wait capacity", identity)
    if capacity < 1 or current > capacity + waiting:
        raise HoengseongContractError(f"family detail {identity}: impossible capacity")
    venue_name = place2 if place2 not in {"", "-"} else "횡성군 가족센터"
    row = _base_row(
        "family_center",
        f"family:{identity}",
        _clean(listed.get("title")),
        "횡성군 가족센터",
    )
    row.update(
        {
            "raw_url": family_detail_url(identity),
            "program_type": "가족센터 프로그램",
            "source_status": source_status,
            "status": _clean(listed.get("status")),
            "reservation_available": _clean(listed.get("status")) == "OPEN",
            "period": f"{start.isoformat()} ~ {end.isoformat()}",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "apply_period": (
                f"{_clean(view.get('reception_date_start_time'))} ~ "
                f"{_clean(view.get('reception_date_end_time'))}"
            ),
            "apply_start_date": _clean(view.get("reception_date_start_time")),
            "apply_end_date": _clean(view.get("reception_date_end_time")),
            "schedule_raw": f"총 {int(listed.get('rounds') or 0)}회",
            "target": target,
            "venue_name": venue_name,
            "fee": "",
            "application_method_raw": "온라인 신청(로그인 필요)",
            "capacity_current": current,
            "capacity_total": capacity,
            "capacity_wait_total": waiting,
        }
    )
    return row


def _collect_family(
    requester: _Requester,
    cutoff: date,
    max_pages: int,
    detail_limit: int,
    workers: int,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    pages: dict[int, _Page] = {}
    sentinel: Optional[_Page] = None
    for page_number in range(1, max_pages + 1):
        page = _family_page(requester.soup(family_list_url(page_number)), page_number)
        if page.empty:
            sentinel = page
            break
        pages[page_number] = page
    if sentinel is None:
        raise HoengseongContractError(
            f"max_pages cap {max_pages} reached before family sentinel"
        )
    if not pages:
        raise HoengseongContractError("family-center programme ledger unexpectedly empty")
    first_recheck = _family_page(requester.soup(family_list_url(1)), 1)
    last_number = max(pages)
    last_recheck = _family_page(
        requester.soup(family_list_url(last_number)), last_number
    )
    sentinel_recheck = _family_page(
        requester.soup(family_list_url(sentinel.page)), sentinel.page
    )
    if (
        _page_signature(first_recheck) != _page_signature(pages[1])
        or _page_signature(last_recheck) != _page_signature(pages[last_number])
        or not sentinel_recheck.empty
    ):
        raise HoengseongContractError("family first/last/sentinel stability changed")
    for page_number, page in pages.items():
        if page_number < last_number and len(page.rows) != HOENGSEONG_FAMILY_PAGE_SIZE:
            raise HoengseongContractError("family page has premature short result")
        if not (1 <= len(page.rows) <= HOENGSEONG_FAMILY_PAGE_SIZE):
            raise HoengseongContractError("family page size changed")
    declared = max(page.declared_last for page in pages.values())
    if declared > last_number:
        raise HoengseongContractError("family pager points past empty sentinel")
    source = [row for number in sorted(pages) for row in pages[number].rows]
    identities = [_clean(row["source_identity"]) for row in source]
    if len(identities) != len(set(identities)):
        raise HoengseongContractError("duplicate family identity")
    if [int(value) for value in identities] != sorted(
        (int(value) for value in identities), reverse=True
    ):
        raise HoengseongContractError("family identities lost descending order")
    current = [row for row in source if row["end"] >= cutoff]
    if len(current) > detail_limit:
        raise HoengseongContractError(
            f"detail_limit cap allows {detail_limit} of {len(current)} family details"
        )

    def fetch_detail(listed: Mapping[str, Any]) -> dict[str, Any]:
        identity = _clean(listed.get("source_identity"))
        csrf = _family_shell(
            requester.soup(family_detail_url(identity)), identity
        )
        payload = requester.json(
            HOENGSEONG_FAMILY_VIEW_API_URL,
            {"seq": identity},
            {
                "X-CSRF-TOKEN": csrf,
                "Origin": "https://hsg.familynet.or.kr",
                "Referer": family_detail_url(identity),
            },
        )
        return _family_payload(listed, payload)

    rows = _parallel_map(current, fetch_detail, workers)
    return rows, identities, {
        "source_rows": len(source),
        "current_source_count": len(current),
        "expired_source_count": len(source) - len(current),
        "data_pages": len(pages),
        "page_counts": {number: len(page.rows) for number, page in sorted(pages.items())},
        "empty_sentinel_page": sentinel.page,
        "empty_sentinel_verified": True,
        "stability_rechecks": 3,
        "detail_verified": len(rows),
        "detail_transport": "public_detail_shell_and_public_view_api",
        "application_controls_verified": sum(
            bool(row.get("application_control")) for row in current
        ),
        "source_branch_counts": {"횡성군 가족센터": len(source)},
    }


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _initial_meta(owner: str, cutoff: date) -> dict[str, Any]:
    config = HOENGSEONG_OWNERS.get(owner, {})
    return {
        "owner": owner,
        "provider": config.get("provider", ""),
        "canonical_url": config.get("url", ""),
        "candidate_id": config.get("candidate_id", ""),
        "municipality_code": HOENGSEONG_MUNICIPALITY_CODE,
        "municipality_name": HOENGSEONG_MUNICIPALITY_NAME,
        "audit_date": cutoff.isoformat(),
        "logical_requests": 0,
        "physical_requests": 0,
        "list_requests": 0,
        "detail_requests": 0,
        "detail_api_requests": 0,
        "request_retry_count": 0,
        "application_endpoint_requests": 0,
        "applicant_endpoint_requests": 0,
        "login_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "payment_endpoint_requests": 0,
        "pii_values_persisted": 0,
        "source_cap_reached": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "returned_count": 0,
        "configured_collection_error": "",
    }


def collect_hoengseong_education(
    target: Any,
    timeout: int = 35,
    max_pages: int = HOENGSEONG_MAX_PAGES,
    detail_limit: int = HOENGSEONG_MAX_DETAILS,
    *,
    today: Optional[date | datetime | str] = None,
    max_workers: int = HOENGSEONG_MAX_WORKERS,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one complete current/future Hoengseong owner snapshot."""

    try:
        cutoff = _today(today)
    except (TypeError, ValueError):
        cutoff = datetime.now(ZoneInfo("Asia/Seoul")).date()
        meta = _initial_meta("", cutoff)
        meta["configured_collection_error"] = "today is invalid"
        return [], HOENGSEONG_PARSER, meta
    owner = owner_for_target(target)
    meta = _initial_meta(owner, cutoff)
    if not owner:
        meta.update(
            {
                "provider": _clean(_target_value(target, "provider")),
                "canonical_url": _clean(_target_value(target, "url")),
                "configured_collection_error": "non-canonical Hoengseong programme target",
            }
        )
        return [], HOENGSEONG_PARSER, meta
    try:
        timeout, max_pages, detail_limit, max_workers = map(
            int, (timeout, max_pages, detail_limit, max_workers)
        )
        if timeout < 1 or max_pages < 1 or detail_limit < 0 or not 1 <= max_workers <= 16:
            raise ValueError
    except (TypeError, ValueError):
        meta["configured_collection_error"] = "invalid collection limits"
        return [], HOENGSEONG_PARSER, meta
    if fetcher is None and session_factory is None and not allow_raw_requests_for_tests:
        meta["configured_collection_error"] = (
            "raw requests disabled; inject the managed session/fetcher or explicitly opt in"
        )
        return [], HOENGSEONG_PARSER, meta
    requester = _Requester(
        owner,
        session_factory or _raw_session,
        fetcher or _default_fetcher,
        timeout,
        meta,
    )
    collectors = {
        "reservation": _collect_reservation,
        "municipal_library": _collect_municipal_library,
        "education_library": _collect_gwe,
        "youth_center": _collect_youth,
        "culture_school": _collect_culture,
        "family_center": _collect_family,
    }
    try:
        rows, source_identities, audit = collectors[owner](
            requester, cutoff, max_pages, detail_limit, max_workers
        )
        original_ids = [_clean(row.get("provider_course_id")) for row in rows]
        if not all(original_ids) or len(original_ids) != len(set(original_ids)):
            raise HoengseongContractError("duplicate or empty emitted identity")
        deduped = list((dedupe_rows or _dedupe_default)(rows))
        if any(not isinstance(row, Mapping) for row in deduped):
            raise HoengseongContractError("dedupe returned a non-object row")
        if [_clean(row.get("provider_course_id")) for row in deduped] != original_ids:
            raise HoengseongContractError("dedupe changed complete owner identity/cardinality")
        privacy = [error for row in deduped for error in _privacy_errors(row)]
        if privacy:
            raise HoengseongContractError("; ".join(dict.fromkeys(privacy)))
        if any(_clean(row.get("application_url")) for row in deduped):
            raise HoengseongContractError("application endpoint escaped output boundary")
        deduped = sorted(
            (dict(row) for row in deduped),
            key=lambda row: (
                _clean(row.get("start_date")),
                _clean(row.get("title")),
                _clean(row.get("provider_course_id")),
            ),
        )
        meta.update(audit)
        details_complete = audit.get("detail_verified") == audit.get(
            "current_source_count"
        )
        meta.update(
            {
                "source_identity_sha256": _identity_hash(source_identities),
                "output_identity_sha256": _identity_hash(
                    row["provider_course_id"] for row in deduped
                ),
                "returned_count": len(deduped),
                "status_counts": dict(
                    Counter(_clean(row.get("status")) for row in deduped)
                ),
                "domain_category_counts": dict(
                    Counter(_clean(row.get("domain_category")) for row in deduped)
                ),
                "output_branch_counts": dict(
                    Counter(_clean(row.get("branch")) for row in deduped)
                ),
                "owner_identity_disjoint": True,
                "pagination_complete": True,
                "details_complete": details_complete,
                "snapshot_complete": details_complete,
                "full_snapshot_validated": details_complete,
                "configured_collection_error": "",
            }
        )
        if len(deduped) != int(audit.get("current_source_count") or 0):
            raise HoengseongContractError("current/detail/output cardinality mismatch")
        return deduped, HOENGSEONG_PARSER, meta
    except Exception as exc:
        text = _clean(exc)
        if "max_pages cap" in text or "detail_limit cap" in text:
            meta["source_cap_reached"] = True
        meta.update(
            {
                "returned_count": 0,
                "pagination_complete": False,
                "details_complete": False,
                "snapshot_complete": False,
                "full_snapshot_validated": False,
                "configured_collection_error": f"{type(exc).__name__}: {text}",
            }
        )
        return [], HOENGSEONG_PARSER, meta
    finally:
        requester.close()


collect_hoengseong_education_courses = collect_hoengseong_education
collect = collect_hoengseong_education

__all__ = [name for name in globals() if name.startswith("HOENGSEONG_")] + [
    "HoengseongContractError",
    "collect",
    "collect_hoengseong_education",
    "collect_hoengseong_education_courses",
    "family_detail_url",
    "family_list_url",
    "gwe_detail_url",
    "gwe_list_url",
    "is_hoengseong_target",
    "is_target",
    "library_detail_url",
    "owner_for_target",
    "reservation_category_url",
    "reservation_detail_url",
    "reservation_list_url",
    "youth_detail_url",
    "youth_list_url",
]
