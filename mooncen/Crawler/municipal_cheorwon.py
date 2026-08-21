"""Fail-closed collector for Cheorwon-gun's official education catalogue.

The registered provider URL is narrowed to institution 3 and courses whose
receipt state is ``RCEPT_ING``.  It therefore cannot represent the municipal
catalogue.  This collector retains that provider identity but always walks the
official, unfiltered ``selectLctreSearch.do`` result, its empty sentinel, and a
stable copy of page one.  The official current/future year partitions are
then used to find the rows whose detail dates still overlap the crawl date.

Cheorwon currently omits the RapidSSL intermediate certificate from its TLS
handshake.  The default transport adds that public CA intermediate to
certifi's trust store; certificate and hostname verification remain enabled.
Instructor/contact data, attachments, descriptions, source HTML, login data,
and application form payloads are never persisted.
"""

from __future__ import annotations

import base64
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import html
import math
import re
import ssl
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import certifi
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter


CHEORWON_PROVIDER = "MUNI_WWW_CWG_GO_KR_C8039214"
CHEORWON_CANONICAL_CANDIDATE_ID = "MUNI_IR_B155FBDFE852"
CHEORWON_MUNICIPALITY_CODE = "5178000000"
CHEORWON_MUNICIPALITY_NAME = "강원특별자치도 철원군"
CHEORWON_HOST = "www.cwg.go.kr"
CHEORWON_LIST_PATH = "/edu/selectLctreSearch.do"
CHEORWON_DETAIL_PATH = "/edu/selectLctreWebView.do"
CHEORWON_APPLICATION_PATH = "/edu/insertReqstLctreWebView.do"
CHEORWON_CANONICAL_URL = (
    f"https://{CHEORWON_HOST}{CHEORWON_LIST_PATH}?key=692"
)
CHEORWON_REGISTERED_URL = (
    f"https://{CHEORWON_HOST}{CHEORWON_LIST_PATH}?key=692&searchHumanAt=N"
    "&insNo=3&lctreType=&rceptSttus=RCEPT_ING&lctreNm="
)
CHEORWON_PAGE_SIZE = 100
CHEORWON_FETCH_ATTEMPTS = 4
CHEORWON_MAX_WORKERS = 6
CHEORWON_MAX_HTML_BYTES = 3_000_000
CHEORWON_PARSER = (
    "cheorwon_official_unfiltered_education+all_pages+empty_sentinel+"
    "stable_page1+official_current_future_year_partitions+current_details+"
    "public_course_bound_application_controls+source_institutions+pii_allowlist"
)
CHEORWON_OWNERSHIP_SCOPE = (
    "cheorwon_official_unfiltered_lifelong_and_culture_welfare_education"
)

CHEORWON_GENERAL_HOMEPAGE_URL = "https://www.cwg.go.kr/"
CHEORWON_ATTACHMENT_NOTICE_URL = (
    "https://www.cwg.go.kr/edu/selectBbsNttView.do?"
    "bbsNo=79&key=709&nttNo=128775"
)
CHEORWON_LIBRARY_MAIN_URL = "https://lib.gwe.go.kr/cwlib/main"
CHEORWON_LIBRARY_PROGRAM_URL = (
    "https://lib.gwe.go.kr/cwlib/menu/3302/lecture-event/list/all"
)
CHEORWON_ORDINANCE_URL = (
    "https://law.go.kr/ordinInfoP.do?ordinSeq=1646601"
)

CHEORWON_INSTITUTION_BY_INS_NO: Mapping[str, str] = {
    "3": "철원평생학습관",
    "4": "철원종합문화복지센터",
}

CHEORWON_EXCLUDED_CANDIDATE_IDS = frozenset(
    {
        "MUNI_IR_73F665EE43A9",
        "MUNI_IR_A649E6E29020",
        "MUNI_IR_D28C2FEC49A1",
    }
)
CHEORWON_CANDIDATE_AUDIT: Mapping[str, Mapping[str, str]] = {
    "MUNI_IR_73F665EE43A9": {
        "decision": "excluded_general_homepage_duplicate",
        "provider": "MUNI_WWW_CWG_GO_KR_B360CE70",
        "url": CHEORWON_GENERAL_HOMEPAGE_URL,
        "owner": CHEORWON_PROVIDER,
        "reason": "general homepage; disabled as a duplicate of the course owner",
    },
    CHEORWON_CANONICAL_CANDIDATE_ID: {
        "decision": "include_existing_owner_retarget_to_unfiltered_catalogue",
        "provider": CHEORWON_PROVIDER,
        "url": CHEORWON_REGISTERED_URL,
        "owner": CHEORWON_PROVIDER,
        "reason": (
            "official list, but the discovered URL fixes insNo=3 and "
            "RCEPT_ING; collection must use the unfiltered endpoint"
        ),
    },
    "MUNI_IR_D28C2FEC49A1": {
        "decision": "excluded_separate_education_library_owner",
        "provider": "MUNI_LIB_GWE_GO_KR_E49C8D9C",
        "url": CHEORWON_LIBRARY_MAIN_URL,
        "owner": "MUNI_LIB_GWE_GO_KR_E49C8D9C",
        "reason": (
            "separate Gangwon education-library catalogue; its exact program "
            "list is already a ready library provider"
        ),
    },
    "MUNI_IR_A649E6E29020": {
        "decision": "excluded_ordinance_historical_text",
        "provider": "MUNI_LAW_GO_KR_DBC95778",
        "url": CHEORWON_ORDINANCE_URL,
        "owner": "",
        "reason": "ordinance page mentioning one 2023 class, not a course list",
    },
}

CHEORWON_PROVIDER_AUDIT: Mapping[str, Mapping[str, str]] = {
    "MUNI_WWW_CWG_GO_KR_982AC30C": {
        "decision": "excluded_attachment_guideline_notice",
        "url": CHEORWON_ATTACHMENT_NOTICE_URL,
        "reason": "single BBS attachment/instruction notice, not structured courses",
    },
    "MUNI_WWW_CWG_GO_KR_B360CE70": {
        "decision": "excluded_disabled_duplicate_homepage",
        "url": CHEORWON_GENERAL_HOMEPAGE_URL,
        "reason": f"disabled duplicate of {CHEORWON_PROVIDER}",
    },
    CHEORWON_PROVIDER: {
        "decision": "include_owner_with_unfiltered_replacement",
        "url": CHEORWON_REGISTERED_URL,
        "reason": "existing owner identity; registered query is too narrow",
    },
    "MUNI_LIB_GWE_GO_KR_E49C8D9C": {
        "decision": "exclude_from_municipal_owner_keep_separate_library_provider",
        "url": CHEORWON_LIBRARY_PROGRAM_URL,
        "reason": "ready separate provider with 10 live rows at audit",
    },
    "MUNI_LAW_GO_KR_DBC95778": {
        "decision": "excluded_non_catalogue_ordinance",
        "url": CHEORWON_ORDINANCE_URL,
        "reason": "law/ordinance source is not an application catalogue",
    },
}

CHEORWON_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "registered_filtered_url": CHEORWON_REGISTERED_URL,
    "canonical_unfiltered_url": CHEORWON_CANONICAL_URL,
    "unfiltered_total": 1000,
    "page_size": 100,
    "advertised_pages": 10,
    "immediate_empty_page": 11,
    "page_one_stable": True,
    "unique_source_identities": 1000,
    "duplicate_source_identities": 0,
    "official_year_totals": {
        "2026": 276,
        "2025": 284,
        "2024": 161,
        "2023": 139,
        "2022": 140,
        "2021": 0,
        "2020": 0,
        "2019": 0,
        "2018": 0,
    },
    "year_partition_sum": 1000,
    "current_year_rows": 276,
    "cancelled_current_year_rows": 12,
    "details_verified": 264,
    "expired_after_detail": 107,
    "current_or_future_rows": 157,
    "current_source_status_counts": {
        "접수대기": 86,
        "접수진행": 4,
        "대기접수": 1,
        "접수마감": 66,
    },
    "current_institution_counts": {
        "철원평생학습관": 94,
        "철원종합문화복지센터": 63,
    },
    "public_application_controls": 5,
    "course_bound_application_controls_verified": 5,
    "separate_library_live_rows": 10,
    "conclusion": (
        "replace the provider's insNo=3/RCEPT_ING query with the unfiltered "
        "municipal catalogue; retain the education library as a separate owner"
    ),
}

