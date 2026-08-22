"""Fail-closed collectors for Haenam-gun's official education catalogues.

Haenam has no single municipal reservation catalogue.  The structured
County-owned records are split between the Haenam Education Foundation and
the Haenam County Library.  The JNE Haenam Library is an independently owned
education-office catalogue and is deliberately left with its existing
provider identity.

The foundation collector walks the current regular cohort plus every
non-regular POST page through a stable structural empty sentinel.  The county
library collector walks every list page through its own stable structural
sentinel.  Both verify every retained current/future course against its
identity-bearing detail and application control.  Application forms are never
requested.

The county-library detail page publishes a masked applicant table after the
course facts.  Its response bytes are cut before that section *before* they
are decoded or parsed, so applicant names, phones, and application timestamps
never enter the parser.  Free-form bodies, instructors, contacts,
attachments, session values, and application payloads are likewise discarded.
"""

from __future__ import annotations

import base64
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import html
import re
import ssl
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import certifi
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


HAENAM_MUNICIPALITY_CODE = "1279000000"
HAENAM_MUNICIPALITY_NAME = "전남광주통합특별시 해남군"

HAENAM_FOUNDATION_PROVIDER = "MUNI_WWW_HAENAMEDU_OR_KR_00C5EA00"
HAENAM_FOUNDATION_HOST = "www.haenamedu.or.kr"
HAENAM_FOUNDATION_PATH = "/index.9is"
HAENAM_FOUNDATION_LIST_UID = "8ae590de8abc1022018bad9f9b5d5725"
HAENAM_FOUNDATION_DETAIL_UID = "8ae590de8abc1022018bada1cca6575f"
HAENAM_FOUNDATION_APPLY_UID = "8ae590de8abc1022018bada20a925766"
HAENAM_FOUNDATION_URL = (
    f"https://{HAENAM_FOUNDATION_HOST}{HAENAM_FOUNDATION_PATH}?"
    f"contentUid={HAENAM_FOUNDATION_LIST_UID}"
)
HAENAM_FOUNDATION_POST_URL = (
    f"https://{HAENAM_FOUNDATION_HOST}{HAENAM_FOUNDATION_PATH}"
)
HAENAM_FOUNDATION_OLD_PROVIDER = "MUNI_WWW_HAENAMEDU_OR_KR_94D9F5B5"
HAENAM_FOUNDATION_OLD_URL = (
    "https://www.haenamedu.or.kr/planweb/board/list.9is?"
    "contentUid=8ae590de8abc1022018bad9e388c56f9&"
    "boardUid=8ae590de8bb378c9018bb7a9f4304f84&"
    "contentUid=8ae590de8abc1022018bad9e388c56f9"
)

HAENAM_LIBRARY_PROVIDER = "MUNI_LIB_HAENAM_GO_KR_7113BCF8"
HAENAM_LIBRARY_HOST = "lib.haenam.go.kr"
HAENAM_LIBRARY_PATH = "/main/sub.php"
HAENAM_LIBRARY_URL = "https://lib.haenam.go.kr/main/sub.php?mno=43"
HAENAM_LIBRARY_CULTURE_ALIAS_URL = (
    "https://lib.haenam.go.kr/main/sub.php?mno=18"
)
HAENAM_LIBRARY_WISH_BOARD_URL = (
    "https://lib.haenam.go.kr/main/sub.php?mno=64"
)
HAENAM_LIBRARY_APPLICATION_HISTORY_URL = (
    "https://lib.haenam.go.kr/main/sub.php?mno=49"
)

HAENAM_JNE_PROVIDER = "MUNI_HNLIB_JNE_GO_KR_3E3E5BCA"
HAENAM_JNE_URL = "https://hnlib.jne.go.kr/lecture.es?mid=b30402000000"
HAENAM_JNE_BRANCH = "전남광주통합특별시교육청해남도서관"
HAENAM_JNE_DUPLICATE_CANDIDATE_IDS = frozenset(
    {"MUNI_IR_1C619F1AD21F", "MUNI_IR_F72CB5954043"}
)

HAENAM_COUNTY_HOME_URL = "https://www.haenam.go.kr/index.9is"
HAENAM_COUNTY_LIFELONG_URLS = (
    "https://www.haenam.go.kr/index.9is?contentUid=18e3368f7ddb78a2017deaae95035f8c",
    "https://www.haenam.go.kr/index.9is?contentUid=18e3368f7ddb78a2017deaaf46f35fad",
    "https://www.haenam.go.kr/index.9is?contentUid=18e3368f7ddb78a2017deaaf9bbd5fb3",
)
HAENAM_RESIDENT_NOTICE_URL = (
    "https://www.haenam.go.kr/planweb/board/view.9is?"
    "contentUid=18e3368f5d745106015de95ebe732057&"
    "boardUid=18e3368f5fb80fdc015fdc42b7e003e0&"
    "pBoardId=BBSMSTR_000000000231&"
    "dataUid=18e3368f5d542987015d63ee65c202ff&nttId=121749"
)

HAENAM_FOUNDATION_PAGE_SIZE = 12
HAENAM_LIBRARY_PAGE_SIZE = 20
HAENAM_MAX_PAGES = 30
HAENAM_MAX_DETAILS = 300
HAENAM_MAX_HTML_BYTES = 3_000_000
HAENAM_FETCH_ATTEMPTS = 2

HAENAM_FOUNDATION_PARSER = (
    "haenam_education_foundation_regular_cohort+nonregular_post_all_pages+"
    "structural_empty_sentinel+stable_boundaries+serial_current_details+"
    "identity_bound_login_controls+facility_branches+pii_allowlist"
)
HAENAM_LIBRARY_PARSER = (
    "haenam_county_library_programs_all_pages+structural_empty_sentinel+"
    "stable_boundaries+education_only+privacy_cut_before_applicant_table+"
    "current_details+identity_bound_application_controls+facility_branches"
)

HAENAM_FOUNDATION_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "canonical_url": HAENAM_FOUNDATION_URL,
    "regular_rows": 36,
    "regular_current_or_future": 36,
    "nonregular_page_counts": [12, 12, 12, 11],
    "nonregular_empty_sentinel_page": 5,
    "nonregular_rows": 47,
    "source_total": 83,
    "unique_source_identities": 83,
    "source_status_counts": {"접수중": 38, "접수마감": 45},
    "current_or_future": 42,
    "current_status_counts": {"접수중": 38, "접수마감": 4},
    "current_regular": 36,
    "current_nonregular": 6,
    "current_branch_counts": {
        "해남군평생학습관": 36,
        "해남군교육재단": 3,
        "해남군 관내": 2,
        "미래행복평생교육원": 1,
    },
    "public_identity_bound_login_controls": 36,
    "tls_leaf_valid_through": "2026-10-30",
    "tls_missing_intermediate": "RapidSSL TLS RSA CA G1",
}

HAENAM_LIBRARY_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "canonical_url": HAENAM_LIBRARY_URL,
    "declared_total": 21,
    "page_counts": [20, 1],
    "empty_sentinel_page": 3,
    "unique_source_identities": 21,
    "excluded_non_education_service": {
        "identity": "991",
        "category": "도서장기대여",
        "title": "다중이용시설 도서대여서비스",
    },
    "current_or_future_education": 20,
    "category_counts": {
        "문화강좌": 18,
        "독서교실": 1,
        "2026 길위의 인문학": 1,
    },
    "source_application_control_counts": {
        "신청하기": 13,
        "대기자신청": 7,
    },
    "current_branch_counts": {
        "해남문화예술회관": 18,
        "해남군립도서관": 2,
    },
    "privacy_boundary": (
        "detail bytes are cut before Progdetails_report/applicant sections"
    ),
}

HAENAM_JNE_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "provider": HAENAM_JNE_PROVIDER,
    "canonical_url": HAENAM_JNE_URL,
    "page_counts": [100, 36],
    "empty_sentinel_page": 3,
    "source_total": 136,
    "current_or_future": 10,
    "current_status_counts": {"마감": 9, "대기자신청하기": 1},
    "exact_branch": HAENAM_JNE_BRANCH,
    "decision": "keep_existing_separate_education_office_owner",
}

HAENAM_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "municipality_code": HAENAM_MUNICIPALITY_CODE,
    "foundation": HAENAM_FOUNDATION_DISCOVERY_AUDIT,
    "county_library": HAENAM_LIBRARY_DISCOVERY_AUDIT,
    "jne_library": HAENAM_JNE_DISCOVERY_AUDIT,
    "county_general_reservation_ledger": "not_found",
    "resident_community": (
        "2026 notice plus one HWPX attachment; no per-course detail/control"
    ),
}

HAENAM_CANDIDATE_AUDIT: Mapping[str, Mapping[str, str]] = {
    "MUNI_IR_1C619F1AD21F": {
        "provider": "MUNI_HNLIB_JNE_GO_KR_95ADB222",
        "url": "https://hnlib.jne.go.kr/index.es?sid=b3",
        "decision": f"duplicate_of_{HAENAM_JNE_PROVIDER}",
    },
    "MUNI_IR_F72CB5954043": {
        "provider": "MUNI_HNLIB_JNE_GO_KR_46063590",
        "url": (
            "https://hnlib.jne.go.kr/lecture.es?mid=b30402000000&act=view"
        ),
        "decision": f"malformed_detail_duplicate_of_{HAENAM_JNE_PROVIDER}",
    },
    "MUNI_IR_6AB89B685EC0": {
        "provider": "MUNI_WWW_HAMAN_GO_KR",
        "url": "https://www.haman.go.kr/yeyak.web",
        "decision": "exclude_wrong_municipality_haman_query_contamination",
    },
}

