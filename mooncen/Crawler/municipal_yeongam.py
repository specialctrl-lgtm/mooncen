"""Fail-closed collector for Yeongam-gun's official library course ledger.

The existing provider points at one expired course detail and is classified as
arts/culture.  Its executable owner is actually the county library's aggregate
``culture_02`` catalogue.  The aggregate contains the Yeongam, Samho and
Haksan library branches and is the only county page found with stable course
identities, application/education dates, capacities and per-course details.

Every advertised page, the immediate empty page, and stable first/last page
rechecks are required.  Only current/future details are opened.  Detail cells
for instructors, telephone numbers, lesson content and attachments are never
read; the complete comment/applicant boundary is detached before any detail
text access.

The visible identity-verification link currently returns to the aggregate
list and contains no course identity.  It is therefore audited but not saved
as an application URL.  A future application control is retained only when
its path or verified login return URL binds the same course identity.  Login,
comment and application forms are never fetched or submitted.

``www.yeongam.go.kr`` omits its public Sectigo intermediate certificate.  The
session below pins that intermediate into certifi's trust context while
keeping CA-chain and hostname verification enabled.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import hashlib
import re
import ssl
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import certifi
import requests
from bs4 import BeautifulSoup, Tag
from requests.adapters import HTTPAdapter


YEONGAM_PROVIDER = "MUNI_WWW_YEONGAM_GO_KR_ACFEFCF0"
YEONGAM_CANDIDATE_ID = "MUNI_IR_883DB2F66734"
YEONGAM_JNTLE_PROVIDER = "MUNI_WWW_JNTLE_KR_8333FCBA"
YEONGAM_JNTLE_CANDIDATE_ID = "MUNI_IR_D7BE901CD10E"
YEONGAM_MUNICIPALITY_CODE = "1280000000"
YEONGAM_MUNICIPALITY_NAME = "전남광주통합특별시 영암군"
YEONGAM_HOST = "www.yeongam.go.kr"
YEONGAM_LIST_PATH = "/home/newlib/culture/culture_02"
YEONGAM_DETAIL_PREFIX = f"{YEONGAM_LIST_PATH}/show/"
YEONGAM_LOGIN_PATH = "/home/newlib/support/login"
YEONGAM_CANONICAL_URL = f"https://{YEONGAM_HOST}{YEONGAM_LIST_PATH}"
YEONGAM_REGISTERED_DETAIL_URL = f"{YEONGAM_CANONICAL_URL}/show/194"
YEONGAM_JNTLE_URL = "https://www.jntle.kr/main/uDamoaLecture/1?queryType=4683"
YEONGAM_EDUCITY_URL = "https://www.yeongam.go.kr/home/educity/experience/experience_01/yeongam.go"
YEONGAM_EDUCITY_LIBRARY_URL = (
    "https://www.yeongam.go.kr/home/educity/experience/experience_01/experience_01_03/yeongam.go"
)
YEONGAM_EDUCITY_SENIOR_URL = (
    "https://www.yeongam.go.kr/home/educity/experience/experience_01/experience_01_06/yeongam.go"
)
YEONGAM_EDUCITY_OTHER_URL = (
    "https://www.yeongam.go.kr/home/educity/experience/experience_01/experience_01_05/yeongam.go"
)
YEONGAM_JNE_LIBRARY_URL = "https://yalib.jne.go.kr/"
YEONGAM_WELFARE_URL = "https://www.yeongam.go.kr/home/welfare"
YEONGAM_HEALTH_URL = "https://www.yeongam.go.kr/home/health"
YEONGAM_ART_MUSEUM_URL = "https://www.yeongam.go.kr/home/haart"

YEONGAM_PAGE_SIZE = 15
YEONGAM_FETCH_ATTEMPTS = 4
YEONGAM_MAX_HTML_BYTES = 4_000_000
YEONGAM_PARSER = (
    "yeongam_library_aggregate_all_pages+empty_post_last+stable_first_last+"
    "current_safe_details+identity_bound_application_controls+"
    "identityless_auth_gate_excluded+comment_boundary_never_read+pii_allowlist"
)
YEONGAM_OWNERSHIP_SCOPE = "yeongam_county_library_aggregate_education_application_ledger"

# Public intermediate omitted by the server during the 2026-07-21 audit.
# Subject: Sectigo Public Server Authentication CA DV R36
# Issuer:  Sectigo Public Server Authentication Root R46 (present in certifi)
YEONGAM_SECTIGO_INTERMEDIATE_SHA256 = "8c54c334b66ba4e426772af4a3f9136c19a1aec729fdb28c535c07a5a4ef22e0"
YEONGAM_SECTIGO_INTERMEDIATE_PEM = """-----BEGIN CERTIFICATE-----
MIIGTDCCBDSgAwIBAgIQOXpmzCdWNi4NqofKbqvjsTANBgkqhkiG9w0BAQwFADBf
MQswCQYDVQQGEwJHQjEYMBYGA1UEChMPU2VjdGlnbyBMaW1pdGVkMTYwNAYDVQQD
Ey1TZWN0aWdvIFB1YmxpYyBTZXJ2ZXIgQXV0aGVudGljYXRpb24gUm9vdCBSNDYw
HhcNMjEwMzIyMDAwMDAwWhcNMzYwMzIxMjM1OTU5WjBgMQswCQYDVQQGEwJHQjEY
MBYGA1UEChMPU2VjdGlnbyBMaW1pdGVkMTcwNQYDVQQDEy5TZWN0aWdvIFB1Ymxp
YyBTZXJ2ZXIgQXV0aGVudGljYXRpb24gQ0EgRFYgUjM2MIIBojANBgkqhkiG9w0B
AQEFAAOCAY8AMIIBigKCAYEAljZf2HIz7+SPUPQCQObZYcrxLTHYdf1ZtMRe7Yeq
RPSwygz16qJ9cAWtWNTcuICc++p8Dct7zNGxCpqmEtqifO7NvuB5dEVexXn9RFFH
12Hm+NtPRQgXIFjx6MSJcNWuVO3XGE57L1mHlcQYj+g4hny90aFh2SCZCDEVkAja
EMMfYPKuCjHuuF+bzHFb/9gV8P9+ekcHENF2nR1efGWSKwnfG5RawlkaQDpRtZTm
M64TIsv/r7cyFO4nSjs1jLdXYdz5q3a4L0NoabZfbdxVb+CUEHfB0bpulZQtH1Rv
38e/lIdP7OTTIlZh6OYL6NhxP8So0/sht/4J9mqIGxRFc0/pC8suja+wcIUna0HB
pXKfXTKpzgis+zmXDL06ASJf5E4A2/m+Hp6b84sfPAwQ766rI65mh50S0Di9E3Pn
2WcaJc+PILsBmYpgtmgWTR9eV9otfKRUBfzHUHcVgarub/XluEpRlTtZudU5xbFN
xx/DgMrXLUAPaI60fZ6wA+PTAgMBAAGjggGBMIIBfTAfBgNVHSMEGDAWgBRWc1hk
lfmSGrASKgRieaFAFYghSTAdBgNVHQ4EFgQUaMASFhgOr872h6YyV6NGUV3LBycw
DgYDVR0PAQH/BAQDAgGGMBIGA1UdEwEB/wQIMAYBAf8CAQAwHQYDVR0lBBYwFAYI
KwYBBQUHAwEGCCsGAQUFBwMCMBsGA1UdIAQUMBIwBgYEVR0gADAIBgZngQwBAgEw
VAYDVR0fBE0wSzBJoEegRYZDaHR0cDovL2NybC5zZWN0aWdvLmNvbS9TZWN0aWdv
UHVibGljU2VydmVyQXV0aGVudGljYXRpb25Sb290UjQ2LmNybDCBhAYIKwYBBQUH
AQEEeDB2ME8GCCsGAQUFBzAChkNodHRwOi8vY3J0LnNlY3RpZ28uY29tL1NlY3Rp
Z29QdWJsaWNTZXJ2ZXJBdXRoZW50aWNhdGlvblJvb3RSNDYucDdjMCMGCCsGAQUF
BzABhhdodHRwOi8vb2NzcC5zZWN0aWdvLmNvbTANBgkqhkiG9w0BAQwFAAOCAgEA
YtOC9Fy+TqECFw40IospI92kLGgoSZGPOSQXMBqmsGWZUQ7rux7cj1du6d9rD6C8
ze1B2eQjkrGkIL/OF1s7vSmgYVafsRoZd/IHUrkoQvX8FZwUsmPu7amgBfaY3g+d
q1x0jNGKb6I6Bzdl6LgMD9qxp+3i7GQOnd9J8LFSietY6Z4jUBzVoOoz8iAU84OF
h2HhAuiPw1ai0VnY38RTI+8kepGWVfGxfBWzwH9uIjeooIeaosVFvE8cmYUB4TSH
5dUyD0jHct2+8ceKEtIoFU/FfHq/mDaVnvcDCZXtIgitdMFQdMZaVehmObyhRdDD
4NQCs0gaI9AAgFj4L9QtkARzhQLNyRf87Kln+YU0lgCGr9HLg3rGO8q+Y4ppLsOd
unQZ6ZxPNGIfOApbPVf5hCe58EZwiWdHIMn9lPP6+F404y8NNugbQixBber+x536
WrZhFZLjEkhp7fFXf9r32rNPfb74X/U90Bdy4lzp3+X1ukh1BuMxA/EEhDoTOS3l
7ABvc7BYSQubQ2490OcdkIzUh3ZwDrakMVrbaTxUM2p24N6dB+ns2zptWCva6jzW
r8IWKIMxzxLPv5Kt3ePKcUdvkBU/smqujSczTzzSjIoR5QqQA6lN1ZRSnuHIWCvh
JEltkYnTAH41QJ6SAWO66GrrUESwN/cgZzL4JLEqz1Y=
-----END CERTIFICATE-----
"""

YEONGAM_PII_FIELDS_NEVER_READ = (
    "강사 value",
    "문의전화 value",
    "수업내용 value",
    "첨부파일 value",
    "comment_form payload",
    "comment_list text",
    "login/attestation form payload",
)

YEONGAM_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    YEONGAM_PROVIDER: {
        "decision": "canonical_county_library_aggregate_owner",
        "candidate_id": YEONGAM_CANDIDATE_ID,
        "registered_url": YEONGAM_REGISTERED_DETAIL_URL,
        "canonical_url": YEONGAM_CANONICAL_URL,
        "operator": "영암군립도서관",
        "reason": "the registered single expired detail is replaced by its full list",
    },
    YEONGAM_JNTLE_PROVIDER: {
        "decision": "exclude_from_county_owner_keep_regional_discovery_owner",
        "candidate_id": YEONGAM_JNTLE_CANDIDATE_ID,
        "url": YEONGAM_JNTLE_URL,
        "operator": "전남인재평생교육진흥원",
        "reason": "regional index with expired external destinations, not an application ledger",
    },
    "OFFICIAL_YEONGAM_LIFELONG_STATIC_DIRECTORY": {
        "decision": "exclude_stale_static_information_without_course_identity",
        "url": YEONGAM_EDUCITY_URL,
        "operator": "영암군 평생교육센터",
        "reason": "2025 static tables have no detail, paging or application identity",
    },
    "OFFICIAL_JNE_YEONGAM_LIBRARY": {
        "decision": "keep_separate_provincial_education_library_owner",
        "url": YEONGAM_JNE_LIBRARY_URL,
        "operator": "전라남도교육청영암도서관",
        "reason": "provincial education-office library, not the county library owner",
    },
    "OFFICIAL_YEONGAM_WELFARE": {
        "decision": "keep_separate_welfare_owner_no_current_structured_ledger",
        "url": YEONGAM_WELFARE_URL,
    },
    "OFFICIAL_YEONGAM_HEALTH": {
        "decision": "keep_separate_health_owner_no_structured_course_ledger",
        "url": YEONGAM_HEALTH_URL,
    },
    "OFFICIAL_YEONGAM_ART_MUSEUM": {
        "decision": "keep_separate_museum_owner",
        "url": YEONGAM_ART_MUSEUM_URL,
    },
}

YEONGAM_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "registered_single_detail": YEONGAM_REGISTERED_DETAIL_URL,
    "canonical_url": YEONGAM_CANONICAL_URL,
    "source_rows": 75,
    "data_pages": 5,
    "page_counts": {1: 15, 2: 15, 3: 15, 4: 15, 5: 15},
    "empty_sentinel_page": 6,
    "empty_sentinel_text": "검색내역이 없습니다.",
    "first_last_rechecks": 2,
    "unique_identities": 75,
    "strictly_descending_identities": True,
    "source_status_counts": {"신청하기": 3, "접수대기": 1, "신청마감": 71},
    "current_or_future_rows": 4,
    "current_ids": ("385", "384", "383", "382"),
    "current_branch_counts": {"학산도서관": 2, "삼호도서관": 2},
    "current_details_verified": 4,
    "identity_bound_application_controls": 0,
    "identityless_auth_gates_excluded": 1,
    "full_open_rows_with_suppressed_control": 2,
    "scheduled_rows_without_control": 1,
    "tls_leaf_valid_through": "2026-09-05",
    "tls_server_omits_intermediate": True,
    "tls_intermediate_sha256": YEONGAM_SECTIGO_INTERMEDIATE_SHA256,
    "lifelong_static_tabs": {
        YEONGAM_EDUCITY_URL: {"rows": 36, "state": "2025 static"},
        YEONGAM_EDUCITY_LIBRARY_URL: {"rows": 6, "state": "2025 static"},
        YEONGAM_EDUCITY_SENIOR_URL: {"rows": 20, "state": "2025 static"},
        YEONGAM_EDUCITY_OTHER_URL: {"rows": 5, "state": "static/no identity"},
    },
    "resident_autonomy_conclusion": (
        "no separate structured county resident-autonomy ledger; the regional "
        "index contains five expired Yeongam-eup resident-center rows"
    ),
    "jntle_regional_audit": {
        "rows": 306,
        "data_pages": 21,
        "empty_page": 22,
        "current_rows": 0,
        "source_status_counts": {"종료": 306},
        "county_destinations": 248,
        "jne_library_destinations": 58,
        "semantic_unique_rows": 270,
    },
    "conclusion": (
        "collect the county library aggregate as the existing Yeongam owner; "
        "exclude static lifelong pages and retain JNTLE/JNE as separate owners"
    ),
}


class YeongamContractError(ValueError):
    """Raised when the audited Yeongam source contract changes."""


class _TransientFetchError(RuntimeError):
    """Retryable transport/block response without retaining its body."""


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
Sleeper = Callable[[float], None]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"^[1-9]\d*$")
_COUNT_RE = re.compile(r"^(?P<current>\d+)\s*/\s*(?P<total>\d+)$")
_RANGE_RE = re.compile(r"(?P<start>20\d{2}-\d{2}-\d{2})\s*~\s*(?P<end>20\d{2}-\d{2}-\d{2})")
_DETAIL_APPLY_RE = re.compile(
    r"^(?P<start>20\d{2}-\d{2}-\d{2})\s+\d{2}:\d{2}\s*~\s*"
    r"(?P<end>20\d{2}-\d{2}-\d{2})\s+\d{2}:\d{2}$"
)
_DETAIL_CAPACITY_RE = re.compile(r"^(?P<total>\d+)명$")
_PHONE_RE = re.compile(r"(?<!\d)(?:0\d{1,2})[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_LIST_TITLE = "강좌 신청 < 문화행사 < 영암군"
_LIST_CAPTION = "교육명, 접수인원, 기간으로 구성된 표"
_LIST_HEADERS = ("교육명", "교육장소", "접수인원", "기간")
_EMPTY_MARKER = "검색내역이 없습니다."
_STATUS_MAP: Mapping[str, str] = {
    "신청하기": "OPEN",
    "접수대기": "SCHEDULED",
    "신청마감": "CLOSED",
}
_EDUCATION_STATES = frozenset({"교육준비", "교육종료"})
_DETAIL_ALLOWED = frozenset(
    {
        "프로그램명",
        "접수기간",
        "모집인원",
        "모집대상",
        "교육기간",
        "교육시간",
        "교육요일",
        "교육장소",
    }
)
_DETAIL_SKIPPED = frozenset({"강사", "문의전화", "수업내용", "첨부파일"})
_BRANCHES = ("영암도서관", "삼호도서관", "학산도서관")

_ALLOWED_RAW_KEYS = frozenset(
    {
        "parser",
        "source_identity",
        "source_page",
        "source_status",
        "source_education_state",
        "source_application_period",
        "source_education_period",
        "source_education_time",
        "source_education_day",
        "source_venue",
        "source_target",
        "fee_source_omission",
        "detail_verified",
        "list_detail_identity_verified",
        "application_control_present",
        "application_control_contract",
        "application_control_verified",
        "identity_bound_application_control",
        "identityless_auth_gate_excluded",
        "comment_boundary_structurally_discarded",
        "service_family",
    }
)
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
        "collection_type",
        "municipality_code",
        "municipality_name",
        "raw_fields",
    }
)


class YeongamVerifiedTLSAdapter(HTTPAdapter):
    def __init__(self, context: ssl.SSLContext, *args: Any, **kwargs: Any) -> None:
        self.ssl_context = context
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        kwargs["ssl_context"] = self.ssl_context
        super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, proxy: str, **proxy_kwargs: Any) -> Any:
        proxy_kwargs["ssl_context"] = self.ssl_context
        return super().proxy_manager_for(proxy, **proxy_kwargs)


def build_yeongam_tls_context() -> ssl.SSLContext:
    der = ssl.PEM_cert_to_DER_cert(YEONGAM_SECTIGO_INTERMEDIATE_PEM)
    if hashlib.sha256(der).hexdigest() != YEONGAM_SECTIGO_INTERMEDIATE_SHA256:
        raise RuntimeError("embedded Sectigo intermediate fingerprint changed")
    context = ssl.create_default_context(cafile=certifi.where())
    context.load_verify_locations(cadata=YEONGAM_SECTIGO_INTERMEDIATE_PEM)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise RuntimeError("verified TLS defaults unexpectedly unavailable")
    return context


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _text(node: Any) -> str:
    if not isinstance(node, Tag):
        return ""
    return _clean(node.get_text(" ", strip=True))


def _one(nodes: Iterable[Any], label: str) -> Any:
    values = list(nodes)
    if len(values) != 1:
        raise YeongamContractError(f"expected one {label}, found {len(values)}")
    return values[0]


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise TypeError("today must be a date, datetime, ISO date string, or None")


def is_yeongam_education_target(target: Any) -> bool:
    return (
        _clean(_target_value(target, "provider")) == YEONGAM_PROVIDER
        and _clean(_target_value(target, "url")) == YEONGAM_CANONICAL_URL
    )


def is_yeongam_candidate_alias_target(target: Any) -> bool:
    return (
        _clean(_target_value(target, "provider")) == YEONGAM_PROVIDER
        and _clean(_target_value(target, "url")) == YEONGAM_REGISTERED_DETAIL_URL
    )


def is_yeongam_jntle_separate_owner_target(target: Any) -> bool:
    return (
        _clean(_target_value(target, "provider")) == YEONGAM_JNTLE_PROVIDER
        and _clean(_target_value(target, "url")) == YEONGAM_JNTLE_URL
    )


def yeongam_list_url(page: int = 1) -> str:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise YeongamContractError("page must be a positive integer")
    if page == 1:
        return YEONGAM_CANONICAL_URL
    return f"{YEONGAM_CANONICAL_URL}?" + urlencode((("page", str(page)), ("search", ""), ("keyword", "")))


def yeongam_detail_url(identity: Any, page: int = 1) -> str:
    identity_text = _clean(identity)
    if not _IDENTITY_RE.fullmatch(identity_text):
        raise YeongamContractError("detail identity must be a positive integer")
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise YeongamContractError("detail source page must be a positive integer")
    return f"{YEONGAM_CANONICAL_URL}/show/{identity_text}?" + urlencode(
        (("page", str(page)), ("search", ""), ("keyword", ""))
    )


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            "Referer": "https://www.yeongam.go.kr/home/newlib/",
        }
    )
    session.mount(
        f"https://{YEONGAM_HOST}/",
        YeongamVerifiedTLSAdapter(build_yeongam_tls_context(), max_retries=0),
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=False)


def _payload_bytes(value: Any, requested_url: str) -> bytes:
    status = getattr(value, "status_code", 200)
    if status != 200:
        raise _TransientFetchError(f"HTTP {status}")
    final_url = _clean(getattr(value, "url", ""))
    if final_url and final_url != requested_url:
        raise _TransientFetchError("response URL changed")
    headers = getattr(value, "headers", None)
    if headers:
        content_type = _clean(headers.get("Content-Type")).casefold()
        if content_type and "html" not in content_type:
            raise _TransientFetchError("response is not HTML")
    if isinstance(value, BeautifulSoup):
        payload = value.encode("utf-8")
    elif isinstance(value, bytes):
        payload = value
    elif isinstance(value, str):
        payload = value.encode("utf-8")
    elif hasattr(value, "content"):
        payload = bytes(value.content)
    else:
        raise _TransientFetchError("response has no HTML payload")
    if not payload or len(payload) > YEONGAM_MAX_HTML_BYTES:
        raise _TransientFetchError("empty or oversized HTML payload")
    head = payload[:32_768].lower()
    if b"request blocked" in head or b"access denied" in head:
        raise _TransientFetchError("server returned a request-block page")
    return payload


def _close_quietly(value: Any) -> None:
    try:
        if value is not None and hasattr(value, "close"):
            value.close()
    except Exception:
        pass


class _Client:
    """Sequential client that rebuilds its verified session on transient failure."""

    def __init__(
        self,
        *,
        timeout: int,
        attempts: int,
        session_factory: SessionFactory,
        fetcher: Fetcher,
        sleeper: Sleeper,
    ) -> None:
        self.timeout = timeout
        self.attempts = attempts
        self.session_factory = session_factory
        self.fetcher = fetcher
        self.sleeper = sleeper
        self.session: Any = None
        self.http_attempts = 0
        self.retry_count = 0
        self.sessions_created = 0

    def _session(self) -> Any:
        if self.session is None:
            self.session = self.session_factory()
            self.sessions_created += 1
        return self.session

    def get(self, url: str) -> BeautifulSoup:
        last_type = "unknown"
        for attempt in range(1, self.attempts + 1):
            try:
                self.http_attempts += 1
                response = self.fetcher(self._session(), url, self.timeout)
                return BeautifulSoup(_payload_bytes(response, url), "html.parser")
            except (
                _TransientFetchError,
                requests.RequestException,
                ssl.SSLError,
                ConnectionError,
                TimeoutError,
                OSError,
            ) as exc:
                last_type = type(exc).__name__
                _close_quietly(self.session)
                self.session = None
                if attempt >= self.attempts:
                    break
                self.retry_count += 1
                self.sleeper(min(2.0, 0.35 * attempt))
        raise YeongamContractError(f"fetch failed after {self.attempts} attempts ({last_type})")

    def close(self) -> None:
        _close_quietly(self.session)
        self.session = None


def _parse_pager(soup: BeautifulSoup, page: int, *, sentinel: bool) -> int:
    pager = _one(soup.select("div.pagenum"), "catalogue pager")
    children = pager.find_all(recursive=False)
    if not children or any(node.name not in {"a", "strong"} for node in children):
        raise YeongamContractError(f"page {page}: pager structure changed")
    strong = [node for node in children if node.name == "strong"]
    if sentinel:
        if strong:
            raise YeongamContractError("empty sentinel advertises an active page")
    elif len(strong) != 1 or _text(strong[0]) != str(page):
        raise YeongamContractError(f"page {page}: active page marker changed")
    values: list[int] = []
    for node in children:
        raw = _text(node)
        if not raw.isdigit() or int(raw) < 1:
            raise YeongamContractError(f"page {page}: pager number changed")
        number = int(raw)
        values.append(number)
        if node.name == "a":
            expected = f"?page={number}&search=&keyword="
            if _clean(node.get("href")) != expected or _clean(node.get("title")) != f"{number} 페이지":
                raise YeongamContractError(f"page {page}: pager link changed")
        elif node.attrs:
            raise YeongamContractError(f"page {page}: active pager attributes changed")
    if len(values) != len(set(values)) or tuple(values) != tuple(range(1, max(values) + 1)):
        raise YeongamContractError(f"page {page}: pager range changed")
    return max(values)


def _identity_from_href(value: Any, page: int) -> str:
    href = _clean(value)
    prefix = f"{YEONGAM_DETAIL_PREFIX}"
    parsed = urlparse(href)
    if parsed.scheme or parsed.netloc or parsed.fragment or not parsed.path.startswith(prefix):
        raise YeongamContractError(f"page {page}: detail link scope changed")
    identity = parsed.path[len(prefix) :]
    if not _IDENTITY_RE.fullmatch(identity) or parsed.path != prefix + identity:
        raise YeongamContractError(f"page {page}: detail identity changed")
    if parse_qsl(parsed.query, keep_blank_values=True) != [
        ("page", str(page)),
        ("search", ""),
        ("keyword", ""),
    ]:
        raise YeongamContractError(f"course {identity}: detail query order changed")
    return identity


def _date_range(start: str, end: str, label: str) -> tuple[str, str]:
    try:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
    except ValueError as exc:
        raise YeongamContractError(f"{label} date changed") from exc
    if end_date < start_date:
        raise YeongamContractError(f"{label} is reversed")
    return start_date.isoformat(), end_date.isoformat()


def _parse_list_row(row: Tag, page: int) -> dict[str, Any]:
    cells = row.find_all("td", recursive=False)
    if len(cells) != 4:
        raise YeongamContractError(f"page {page}: course row must have four cells")
    title_anchor = _one(cells[0].find_all("a", href=True, recursive=False), "title link")
    identity = _identity_from_href(title_anchor.get("href"), page)
    title = _text(title_anchor)
    venue = _text(cells[1])
    if not title or not venue:
        raise YeongamContractError(f"course {identity}: title or venue is empty")

    count_match = _COUNT_RE.fullmatch(_text(cells[2]))
    if not count_match:
        raise YeongamContractError(f"course {identity}: capacity format changed")
    current = int(count_match.group("current"))
    total = int(count_match.group("total"))
    if total < 1 or current < 0:
        raise YeongamContractError(f"course {identity}: invalid capacity")

    images = cells[3].find_all("img")
    alts = tuple(_clean(node.get("alt")) for node in images)
    if len(alts) != 4 or alts[0] != "신청" or alts[2] != "교육":
        raise YeongamContractError(f"course {identity}: status image contract changed")
    source_status = alts[1]
    education_state = alts[3]
    if source_status not in _STATUS_MAP or education_state not in _EDUCATION_STATES:
        raise YeongamContractError(f"course {identity}: unaudited source status")
    date_matches = list(_RANGE_RE.finditer(_text(cells[3])))
    if len(date_matches) != 2:
        raise YeongamContractError(f"course {identity}: expected two list periods")
    apply_start, apply_end = _date_range(
        date_matches[0].group("start"),
        date_matches[0].group("end"),
        f"course {identity} application period",
    )
    start, end = _date_range(
        date_matches[1].group("start"),
        date_matches[1].group("end"),
        f"course {identity} education period",
    )

    secondary = cells[3].find_all("a", href=True, recursive=False)
    if source_status == "신청하기":
        if len(secondary) != 1 or _identity_from_href(secondary[0].get("href"), page) != identity:
            raise YeongamContractError(f"course {identity}: active detail control changed")
    elif secondary:
        raise YeongamContractError(f"course {identity}: inactive row gained a detail control")

    return {
        "identity": identity,
        "source_page": page,
        "title": title,
        "venue": venue,
        "capacity_current": current,
        "capacity_total": total,
        "source_status": source_status,
        "status": _STATUS_MAP[source_status],
        "education_state": education_state,
        "apply_start_date": apply_start,
        "apply_end_date": apply_end,
        "start_date": start,
        "end_date": end,
        "detail_url": yeongam_detail_url(identity, page),
        "_source_end_date": date.fromisoformat(end),
    }


def _parse_list_page(
    soup: BeautifulSoup,
    page: int,
    *,
    expected_last: Optional[int] = None,
    sentinel: bool = False,
) -> tuple[int, tuple[dict[str, Any], ...]]:
    title = _one(soup.select("title"), "document title")
    if _text(title) != _LIST_TITLE:
        raise YeongamContractError(f"page {page}: document title changed")
    table = _one(soup.select("table#board_list_table.list_table"), "course table")
    caption = _one(table.select("caption"), "course table caption")
    if _text(caption) != _LIST_CAPTION:
        raise YeongamContractError("course table caption changed")
    headers = tuple(_text(node) for node in table.select("thead th"))
    if headers != _LIST_HEADERS:
        raise YeongamContractError("course table columns changed")
    body = _one(table.select("tbody"), "course table body")
    tr_nodes = body.find_all("tr", recursive=False)
    if not tr_nodes:
        raise YeongamContractError(f"page {page}: empty body without marker")

    if sentinel:
        if len(tr_nodes) != 1:
            raise YeongamContractError("empty sentinel has multiple rows")
        cells = tr_nodes[0].find_all("td", recursive=False)
        if (
            len(cells) != 1
            or _clean(cells[0].get("colspan")) != "5"
            or _text(cells[0]) != _EMPTY_MARKER
            or cells[0].select("a[href]")
        ):
            raise YeongamContractError("empty sentinel marker changed")
        advertised = _parse_pager(soup, page, sentinel=True)
        if expected_last is not None and advertised != expected_last:
            raise YeongamContractError("empty sentinel pager boundary changed")
        return advertised, ()

    if any(len(tr.find_all("td", recursive=False)) == 1 for tr in tr_nodes):
        raise YeongamContractError(f"page {page}: unexpected empty marker")
    advertised = _parse_pager(soup, page, sentinel=False)
    if expected_last is not None and advertised != expected_last:
        raise YeongamContractError(f"page {page}: advertised last page changed")
    rows = tuple(_parse_list_row(row, page) for row in tr_nodes)
    if len(rows) > YEONGAM_PAGE_SIZE:
        raise YeongamContractError(f"page {page}: row count exceeds page size")
    return advertised, rows


def _list_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            _clean(row.get("identity")),
            _clean(row.get("title")),
            _clean(row.get("venue")),
            row.get("capacity_current"),
            row.get("capacity_total"),
            _clean(row.get("source_status")),
            _clean(row.get("education_state")),
            _clean(row.get("apply_start_date")),
            _clean(row.get("apply_end_date")),
            _clean(row.get("start_date")),
            _clean(row.get("end_date")),
        )
        for row in rows
    )


def _safe_detail_fields(table: Tag, identity: str) -> dict[str, str]:
    result: dict[str, str] = {}
    rows = table.find_all("tr")
    if not rows:
        raise YeongamContractError(f"course {identity}: detail table is empty")
    for tr in rows:
        th = tr.find("th", recursive=False)
        td = tr.find("td", recursive=False)
        if th is None or td is None:
            raise YeongamContractError(f"course {identity}: detail row structure changed")
        label = _text(th)
        if label in result:
            raise YeongamContractError(f"course {identity}: duplicate detail field {label}")
        if label in _DETAIL_ALLOWED:
            value = _text(td)
            if not value:
                raise YeongamContractError(f"course {identity}: safe field {label} is empty")
            result[label] = value
        elif label not in _DETAIL_SKIPPED:
            raise YeongamContractError(f"course {identity}: unknown detail field {label}")
        # Skipped td values are intentionally never accessed.
    missing = _DETAIL_ALLOWED - set(result)
    if missing:
        raise YeongamContractError(f"course {identity}: safe fields missing ({', '.join(sorted(missing))})")
    return result


def _branch_from_venue(venue: str, identity: str) -> str:
    matches = [branch for branch in _BRANCHES if venue.startswith(branch)]
    if len(matches) != 1:
        raise YeongamContractError(f"course {identity}: exact library branch is ambiguous")
    return matches[0]


def _branch_code(branch: str) -> str:
    digest = hashlib.sha1(branch.encode("utf-8")).hexdigest()[:12].upper()
    return f"YEONGAM_{digest}"


def _query_pairs(value: str) -> tuple[Any, list[tuple[str, str]]]:
    parsed = urlparse(value)
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if len({key for key, _ in pairs}) != len(pairs):
        raise YeongamContractError("application URL has duplicate query fields")
    return parsed, pairs


def _identity_bound_application_url(href: str, identity: str) -> str:
    absolute = urljoin(YEONGAM_CANONICAL_URL, href)
    parsed, pairs = _query_pairs(absolute)
    if (
        parsed.scheme != "https"
        or parsed.hostname != YEONGAM_HOST
        or parsed.username
        or parsed.password
        or parsed.port
        or parsed.fragment
    ):
        return ""
    identity_path = f"{YEONGAM_DETAIL_PREFIX}{identity}"
    if parsed.path == identity_path:
        return absolute
    if parsed.path != YEONGAM_LOGIN_PATH:
        return ""
    values = dict(pairs)
    if values.get("set") != "attest" or set(values) != {"set", "return_url"}:
        return ""
    return_url = values["return_url"]
    returned = urlparse(urljoin(YEONGAM_CANONICAL_URL, return_url))
    if (
        returned.scheme != "https"
        or returned.hostname != YEONGAM_HOST
        or returned.username
        or returned.password
        or returned.port
        or returned.fragment
        or returned.path != identity_path
    ):
        return ""
    return absolute


def _is_identityless_audited_gate(href: str) -> bool:
    absolute = urljoin(YEONGAM_CANONICAL_URL, href)
    parsed, pairs = _query_pairs(absolute)
    return bool(
        parsed.scheme == "https"
        and parsed.hostname == YEONGAM_HOST
        and not parsed.username
        and not parsed.password
        and not parsed.port
        and not parsed.fragment
        and parsed.path == YEONGAM_LOGIN_PATH
        and pairs
        == [
            ("set", "attest"),
            ("return_url", f"//{YEONGAM_HOST}{YEONGAM_LIST_PATH}"),
        ]
    )


def _application_control(
    soup: BeautifulSoup,
    listed: Mapping[str, Any],
) -> tuple[str, bool, str, str, bool]:
    identity = _clean(listed.get("identity"))
    controls = list(soup.select("a.btn_submit.next[href]"))
    status = _clean(listed.get("status"))
    current = int(listed.get("capacity_current") or 0)
    total = int(listed.get("capacity_total") or 0)
    full = total > 0 and current >= total

    if status == "OPEN" and not full:
        if len(controls) != 1:
            raise YeongamContractError(f"course {identity}: open course application control changed")
        control = controls[0]
        label = _text(control)
        href = _clean(control.get("href"))
        if label != "본인 확인 후 신청하기":
            raise YeongamContractError(f"course {identity}: application label changed")
        bound = _identity_bound_application_url(href, identity)
        if bound:
            return bound, True, label, "identity_bound_auth_control", False
        if _is_identityless_audited_gate(href):
            return (
                "",
                False,
                label,
                "identityless_auth_gate_excluded_no_course_application_url",
                True,
            )
        raise YeongamContractError(f"course {identity}: application control is not identity-bound")

    if controls:
        raise YeongamContractError(f"course {identity}: unavailable course gained a control")
    if status == "OPEN" and full:
        return "", False, "신청하기", "full_capacity_control_suppressed", False
    if status == "SCHEDULED":
        return "", False, "접수대기", "scheduled_control_not_yet_visible", False
    if status == "CLOSED":
        return "", False, "신청마감", "closed_control_not_visible", False
    raise YeongamContractError(f"course {identity}: unaudited normalized status")


def _parse_detail(listed: Mapping[str, Any], soup: BeautifulSoup) -> dict[str, Any]:
    identity = _clean(listed.get("identity"))
    source_page = int(listed.get("source_page") or 0)
    if _clean(listed.get("detail_url")) != yeongam_detail_url(identity, source_page):
        raise YeongamContractError(f"course {identity}: requested detail identity changed")

    comments = list(soup.select("div.comment"))
    if len(comments) != 1:
        raise YeongamContractError(f"course {identity}: comment boundary changed")
    comment = comments[0]
    form = _one(comment.select("form#comment_form"), f"course {identity} comment form")
    expected_action = f"{YEONGAM_DETAIL_PREFIX}{identity}?sub_mode=comment_write"
    if (
        _clean(form.get("method")).lower() != "post"
        or _clean(form.get("action")) != expected_action
        or len(comment.select("#comment_list")) != 1
    ):
        raise YeongamContractError(f"course {identity}: comment boundary contract changed")
    comment.extract()  # Must precede detail-table or control text access.

    title = _one(soup.select("title"), f"course {identity} document title")
    if _text(title) != _LIST_TITLE:
        raise YeongamContractError(f"course {identity}: document title changed")
    table = _one(soup.select("table.edu_form.res_th"), f"course {identity} detail table")
    safe = _safe_detail_fields(table, identity)
    if safe["프로그램명"] != _clean(listed.get("title")):
        raise YeongamContractError(f"course {identity}: list/detail title differs")

    apply_match = _DETAIL_APPLY_RE.fullmatch(safe["접수기간"])
    if not apply_match:
        raise YeongamContractError(f"course {identity}: detail application period changed")
    apply_start, apply_end = _date_range(
        apply_match.group("start"),
        apply_match.group("end"),
        f"course {identity} detail application period",
    )
    education_matches = list(_RANGE_RE.finditer(safe["교육기간"]))
    if len(education_matches) != 1 or education_matches[0].group(0) != safe["교육기간"]:
        raise YeongamContractError(f"course {identity}: detail education period changed")
    start, end = _date_range(
        education_matches[0].group("start"),
        education_matches[0].group("end"),
        f"course {identity} detail education period",
    )
    for actual, expected, label in (
        (apply_start, listed.get("apply_start_date"), "application start"),
        (apply_end, listed.get("apply_end_date"), "application end"),
        (start, listed.get("start_date"), "education start"),
        (end, listed.get("end_date"), "education end"),
        (safe["교육장소"], listed.get("venue"), "venue"),
    ):
        if _clean(actual) != _clean(expected):
            raise YeongamContractError(f"course {identity}: list/detail {label} differs")
    capacity_match = _DETAIL_CAPACITY_RE.fullmatch(safe["모집인원"])
    if not capacity_match or int(capacity_match.group("total")) != listed.get("capacity_total"):
        raise YeongamContractError(f"course {identity}: list/detail capacity differs")

    application_url, present, method, contract, identityless = _application_control(soup, listed)
    branch = _branch_from_venue(safe["교육장소"], identity)
    schedule = f"{safe['교육요일']} / {safe['교육시간']}"
    raw_fields = {
        "parser": YEONGAM_PARSER,
        "source_identity": identity,
        "source_page": source_page,
        "source_status": _clean(listed.get("source_status")),
        "source_education_state": _clean(listed.get("education_state")),
        "source_application_period": safe["접수기간"],
        "source_education_period": safe["교육기간"],
        "source_education_time": safe["교육시간"],
        "source_education_day": safe["교육요일"],
        "source_venue": safe["교육장소"],
        "source_target": safe["모집대상"],
        "fee_source_omission": True,
        "detail_verified": True,
        "list_detail_identity_verified": True,
        "application_control_present": present,
        "application_control_contract": contract,
        "application_control_verified": True,
        "identity_bound_application_control": bool(application_url),
        "identityless_auth_gate_excluded": identityless,
        "comment_boundary_structurally_discarded": True,
        "service_family": "education",
    }
    return {
        "provider": YEONGAM_PROVIDER,
        "provider_course_id": identity,
        "prefer_incoming_provider_course_id": True,
        "title": safe["프로그램명"],
        "description": safe["프로그램명"],
        "branch": branch,
        "branch_code": _branch_code(branch),
        "preserve_branch": True,
        "category": "도서관 교육",
        "program_type": "교육",
        "raw_url": yeongam_detail_url(identity, source_page),
        "application_url": application_url,
        "application_type": "ONLINE_RESERVATION_AUTH_REQUIRED",
        "application_method_raw": method,
        "reservation_available": present,
        "status": _clean(listed.get("status")),
        "fee": "공식 페이지 미기재",
        "period": f"{start} ~ {end}",
        "start_date": start,
        "end_date": end,
        "apply_period": f"{apply_start} ~ {apply_end}",
        "apply_start_date": apply_start,
        "apply_end_date": apply_end,
        "schedule_raw": schedule,
        "target": safe["모집대상"],
        "capacity_current": listed.get("capacity_current"),
        "capacity_total": listed.get("capacity_total"),
        "venue_name": safe["교육장소"],
        "collection_category": "공공교육",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_education",
        "collection_type": YEONGAM_PARSER,
        "municipality_code": YEONGAM_MUNICIPALITY_CODE,
        "municipality_name": YEONGAM_MUNICIPALITY_NAME,
        "raw_fields": raw_fields,
    }


def _walk_values(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield _clean(key), child
            yield from _walk_values(child)
    elif isinstance(value, (list, tuple, set)):
        for child in value:
            yield "", child


def _validate_output(row: Mapping[str, Any]) -> None:
    unexpected = set(row) - _ALLOWED_ROW_KEYS
    if unexpected:
        raise YeongamContractError("unexpected persisted fields: " + ", ".join(sorted(unexpected)))
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping):
        raise YeongamContractError("raw_fields must be a mapping")
    raw_unexpected = set(raw) - _ALLOWED_RAW_KEYS
    if raw_unexpected:
        raise YeongamContractError("unexpected persisted raw fields: " + ", ".join(sorted(raw_unexpected)))
    forbidden_tokens = (
        "instructor",
        "teacher",
        "contact",
        "phone",
        "email",
        "attachment",
        "강사",
        "문의",
        "연락처",
        "첨부",
        "수업내용",
        "source_html",
        "form_payload",
    )
    for key, value in _walk_values(row):
        if any(token.casefold() in key.casefold() for token in forbidden_tokens):
            raise YeongamContractError(f"forbidden persisted key: {key}")
        if isinstance(value, str) and (_PHONE_RE.search(value) or _EMAIL_RE.search(value)):
            raise YeongamContractError("PII-like value reached persisted output")
    if row.get("description") != row.get("title"):
        raise YeongamContractError("free-form description reached output")
    if raw.get("service_family") != "education":
        raise YeongamContractError("non-education row reached output")
    application_url = _clean(row.get("application_url"))
    if application_url and not _identity_bound_application_url(application_url, _clean(row.get("provider_course_id"))):
        raise YeongamContractError("persisted application URL is not identity-bound")


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity in seen:
            raise YeongamContractError("duplicate output identity")
        seen.add(identity)
        result.append(row)
    return result


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "http_attempts": 0,
        "network_retry_count": 0,
        "sessions_created": 0,
        "network_concurrency": 1,
        "data_pages": 0,
        "page_counts": {},
        "sentinel_requests": 0,
        "stability_rechecks": 0,
        "source_rows": 0,
        "current_source_count": 0,
        "expired_count": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "comment_boundaries_discarded": 0,
        "identity_duplicate_count": 0,
        "raw_url_duplicate_count": 0,
        "identity_bound_application_control_count": 0,
        "identityless_auth_gate_excluded_count": 0,
        "full_capacity_control_suppressed_count": 0,
        "scheduled_control_not_visible_count": 0,
        "returned_count": 0,
        "source_cap_reached": False,
        "pagination_complete": False,
        "details_complete": False,
        "application_controls_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": "",
        "municipality_code": YEONGAM_MUNICIPALITY_CODE,
        "municipality_name": YEONGAM_MUNICIPALITY_NAME,
        "canonical_url": YEONGAM_CANONICAL_URL,
        "ownership_scope": YEONGAM_OWNERSHIP_SCOPE,
        "tls_certificate_verification": True,
        "tls_hostname_verification": True,
        "tls_intermediate_sha256": YEONGAM_SECTIGO_INTERMEDIATE_SHA256,
    }


def collect_yeongam_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 50,
    detail_limit: int = 200,
    today: Optional[date | datetime | str] = None,
    fetch_attempts: int = YEONGAM_FETCH_ATTEMPTS,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    sleeper: Optional[Sleeper] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future Yeongam library education snapshot."""

    meta = _base_meta()
    if not is_yeongam_education_target(target):
        meta["configured_collection_error"] = "target does not match the exact canonical Yeongam owner URL"
        return [], YEONGAM_PARSER, meta
    integers = (timeout, max_pages, detail_limit, fetch_attempts)
    if (
        any(isinstance(value, bool) or not isinstance(value, int) for value in integers)
        or timeout < 1
        or max_pages < 1
        or detail_limit < 0
        or not 1 <= fetch_attempts <= 8
    ):
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": "invalid collection limits",
            }
        )
        return [], YEONGAM_PARSER, meta
    try:
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], YEONGAM_PARSER, meta

    client = _Client(
        timeout=timeout,
        attempts=fetch_attempts,
        session_factory=session_factory or _default_session_factory,
        fetcher=fetcher or _default_fetcher,
        sleeper=sleeper or time.sleep,
    )

    def update_network_meta() -> None:
        meta.update(
            {
                "http_attempts": client.http_attempts,
                "network_retry_count": client.retry_count,
                "sessions_created": client.sessions_created,
            }
        )

    try:
        advertised_last, first_rows = _parse_list_page(client.get(yeongam_list_url(1)), 1)
        if not first_rows:
            raise YeongamContractError("canonical catalogue unexpectedly starts empty")
        required = advertised_last + 3
        meta.update(
            {
                "list_requests": 1,
                "pages": 1,
                "required_list_requests": required,
            }
        )
        if required > max_pages:
            meta["source_cap_reached"] = True
            raise YeongamContractError(f"max_pages cap allows {max_pages} of {required} required list requests")

        page_rows: dict[int, tuple[dict[str, Any], ...]] = {1: first_rows}
        for page in range(2, advertised_last + 1):
            _, rows = _parse_list_page(
                client.get(yeongam_list_url(page)),
                page,
                expected_last=advertised_last,
            )
            page_rows[page] = rows
            meta["list_requests"] += 1
            meta["pages"] += 1

        for page in range(1, advertised_last):
            if len(page_rows[page]) != YEONGAM_PAGE_SIZE:
                raise YeongamContractError(f"page {page}: non-final row count changed")
        if not 1 <= len(page_rows[advertised_last]) <= YEONGAM_PAGE_SIZE:
            raise YeongamContractError("final data page row count changed")

        _, sentinel_rows = _parse_list_page(
            client.get(yeongam_list_url(advertised_last + 1)),
            advertised_last + 1,
            expected_last=advertised_last,
            sentinel=True,
        )
        if sentinel_rows:
            raise YeongamContractError("immediate post-last page is not empty")
        meta.update(
            {
                "list_requests": meta["list_requests"] + 1,
                "pages": meta["pages"] + 1,
                "sentinel_requests": 1,
            }
        )

        _, first_recheck = _parse_list_page(
            client.get(yeongam_list_url(1)),
            1,
            expected_last=advertised_last,
        )
        _, last_recheck = _parse_list_page(
            client.get(yeongam_list_url(advertised_last)),
            advertised_last,
            expected_last=advertised_last,
        )
        meta.update(
            {
                "list_requests": meta["list_requests"] + 2,
                "pages": meta["pages"] + 2,
                "stability_rechecks": 2,
            }
        )
        if _list_signature(first_recheck) != _list_signature(first_rows):
            raise YeongamContractError("first-page stability recheck changed")
        if _list_signature(last_recheck) != _list_signature(page_rows[advertised_last]):
            raise YeongamContractError("last-page stability recheck changed")

        listed = [row for page in range(1, advertised_last + 1) for row in page_rows[page]]
        identities = [_clean(row.get("identity")) for row in listed]
        duplicates = len(identities) - len(set(identities))
        if duplicates:
            raise YeongamContractError(f"{duplicates} duplicate official identities")
        if any(int(left) <= int(right) for left, right in zip(identities, identities[1:])):
            raise YeongamContractError("official identities are not strictly descending")
        meta.update(
            {
                "data_pages": advertised_last,
                "page_counts": {page: len(rows) for page, rows in page_rows.items()},
                "source_rows": len(listed),
                "identity_duplicate_count": duplicates,
                "pagination_complete": (
                    meta["list_requests"] == required
                    and meta["sentinel_requests"] == 1
                    and meta["stability_rechecks"] == 2
                ),
            }
        )
        if not meta["pagination_complete"]:
            raise YeongamContractError("complete pagination contract was not satisfied")

        current_rows = [row for row in listed if row["_source_end_date"] >= cutoff]
        meta.update(
            {
                "current_source_count": len(current_rows),
                "expired_count": len(listed) - len(current_rows),
                "current_source_ids": [_clean(row.get("identity")) for row in current_rows],
            }
        )
        if len(current_rows) > detail_limit:
            meta["source_cap_reached"] = True
            raise YeongamContractError(
                f"detail_limit cap allows {detail_limit} of {len(current_rows)} required details"
            )

        meta["detail_attempts"] = len(current_rows)
        detailed: list[dict[str, Any]] = []
        for listed_row in current_rows:
            try:
                parsed = _parse_detail(listed_row, client.get(_clean(listed_row.get("detail_url"))))
                _validate_output(parsed)
            except Exception:
                meta["detail_errors"] += 1
                raise
            detailed.append(parsed)
            meta["detail_pages"] += 1
            meta["pages"] += 1
            meta["comment_boundaries_discarded"] += 1

        meta["details_complete"] = bool(
            meta["detail_attempts"] == meta["detail_pages"] == len(current_rows) and meta["detail_errors"] == 0
        )
        meta["application_controls_complete"] = bool(
            meta["details_complete"]
            and all(row["raw_fields"].get("application_control_verified") is True for row in detailed)
        )
        raw_urls = [_clean(row.get("raw_url")) for row in detailed]
        raw_duplicates = len(raw_urls) - len(set(raw_urls))
        meta["raw_url_duplicate_count"] = raw_duplicates
        if raw_duplicates:
            raise YeongamContractError(f"{raw_duplicates} duplicate current detail URLs")

        deduper = dedupe_rows or _dedupe_default
        result = list(deduper(detailed))
        expected_ids = [_clean(row.get("provider_course_id")) for row in detailed]
        if [_clean(row.get("provider_course_id")) for row in result] != expected_ids:
            raise YeongamContractError("dedupe changed official identity/order cardinality")

        snapshot_complete = bool(
            meta["pagination_complete"] and meta["details_complete"] and meta["application_controls_complete"]
        )
        if not snapshot_complete:
            raise YeongamContractError("complete snapshot contract was not satisfied")
        contracts = Counter(_clean(row["raw_fields"].get("application_control_contract")) for row in detailed)
        meta.update(
            {
                "snapshot_complete": True,
                "full_snapshot_validated": True,
                "returned_count": len(result),
                "no_current_data": not current_rows,
                "no_current_reason": (
                    "the complete official Yeongam library catalogue has no current/future courses"
                    if not current_rows
                    else ""
                ),
                "source_status_counts": dict(Counter(_clean(row.get("source_status")) for row in listed)),
                "current_status_counts": dict(Counter(_clean(row.get("status")) for row in result)),
                "current_branch_counts": dict(Counter(_clean(row.get("branch")) for row in result)),
                "current_detail_ids": expected_ids,
                "identity_bound_application_control_count": sum(bool(row.get("application_url")) for row in result),
                "identityless_auth_gate_excluded_count": contracts.get(
                    "identityless_auth_gate_excluded_no_course_application_url", 0
                ),
                "full_capacity_control_suppressed_count": contracts.get("full_capacity_control_suppressed", 0),
                "scheduled_control_not_visible_count": contracts.get("scheduled_control_not_yet_visible", 0),
                "semantic_duplicate_policy": "preserve_distinct_official_show_identity",
                "municipality_coverage": [YEONGAM_MUNICIPALITY_CODE],
                "owner_boundary_audit": {key: dict(value) for key, value in YEONGAM_OWNER_BOUNDARY_AUDIT.items()},
                "discovery_audit": dict(YEONGAM_DISCOVERY_AUDIT),
                "pii_fields_never_read": list(YEONGAM_PII_FIELDS_NEVER_READ),
                "pii_payload_persisted": False,
            }
        )
        update_network_meta()
        return result, YEONGAM_PARSER, meta
    except Exception as exc:
        update_network_meta()
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)[:500]}"
        meta["returned_count"] = 0
        return [], YEONGAM_PARSER, meta
    finally:
        client.close()