CHEORWON_PII_FIELDS_DISCARDED = (
    "강사명",
    "문의전화",
    "강의계획서/첨부파일",
    "강좌안내",
    "참고사항",
    "로그인/신청자 정보",
    "신청 form payload",
    "source HTML",
)


# Public RapidSSL TLS RSA CA G1 intermediate (DER).  The official server's
# 2026 certificate chains through this CA but does not send the intermediate.
_RAPIDSSL_G1_DER_B64 = (
    "MIIEszCCA5ugAwIBAgIQCyWUIs7ZgSoVoE6ZUooO+jANBgkqhkiG9w0BAQsFADBh"
    "MQswCQYDVQQGEwJVUzEVMBMGA1UEChMMRGlnaUNlcnQgSW5jMRkwFwYDVQQLExB3"
    "d3cuZGlnaWNlcnQuY29tMSAwHgYDVQQDExdEaWdpQ2VydCBHbG9iYWwgUm9vdCBH"
    "MjAeFw0xNzExMDIxMjI0MzNaFw0yNzExMDIxMjI0MzNaMGAxCzAJBgNVBAYTAlVT"
    "MRUwEwYDVQQKEwxEaWdpQ2VydCBJbmMxGTAXBgNVBAsTEHd3dy5kaWdpY2VydC5j"
    "b20xHzAdBgNVBAMTFlJhcGlkU1NMIFRMUyBSU0EgQ0EgRzEwggEiMA0GCSqGSIb3"
    "DQEBAQUAA4IBDwAwggEKAoIBAQC/uVklRBI1FuJdUEkFCuDL/I3aJQiaZ6aibRHj"
    "ap/ap9zy1aYNrphe7YcaNwMoPsZvXDR+hNJOo9gbgOYVTPq8gXc84I75YKOHiVA4"
    "NrJJQZ6p2sJQyqx60HkEIjzIN+1LQLfXTlpuznToOa1hyTD0yyitFyOYwURM+/CI"
    "8FNFMpBhw22hpeAQkOOLmsqT5QZJYeik7qlvn8gfD+XdDnk3kkuuu0eG+vuyrSGr"
    "5uX5LRhFWlv1zFQDch/EKmd163m6z/ycx/qLa9zyvILc7cQpb+k7TLra9WE17YPS"
    "n9ANjG+ECo9PDW3N9lwhKQCNvw1gGoguyCQu7HE7BnW8eSSFAgMBAAGjggFmMIIB"
    "YjAdBgNVHQ4EFgQUDNtsgkkPSmcKuBTuesRIUojrVjgwHwYDVR0jBBgwFoAUTiJUI"
    "BiV5uNu5g/6+rkS7QYXjzkwDgYDVR0PAQH/BAQDAgGGMB0GA1UdJQQWMBQGCCsG"
    "AQUFBwMBBggrBgEFBQcDAjASBgNVHRMBAf8ECDAGAQH/AgEAMDQGCCsGAQUFBwEB"
    "BCgwJjAkBggrBgEFBQcwAYYYaHR0cDovL29jc3AuZGlnaWNlcnQuY29tMEIGA1Ud"
    "HwQ7MDkwN6A1oDOGMWh0dHA6Ly9jcmwzLmRpZ2ljZXJ0LmNvbS9EaWdpQ2VydEds"
    "b2JhbFJvb3RHMi5jcmwwYwYDVR0gBFwwWjA3BglghkgBhv1sAQEwKjAoBggrBgEF"
    "BQcCARYcaHR0cHM6Ly93d3cuZGlnaWNlcnQuY29tL0NQUzALBglghkgBhv1sAQIw"
    "CAYGZ4EMAQIBMAgGBmeBDAECAjANBgkqhkiG9w0BAQsFAAOCAQEAGUSlOb4K3Wtm"
    "SlbmE50UYBHXM0SKXPqHMzk6XQUpCheF/4qU8aOhajsyRQFDV1ih/uPIg7YHRtFi"
    "CTq4G+zb43X1T77nJgSOI9pq/TqCwtukZ7u9VLL3JAq3Wdy2moKLvvC8tVmRzkA"
    "e0xQCkRKIjbBG80MSyDX/R4uYgj6ZiNT/Zg6GI6RofgqgpDdssLc0XIRQEotxIZc"
    "KzP3pGJ9FCbMHmMLLyuBd+uCWvVcF2ogYAawufChS/PT61D9rqzPRS5I2uqa3tm"
    "IT44JhJgWhBnFMb7AGQkvNq9KNS9dd3GWc17H/dXa1enoxzWjE0hBdFjxPhUb0W3"
    "wi8o34/m8Fxw=="
)
_RAPIDSSL_G1_SHA256 = (
    "4422e963ee53cd58cc9f85cd40bf5ffec0095fdf1a154535661c1c06bcadc69b"
)


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_TOTAL_RE = re.compile(r"총\s*([\d,]+)\s*건")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})년\s*(\d{1,2})월\s*(\d{1,2})일(?!\d)"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2})[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_CAPACITY_RE = re.compile(
    r"^(\d{1,6})\s*/\s*(\d{1,6})\s*"
    r"\(\s*(\d{1,6})(?:\s*/\s*(\d{1,6}))?\s*\)$"
)
_SOURCE_STATUS_MAP: Mapping[str, str] = {
    "접수대기": "SCHEDULED",
    "접수진행": "OPEN",
    "대기접수": "OPEN",
    "접수마감": "CLOSED",
    "폐강": "CANCELLED",
}
_DETAIL_HEADING_STATUS: Mapping[str, str] = {
    "접수대기": "접수대기",
    "접수진행": "접수중",
    "대기접수": "대기접수",
    "접수마감": "접수마감",
}
_DETAIL_FIELDS = frozenset(
    {
        "기수",
        "강좌분야",
        "강좌대상",
        "강좌장소",
        "모집정원",
        "대기인원",
        "문의전화",
        "접수방법",
        "선정방법",
        "접수기간",
        "강좌일자",
        "수강료",
        "재료비",
        "강사명",
        "기관명",
        "강의계획서",
        "강좌안내",
        "참고사항",
    }
)
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "course_code",
        "list_page",
        "source_year",
        "ins_no",
        "source_status",
        "source_category",
        "source_period",
        "source_application_period",
        "source_schedule",
        "source_capacity_current",
        "source_capacity_total",
        "source_waiting_current",
        "source_waiting_total",
        "education_institution",
        "source_target",
        "source_venue",
        "source_fee",
        "source_application_method",
        "source_selection_method",
        "service_family",
        "detail_verified",
        "application_control_present",
        "application_control_contract",
        "application_control_verified",
    }
)
_FORBIDDEN_PERSISTED_KEYS = frozenset(
    {
        "instructor",
        "instructor_name",
        "contact",
        "contact_name",
        "phone",
        "email",
        "attachments",
        "attachment_urls",
        "detail_pairs",
        "detail_description",
        "source_html",
        "raw_html",
        "login_payload",
        "application_payload",
    }
)


class CheorwonContractError(ValueError):
    """Raised when an official Cheorwon source contract changes."""


@dataclass
class _ListPage:
    rows: list[dict[str, Any]]
    total: int
    current_page: int
    last_page: int
    advertised_years: tuple[int, ...]
    empty_sentinel: bool
    errors: list[str]