HAENAM_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    HAENAM_FOUNDATION_PROVIDER: {
        "decision": "new_structured_education_foundation_owner",
        "catalogues": (HAENAM_FOUNDATION_URL,),
        "replaces": HAENAM_FOUNDATION_OLD_PROVIDER,
    },
    HAENAM_LIBRARY_PROVIDER: {
        "decision": "new_separate_county_library_program_owner",
        "catalogues": (HAENAM_LIBRARY_URL,),
        "aliases": (HAENAM_LIBRARY_CULTURE_ALIAS_URL,),
    },
    HAENAM_JNE_PROVIDER: {
        "decision": "keep_separate_education_office_library_owner",
        "catalogues": (HAENAM_JNE_URL,),
        "exact_branch": HAENAM_JNE_BRANCH,
    },
    HAENAM_FOUNDATION_OLD_PROVIDER: {
        "decision": "replace_deprecated_notice_board_with_current_catalogue",
        "catalogues": (HAENAM_FOUNDATION_OLD_URL,),
    },
}

HAENAM_NO_LEDGER_AUDIT: Mapping[str, Any] = {
    "county_home": {
        "url": HAENAM_COUNTY_HOME_URL,
        "decision": "exclude_general_homepage_no_course_ledger",
    },
    "county_lifelong_pages": {
        "urls": HAENAM_COUNTY_LIFELONG_URLS,
        "decision": "exclude_static_facility_and_program_family_summaries",
    },
    "resident_community_notice": {
        "url": HAENAM_RESIDENT_NOTICE_URL,
        "decision": "exclude_notice_and_hwpx_attachment_only",
        "live_evidence": (
            "2026-06-16 notice; 11 courses/97 learners stated only in notice "
            "and attachment; applications are in-person at 읍면 centers"
        ),
    },
    "library_wish_board": {
        "url": HAENAM_LIBRARY_WISH_BOARD_URL,
        "decision": "exclude_user_submitted_wish_course_board",
    },
    "library_application_history": {
        "url": HAENAM_LIBRARY_APPLICATION_HISTORY_URL,
        "decision": "never_request_private_application_history",
    },
}

HAENAM_PII_FIELDS_DISCARDED = (
    "instructor and staff names",
    "contact phones and emails",
    "masked applicant names and phone numbers",
    "applicant dates/statuses and applicant tables",
    "free-form descriptions and images",
    "attachments and download URLs",
    "login/session/CI values",
    "application form fields and payloads",
    "source HTML",
)


# Public RapidSSL TLS RSA CA G1 intermediate.  The foundation server omits
# this intermediate from its handshake.  Hostname and certificate verification
# remain enabled and the embedded DER is fingerprint-checked before use.
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


Requester = Callable[[Any, str, str, int, Optional[Mapping[str, str]]], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:010[\s().-]*\d{3,4}[\s.-]*\d{4}|"
    r"0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4})(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_FOUNDATION_ID_RE = re.compile(r"^OES_[0-9]{16}$")
_FOUNDATION_COHORT_RE = re.compile(r"^OEC_[0-9]{16}$")
_FOUNDATION_PLACE_RE = re.compile(r"^OEP_[0-9]{16}$")
_ISO_RANGE_RE = re.compile(
    r"^(?P<start>20\d{2}-\d{2}-\d{2})\s*~\s*"
    r"(?P<end>20\d{2}-\d{2}-\d{2})$"
)
_ISO_DATETIME_RANGE_RE = re.compile(
    r"^(?P<start>20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2})\s*~\s*"
    r"(?P<end>20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2})$"
)
_LIBRARY_TOTAL_RE = re.compile(r"게시물\s*:\s*(?P<total>[0-9,]+)개")
_LIBRARY_COUNT_RE = re.compile(
    r"^신청\s*(?P<current>\d+)\s*/\s*정원\s*(?P<total>\d+)$"
)
_LIBRARY_DETAIL_COUNT_RE = re.compile(
    r"^(?P<current>\d+)\s*/\s*(?P<total>\d+)\s*/\s*"
    r"(?P<waiting>\d+)\s*\(총(?P<overall>\d+)\)"
)
_LIBRARY_PRIVACY_MARKER = b'<div class="Progdetails_report_tit'

_FOUNDATION_STATUS_MAP: Mapping[str, tuple[str, str, str]] = {
    "접수중": ("OPEN", "btn_acc", "P"),
    "접수마감": ("CLOSED", "btn_end", "C"),
}
_LIBRARY_APPLICATION_LABELS = frozenset({"신청하기", "대기자신청"})
_LIBRARY_CLOSED_LABELS = frozenset({"마감"})
_LIBRARY_EDUCATION_CATEGORIES = frozenset({"문화강좌", "독서교실"})
_LIBRARY_EXCLUDED_CATEGORIES = frozenset({"도서장기대여", "전집대출"})

_ALLOWED_ROW_KEYS = frozenset(
    {
        "provider",
        "provider_course_id",
        "prefer_incoming_provider_course_id",
        "title",
        "description",
        "branch",
        "branch_code",
        "preserve_branch",
        "category",
        "program_type",
        "raw_url",
        "application_url",
        "application_type",
        "application_method_raw",
        "reservation_available",
        "status",
        "fee",
        "period",
        "start_date",
        "end_date",
        "apply_period",
        "apply_start_date",
        "apply_end_date",
        "schedule_raw",
        "target",
        "capacity_current",
        "capacity_total",
        "venue_name",
        "collection_category",
        "domain_category",
        "operator_type",
        "source_group",
        "service_group",
        "collection_type",
        "municipality_code",
        "municipality_name",
        "raw_fields",
    }
)
_ALLOWED_RAW_KEYS = frozenset(
    {
        "parser",
        "source_identity",
        "source_page",
        "source_partition",
        "source_category",
        "source_status",
        "source_application_control",
        "source_application_method",
        "source_waiting_capacity",
        "source_material_fee",
        "fee_evidence",
        "target_evidence",
        "schedule_evidence",
        "venue_evidence",
        "detail_verified",
        "application_control_verified",
        "privacy_cut_applied",
    }
)
_PII_CHECK_ROW_KEYS = frozenset(
    {
        "title",
        "description",
        "branch",
        "program_type",
        "application_method_raw",
        "fee",
        "schedule_raw",
        "target",
        "venue_name",
    }
)
_PII_CHECK_RAW_KEYS = frozenset(
    {
        "source_category",
        "source_status",
        "source_application_control",
        "source_application_method",
        "source_material_fee",
    }
)


class HaenamContractError(ValueError):
    """Raised when an audited Haenam source contract changes."""


@dataclass(frozen=True)
class _FoundationPage:
    partition: str
    page: int
    rows: tuple[dict[str, Any], ...]
    empty_sentinel: bool


@dataclass(frozen=True)
class _LibraryPage:
    page: int
    rows: tuple[dict[str, Any], ...]
    declared_total: int
    declared_last_page: int
    empty_sentinel: bool


class _SSLContextAdapter(HTTPAdapter):
    def __init__(self, context: ssl.SSLContext) -> None:
        retry = Retry(
            total=2,
            connect=2,
            read=1,
            redirect=0,
            backoff_factor=0.2,
            allowed_methods=frozenset({"GET", "POST"}),
        )
        self._context = context
        super().__init__(max_retries=retry)

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        kwargs["ssl_context"] = self._context
        super().init_poolmanager(*args, **kwargs)


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
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    try:
        return date.fromisoformat(_clean(value))
    except ValueError as exc:
        raise HaenamContractError("cutoff must be an ISO date") from exc


def _branch_code(provider: str, branch: str) -> str:
    digest = hashlib.sha1(branch.encode("utf-8")).hexdigest()[:12].upper()
    return f"{provider}:BRANCH:{digest}"[:100]


def _tls_context() -> ssl.SSLContext:
    der = base64.b64decode(_RAPIDSSL_G1_DER_B64)
    if hashlib.sha256(der).hexdigest() != _RAPIDSSL_G1_SHA256:
        raise RuntimeError("embedded RapidSSL intermediate fingerprint changed")
    context = ssl.create_default_context(cafile=certifi.where())
    context.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(der))
    return context


def _foundation_session() -> requests.Session:
    current = requests.Session()
    current.mount(
        f"https://{HAENAM_FOUNDATION_HOST}/",
        _SSLContextAdapter(_tls_context()),
    )
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (compatible; MooncenMunicipalAudit/1.0; "
                "+https://www.haenamedu.or.kr/)"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "*/*;q=0.8"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9",
            "Referer": "https://www.haenamedu.or.kr/",
        }
    )
    return current