collect = collect_yeongam_education
is_target = is_yeongam_education_target


__all__ = [
    "YEONGAM_CANONICAL_URL",
    "YEONGAM_CANDIDATE_ID",
    "YEONGAM_DISCOVERY_AUDIT",
    "YEONGAM_EDUCITY_URL",
    "YEONGAM_FETCH_ATTEMPTS",
    "YEONGAM_JNTLE_CANDIDATE_ID",
    "YEONGAM_JNTLE_PROVIDER",
    "YEONGAM_JNTLE_URL",
    "YEONGAM_MUNICIPALITY_CODE",
    "YEONGAM_MUNICIPALITY_NAME",
    "YEONGAM_OWNER_BOUNDARY_AUDIT",
    "YEONGAM_OWNERSHIP_SCOPE",
    "YEONGAM_PAGE_SIZE",
    "YEONGAM_PARSER",
    "YEONGAM_PII_FIELDS_NEVER_READ",
    "YEONGAM_PROVIDER",
    "YEONGAM_REGISTERED_DETAIL_URL",
    "YEONGAM_SECTIGO_INTERMEDIATE_SHA256",
    "YeongamContractError",
    "build_yeongam_tls_context",
    "collect",
    "collect_yeongam_education",
    "is_target",
    "is_yeongam_candidate_alias_target",
    "is_yeongam_education_target",
    "is_yeongam_jntle_separate_owner_target",
    "yeongam_detail_url",
    "yeongam_list_url",
]