class _SSLContextAdapter(HTTPAdapter):
    def __init__(self, context: ssl.SSLContext) -> None:
        self._context = context
        super().__init__()

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        kwargs["ssl_context"] = self._context
        super().init_poolmanager(*args, **kwargs)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(
        " ", html.unescape(str(value or "")).replace("\xa0", " ")
    ).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[\W_]+", "", _clean(value).casefold(), flags=re.UNICODE)


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    try:
        return date.fromisoformat(_clean(value))
    except ValueError as exc:
        raise ValueError("today must be an ISO date") from exc


def _compare_url(value: Any) -> str:
    parsed = urlparse(_clean(value))
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port
        or parsed.fragment
    ):
        return ""
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return f"https://{parsed.hostname.lower()}{parsed.path}" + (
        f"?{query}" if query else ""
    )


def is_cheorwon_education_target(target: Any) -> bool:
    compared = _compare_url(_target_value(target, "url"))
    return (
        _clean(_target_value(target, "provider")) == CHEORWON_PROVIDER
        and compared
        in {
            _compare_url(CHEORWON_REGISTERED_URL),
            _compare_url(CHEORWON_CANONICAL_URL),
        }
    )


def is_cheorwon_excluded_candidate(target: Any) -> bool:
    candidate_id = _clean(_target_value(target, "candidate_id"))
    compared = _compare_url(_target_value(target, "url"))
    return candidate_id in CHEORWON_EXCLUDED_CANDIDATE_IDS or compared in {
        _compare_url(CHEORWON_GENERAL_HOMEPAGE_URL),
        _compare_url(CHEORWON_ATTACHMENT_NOTICE_URL),
        _compare_url(CHEORWON_LIBRARY_MAIN_URL),
        _compare_url(CHEORWON_LIBRARY_PROGRAM_URL),
        _compare_url(CHEORWON_ORDINANCE_URL),
    }


def is_cheorwon_separate_library_target(target: Any) -> bool:
    return _compare_url(_target_value(target, "url")) in {
        _compare_url(CHEORWON_LIBRARY_MAIN_URL),
        _compare_url(CHEORWON_LIBRARY_PROGRAM_URL),
    }


def cheorwon_list_url(page: Any = 1, year: Any = "") -> str:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        return ""
    year_text = _clean(year)
    if year_text and not re.fullmatch(r"20\d{2}", year_text):
        return ""
    params = {
        "key": "692",
        "insNo": "",
        "semesterType": "",
        "year": year_text,
        "lctreSe": "",
        "lctreNm": "",
        "lctreType": "",
        "rceptTrgter": "",
        "rceptSttus": "",
        "lctreSttus": "",
        "lctrePdBgnde": "",
        "lctrePdEndde": "",
        "pageUnit": str(CHEORWON_PAGE_SIZE),
        "pageIndex": str(page),
    }
    return f"https://{CHEORWON_HOST}{CHEORWON_LIST_PATH}?{urlencode(params)}"


def cheorwon_detail_url(identity: Any, ins_no: Any) -> str:
    identity_text, ins_text = _clean(identity), _clean(ins_no)
    if not re.fullmatch(r"[1-9]\d*", identity_text):
        return ""
    if ins_text not in CHEORWON_INSTITUTION_BY_INS_NO:
        return ""
    return f"https://{CHEORWON_HOST}{CHEORWON_DETAIL_PATH}?" + urlencode(
        {"key": "692", "insNo": ins_text, "lctreSe": "", "lctreNo": identity_text}
    )


def cheorwon_application_url(identity: Any, ins_no: Any) -> str:
    identity_text, ins_text = _clean(identity), _clean(ins_no)
    if not re.fullmatch(r"[1-9]\d*", identity_text):
        return ""
    if ins_text not in CHEORWON_INSTITUTION_BY_INS_NO:
        return ""
    return f"https://{CHEORWON_HOST}{CHEORWON_APPLICATION_PATH}?" + urlencode(
        {"key": "692", "insNo": ins_text, "lctreNo": identity_text}
    )


def _tls_context() -> ssl.SSLContext:
    der = base64.b64decode(_RAPIDSSL_G1_DER_B64)
    if hashlib.sha256(der).hexdigest() != _RAPIDSSL_G1_SHA256:
        raise RuntimeError("embedded RapidSSL intermediate fingerprint changed")
    context = ssl.create_default_context(cafile=certifi.where())
    context.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(der))
    return context


def _default_session_factory() -> requests.Session:
    value = requests.Session()
    value.mount(
        f"https://{CHEORWON_HOST}/",
        _SSLContextAdapter(_tls_context()),
    )
    value.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (compatible; MooncenMunicipalAudit/1.0; "
                "+https://www.cwg.go.kr/edu/)"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return value


def _default_fetcher(session: Any, url: str, timeout: int) -> BeautifulSoup:
    response = session.get(url, timeout=timeout, allow_redirects=True)
    response.raise_for_status()
    final = urlparse(_clean(getattr(response, "url", url)))
    if (
        final.scheme.lower() != "https"
        or final.hostname != CHEORWON_HOST
        or final.username
        or final.password
        or final.port
        or final.fragment
        or final.path not in {CHEORWON_LIST_PATH, CHEORWON_DETAIL_PATH}
    ):
        raise ValueError("response left the official Cheorwon HTTPS scope")
    if "html" not in _clean(response.headers.get("Content-Type")).lower():
        raise ValueError("response is not HTML")
    content = response.content
    if len(content) > CHEORWON_MAX_HTML_BYTES:
        raise ValueError("HTML response exceeded the bounded size limit")
    return BeautifulSoup(content, "html.parser")


def _close_quietly(value: Any) -> None:
    try:
        close = getattr(value, "close", None)
        if callable(close):
            close()
    except Exception:
        pass


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, bytes):
        if len(value) > CHEORWON_MAX_HTML_BYTES:
            raise ValueError("fixture HTML exceeded the bounded size limit")
        return BeautifulSoup(value, "html.parser")
    if isinstance(value, str):
        if len(value.encode("utf-8")) > CHEORWON_MAX_HTML_BYTES:
            raise ValueError("fixture HTML exceeded the bounded size limit")
        return BeautifulSoup(value, "html.parser")
    content = getattr(value, "content", None)
    if isinstance(content, (bytes, bytearray)):
        return _coerce_soup(bytes(content))
    raise TypeError("fetcher must return HTML, bytes, a response, or BeautifulSoup")