def _library_session() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (compatible; MooncenMunicipalAudit/1.0; "
                "+https://lib.haenam.go.kr/)"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "*/*;q=0.8"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return current


def _default_requester(
    session: Any,
    method: str,
    url: str,
    timeout: int,
    data: Optional[Mapping[str, str]],
) -> Any:
    return session.request(
        method,
        url,
        timeout=timeout,
        allow_redirects=False,
        data=data,
    )


def _header(response: Any, name: str) -> str:
    headers = getattr(response, "headers", {}) or {}
    for key, value in headers.items():
        if str(key).casefold() == name.casefold():
            return _clean(value)
    return ""


def _response_bytes(response: Any) -> bytes:
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        return content
    if isinstance(content, bytearray):
        return bytes(content)
    raise HaenamContractError("requester must return a byte-backed response")


def _request_soup(
    session: Any,
    requester: Requester,
    method: str,
    url: str,
    timeout: int,
    *,
    data: Optional[Mapping[str, str]] = None,
    encoding: str,
    privacy_cut: bool = False,
) -> BeautifulSoup:
    response = requester(session, method, url, timeout, data)
    if int(getattr(response, "status_code", 0) or 0) != 200:
        raise HaenamContractError(
            f"{url}: HTTP {getattr(response, 'status_code', None)!r}"
        )
    if tuple(getattr(response, "history", ()) or ()):
        raise HaenamContractError(f"{url}: redirects are not permitted")
    if _clean(getattr(response, "url", "")) != url:
        raise HaenamContractError(f"{url}: unexpected final URL")
    if "text/html" not in _header(response, "Content-Type").lower():
        raise HaenamContractError(f"{url}: non-HTML response")
    body = _response_bytes(response)
    if not body or len(body) > HAENAM_MAX_HTML_BYTES:
        raise HaenamContractError(f"{url}: invalid body size {len(body)}")
    if privacy_cut:
        marker = body.find(_LIBRARY_PRIVACY_MARKER)
        if marker < 0:
            raise HaenamContractError(
                f"{url}: applicant/free-body privacy boundary missing"
            )
        body = body[:marker] + b"</body></html>"
    try:
        text = body.decode(encoding, errors="strict")
    except UnicodeDecodeError as exc:
        raise HaenamContractError(f"{url}: invalid {encoding} document") from exc
    return BeautifulSoup(text, "html.parser")


def _close_quietly(value: Any) -> None:
    try:
        close = getattr(value, "close", None)
        if callable(close):
            close()
    except Exception:
        pass


def _parse_date_range(value: Any, identity: str) -> tuple[date, date]:
    match = _ISO_RANGE_RE.fullmatch(_clean(value))
    if not match:
        raise HaenamContractError(f"course {identity}: invalid date range")
    start = date.fromisoformat(match.group("start"))
    end = date.fromisoformat(match.group("end"))
    if start > end:
        raise HaenamContractError(f"course {identity}: reversed date range")
    return start, end


def _parse_datetime_range(
    value: Any,
    identity: str,
) -> tuple[datetime, datetime]:
    match = _ISO_DATETIME_RANGE_RE.fullmatch(_clean(value))
    if not match:
        raise HaenamContractError(f"course {identity}: invalid datetime range")
    start = datetime.strptime(match.group("start"), "%Y-%m-%d %H:%M")
    end = datetime.strptime(match.group("end"), "%Y-%m-%d %H:%M")
    if start > end:
        raise HaenamContractError(f"course {identity}: reversed datetime range")
    return start, end


def _safe_source_text(value: Any, identity: str, field: str) -> str:
    text = _clean(value)
    if (
        not text
        or len(text) > 500
        or _PHONE_RE.search(text)
        or _EMAIL_RE.search(text)
    ):
        raise HaenamContractError(f"course {identity}: unsafe {field}")
    return text


def _validate_output(row: Mapping[str, Any]) -> None:
    if frozenset(row) - _ALLOWED_ROW_KEYS:
        raise HaenamContractError("emitted row contains unknown fields")
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping) or frozenset(raw) - _ALLOWED_RAW_KEYS:
        raise HaenamContractError("emitted row contains unsafe raw_fields")
    values = [
        str(row[key])
        for key in _PII_CHECK_ROW_KEYS
        if key in row and row[key] is not None
    ]
    values.extend(
        str(raw[key])
        for key in _PII_CHECK_RAW_KEYS
        if key in raw and raw[key] is not None
    )
    combined = " ".join(values)
    if _PHONE_RE.search(combined) or _EMAIL_RE.search(combined):
        raise HaenamContractError("emitted row leaked contact data")


def _foundation_post_data(page: int) -> dict[str, str]:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise HaenamContractError("invalid foundation page")
    return {
        "contentUid": HAENAM_FOUNDATION_LIST_UID,
        "oecRegularYn": "2",
        "mberId": "",
        "userorgId": "",
        "userNm": "",
        "birthDe": "",
        "phoneNo": "",
        "searchKeyword": "",
        "nowPage": str(page),
    }


def _validate_https_url(value: Any, host: str, path: str) -> Any:
    parsed = urlparse(_clean(value))
    if (
        parsed.scheme != "https"
        or parsed.hostname != host
        or parsed.username
        or parsed.password
        or parsed.port is not None
        or parsed.fragment
        or parsed.path != path
    ):
        raise HaenamContractError("URL left the audited HTTPS owner scope")
    return parsed


def haenam_foundation_detail_url(
    identity: Any,
    cohort: Any,
    place: Any,
    accept: Any,
    add_accept: Any,
) -> str:
    identity_text = _clean(identity)
    cohort_text = _clean(cohort)
    place_text = _clean(place)
    accept_text = _clean(accept)
    add_text = _clean(add_accept)
    if (
        not _FOUNDATION_ID_RE.fullmatch(identity_text)
        or not _FOUNDATION_COHORT_RE.fullmatch(cohort_text)
        or not (
            _FOUNDATION_PLACE_RE.fullmatch(place_text)
            or place_text in {"ALL", "DMCCTZUNV"}
        )
        or accept_text not in {"P", "C"}
        or add_text not in {"Y", "N", "C"}
    ):
        return ""
    params = {
        "contentUid": HAENAM_FOUNDATION_DETAIL_UID,
        "oesSubjectId": identity_text,
        "oecId": cohort_text,
        "isAccept": accept_text,
        "isAddAccept": add_text,
        "oecPlaceId": place_text,
    }
    return f"{HAENAM_FOUNDATION_POST_URL}?{urlencode(params)}"


def _foundation_identity_link(value: Any) -> dict[str, str]:
    absolute = urljoin(HAENAM_FOUNDATION_URL, _clean(value))
    parsed = _validate_https_url(
        absolute,
        HAENAM_FOUNDATION_HOST,
        HAENAM_FOUNDATION_PATH,
    )
    query = parse_qs(parsed.query, keep_blank_values=True)
    expected = {
        "contentUid",
        "oesSubjectId",
        "oecId",
        "isAccept",
        "isAddAccept",
        "oecPlaceId",
    }
    if set(query) != expected or any(len(values) != 1 for values in query.values()):
        raise HaenamContractError("foundation detail identity query changed")
    values = {key: items[0] for key, items in query.items()}
    expected_url = haenam_foundation_detail_url(
        values["oesSubjectId"],
        values["oecId"],
        values["oecPlaceId"],
        values["isAccept"],
        values["isAddAccept"],
    )
    if not expected_url:
        raise HaenamContractError("foundation detail identity is invalid")
    values["url"] = expected_url
    return values


def _foundation_document_contract(
    soup: BeautifulSoup,
    partition: str,
    page: int,
) -> None:
    if (
        soup.title is None
        or _clean(soup.title.get_text(" ", strip=True))
        != "해남군교육재단 > 교육재단 교육정보"
    ):
        raise HaenamContractError(
            f"foundation {partition} page {page}: owner/title changed"
        )
    form = soup.select_one("form#searchMyAccept")
    if form is None:
        raise HaenamContractError(
            f"foundation {partition} page {page}: form missing"
        )
    action = urljoin(HAENAM_FOUNDATION_URL, _clean(form.get("action")))
    if (
        action != HAENAM_FOUNDATION_POST_URL
        or _clean(form.get("method")).casefold() != "post"
    ):
        raise HaenamContractError(
            f"foundation {partition} page {page}: form action changed"
        )
    content = form.select_one("input[name=contentUid]")
    regular = form.select_one("input[name=oecRegularYn]")
    if (
        content is None
        or _clean(content.get("value")) != HAENAM_FOUNDATION_LIST_UID
        or regular is None
        or _clean(regular.get("value")) != partition
    ):
        raise HaenamContractError(
            f"foundation {partition} page {page}: form scope changed"
        )


def _foundation_card_field(
    card: Any,
    label: str,
    identity: str,
    *,
    allow_empty: bool = False,
) -> str:
    values: list[str] = []
    for item in card.select(".txtBox > dd"):
        text = _clean(item.get_text(" ", strip=True))
        if text.startswith(label):
            values.append(_clean(text[len(label) :]))
    if len(values) != 1 or (not values[0] and not allow_empty):
        raise HaenamContractError(
            f"course {identity}: list field {label!r} changed"
        )
    return values[0]


def _foundation_checkbox_datetime(
    checkbox: Any,
    prefix: str,
    identity: str,
) -> datetime:
    day = _clean(checkbox.get(f"data-{prefix}-date"))
    hour = _clean(checkbox.get(f"data-{prefix}-time"))
    minute = _clean(checkbox.get(f"data-{prefix}-minute"))
    if (
        not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", day)
        or not re.fullmatch(r"\d{2}", hour)
        or not re.fullmatch(r"\d{2}", minute)
    ):
        raise HaenamContractError(
            f"course {identity}: checkbox {prefix} datetime changed"
        )
    try:
        return datetime.strptime(f"{day} {hour}:{minute}", "%Y-%m-%d %H:%M")
    except ValueError as exc:
        raise HaenamContractError(
            f"course {identity}: invalid checkbox {prefix} datetime"
        ) from exc


def _parse_foundation_page(
    soup: BeautifulSoup,
    partition: str,
    page: int,
) -> _FoundationPage:
    if partition not in {"1", "2"}:
        raise HaenamContractError("unknown foundation partition")
    _foundation_document_contract(soup, partition, page)
    guide = soup.select_one(f'.guideList[data-regular-yn="{partition}"]')
    if guide is None or guide.select_one(":scope > .listBox") is None:
        raise HaenamContractError(
            f"foundation {partition} page {page}: list container changed"
        )

    rows: list[dict[str, Any]] = []
    for card in guide.select(":scope > .listBox > li"):
        links = card.select(":scope > a[href*='oesSubjectId']")
        if len(links) != 1:
            raise HaenamContractError(
                f"foundation {partition} page {page}: identity link changed"
            )
        link = _foundation_identity_link(links[0].get("href"))
        identity = link["oesSubjectId"]
        title_node = card.select_one(".txtBox > dt")
        title = _safe_source_text(
            title_node.get_text(" ", strip=True) if title_node else "",
            identity,
            "title",
        )
        event_text = _foundation_card_field(card, "교육기간", identity)
        event_start, event_end = _parse_date_range(event_text, identity)
        institution = _clean(
            _foundation_card_field(
                card,
                "교육기관",
                identity,
                allow_empty=True,
            )
        )
        if institution not in {"해남군교육재단", "", "-"}:
            raise HaenamContractError(
                f"course {identity}: education owner changed"
            )
        method = _safe_source_text(
            _foundation_card_field(card, "접수방법", identity),
            identity,
            "application method",
        )

        status_node = card.select_one(".txt_day > .txt > span:last-child")
        source_status = _clean(
            status_node.get_text(" ", strip=True) if status_node else ""
        )
        if source_status not in _FOUNDATION_STATUS_MAP:
            raise HaenamContractError(
                f"course {identity}: source status changed"
            )
        status, status_class, accept_state = _FOUNDATION_STATUS_MAP[source_status]
        if status_class not in {
            _clean(value) for value in (status_node.get("class") or [])
        }:
            raise HaenamContractError(
                f"course {identity}: status class changed"
            )
        accepting = "accepting" in {
            _clean(value) for value in (card.get("class") or [])
        }
        if accepting != (source_status == "접수중"):
            raise HaenamContractError(
                f"course {identity}: status/card class contradiction"
            )

        checkbox = card.select_one("input.course-checkbox")
        if (
            checkbox is None
            or _clean(checkbox.get("type")) != "checkbox"
            or _clean(checkbox.get("name")) != "selectedCourse"
            or _clean(checkbox.get("value")) != identity
            or _clean(checkbox.get("data-oec-id")) != link["oecId"]
            or _clean(checkbox.get("data-accept-status")) != accept_state
            or link["isAccept"] != accept_state
            or _clean(checkbox.get("data-add-accept-status"))
            != link["isAddAccept"]
        ):
            raise HaenamContractError(
                f"course {identity}: identity checkbox changed"
            )
        apply_start = _foundation_checkbox_datetime(
            checkbox,
            "start",
            identity,
        )
        apply_end = _foundation_checkbox_datetime(checkbox, "end", identity)
        if apply_start > apply_end:
            raise HaenamContractError(
                f"course {identity}: reversed application period"
            )
        current_text = _clean(checkbox.get("data-confirm-count"))
        total_text = _clean(checkbox.get("data-user-no"))
        if not current_text.isdigit() or not (
            total_text.isdigit() or total_text == "null"
        ):
            raise HaenamContractError(
                f"course {identity}: list capacity changed"
            )
        capacity_current = int(current_text)
        capacity_total: Optional[int] = (
            None if total_text == "null" else int(total_text)
        )
        if capacity_total and capacity_current > capacity_total:
            raise HaenamContractError(
                f"course {identity}: impossible list capacity"
            )
        rows.append(
            {
                "identity": identity,
                "cohort": link["oecId"],
                "place_identity": link["oecPlaceId"],
                "accept": link["isAccept"],
                "add_accept": link["isAddAccept"],
                "detail_url": link["url"],
                "partition": "regular" if partition == "1" else "nonregular",
                "page": page,
                "title": title,
                "event_start": event_start,
                "event_end": event_end,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "institution": institution,
                "method": method,
                "source_status": source_status,
                "status": status,
                "capacity_current": capacity_current,
                "capacity_total": capacity_total,
            }
        )
    return _FoundationPage(
        partition="regular" if partition == "1" else "nonregular",
        page=page,
        rows=tuple(rows),
        empty_sentinel=not rows,
    )


def _foundation_page_signature(page: _FoundationPage) -> tuple[Any, ...]:
    return (
        page.partition,
        page.page,
        page.empty_sentinel,
        tuple(
            (
                row["identity"],
                row["cohort"],
                row["title"],
                row["source_status"],
                row["event_start"],
                row["event_end"],
                row["apply_start"],
                row["apply_end"],
            )
            for row in page.rows
        ),
    )


def _foundation_detail_fields(
    soup: BeautifulSoup,
    identity: str,
) -> dict[str, str]:
    required = {
        "교육명",
        "년도",
        "교육기간",
        "접수기간",
        "교육장소",
        "교육기관",
        "수강료",
        "재료비",
        "접수방법",
    }
    optional = {"추가접수기간", "모집인원", "문의"}
    fields: dict[str, str] = {}
    items = soup.select(".shopViewList > ul.b_dot1 > li.txt_line")
    if not items:
        raise HaenamContractError(f"detail {identity}: structured facts missing")
    for item in items:
        label_node = item.find("span", recursive=False)
        if label_node is None:
            raise HaenamContractError(
                f"detail {identity}: malformed structured fact"
            )
        label = _clean(label_node.get_text(" ", strip=True))
        if label not in required | optional or label in fields:
            raise HaenamContractError(
                f"detail {identity}: unexpected/duplicate field {label!r}"
            )
        if label == "문의":
            fields[label] = "discarded"
            continue
        label_node.extract()
        fields[label] = _clean(item.get_text(" ", strip=True))
    if not required.issubset(fields):
        raise HaenamContractError(
            f"detail {identity}: incomplete structured facts"
        )
    return fields


def _foundation_application_control(
    soup: BeautifulSoup,
    listed: Mapping[str, Any],
) -> tuple[str, str]:
    identity = _clean(listed.get("identity"))
    controls = [
        node
        for node in soup.select("a.btns")
        if _clean(node.get_text(" ", strip=True)) == "신청"
    ]
    if len(controls) > 1:
        raise HaenamContractError(
            f"detail {identity}: duplicate application controls"
        )
    expects_control = (
        listed.get("status") == "OPEN"
        and (
            "온라인" in _clean(listed.get("method"))
            or "온/오프라인" in _clean(listed.get("method"))
        )
    )
    if not controls:
        if expects_control:
            raise HaenamContractError(
                f"detail {identity}: online application control missing"
            )
        return "", "no_public_online_control"
    if not expects_control:
        raise HaenamContractError(
            f"detail {identity}: unexpected application control"
        )
    control = controls[0]
    if (
        _clean(control.get("href")) != "javascript:void(0)"
        or "theme"
        not in {_clean(value) for value in (control.get("class") or [])}
    ):
        raise HaenamContractError(
            f"detail {identity}: login control changed"
        )
    onclick = _clean(control.get("onclick"))
    match = re.fullmatch(
        r"alert\('로그인 후 이용이 가능합니다\.'\);"
        r"loginReturnUrlMain\('(?P<query>[^']+)'\)",
        onclick,
    )
    if not match:
        raise HaenamContractError(
            f"detail {identity}: login gate changed"
        )
    query = parse_qs(match.group("query"), keep_blank_values=True)
    expected = {
        "contentUid": [HAENAM_FOUNDATION_APPLY_UID],
        "oesSubjectId": [identity],
        "oecId": [_clean(listed.get("cohort"))],
        "isAccept": [_clean(listed.get("accept"))],
        "isAddAccept": [_clean(listed.get("add_accept"))],
    }
    if query != expected:
        raise HaenamContractError(
            f"detail {identity}: login application identity changed"
        )
    application_url = (
        f"{HAENAM_FOUNDATION_POST_URL}?"
        f"{urlencode({key: values[0] for key, values in expected.items()})}"
    )
    return application_url, "identity_bound_login_gate"


def _foundation_branch(value: Any) -> str:
    venue = _clean(value)
    if not venue or venue == "-":
        return "해남군교육재단"
    if "해남군평생학습관" in venue:
        return "해남군평생학습관"
    compact = venue.replace(" ", "")
    if compact == "해남군관내":
        return "해남군 관내"
    return venue