def _fetch_parse_many(
    items: Iterable[tuple[Any, str, Callable[[BeautifulSoup], Any]]],
    *,
    fetcher: Fetcher,
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
) -> tuple[dict[Any, Any], list[str]]:
    tasks = list(items)
    if not tasks:
        return {}, []

    def worker(key: Any, url: str, parser: Callable[[BeautifulSoup], Any]):
        last_error: Optional[Exception] = None
        for attempt in range(CHEORWON_FETCH_ATTEMPTS):
            current = session_factory()
            try:
                return key, parser(_coerce_soup(fetcher(current, url, timeout)))
            except Exception as exc:
                last_error = exc
            finally:
                _close_quietly(current)
            if attempt + 1 < CHEORWON_FETCH_ATTEMPTS:
                time.sleep(0.3 * (2**attempt))
        raise RuntimeError(_clean(last_error))

    values: dict[Any, Any] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(tasks))) as executor:
        futures = {
            executor.submit(worker, key, url, parser): key
            for key, url, parser in tasks
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                result_key, result = future.result()
                values[result_key] = result
            except Exception as exc:
                errors.append(f"{key}: {_clean(exc)}")
    return values, errors


def _query_page(href: Any, expected_path: str) -> Optional[int]:
    parsed = urlparse(urljoin(CHEORWON_CANONICAL_URL, _clean(href)))
    if parsed.hostname != CHEORWON_HOST or parsed.path != expected_path:
        return None
    values = parse_qs(parsed.query, keep_blank_values=True).get("pageIndex", [])
    if len(values) != 1 or not values[0].isdigit():
        return None
    return int(values[0])


def _form_value(form: Any, name: str) -> tuple[int, str]:
    controls = form.select(f"[name='{name}']")
    return len(controls), _clean(controls[0].get("value")) if controls else ""


def _list_form_errors(
    soup: BeautifulSoup, expected_page: int, expected_year: str
) -> list[str]:
    forms = soup.select("form#lctreVO[name='lctreVOForm']")
    if len(forms) != 1:
        return [f"page {expected_page}: list form missing or duplicated"]
    form = forms[0]
    errors: list[str] = []
    action = urlparse(urljoin(CHEORWON_CANONICAL_URL, _clean(form.get("action"))))
    if (
        _clean(form.get("method")).lower() != "get"
        or action.scheme != "https"
        or action.hostname != CHEORWON_HOST
        or action.path.split(";", 1)[0] != CHEORWON_LIST_PATH
        or action.fragment
    ):
        errors.append(f"page {expected_page}: list form method/action changed")
    for name, expected in (
        ("key", "692"),
        ("insNo", ""),
        # The server honours the requested pageUnit=100 in rows and paging
        # links but renders its search form with the product default 10.
        ("pageUnit", "10"),
    ):
        count, value = _form_value(form, name)
        if count != 1 or value != expected:
            errors.append(f"page {expected_page}: unfiltered form field {name} changed")
    for name in ("semesterType", "rceptSttus", "lctreType"):
        controls = form.select(f"select[name='{name}']")
        if len(controls) != 1:
            errors.append(f"page {expected_page}: filter {name} missing or duplicated")
            continue
        selected = controls[0].select("option[selected]")
        if selected and any(_clean(node.get("value")) for node in selected):
            errors.append(f"page {expected_page}: filter {name} is not unfiltered")
    year_controls = form.select("select#year[name='year']")
    if len(year_controls) != 1:
        errors.append(f"page {expected_page}: year filter missing or duplicated")
    elif expected_year:
        selected = [
            _clean(node.get("value"))
            for node in year_controls[0].select("option[selected]")
        ]
        if selected != [expected_year]:
            errors.append(f"page {expected_page}: year partition selection changed")
    return errors


def _advertised_years(soup: BeautifulSoup) -> tuple[int, ...]:
    controls = soup.select("form#lctreVO select#year[name='year']")
    if len(controls) != 1:
        return ()
    values: list[int] = []
    for option in controls[0].select("option[value]"):
        value = _clean(option.get("value"))
        if re.fullmatch(r"20\d{2}", value):
            values.append(int(value))
    return tuple(values)


def _capacity(value: Any) -> tuple[int, int, int, int]:
    match = _CAPACITY_RE.fullmatch(_clean(value).replace(",", ""))
    if not match:
        raise CheorwonContractError("capacity contract changed")
    current, total, waiting = map(int, match.groups()[:3])
    # Four historical rows publish only one waiting figure.  Keep -1 as an
    # explicit unknown until a required detail supplies the waiting capacity.
    waiting_total = int(match.group(4)) if match.group(4) is not None else -1
    if current < 0 or total < 0 or waiting < 0:
        raise CheorwonContractError("capacity values are invalid")
    return current, total, waiting, waiting_total


def _parse_list_row(
    tr: Any, *, expected_page: int, expected_year: str
) -> dict[str, Any]:
    cells = tr.find_all("td", recursive=False)
    if len(cells) != 10:
        raise CheorwonContractError("course row cell count changed")
    values = [_clean(cell.get_text(" ", strip=True)) for cell in cells]
    links = cells[1].select(":scope > a[href]")
    if len(links) != 1:
        raise CheorwonContractError("course title link missing or duplicated")
    title = _clean(links[0].get_text(" ", strip=True))
    if not title or title != values[1]:
        raise CheorwonContractError("course title changed or is empty")
    detail = urlparse(urljoin(CHEORWON_CANONICAL_URL, _clean(links[0].get("href"))))
    query = parse_qs(detail.query, keep_blank_values=True)
    identity_values = query.get("lctreNo", [])
    ins_values = query.get("insNo", [])
    key_values = query.get("key", [])
    if (
        detail.scheme != "https"
        or detail.hostname != CHEORWON_HOST
        or detail.path != CHEORWON_DETAIL_PATH
        or detail.fragment
        or len(identity_values) != 1
        or not re.fullmatch(r"[1-9]\d*", identity_values[0])
        or len(ins_values) != 1
        or ins_values[0] not in CHEORWON_INSTITUTION_BY_INS_NO
        or key_values != ["692"]
    ):
        raise CheorwonContractError("course detail identity/owner URL changed")
    status = values[9]
    if status not in _SOURCE_STATUS_MAP:
        raise CheorwonContractError("course public status changed")
    if not values[0] or not values[3] or not values[4] or not values[7] or not values[8]:
        raise CheorwonContractError("required list field is empty")
    if values[5] != "-" and not re.fullmatch(
        r"\d{1,6}\s*/\s*\d{1,6}", values[5].replace(",", "")
    ):
        raise CheorwonContractError("receipt-count field changed")
    current, total, waiting, waiting_total = _capacity(values[6])
    identity, ins_no = identity_values[0], ins_values[0]
    return {
        "identity": identity,
        "course_code": values[0],
        "title": title,
        "ins_no": ins_no,
        "source_status": status,
        "source_fee": values[3],
        "source_application_method": values[4],
        "source_capacity_current": current,
        "source_capacity_total": total,
        "source_waiting_current": waiting,
        "source_waiting_total": waiting_total,
        "source_schedule": values[7],
        "source_selection_method": values[8],
        "list_page": expected_page,
        "source_year": expected_year,
        "raw_url": cheorwon_detail_url(identity, ins_no),
    }


def _parse_list(
    soup: BeautifulSoup, expected_page: int, expected_year: str
) -> _ListPage:
    errors = _list_form_errors(soup, expected_page, expected_year)
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != "교육신청 - 철원군평생학습관":
        errors.append(f"page {expected_page}: official list title changed")
    text = _clean(soup.get_text(" ", strip=True))
    total_matches = _TOTAL_RE.findall(text)
    if len(total_matches) != 1:
        total = -1
        errors.append(f"page {expected_page}: advertised total missing or duplicated")
    else:
        total = int(total_matches[0].replace(",", ""))
    last_page = max(1, math.ceil(max(0, total) / CHEORWON_PAGE_SIZE))
    last_links = soup.select("a.p-page__link.next-end[href]")
    if last_links:
        advertised_last = {
            _query_page(node.get("href"), CHEORWON_LIST_PATH) for node in last_links
        }
        if advertised_last != {last_page}:
            errors.append(f"page {expected_page}: advertised last-page link changed")
    elif total > CHEORWON_PAGE_SIZE:
        errors.append(f"page {expected_page}: advertised last-page link missing")
    active = [_clean(node.get_text(" ", strip=True)) for node in soup.select(".p-page__link.active")]
    tables = soup.select("table.table.responsive")
    rows: list[dict[str, Any]] = []
    empty_sentinel = False
    if len(tables) != 1:
        errors.append(f"page {expected_page}: course table missing or duplicated")
    else:
        table = tables[0]
        headers = [_clean(node.get_text(" ", strip=True)) for node in table.select("thead th")]
        expected_headers = [
            "코드번호",
            "강좌명",
            "강사명",
            "수강료",
            "접수 방법",
            "접수 인원",
            "승인/모집 (대기인원)",
            "교육시간 (교육요일)",
            "선정 방법",
            "상태",
        ]
        if headers != expected_headers:
            errors.append(f"page {expected_page}: course table headers changed")
        body_rows = table.select("tbody > tr")
        if len(body_rows) == 1:
            sentinel_cells = body_rows[0].find_all("td", recursive=False)
            empty_sentinel = (
                len(sentinel_cells) == 1
                and _clean(sentinel_cells[0].get_text(" ", strip=True))
                == "등록된 강좌가 없습니다."
            )
        if not empty_sentinel:
            for index, tr in enumerate(body_rows, 1):
                try:
                    rows.append(
                        _parse_list_row(
                            tr,
                            expected_page=expected_page,
                            expected_year=expected_year,
                        )
                    )
                except Exception as exc:
                    errors.append(f"page {expected_page} row {index}: {_clean(exc)}")
    if rows:
        if active != [str(expected_page)]:
            errors.append(f"page {expected_page}: active-page indicator changed")
    elif total == 0 and expected_page == 1:
        if not empty_sentinel:
            errors.append("page 1: zero-total sentinel changed")
    elif expected_page <= last_page:
        errors.append(f"page {expected_page}: advertised page is unexpectedly empty")
    years = _advertised_years(soup)
    if not years or len(years) != len(set(years)) or tuple(sorted(years, reverse=True)) != years:
        errors.append(f"page {expected_page}: advertised year options changed")
    return _ListPage(
        rows=rows,
        total=total,
        current_page=expected_page,
        last_page=last_page,
        advertised_years=years,
        empty_sentinel=empty_sentinel,
        errors=errors,
    )


def _row_signature(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        _clean(row.get("identity")),
        _clean(row.get("course_code")),
        _clean(row.get("title")),
        _clean(row.get("ins_no")),
        _clean(row.get("source_status")),
        _clean(row.get("source_fee")),
        _clean(row.get("source_application_method")),
        int(row.get("source_capacity_current", -1)),
        int(row.get("source_capacity_total", -1)),
        int(row.get("source_waiting_current", -1)),
        int(row.get("source_waiting_total", -1)),
        _clean(row.get("source_schedule")),
        _clean(row.get("source_selection_method")),
    )


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    return tuple(_row_signature(row) for row in rows)


def _date_pair(value: Any, field: str) -> tuple[date, date]:
    matches = _DATE_RE.findall(_clean(value))
    if len(matches) != 2:
        raise CheorwonContractError(f"{field}: expected exactly two dates")
    result: list[date] = []
    for year, month, day_value in matches:
        try:
            result.append(date(int(year), int(month), int(day_value)))
        except ValueError as exc:
            raise CheorwonContractError(f"{field}: invalid calendar date") from exc
    if result[0] > result[1]:
        raise CheorwonContractError(f"{field}: reversed dates")
    return result[0], result[1]


def _integer_people(value: Any, field: str) -> int:
    match = re.fullmatch(r"([\d,]+)\s*명", _clean(value))
    if not match:
        raise CheorwonContractError(f"{field}: person-count contract changed")
    return int(match.group(1).replace(",", ""))


def _fee(value: Any) -> tuple[str, int]:
    text = _clean(value)
    if text == "무료":
        return text, 0
    numbers = re.findall(r"\d+", text.replace(",", ""))
    if len(numbers) != 1 or "원" not in text:
        raise CheorwonContractError("course fee contract changed")
    return text, int(numbers[0])


def _detail_pairs(table: Any) -> tuple[str, dict[str, str], list[str]]:
    heading = ""
    pairs: dict[str, str] = {}
    errors: list[str] = []
    for tr in table.find_all("tr"):
        children = tr.find_all(["th", "td"], recursive=False)
        if len(children) == 1 and children[0].name == "td":
            if heading:
                errors.append("detail heading duplicated")
            heading = _clean(children[0].get_text(" ", strip=True))
            continue
        index = 0
        while index < len(children):
            label = children[index]
            if label.name != "th" or index + 1 >= len(children) or children[index + 1].name != "td":
                errors.append("detail label/value structure changed")
                break
            key = _clean(label.get_text(" ", strip=True))
            value = _clean(children[index + 1].get_text(" ", strip=True))
            if not key or key in pairs:
                errors.append("detail field missing or duplicated")
            else:
                pairs[key] = value
            index += 2
    if not heading:
        errors.append("detail heading missing")
    if set(pairs) != _DETAIL_FIELDS:
        errors.append("detail field set changed")
    return heading, pairs, errors


def _application_controls(
    soup: BeautifulSoup, identity: str, ins_no: str
) -> tuple[list[str], list[str]]:
    controls: list[str] = []
    errors: list[str] = []
    for anchor in soup.find_all("a", href=True):
        parsed = urlparse(urljoin(CHEORWON_CANONICAL_URL, _clean(anchor.get("href"))))
        if parsed.path != CHEORWON_APPLICATION_PATH:
            continue
        query = parse_qs(parsed.query, keep_blank_values=True)
        label = _clean(anchor.get_text(" ", strip=True))
        if (
            parsed.scheme != "https"
            or parsed.hostname != CHEORWON_HOST
            or parsed.fragment
            or query.get("key") != ["692"]
            or query.get("lctreNo") != [identity]
            or query.get("insNo") != [ins_no]
            or label != "강좌신청"
        ):
            errors.append("public application control identity changed")
        else:
            controls.append(cheorwon_application_url(identity, ins_no))
    return controls, errors


def _validate_detail(
    listed: Mapping[str, Any], soup: BeautifulSoup, cutoff: date
) -> tuple[Optional[dict[str, Any]], bool, list[str]]:
    identity = _clean(listed["identity"])
    ins_no = _clean(listed["ins_no"])
    label = f"course {identity} detail"
    errors: list[str] = []
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != "교육신청 - 철원군평생학습관":
        errors.append(f"{label}: official detail title changed")
    tables = soup.select("table")
    if len(tables) != 1:
        return None, False, [f"{label}: detail table missing or duplicated"]
    heading, pairs, pair_errors = _detail_pairs(tables[0])
    errors.extend(f"{label}: {item}" for item in pair_errors)
    if set(pairs) != _DETAIL_FIELDS:
        return None, False, errors
    expected_heading_status = _DETAIL_HEADING_STATUS[_clean(listed["source_status"])]
    if (
        expected_heading_status not in heading
        or _normalized(listed["title"]) not in _normalized(heading)
        or _normalized(listed["course_code"]) not in _normalized(heading)
    ):
        errors.append(f"{label}: heading status/title/code mismatch")
    if pairs["기수"] != _clean(listed["source_year"]):
        errors.append(f"{label}: official year partition mismatch")
    expected_institution = CHEORWON_INSTITUTION_BY_INS_NO.get(ins_no, "")
    institution = _clean(pairs["기관명"])
    if institution != expected_institution:
        errors.append(f"{label}: institution/insNo binding changed")
    for field, actual, wanted in (
        ("접수방법", pairs["접수방법"], listed["source_application_method"]),
        ("수강료", pairs["수강료"], listed["source_fee"]),
        ("선정방법", pairs["선정방법"], listed["source_selection_method"]),
    ):
        if _clean(actual) != _clean(wanted):
            errors.append(f"{label}: {field} list/detail mismatch")
    try:
        start, end = _date_pair(pairs["강좌일자"], f"{label} course period")
    except Exception as exc:
        errors.append(_clean(exc))
        start = end = cutoff
    try:
        apply_start, apply_end = _date_pair(
            pairs["접수기간"], f"{label} application period"
        )
    except Exception as exc:
        errors.append(_clean(exc))
        apply_start = apply_end = cutoff
    try:
        if _integer_people(pairs["모집정원"], f"{label} capacity") != int(
            listed["source_capacity_total"]
        ):
            errors.append(f"{label}: 모집정원 list/detail mismatch")
        detail_waiting_total = _integer_people(
            pairs["대기인원"], f"{label} waiting capacity"
        )
        listed_waiting_total = int(listed["source_waiting_total"])
        if listed_waiting_total >= 0 and detail_waiting_total != listed_waiting_total:
            errors.append(f"{label}: 대기인원 list/detail mismatch")
    except Exception as exc:
        errors.append(_clean(exc))
        detail_waiting_total = max(0, int(listed["source_waiting_total"]))
    try:
        fee_text, fee_amount = _fee(pairs["수강료"])
    except Exception as exc:
        errors.append(_clean(exc))
        fee_text, fee_amount = "", 0
    if not _clean(pairs["강좌분야"]) or not _clean(pairs["강좌대상"]):
        errors.append(f"{label}: required current detail field is empty")
    controls, control_errors = _application_controls(soup, identity, ins_no)
    errors.extend(f"{label}: {item}" for item in control_errors)
    online = "온라인" in _clean(pairs["접수방법"])
    actionable = _clean(listed["source_status"]) in {"접수진행", "대기접수"}
    if actionable and online:
        if len(controls) != 1:
            errors.append(f"{label}: open online application control changed")
    elif controls:
        errors.append(f"{label}: inactive/offline course exposes application control")
    current = end >= cutoff
    if errors:
        return None, current, errors
    methods = [item for item in re.split(r"\s*,\s*", _clean(pairs["접수방법"])) if item]
    application_url = controls[0] if controls else ""
    status = _SOURCE_STATUS_MAP[_clean(listed["source_status"])]
    row = {
        "provider": CHEORWON_PROVIDER,
        "provider_course_id": f"{CHEORWON_PROVIDER}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": _clean(listed["title"]),
        "description": _clean(listed["title"]),
        "branch": institution,
        "branch_code": f"cheorwon:{ins_no}",
        "preserve_branch": True,
        "provider_organizer": institution,
        "category": _clean(pairs["강좌분야"]),
        "program_type": "교육",
        "raw_url": cheorwon_detail_url(identity, ins_no),
        "application_url": application_url,
        "application_type": (
            "ONLINE_RESERVATION"
            if actionable and online
            else "OFFLINE_APPLICATION" if actionable else "INFO_ONLY"
        ),
        "application_method": _clean(pairs["접수방법"]),
        "application_methods": methods,
        "reservation_available": actionable,
        "status": status,
        "fee": fee_text,
        "fee_amount": fee_amount,
        "period": f"{start.isoformat()} ~ {end.isoformat()}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": f"{apply_start.isoformat()} ~ {apply_end.isoformat()}",
        "apply_start": apply_start.isoformat(),
        "apply_end": apply_end.isoformat(),
        "schedule_raw": _clean(listed["source_schedule"]),
        "capacity": (
            f"{listed['source_capacity_current']}/{listed['source_capacity_total']}"
        ),
        "capacity_current": int(listed["source_capacity_current"]),
        "capacity_total": int(listed["source_capacity_total"]),
        "waiting_current": int(listed["source_waiting_current"]),
        "waiting_total": detail_waiting_total,
        "target": _clean(pairs["강좌대상"]),
        "venue": _clean(pairs["강좌장소"]),
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": CHEORWON_PARSER,
        "municipality_code": CHEORWON_MUNICIPALITY_CODE,
        "municipality_full_name": CHEORWON_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "course_code": _clean(listed["course_code"]),
            "list_page": int(listed["list_page"]),
            "source_year": _clean(listed["source_year"]),
            "ins_no": ins_no,
            "source_status": _clean(listed["source_status"]),
            "source_category": _clean(pairs["강좌분야"]),
            "source_period": _clean(pairs["강좌일자"]),
            "source_application_period": _clean(pairs["접수기간"]),
            "source_schedule": _clean(listed["source_schedule"]),
            "source_capacity_current": int(listed["source_capacity_current"]),
            "source_capacity_total": int(listed["source_capacity_total"]),
            "source_waiting_current": int(listed["source_waiting_current"]),
            "source_waiting_total": detail_waiting_total,
            "education_institution": institution,
            "source_target": _clean(pairs["강좌대상"]),
            "source_venue": _clean(pairs["강좌장소"]),
            "source_fee": fee_text,
            "source_application_method": _clean(pairs["접수방법"]),
            "source_selection_method": _clean(pairs["선정방법"]),
            "service_family": "education",
            "detail_verified": True,
            "application_control_present": bool(controls),
            "application_control_contract": (
                "detail_link:key+insNo+lctreNo"
                if controls
                else "inactive_or_offline_detail_has_no_application_control"
            ),
            "application_control_verified": True,
        },
    }
    return row, current, []


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_PERSISTED_KEYS:
        errors.append("forbidden PII/detail/application keys persisted")
    raw_fields = row.get("raw_fields")
    if not isinstance(raw_fields, Mapping) or not set(raw_fields) <= _SAFE_RAW_FIELDS:
        errors.append("raw_fields exceeded the PII-safe allowlist")
    payload = repr(
        {
            key: value
            for key, value in row.items()
            if key not in {"raw_url", "application_url"}
        }
    )
    if _PHONE_RE.search(payload) or _EMAIL_RE.search(payload):
        errors.append("PII-like contact data persisted")
    if row.get("description") != row.get("title"):
        errors.append("arbitrary detail description persisted")
    if _clean(row.get("raw_fields", {}).get("service_family")) != "education":
        errors.append("non-education row reached education persistence")
    return errors


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity in seen:
            continue
        seen.add(identity)
        result.append(row)
    return result