def _parse_foundation_detail(
    soup: BeautifulSoup,
    listed: Mapping[str, Any],
) -> dict[str, Any]:
    identity = _clean(listed.get("identity"))
    if (
        soup.title is None
        or _clean(soup.title.get_text(" ", strip=True))
        != "해남군교육재단 > 상세정보"
    ):
        raise HaenamContractError(f"detail {identity}: owner/title changed")
    fields = _foundation_detail_fields(soup, identity)
    title = _safe_source_text(fields["교육명"], identity, "title")
    if title != listed.get("title"):
        raise HaenamContractError(f"detail {identity}: title mismatch")
    event_start, event_end = _parse_date_range(fields["교육기간"], identity)
    apply_start, apply_end = _parse_datetime_range(
        fields["접수기간"],
        identity,
    )
    if (
        event_start != listed.get("event_start")
        or event_end != listed.get("event_end")
        or apply_start != listed.get("apply_start")
        or apply_end != listed.get("apply_end")
        or fields["교육기관"] != listed.get("institution")
        or fields["접수방법"] != listed.get("method")
    ):
        raise HaenamContractError(
            f"detail {identity}: list/detail facts mismatch"
        )
    if fields["년도"] != str(event_start.year):
        raise HaenamContractError(f"detail {identity}: year mismatch")
    venue = _clean(fields["교육장소"])
    if len(venue) > 300 or _PHONE_RE.search(venue) or _EMAIL_RE.search(venue):
        raise HaenamContractError(f"detail {identity}: unsafe venue")
    capacity_total = int(listed.get("capacity_total") or 0)
    capacity_field = _clean(fields.get("모집인원"))
    if capacity_field:
        match = re.fullmatch(r"(?P<count>\d+)명", capacity_field)
        if not match or int(match.group("count")) != capacity_total:
            raise HaenamContractError(
                f"detail {identity}: capacity mismatch"
            )
    elif capacity_total:
        raise HaenamContractError(
            f"detail {identity}: capacity field disappeared"
        )
    application_url, control_contract = _foundation_application_control(
        soup,
        listed,
    )
    branch = _foundation_branch(venue)
    venue_name = (
        "장소 별도 안내"
        if venue == "-"
        else "해남군 관내"
        if venue.replace(" ", "") == "해남군관내"
        else venue
    )
    venue_evidence = (
        "official_structured_detail_dash"
        if venue == "-"
        else "official_structured_detail"
    )
    status = _clean(listed.get("status"))
    row = {
        "provider": HAENAM_FOUNDATION_PROVIDER,
        "provider_course_id": f"haenamedu:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": title,
        "branch": branch,
        "branch_code": _branch_code(HAENAM_FOUNDATION_PROVIDER, branch),
        "preserve_branch": True,
        "category": "교육",
        "program_type": "해남군교육재단 교육",
        "raw_url": _clean(listed.get("detail_url")),
        "application_url": application_url,
        "application_type": "온라인" if application_url else fields["접수방법"],
        "application_method_raw": fields["접수방법"],
        "reservation_available": bool(application_url) and status == "OPEN",
        "status": status,
        "fee": _clean(fields["수강료"]),
        "period": f"{event_start.isoformat()} ~ {event_end.isoformat()}",
        "start_date": event_start.isoformat(),
        "end_date": event_end.isoformat(),
        "apply_period": (
            f"{apply_start.strftime('%Y-%m-%d %H:%M')} ~ "
            f"{apply_end.strftime('%Y-%m-%d %H:%M')}"
        ),
        "apply_start_date": apply_start.strftime("%Y-%m-%d %H:%M"),
        "apply_end_date": apply_end.strftime("%Y-%m-%d %H:%M"),
        "schedule_raw": "시간 별도 안내",
        "target": "대상 별도 안내",
        "capacity_current": int(listed.get("capacity_current") or 0),
        "capacity_total": capacity_total,
        "venue_name": venue_name,
        "collection_category": "education",
        "domain_category": "교육",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "education",
        "collection_type": "course",
        "municipality_code": HAENAM_MUNICIPALITY_CODE,
        "municipality_name": HAENAM_MUNICIPALITY_NAME,
        "raw_fields": {
            "parser": HAENAM_FOUNDATION_PARSER,
            "source_identity": identity,
            "source_page": int(listed.get("page") or 0),
            "source_partition": _clean(listed.get("partition")),
            "source_status": _clean(listed.get("source_status")),
            "source_application_control": control_contract,
            "source_application_method": fields["접수방법"],
            "source_material_fee": _clean(fields["재료비"]),
            "target_evidence": "official_structured_detail_field_absent",
            "schedule_evidence": "official_structured_detail_field_absent",
            "venue_evidence": venue_evidence,
            "detail_verified": True,
            "application_control_verified": True,
        },
    }
    _validate_output(row)
    return row


def collect_haenam_foundation_education(
    target: Any,
    *,
    timeout: int = 40,
    max_pages: int = HAENAM_MAX_PAGES,
    detail_limit: int = HAENAM_MAX_DETAILS,
    cutoff: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    requester: Optional[Requester] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect the complete current/future Education Foundation snapshot."""

    audit_date = _today(cutoff)
    factory = session_factory or _foundation_session
    request = requester or _default_requester
    meta: dict[str, Any] = {
        "municipality_code": HAENAM_MUNICIPALITY_CODE,
        "owner_provider": HAENAM_FOUNDATION_PROVIDER,
        "canonical_url": HAENAM_FOUNDATION_URL,
        "parser": HAENAM_FOUNDATION_PARSER,
        "cutoff": audit_date.isoformat(),
        "list_requests": 0,
        "detail_pages": 0,
        "application_form_requests": 0,
        "pagination_complete": False,
        "source_cap_reached": False,
        "serial_detail_transport": True,
    }
    current: Any = None
    try:
        if (
            _clean(_target_value(target, "provider"))
            != HAENAM_FOUNDATION_PROVIDER
            or _clean(_target_value(target, "url")) != HAENAM_FOUNDATION_URL
        ):
            raise HaenamContractError(
                "target does not own the foundation catalogue"
            )
        if timeout < 1 or max_pages < 2 or detail_limit < 0:
            raise HaenamContractError("invalid collector limits")
        current = factory()

        regular = _parse_foundation_page(
            _request_soup(
                current,
                request,
                "GET",
                HAENAM_FOUNDATION_URL,
                timeout,
                encoding="utf-8-sig",
            ),
            "1",
            1,
        )
        meta["list_requests"] += 1
        if regular.empty_sentinel or not regular.rows:
            raise HaenamContractError("regular cohort is unexpectedly empty")

        nonregular: dict[int, _FoundationPage] = {}
        sentinel: Optional[_FoundationPage] = None
        for page_number in range(1, max_pages + 1):
            parsed = _parse_foundation_page(
                _request_soup(
                    current,
                    request,
                    "POST",
                    HAENAM_FOUNDATION_POST_URL,
                    timeout,
                    data=_foundation_post_data(page_number),
                    encoding="utf-8-sig",
                ),
                "2",
                page_number,
            )
            meta["list_requests"] += 1
            if parsed.empty_sentinel:
                sentinel = parsed
                break
            nonregular[page_number] = parsed
        if sentinel is None:
            meta["source_cap_reached"] = True
            raise HaenamContractError(
                "max_pages reached before foundation empty sentinel"
            )
        if not nonregular or sorted(nonregular) != list(
            range(1, max(nonregular) + 1)
        ):
            raise HaenamContractError("nonregular pages are not consecutive")
        final_page = max(nonregular)
        for number, parsed in nonregular.items():
            count = len(parsed.rows)
            if number < final_page and count != HAENAM_FOUNDATION_PAGE_SIZE:
                raise HaenamContractError(
                    f"nonregular page {number}: premature short page"
                )
            if number == final_page and not (
                1 <= count <= HAENAM_FOUNDATION_PAGE_SIZE
            ):
                raise HaenamContractError("invalid nonregular final page")

        regular_check = _parse_foundation_page(
            _request_soup(
                current,
                request,
                "GET",
                HAENAM_FOUNDATION_URL,
                timeout,
                encoding="utf-8-sig",
            ),
            "1",
            1,
        )
        first_check = _parse_foundation_page(
            _request_soup(
                current,
                request,
                "POST",
                HAENAM_FOUNDATION_POST_URL,
                timeout,
                data=_foundation_post_data(1),
                encoding="utf-8-sig",
            ),
            "2",
            1,
        )
        last_check = _parse_foundation_page(
            _request_soup(
                current,
                request,
                "POST",
                HAENAM_FOUNDATION_POST_URL,
                timeout,
                data=_foundation_post_data(final_page),
                encoding="utf-8-sig",
            ),
            "2",
            final_page,
        )
        sentinel_check = _parse_foundation_page(
            _request_soup(
                current,
                request,
                "POST",
                HAENAM_FOUNDATION_POST_URL,
                timeout,
                data=_foundation_post_data(sentinel.page),
                encoding="utf-8-sig",
            ),
            "2",
            sentinel.page,
        )
        meta["list_requests"] += 4
        if (
            _foundation_page_signature(regular_check)
            != _foundation_page_signature(regular)
            or _foundation_page_signature(first_check)
            != _foundation_page_signature(nonregular[1])
            or _foundation_page_signature(last_check)
            != _foundation_page_signature(nonregular[final_page])
            or not sentinel.empty_sentinel
            or sentinel.rows
            or not sentinel_check.empty_sentinel
            or sentinel_check.rows
        ):
            raise HaenamContractError(
                "foundation first/last/sentinel stability changed"
            )

        listed = list(regular.rows)
        listed.extend(
            row
            for number in sorted(nonregular)
            for row in nonregular[number].rows
        )
        identities = [_clean(row.get("identity")) for row in listed]
        if len(identities) != len(set(identities)):
            raise HaenamContractError("duplicate foundation identities")
        cohort_count = len(
            {_clean(row.get("cohort")) for row in regular.rows}
        )
        if cohort_count != 1:
            raise HaenamContractError("regular cohort scope changed")
        current_rows = [
            row for row in listed if row["event_end"] >= audit_date
        ]
        if any(
            row.get("institution") != "해남군교육재단"
            for row in current_rows
        ):
            raise HaenamContractError(
                "current foundation row is missing its education owner"
            )
        if len(current_rows) > detail_limit:
            meta["source_cap_reached"] = True
            raise HaenamContractError(
                "detail_limit would create a partial foundation snapshot"
            )
        meta.update(
            {
                "regular_rows": len(regular.rows),
                "regular_cohorts": cohort_count,
                "nonregular_pages": len(nonregular),
                "nonregular_page_counts": {
                    number: len(page.rows)
                    for number, page in sorted(nonregular.items())
                },
                "empty_sentinel_page": sentinel.page,
                "source_rows": len(listed),
                "source_total": len(listed),
                "source_status_counts": dict(
                    Counter(row["source_status"] for row in listed)
                ),
                "current_source_count": len(current_rows),
                "expired_source_count": len(listed) - len(current_rows),
                "stability_rechecks": 4,
            }
        )

        output: list[dict[str, Any]] = []
        for listed_row in current_rows:
            last_error: Optional[Exception] = None
            detail_soup: Optional[BeautifulSoup] = None
            for _attempt in range(HAENAM_FETCH_ATTEMPTS):
                try:
                    detail_soup = _request_soup(
                        current,
                        request,
                        "GET",
                        _clean(listed_row.get("detail_url")),
                        timeout,
                        encoding="utf-8-sig",
                    )
                    meta["detail_pages"] += 1
                    break
                except (HaenamContractError, requests.RequestException) as exc:
                    last_error = exc
            if detail_soup is None:
                raise HaenamContractError(
                    f"detail {listed_row['identity']}: {last_error}"
                )
            output.append(_parse_foundation_detail(detail_soup, listed_row))
        if dedupe_rows is not None:
            output = list(dedupe_rows(output))
        meta.update(
            {
                "pagination_complete": True,
                "detail_verified": len(current_rows),
                "application_controls_verified": len(current_rows),
                "identity_bound_application_controls": sum(
                    bool(row.get("application_url")) for row in output
                ),
                "branch_counts": dict(
                    Counter(row["branch"] for row in output)
                ),
                "output_rows": len(output),
                "configured_collection_error": "",
            }
        )
        return output, HAENAM_FOUNDATION_PARSER, meta
    except (
        HaenamContractError,
        requests.RequestException,
        ValueError,
        TypeError,
    ) as exc:
        meta["configured_collection_error"] = _clean(exc)
        meta["pagination_complete"] = False
        meta["output_rows"] = 0
        return [], HAENAM_FOUNDATION_PARSER, meta
    finally:
        _close_quietly(current)


def haenam_library_list_url(page: Any = 1) -> str:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        return ""
    if page == 1:
        return HAENAM_LIBRARY_URL
    return (
        f"https://{HAENAM_LIBRARY_HOST}{HAENAM_LIBRARY_PATH}?"
        f"{urlencode({'mno': '43', 'page': str(page), 'key': 'all', 'searchword': ''})}"
    )


def haenam_library_detail_url(identity: Any, page: Any) -> str:
    identity_text = _clean(identity)
    if (
        not re.fullmatch(r"[1-9]\d*", identity_text)
        or isinstance(page, bool)
        or not isinstance(page, int)
        or page < 1
    ):
        return ""
    params = {
        "mno": "43",
        "mode": "read",
        "no": identity_text,
        "page": str(page),
        "key": "all",
        "searchword": "",
    }
    return (
        f"https://{HAENAM_LIBRARY_HOST}{HAENAM_LIBRARY_PATH}?"
        f"{urlencode(params)}"
    )


def haenam_library_application_url(identity: Any) -> str:
    identity_text = _clean(identity)
    if not re.fullmatch(r"[1-9]\d*", identity_text):
        return ""
    params = {"mno": "43", "mode": "write", "lesson_no": identity_text}
    return (
        f"https://{HAENAM_LIBRARY_HOST}{HAENAM_LIBRARY_PATH}?"
        f"{urlencode(params)}"
    )


def _library_detail_link(value: Any, expected_page: int) -> tuple[str, str]:
    absolute = urljoin(HAENAM_LIBRARY_URL, _clean(value))
    parsed = _validate_https_url(
        absolute,
        HAENAM_LIBRARY_HOST,
        HAENAM_LIBRARY_PATH,
    )
    query = parse_qs(parsed.query, keep_blank_values=True)
    expected_keys = {"mno", "mode", "no", "page", "key", "searchword"}
    identity = _clean((query.get("no") or [""])[0])
    if (
        set(query) != expected_keys
        or any(len(values) != 1 for values in query.values())
        or query["mno"] != ["43"]
        or query["mode"] != ["read"]
        or query["page"] != [str(expected_page)]
        or query["key"] != ["all"]
        or query["searchword"] != [""]
    ):
        raise HaenamContractError("county-library detail identity changed")
    canonical = haenam_library_detail_url(identity, expected_page)
    if not canonical:
        raise HaenamContractError("invalid county-library course identity")
    return identity, canonical


def _library_application_link(value: Any, identity: str) -> str:
    absolute = urljoin(HAENAM_LIBRARY_URL, _clean(value))
    parsed = _validate_https_url(
        absolute,
        HAENAM_LIBRARY_HOST,
        HAENAM_LIBRARY_PATH,
    )
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        set(query) != {"mno", "mode", "lesson_no"}
        or query.get("mno") != ["43"]
        or query.get("mode") != ["write"]
        or query.get("lesson_no") != [identity]
    ):
        raise HaenamContractError(
            f"course {identity}: application identity link changed"
        )
    return haenam_library_application_url(identity)


def _library_category(value: Any, identity: str) -> tuple[str, bool]:
    category = _safe_source_text(value, identity, "source category")
    if category in _LIBRARY_EDUCATION_CATEGORIES or re.fullmatch(
        r"20\d{2} 길위의 인문학",
        category,
    ):
        return category, True
    if category in _LIBRARY_EXCLUDED_CATEGORIES:
        return category, False
    raise HaenamContractError(
        f"course {identity}: unreviewed library category {category!r}"
    )


def _parse_library_page(soup: BeautifulSoup, page: int) -> _LibraryPage:
    if (
        soup.title is None
        or _clean(soup.title.get_text(" ", strip=True))
        != "해남군립도서관 - 프로그램신청"
    ):
        raise HaenamContractError(
            f"county-library page {page}: owner/title changed"
        )
    total_matches = _LIBRARY_TOTAL_RE.findall(
        _clean(soup.get_text(" ", strip=True))
    )
    if len(set(total_matches)) != 1:
        raise HaenamContractError(
            f"county-library page {page}: declared total changed"
        )
    declared_total = int(total_matches[0].replace(",", ""))
    container = soup.select_one(".boardlist > .ProgramList")
    if container is None:
        raise HaenamContractError(
            f"county-library page {page}: list container changed"
        )

    rows: list[dict[str, Any]] = []
    for card in container.select(":scope > dl"):
        title_links = card.select(".online_tit > a[href*='mode=read']")
        if len(title_links) != 1:
            raise HaenamContractError(
                f"county-library page {page}: detail link changed"
            )
        identity, detail_url = _library_detail_link(
            title_links[0].get("href"),
            page,
        )
        title = _safe_source_text(
            title_links[0].get_text(" ", strip=True),
            identity,
            "title",
        )
        category_node = card.select_one("dt > span")
        category, is_education = _library_category(
            category_node.get_text(" ", strip=True) if category_node else "",
            identity,
        )
        fields: dict[str, str] = {}
        for item in card.select("dt > p.online_sub"):
            label_node = item.find("span", recursive=False)
            if label_node is None:
                raise HaenamContractError(
                    f"course {identity}: malformed list field"
                )
            label = re.sub(
                r"\s+",
                "",
                _clean(label_node.get_text(" ", strip=True)),
            )
            label_node.extract()
            value = _clean(item.get_text(" ", strip=True))
            if label not in {"운영기간", "운영시간", "대상", "접수기간"}:
                raise HaenamContractError(
                    f"course {identity}: unexpected list field {label!r}"
                )
            if label in fields:
                raise HaenamContractError(
                    f"course {identity}: duplicate list field {label!r}"
                )
            fields[label] = value
        if set(fields) != {"운영기간", "운영시간", "대상", "접수기간"}:
            raise HaenamContractError(
                f"course {identity}: incomplete list fields"
            )
        event_start, event_end = _parse_date_range(
            fields["운영기간"],
            identity,
        )
        apply_start, apply_end = _parse_date_range(
            fields["접수기간"],
            identity,
        )
        schedule = _safe_source_text(
            fields["운영시간"],
            identity,
            "schedule",
        )
        target = _safe_source_text(fields["대상"], identity, "target")
        count_node = card.select_one("dd > p")
        count_match = _LIBRARY_COUNT_RE.fullmatch(
            _clean(
                count_node.get_text(" ", strip=True) if count_node else ""
            )
        )
        if not count_match:
            raise HaenamContractError(
                f"course {identity}: list capacity changed"
            )
        capacity_current = int(count_match.group("current"))
        capacity_total = int(count_match.group("total"))
        if capacity_total < 1:
            raise HaenamContractError(
                f"course {identity}: invalid list capacity"
            )

        controls = [
            anchor
            for anchor in card.select("dd > a[href]")
            if "Tplanbtn"
            not in {_clean(value) for value in (anchor.get("class") or [])}
        ]
        if len(controls) > 1:
            raise HaenamContractError(
                f"course {identity}: duplicate application controls"
            )
        application_url = ""
        control_label = ""
        if controls:
            control_label = _clean(controls[0].get_text(" ", strip=True))
            control_classes = {
                _clean(value) for value in (controls[0].get("class") or [])
            }
            control_href = _clean(controls[0].get("href"))
            if not is_education:
                # Non-education circulation services are counted for ledger
                # completeness, but their service controls are not followed.
                application_url = ""
            elif control_label in _LIBRARY_APPLICATION_LABELS:
                if "Prostate02" not in control_classes:
                    raise HaenamContractError(
                        f"course {identity}: open application class changed"
                    )
                application_url = _library_application_link(
                    control_href,
                    identity,
                )
            elif control_label in _LIBRARY_CLOSED_LABELS:
                if control_href != "#" or "Prostate03" not in control_classes:
                    raise HaenamContractError(
                        f"course {identity}: closed application control changed"
                    )
            else:
                raise HaenamContractError(
                    f"course {identity}: application label changed"
                )
        status = "OPEN" if application_url else "CLOSED"
        rows.append(
            {
                "identity": identity,
                "page": page,
                "title": title,
                "category": category,
                "is_education": is_education,
                "detail_url": detail_url,
                "event_start": event_start,
                "event_end": event_end,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "schedule": schedule,
                "target": target,
                "capacity_current": capacity_current,
                "capacity_total": capacity_total,
                "application_url": application_url,
                "application_control": control_label,
                "status": status,
            }
        )

    pager_pages: set[int] = set()
    for anchor in soup.select("a[href*='mno=43'][href*='page=']"):
        parsed = urlparse(urljoin(HAENAM_LIBRARY_URL, _clean(anchor.get("href"))))
        query = parse_qs(parsed.query, keep_blank_values=True)
        value = _clean((query.get("page") or [""])[0])
        if (
            parsed.hostname == HAENAM_LIBRARY_HOST
            and parsed.path == HAENAM_LIBRARY_PATH
            and query.get("mno") == ["43"]
            and value.isdigit()
            and int(value) >= 1
        ):
            pager_pages.add(int(value))
    if not pager_pages:
        raise HaenamContractError(
            f"county-library page {page}: pager contract missing"
        )
    return _LibraryPage(
        page=page,
        rows=tuple(rows),
        declared_total=declared_total,
        declared_last_page=max(pager_pages),
        empty_sentinel=not rows,
    )


def _library_page_signature(page: _LibraryPage) -> tuple[Any, ...]:
    return (
        page.page,
        page.declared_total,
        page.declared_last_page,
        page.empty_sentinel,
        tuple(
            (
                row["identity"],
                row["title"],
                row["category"],
                row["event_start"],
                row["event_end"],
                row["application_control"],
            )
            for row in page.rows
        ),
    )


def _library_detail_fields(
    soup: BeautifulSoup,
    identity: str,
) -> dict[str, str]:
    required = {
        "대상",
        "운영기간",
        "운영시간",
        "운영장소",
        "접수기간",
        "강사명",
        "재료비",
        "수강인원",
    }
    ignored = {"강사명", "강의계획서"}
    allowed = required | ignored
    fields: dict[str, str] = {}
    for item in soup.select(".bviewlist .Progdetails_list > li"):
        label_node = item.select_one("dt")
        value_node = item.select_one("dd")
        if label_node is None or value_node is None:
            raise HaenamContractError(
                f"detail {identity}: malformed structured field"
            )
        label = _clean(label_node.get_text(" ", strip=True))
        if label not in allowed or label in fields:
            raise HaenamContractError(
                f"detail {identity}: unexpected/duplicate field {label!r}"
            )
        if label in ignored:
            fields[label] = "discarded"
        else:
            fields[label] = _clean(value_node.get_text(" ", strip=True))
    if not required.issubset(fields):
        raise HaenamContractError(
            f"detail {identity}: incomplete structured fields"
        )
    return fields


def _library_detail_heading(
    soup: BeautifulSoup,
    identity: str,
) -> tuple[str, str]:
    heading = soup.select_one(".bviewlist .Progdetails_tit")
    if heading is None:
        raise HaenamContractError(f"detail {identity}: heading missing")
    category_node = heading.find("span", recursive=False)
    if category_node is None:
        raise HaenamContractError(
            f"detail {identity}: category heading changed"
        )
    category = _clean(category_node.get_text(" ", strip=True))
    category_node.extract()
    title = _clean(heading.get_text(" ", strip=True))
    return category, title


def _normalized_schedule(value: Any) -> str:
    return re.sub(r"\s+", "", _clean(value)).replace("요일", "")


def _library_detail_control(
    soup: BeautifulSoup,
    listed: Mapping[str, Any],
) -> str:
    identity = _clean(listed.get("identity"))
    controls = soup.select(".boardlist_p > .pagelist a[href]")
    if len(controls) > 1:
        raise HaenamContractError(
            f"detail {identity}: duplicate application controls"
        )
    expected_url = _clean(listed.get("application_url"))
    expected_label = _clean(listed.get("application_control"))
    if not controls:
        if expected_url or expected_label:
            raise HaenamContractError(
                f"detail {identity}: application control disappeared"
            )
        return ""
    label = _clean(controls[0].get_text(" ", strip=True))
    classes = {_clean(value) for value in (controls[0].get("class") or [])}
    href = _clean(controls[0].get("href"))
    if expected_url:
        if label not in _LIBRARY_APPLICATION_LABELS or "Prostate02" not in classes:
            raise HaenamContractError(
                f"detail {identity}: open application control changed"
            )
        application_url = _library_application_link(href, identity)
    else:
        if (
            expected_label not in _LIBRARY_CLOSED_LABELS
            or label != expected_label
            or href != "#"
            or "Prostate03" not in classes
        ):
            raise HaenamContractError(
                f"detail {identity}: closed application control mismatch"
            )
        application_url = ""
    if label != expected_label or application_url != expected_url:
        raise HaenamContractError(
            f"detail {identity}: application control mismatch"
        )
    return application_url


def _library_branch(value: Any) -> str:
    venue = _clean(value)
    if venue.startswith("해남문화예술회관"):
        return "해남문화예술회관"
    if venue.startswith("해남군립도서관") or not venue:
        return "해남군립도서관"
    return venue


def _parse_library_detail(
    soup: BeautifulSoup,
    listed: Mapping[str, Any],
) -> dict[str, Any]:
    identity = _clean(listed.get("identity"))
    if (
        soup.title is None
        or _clean(soup.title.get_text(" ", strip=True))
        != "해남군립도서관 - 프로그램신청"
    ):
        raise HaenamContractError(f"detail {identity}: owner/title changed")
    category, title = _library_detail_heading(soup, identity)
    if category != listed.get("category") or title != listed.get("title"):
        raise HaenamContractError(
            f"detail {identity}: category/title mismatch"
        )
    fields = _library_detail_fields(soup, identity)
    event_start, event_end = _parse_date_range(fields["운영기간"], identity)
    apply_start, apply_end = _parse_date_range(fields["접수기간"], identity)
    target = _safe_source_text(fields["대상"], identity, "target")
    schedule = _safe_source_text(
        fields["운영시간"],
        identity,
        "schedule",
    )
    if (
        event_start != listed.get("event_start")
        or event_end != listed.get("event_end")
        or apply_start != listed.get("apply_start")
        or apply_end != listed.get("apply_end")
        or target != listed.get("target")
        or _normalized_schedule(schedule)
        != _normalized_schedule(listed.get("schedule"))
    ):
        raise HaenamContractError(
            f"detail {identity}: list/detail facts mismatch"
        )
    count_match = _LIBRARY_DETAIL_COUNT_RE.match(fields["수강인원"])
    if not count_match:
        raise HaenamContractError(
            f"detail {identity}: capacity contract changed"
        )
    capacity_current = int(count_match.group("current"))
    capacity_total = int(count_match.group("total"))
    waiting = int(count_match.group("waiting"))
    overall = int(count_match.group("overall"))
    if (
        capacity_current != listed.get("capacity_current")
        or capacity_total != listed.get("capacity_total")
        or capacity_total + waiting != overall
    ):
        raise HaenamContractError(
            f"detail {identity}: capacity mismatch"
        )
    source_venue = _clean(fields["운영장소"])
    if (
        len(source_venue) > 300
        or _PHONE_RE.search(source_venue)
        or _EMAIL_RE.search(source_venue)
    ):
        raise HaenamContractError(f"detail {identity}: unsafe venue")
    material_fee = _clean(fields["재료비"])
    if (
        len(material_fee) > 200
        or _PHONE_RE.search(material_fee)
        or _EMAIL_RE.search(material_fee)
    ):
        raise HaenamContractError(
            f"detail {identity}: unsafe material fee"
        )
    application_url = _library_detail_control(soup, listed)
    status = _clean(listed.get("status"))
    branch = _library_branch(source_venue)
    venue = source_venue or branch
    row = {
        "provider": HAENAM_LIBRARY_PROVIDER,
        "provider_course_id": f"haenam_library:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": title,
        "branch": branch,
        "branch_code": _branch_code(HAENAM_LIBRARY_PROVIDER, branch),
        "preserve_branch": True,
        "category": "교육",
        "program_type": category,
        "raw_url": _clean(listed.get("detail_url")),
        "application_url": application_url,
        "application_type": "온라인" if application_url else "",
        "application_method_raw": "온라인" if application_url else "",
        "reservation_available": bool(application_url) and status == "OPEN",
        "status": status,
        "fee": "요금 별도 안내",
        "period": f"{event_start.isoformat()} ~ {event_end.isoformat()}",
        "start_date": event_start.isoformat(),
        "end_date": event_end.isoformat(),
        "apply_period": f"{apply_start.isoformat()} ~ {apply_end.isoformat()}",
        "apply_start_date": apply_start.isoformat(),
        "apply_end_date": apply_end.isoformat(),
        "schedule_raw": schedule,
        "target": target,
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "venue_name": venue,
        "collection_category": "education",
        "domain_category": "교육",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "education",
        "collection_type": "course",
        "municipality_code": HAENAM_MUNICIPALITY_CODE,
        "municipality_name": HAENAM_MUNICIPALITY_NAME,
        "raw_fields": {
            "parser": HAENAM_LIBRARY_PARSER,
            "source_identity": identity,
            "source_page": int(listed.get("page") or 0),
            "source_category": category,
            "source_status": status,
            "source_application_control": _clean(
                listed.get("application_control")
            ),
            "source_waiting_capacity": waiting,
            "source_material_fee": material_fee,
            "fee_evidence": "official_detail_omits_tuition",
            "venue_evidence": (
                "official_detail_value"
                if source_venue
                else "official_detail_omits_venue_owner_fallback"
            ),
            "detail_verified": True,
            "application_control_verified": True,
            "privacy_cut_applied": True,
        },
    }
    _validate_output(row)
    return row


def collect_haenam_county_library_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = HAENAM_MAX_PAGES,
    detail_limit: int = HAENAM_MAX_DETAILS,
    cutoff: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    requester: Optional[Requester] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect education rows from the complete County Library ledger."""

    audit_date = _today(cutoff)
    factory = session_factory or _library_session
    request = requester or _default_requester
    meta: dict[str, Any] = {
        "municipality_code": HAENAM_MUNICIPALITY_CODE,
        "owner_provider": HAENAM_LIBRARY_PROVIDER,
        "canonical_url": HAENAM_LIBRARY_URL,
        "parser": HAENAM_LIBRARY_PARSER,
        "cutoff": audit_date.isoformat(),
        "list_requests": 0,
        "detail_pages": 0,
        "application_form_requests": 0,
        "pagination_complete": False,
        "source_cap_reached": False,
        "privacy_cut_before_parse": True,
    }
    current: Any = None
    try:
        if (
            _clean(_target_value(target, "provider"))
            != HAENAM_LIBRARY_PROVIDER
            or _clean(_target_value(target, "url")) != HAENAM_LIBRARY_URL
        ):
            raise HaenamContractError(
                "target does not own the County Library catalogue"
            )
        if timeout < 1 or max_pages < 2 or detail_limit < 0:
            raise HaenamContractError("invalid collector limits")
        current = factory()
        pages: dict[int, _LibraryPage] = {}
        sentinel: Optional[_LibraryPage] = None
        for page_number in range(1, max_pages + 1):
            url = haenam_library_list_url(page_number)
            parsed = _parse_library_page(
                _request_soup(
                    current,
                    request,
                    "GET",
                    url,
                    timeout,
                    encoding="cp949",
                ),
                page_number,
            )
            meta["list_requests"] += 1
            if parsed.empty_sentinel:
                sentinel = parsed
                break
            pages[page_number] = parsed
        if sentinel is None:
            meta["source_cap_reached"] = True
            raise HaenamContractError(
                "max_pages reached before County Library empty sentinel"
            )
        if not pages or sorted(pages) != list(range(1, max(pages) + 1)):
            raise HaenamContractError(
                "County Library pages are not consecutive"
            )
        final_page = max(pages)
        for number, parsed in pages.items():
            count = len(parsed.rows)
            if number < final_page and count != HAENAM_LIBRARY_PAGE_SIZE:
                raise HaenamContractError(
                    f"County Library page {number}: premature short page"
                )
            if number == final_page and not (
                1 <= count <= HAENAM_LIBRARY_PAGE_SIZE
            ):
                raise HaenamContractError(
                    "invalid County Library final page"
                )
        declared_totals = {
            parsed.declared_total for parsed in (*pages.values(), sentinel)
        }
        declared_pages = {
            parsed.declared_last_page
            for parsed in (*pages.values(), sentinel)
        }
        if declared_totals != {sum(len(page.rows) for page in pages.values())}:
            raise HaenamContractError(
                "County Library declared/observed total mismatch"
            )
        if declared_pages != {final_page}:
            raise HaenamContractError(
                "County Library declared/observed page boundary mismatch"
            )

        first_check = _parse_library_page(
            _request_soup(
                current,
                request,
                "GET",
                haenam_library_list_url(1),
                timeout,
                encoding="cp949",
            ),
            1,
        )
        last_check = _parse_library_page(
            _request_soup(
                current,
                request,
                "GET",
                haenam_library_list_url(final_page),
                timeout,
                encoding="cp949",
            ),
            final_page,
        )
        sentinel_check = _parse_library_page(
            _request_soup(
                current,
                request,
                "GET",
                haenam_library_list_url(sentinel.page),
                timeout,
                encoding="cp949",
            ),
            sentinel.page,
        )
        meta["list_requests"] += 3
        if (
            _library_page_signature(first_check)
            != _library_page_signature(pages[1])
            or _library_page_signature(last_check)
            != _library_page_signature(pages[final_page])
            or not sentinel.empty_sentinel
            or sentinel.rows
            or not sentinel_check.empty_sentinel
            or sentinel_check.rows
        ):
            raise HaenamContractError(
                "County Library first/last/sentinel stability changed"
            )

        listed = [
            row for number in sorted(pages) for row in pages[number].rows
        ]
        identities = [_clean(row.get("identity")) for row in listed]
        if len(identities) != len(set(identities)):
            raise HaenamContractError(
                "duplicate County Library identities"
            )
        education = [row for row in listed if row["is_education"]]
        excluded = [row for row in listed if not row["is_education"]]
        current_rows = [
            row for row in education if row["event_end"] >= audit_date
        ]
        if len(current_rows) > detail_limit:
            meta["source_cap_reached"] = True
            raise HaenamContractError(
                "detail_limit would create a partial library snapshot"
            )
        meta.update(
            {
                "data_pages": len(pages),
                "page_counts": {
                    number: len(page.rows)
                    for number, page in sorted(pages.items())
                },
                "empty_sentinel_page": sentinel.page,
                "declared_total": next(iter(declared_totals)),
                "source_rows": len(listed),
                "source_total": len(listed),
                "education_source_count": len(education),
                "excluded_non_education_count": len(excluded),
                "excluded_non_education_identities": [
                    row["identity"] for row in excluded
                ],
                "source_category_counts": dict(
                    Counter(row["category"] for row in listed)
                ),
                "source_application_control_counts": dict(
                    Counter(
                        row["application_control"] or "none"
                        for row in listed
                    )
                ),
                "current_source_count": len(current_rows),
                "expired_education_count": len(education)
                - len(current_rows),
                "stability_rechecks": 3,
            }
        )

        output: list[dict[str, Any]] = []
        for listed_row in current_rows:
            last_error: Optional[Exception] = None
            detail_soup: Optional[BeautifulSoup] = None
            for _attempt in range(HAENAM_FETCH_ATTEMPTS):
                try:
                    detail_soup = _request_soup(
                        current,
                        request,
                        "GET",
                        _clean(listed_row.get("detail_url")),
                        timeout,
                        encoding="cp949",
                        privacy_cut=True,
                    )
                    meta["detail_pages"] += 1
                    break
                except (HaenamContractError, requests.RequestException) as exc:
                    last_error = exc
            if detail_soup is None:
                raise HaenamContractError(
                    f"detail {listed_row['identity']}: {last_error}"
                )
            output.append(_parse_library_detail(detail_soup, listed_row))
        if dedupe_rows is not None:
            output = list(dedupe_rows(output))
        meta.update(
            {
                "pagination_complete": True,
                "detail_verified": len(current_rows),
                "privacy_cuts_verified": len(current_rows),
                "application_controls_verified": len(current_rows),
                "identity_bound_application_controls": sum(
                    bool(row.get("application_url")) for row in output
                ),
                "branch_counts": dict(
                    Counter(row["branch"] for row in output)
                ),
                "output_rows": len(output),
                "configured_collection_error": "",
            }
        )
        return output, HAENAM_LIBRARY_PARSER, meta
    except (
        HaenamContractError,
        requests.RequestException,
        ValueError,
        TypeError,
    ) as exc:
        meta["configured_collection_error"] = _clean(exc)
        meta["pagination_complete"] = False
        meta["output_rows"] = 0
        return [], HAENAM_LIBRARY_PARSER, meta
    finally:
        _close_quietly(current)


def is_haenam_education_target(target: Any) -> bool:
    provider = _clean(_target_value(target, "provider"))
    url = _clean(_target_value(target, "url"))
    return (provider, url) in {
        (HAENAM_FOUNDATION_PROVIDER, HAENAM_FOUNDATION_URL),
        (HAENAM_LIBRARY_PROVIDER, HAENAM_LIBRARY_URL),
    }


is_target = is_haenam_education_target


def collect_haenam_education(
    target: Any,
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    provider = _clean(_target_value(target, "provider"))
    if provider == HAENAM_FOUNDATION_PROVIDER:
        return collect_haenam_foundation_education(target, **kwargs)
    if provider == HAENAM_LIBRARY_PROVIDER:
        return collect_haenam_county_library_education(target, **kwargs)
    parser = "haenam_owner_dispatch"
    return [], parser, {
        "municipality_code": HAENAM_MUNICIPALITY_CODE,
        "owner_provider": provider,
        "pagination_complete": False,
        "output_rows": 0,
        "configured_collection_error": "target is not an audited Haenam owner",
    }


collect = collect_haenam_education


__all__ = [
    "HAENAM_CANDIDATE_AUDIT",
    "HAENAM_COUNTY_HOME_URL",
    "HAENAM_COUNTY_LIFELONG_URLS",
    "HAENAM_DISCOVERY_AUDIT",
    "HAENAM_FOUNDATION_DISCOVERY_AUDIT",
    "HAENAM_FOUNDATION_OLD_PROVIDER",
    "HAENAM_FOUNDATION_OLD_URL",
    "HAENAM_FOUNDATION_PARSER",
    "HAENAM_FOUNDATION_PROVIDER",
    "HAENAM_FOUNDATION_URL",
    "HAENAM_JNE_BRANCH",
    "HAENAM_JNE_DISCOVERY_AUDIT",
    "HAENAM_JNE_DUPLICATE_CANDIDATE_IDS",
    "HAENAM_JNE_PROVIDER",
    "HAENAM_JNE_URL",
    "HAENAM_LIBRARY_CULTURE_ALIAS_URL",
    "HAENAM_LIBRARY_DISCOVERY_AUDIT",
    "HAENAM_LIBRARY_PARSER",
    "HAENAM_LIBRARY_PROVIDER",
    "HAENAM_LIBRARY_URL",
    "HAENAM_MUNICIPALITY_CODE",
    "HAENAM_MUNICIPALITY_NAME",
    "HAENAM_NO_LEDGER_AUDIT",
    "HAENAM_OWNER_BOUNDARY_AUDIT",
    "HAENAM_PII_FIELDS_DISCARDED",
    "HAENAM_RESIDENT_NOTICE_URL",
    "HaenamContractError",
    "collect_haenam_county_library_education",
    "collect_haenam_education",
    "collect_haenam_foundation_education",
    "haenam_foundation_detail_url",
    "haenam_library_application_url",
    "haenam_library_detail_url",
    "haenam_library_list_url",
    "is_haenam_education_target",
]