def _base_meta(error: str = "") -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "unfiltered_list_requests": 0,
        "partition_list_requests": 0,
        "sentinel_requests": 0,
        "stability_rechecks": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "source_cap_reached": False,
        "pagination_complete": False,
        "details_complete": False,
        "application_controls_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "returned_count": 0,
        "configured_collection_error": error,
    }


def _validate_partition_pages(
    *,
    label: str,
    year: str,
    first: _ListPage,
    remaining: Mapping[Any, Any],
    errors: list[str],
) -> tuple[list[dict[str, Any]], dict[int, int], int, int]:
    total, last = first.total, first.last_page
    rows: list[dict[str, Any]] = []
    page_counts: dict[int, int] = {}
    first_signature: tuple[tuple[Any, ...], ...] = ()
    for page in range(1, last + 1):
        parsed = first if page == 1 else remaining.get((label, year, page, "data"))
        if not isinstance(parsed, _ListPage):
            errors.append(f"{label} {year or 'all'} page {page}: response missing")
            continue
        errors.extend(parsed.errors)
        if parsed.total != total or parsed.last_page != last:
            errors.append(f"{label} {year or 'all'} page {page}: total/last changed")
        expected = (
            CHEORWON_PAGE_SIZE
            if page < last
            else total - (last - 1) * CHEORWON_PAGE_SIZE
        )
        if total == 0:
            expected = 0
        if len(parsed.rows) != expected:
            errors.append(
                f"{label} {year or 'all'} page {page}: expected {expected} rows, "
                f"got {len(parsed.rows)}"
            )
        page_counts[page] = len(parsed.rows)
        if page == 1:
            first_signature = _page_signature(parsed.rows)
        rows.extend(parsed.rows)
    sentinel = remaining.get((label, year, last + 1, "sentinel"))
    if not isinstance(sentinel, _ListPage):
        errors.append(f"{label} {year or 'all'} sentinel response missing")
    else:
        errors.extend(sentinel.errors)
        if (
            sentinel.total != total
            or sentinel.last_page != last
            or sentinel.rows
            or not sentinel.empty_sentinel
        ):
            errors.append(f"{label} {year or 'all'} empty sentinel changed")
    recheck = remaining.get((label, year, 1, "recheck"))
    if not isinstance(recheck, _ListPage):
        errors.append(f"{label} {year or 'all'} page-one recheck missing")
    else:
        errors.extend(recheck.errors)
        if (
            recheck.total != total
            or recheck.last_page != last
            or _page_signature(recheck.rows) != first_signature
        ):
            errors.append(f"{label} {year or 'all'} page-one stability recheck changed")
    return rows, page_counts, int(isinstance(sentinel, _ListPage)), int(
        isinstance(recheck, _ListPage)
    )


def collect_cheorwon_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 100,
    detail_limit: int = 500,
    today: Optional[date | datetime | str] = None,
    max_workers: int = CHEORWON_MAX_WORKERS,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future Cheorwon education snapshot."""

    meta = _base_meta()
    if not is_cheorwon_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match canonical Cheorwon owner"
        )
        return [], CHEORWON_PARSER, meta
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, int)
        or timeout < 1
        or isinstance(max_pages, bool)
        or not isinstance(max_pages, int)
        or max_pages < 1
        or isinstance(detail_limit, bool)
        or not isinstance(detail_limit, int)
        or detail_limit < 0
        or isinstance(max_workers, bool)
        or not isinstance(max_workers, int)
        or max_workers < 1
    ):
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": (
                    "invalid timeout/max_pages/detail_limit/max_workers cap"
                ),
            }
        )
        return [], CHEORWON_PARSER, meta
    try:
        cutoff = _today(today)
    except ValueError as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], CHEORWON_PARSER, meta
    factory = session_factory or _default_session_factory
    current_fetcher = fetcher or _default_fetcher
    errors: list[str] = []

    first_values, fetch_errors = _fetch_parse_many(
        [
            (
                ("all", "", 1, "data"),
                cheorwon_list_url(1, ""),
                lambda soup: _parse_list(soup, 1, ""),
            )
        ],
        fetcher=current_fetcher,
        session_factory=factory,
        timeout=timeout,
        max_workers=max_workers,
    )
    errors.extend(fetch_errors)
    meta["pages"] += len(first_values)
    meta["list_requests"] += len(first_values)
    meta["unfiltered_list_requests"] += len(first_values)
    first_all = first_values.get(("all", "", 1, "data"))
    if not isinstance(first_all, _ListPage):
        errors.append("unfiltered page 1: response missing")
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], CHEORWON_PARSER, meta
    errors.extend(first_all.errors)
    advertised_years = first_all.advertised_years
    current_future_years = tuple(
        year for year in advertised_years if year >= cutoff.year
    )
    if cutoff.year not in current_future_years:
        errors.append("official year options do not contain the crawl year")
    if errors:
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], CHEORWON_PARSER, meta

    partition_first_items = [
        (
            ("year", str(year), 1, "data"),
            cheorwon_list_url(1, str(year)),
            lambda soup, current_year=str(year): _parse_list(
                soup, 1, current_year
            ),
        )
        for year in current_future_years
    ]
    partition_first, fetch_errors = _fetch_parse_many(
        partition_first_items,
        fetcher=current_fetcher,
        session_factory=factory,
        timeout=timeout,
        max_workers=max_workers,
    )
    errors.extend(fetch_errors)
    meta["pages"] += len(partition_first)
    meta["list_requests"] += len(partition_first)
    meta["partition_list_requests"] += len(partition_first)
    year_first: dict[str, _ListPage] = {}
    for year in current_future_years:
        value = partition_first.get(("year", str(year), 1, "data"))
        if not isinstance(value, _ListPage):
            errors.append(f"year {year} page 1: response missing")
        else:
            errors.extend(value.errors)
            if value.advertised_years != advertised_years:
                errors.append(f"year {year}: advertised year options changed")
            year_first[str(year)] = value
    required_list_requests = first_all.last_page + 2 + sum(
        value.last_page + 2 for value in year_first.values()
    )
    meta.update(
        {
            "source_total": first_all.total,
            "declared_pages": first_all.last_page,
            "advertised_years": list(advertised_years),
            "current_future_years": list(current_future_years),
            "required_list_requests": required_list_requests,
        }
    )
    if required_list_requests > max_pages:
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": (
                    f"max_pages cap allows {max_pages} of "
                    f"{required_list_requests} required list requests"
                ),
            }
        )
        return [], CHEORWON_PARSER, meta
    if errors or len(year_first) != len(current_future_years):
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], CHEORWON_PARSER, meta

    list_items: list[tuple[Any, str, Callable[[BeautifulSoup], Any]]] = []
    for page in range(2, first_all.last_page + 1):
        list_items.append(
            (
                ("all", "", page, "data"),
                cheorwon_list_url(page, ""),
                lambda soup, current_page=page: _parse_list(
                    soup, current_page, ""
                ),
            )
        )
    list_items.extend(
        [
            (
                ("all", "", first_all.last_page + 1, "sentinel"),
                cheorwon_list_url(first_all.last_page + 1, ""),
                lambda soup, current_page=first_all.last_page + 1: _parse_list(
                    soup, current_page, ""
                ),
            ),
            (
                ("all", "", 1, "recheck"),
                cheorwon_list_url(1, ""),
                lambda soup: _parse_list(soup, 1, ""),
            ),
        ]
    )
    for year, first in year_first.items():
        for page in range(2, first.last_page + 1):
            list_items.append(
                (
                    ("year", year, page, "data"),
                    cheorwon_list_url(page, year),
                    lambda soup, current_page=page, current_year=year: _parse_list(
                        soup, current_page, current_year
                    ),
                )
            )
        list_items.extend(
            [
                (
                    ("year", year, first.last_page + 1, "sentinel"),
                    cheorwon_list_url(first.last_page + 1, year),
                    lambda soup, current_page=first.last_page + 1, current_year=year: _parse_list(
                        soup, current_page, current_year
                    ),
                ),
                (
                    ("year", year, 1, "recheck"),
                    cheorwon_list_url(1, year),
                    lambda soup, current_year=year: _parse_list(
                        soup, 1, current_year
                    ),
                ),
            ]
        )
    remaining, fetch_errors = _fetch_parse_many(
        list_items,
        fetcher=current_fetcher,
        session_factory=factory,
        timeout=timeout,
        max_workers=max_workers,
    )
    errors.extend(fetch_errors)
    meta["pages"] += len(remaining)
    meta["list_requests"] += len(remaining)
    all_remaining_count = sum(key[0] == "all" for key in remaining)
    meta["unfiltered_list_requests"] += all_remaining_count
    meta["partition_list_requests"] += len(remaining) - all_remaining_count

    all_rows, all_page_counts, sentinels, rechecks = _validate_partition_pages(
        label="all",
        year="",
        first=first_all,
        remaining=remaining,
        errors=errors,
    )
    meta["sentinel_requests"] += sentinels
    meta["stability_rechecks"] += rechecks
    partition_rows: list[dict[str, Any]] = []
    year_totals: dict[str, int] = {}
    year_page_counts: dict[str, dict[int, int]] = {}
    for year, first in year_first.items():
        rows, counts, sentinels, rechecks = _validate_partition_pages(
            label="year",
            year=year,
            first=first,
            remaining=remaining,
            errors=errors,
        )
        partition_rows.extend(rows)
        year_totals[year] = first.total
        year_page_counts[year] = counts
        meta["sentinel_requests"] += sentinels
        meta["stability_rechecks"] += rechecks

    all_identities = [_clean(row["identity"]) for row in all_rows]
    all_duplicate_count = len(all_identities) - len(set(all_identities))
    if all_duplicate_count:
        errors.append(f"{all_duplicate_count} duplicate unfiltered source identities")
    partition_identities = [_clean(row["identity"]) for row in partition_rows]
    partition_duplicate_count = len(partition_identities) - len(
        set(partition_identities)
    )
    if partition_duplicate_count:
        errors.append(
            f"{partition_duplicate_count} duplicate current/future partition identities"
        )
    all_by_id = {_clean(row["identity"]): row for row in all_rows}
    for row in partition_rows:
        identity = _clean(row["identity"])
        original = all_by_id.get(identity)
        if original is None:
            errors.append(f"year partition identity {identity} is absent from all-source")
        elif _row_signature(original) != _row_signature(row):
            errors.append(f"year partition identity {identity} differs from all-source")
    list_complete = bool(
        not errors
        and len(all_rows) == first_all.total
        and meta["list_requests"] == required_list_requests
        and meta["sentinel_requests"] == 1 + len(year_first)
        and meta["stability_rechecks"] == 1 + len(year_first)
    )
    cancelled_rows = [
        row for row in partition_rows if row["source_status"] == "폐강"
    ]
    detail_candidates = [
        row for row in partition_rows if row["source_status"] != "폐강"
    ]
    if len(detail_candidates) > detail_limit:
        meta["source_cap_reached"] = True
        errors.append(
            f"detail_limit cap allows {detail_limit} of "
            f"{len(detail_candidates)} required current/future-year details"
        )

    current_rows: list[dict[str, Any]] = []
    expired_rows: list[dict[str, Any]] = []
    detail_errors: list[str] = []
    if list_complete and not errors:
        detail_items = [
            (
                ("detail", _clean(row["identity"])),
                _clean(row["raw_url"]),
                lambda soup, listed=dict(row): _validate_detail(
                    listed, soup, cutoff
                ),
            )
            for row in detail_candidates
        ]
        meta["detail_attempts"] = len(detail_items)
        details, detail_fetch_errors = _fetch_parse_many(
            detail_items,
            fetcher=current_fetcher,
            session_factory=factory,
            timeout=timeout,
            max_workers=max_workers,
        )
        detail_errors.extend(detail_fetch_errors)
        meta["pages"] += len(details)
        for listed in detail_candidates:
            identity = _clean(listed["identity"])
            value = details.get(("detail", identity))
            if not isinstance(value, tuple) or len(value) != 3:
                detail_errors.append(f"course {identity}: detail response missing")
                continue
            row, current, item_errors = value
            if item_errors:
                detail_errors.extend(item_errors)
            elif not isinstance(row, dict):
                detail_errors.append(f"course {identity}: validated row missing")
            else:
                meta["detail_pages"] += 1
                if current:
                    current_rows.append(row)
                else:
                    expired_rows.append(row)
    errors.extend(detail_errors)
    meta["detail_errors"] = len(detail_errors)
    details_complete = bool(
        list_complete
        and meta["detail_attempts"] == len(detail_candidates)
        and meta["detail_pages"] == len(detail_candidates)
        and not detail_errors
    )
    application_controls_complete = bool(
        details_complete
        and all(
            row["raw_fields"]["application_control_verified"] is True
            for row in current_rows + expired_rows
        )
    )

    result: list[dict[str, Any]] = []
    if details_complete and application_controls_complete and not errors:
        for row in current_rows:
            errors.extend(_privacy_errors(row))
        if not errors:
            deduper = dedupe_rows or _dedupe_default
            try:
                result = list(deduper(current_rows))
            except Exception as exc:
                errors.append(f"dedupe failed: {_clean(exc)}")
            if len(result) != len(current_rows):
                errors.append(
                    "dedupe changed official identity cardinality "
                    f"{len(current_rows)} to {len(result)}"
                )
                result = []
            else:
                for row in result:
                    errors.extend(_privacy_errors(row))
                if errors:
                    result = []
    snapshot_complete = bool(
        list_complete
        and details_complete
        and application_controls_complete
        and not errors
    )
    if not snapshot_complete:
        result = []
    semantic_counter = Counter(
        (
            _normalized(row["title"]),
            _clean(row["start_date"]),
            _clean(row["end_date"]),
            _clean(row["branch"]),
        )
        for row in current_rows
    )
    meta.update(
        {
            "ownership_scope": CHEORWON_OWNERSHIP_SCOPE,
            "canonical_url": CHEORWON_CANONICAL_URL,
            "registered_url": CHEORWON_REGISTERED_URL,
            "page_counts": all_page_counts,
            "year_page_counts": year_page_counts,
            "year_partition_totals": year_totals,
            "source_rows": len(all_rows),
            "current_future_partition_rows": len(partition_rows),
            "cancelled_partition_count": len(cancelled_rows),
            "detail_candidate_count": len(detail_candidates),
            "expired_after_detail_count": len(expired_rows),
            "current_source_count": len(current_rows),
            "identity_duplicate_count": all_duplicate_count,
            "partition_identity_duplicate_count": partition_duplicate_count,
            "semantic_duplicate_group_count": sum(
                count > 1 for count in semantic_counter.values()
            ),
            "semantic_duplicate_excess_rows": sum(
                max(0, count - 1) for count in semantic_counter.values()
            ),
            "semantic_duplicate_policy": (
                "preserve_distinct_official_lctreNo_within_source_institution"
            ),
            "branch_counts": dict(Counter(_clean(row["branch"]) for row in result)),
            "source_ins_no_counts": dict(
                Counter(_clean(row["ins_no"]) for row in all_rows)
            ),
            "status_counts": dict(Counter(_clean(row["status"]) for row in result)),
            "source_status_counts": dict(
                Counter(_clean(row["source_status"]) for row in all_rows)
            ),
            "current_source_status_counts": dict(
                Counter(
                    _clean(row["raw_fields"]["source_status"])
                    for row in current_rows
                )
            ),
            "application_method_counts": dict(
                Counter(_clean(row["application_method"]) for row in result)
            ),
            "public_application_control_count": sum(
                bool(row["application_url"]) for row in result
            ),
            "pagination_complete": list_complete,
            "details_complete": details_complete,
            "application_controls_complete": application_controls_complete,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": snapshot_complete,
            "returned_count": len(result),
            "no_current_data": bool(snapshot_complete and not current_rows),
            "no_current_reason": (
                "official current/future year partitions have no unexpired courses"
                if snapshot_complete and not current_rows
                else ""
            ),
            "municipality_coverage": [CHEORWON_MUNICIPALITY_CODE],
            "candidate_audit": {
                key: dict(value) for key, value in CHEORWON_CANDIDATE_AUDIT.items()
            },
            "provider_audit": {
                key: dict(value) for key, value in CHEORWON_PROVIDER_AUDIT.items()
            },
            "discovery_audit": dict(CHEORWON_DISCOVERY_AUDIT),
            "separate_library_boundary": {
                "provider": "MUNI_LIB_GWE_GO_KR_E49C8D9C",
                "url": CHEORWON_LIBRARY_PROGRAM_URL,
                "included_in_municipal_result": False,
            },
            "pii_fields_discarded": list(CHEORWON_PII_FIELDS_DISCARDED),
            "pii_payload_persisted": False,
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
    )
    return result, CHEORWON_PARSER, meta


collect = collect_cheorwon_education


__all__ = [
    "CHEORWON_APPLICATION_PATH",
    "CHEORWON_ATTACHMENT_NOTICE_URL",
    "CHEORWON_CANONICAL_CANDIDATE_ID",
    "CHEORWON_CANONICAL_URL",
    "CHEORWON_CANDIDATE_AUDIT",
    "CHEORWON_DETAIL_PATH",
    "CHEORWON_DISCOVERY_AUDIT",
    "CHEORWON_EXCLUDED_CANDIDATE_IDS",
    "CHEORWON_GENERAL_HOMEPAGE_URL",
    "CHEORWON_HOST",
    "CHEORWON_INSTITUTION_BY_INS_NO",
    "CHEORWON_LIBRARY_MAIN_URL",
    "CHEORWON_LIBRARY_PROGRAM_URL",
    "CHEORWON_LIST_PATH",
    "CHEORWON_MUNICIPALITY_CODE",
    "CHEORWON_MUNICIPALITY_NAME",
    "CHEORWON_ORDINANCE_URL",
    "CHEORWON_PAGE_SIZE",
    "CHEORWON_PARSER",
    "CHEORWON_PII_FIELDS_DISCARDED",
    "CHEORWON_PROVIDER",
    "CHEORWON_PROVIDER_AUDIT",
    "CHEORWON_REGISTERED_URL",
    "CheorwonContractError",
    "cheorwon_application_url",
    "cheorwon_detail_url",
    "cheorwon_list_url",
    "collect",
    "collect_cheorwon_education",
    "is_cheorwon_education_target",
    "is_cheorwon_excluded_candidate",
    "is_cheorwon_separate_library_target",
]
