"""Fail-closed collectors for Hongcheon-gun's public education owners.

Hongcheon-gun's configured integrated-education owner
``MUNI_WWW_HONGCHEON_GO_KR_F5083BE8`` publishes one 1,067-row ledger.  Its UI
defaults to ten rows, so the collector forces ``pageUnit=100``, validates the
declared total, scans every data page plus a proven-empty sentinel, repeats the
complete scan for stability, and verifies every current/future public detail.

The current county-library service is a separate structured owner.  Its
official intro page advertises six partitions: Yeonbong, Seoseok, Nammyeon,
Naemyeon, Byeolbit Naru and Hongcheon Children's Library.  Each partition
publishes its complete programme catalogue on one page.  The collector
verifies the official directory, every catalogue, a numeric empty-filter
sentinel for every partition, stable catalogue rechecks, and every current or
future public detail before returning an atomic snapshot.

Only public intro, list and detail GET routes are requested.  Application,
applicant-history, identity, login, cancellation and attachment routes are
forbidden.  Identity-bound application URLs may be retained from public
controls, but are never called.  Instructor names, staff contacts, free-form
notices, materials and attachments are structurally skipped before their
values are read.

``hongcheonlib.go.kr`` currently omits its public Sectigo intermediate.  The
embedded intermediate is fingerprint checked and added to the default trust
context; root-chain and hostname verification remain enabled.  Managed
``SafeSession`` instances retain DNS pinning through a dedicated pinned
adapter.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
import hashlib
import html
import json
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

from utils.outbound_http import OutboundRequestBlocked, SafeSession, _PinnedHTTPAdapter


HONGCHEON_MUNICIPALITY_CODE = "5172000000"
HONGCHEON_MUNICIPALITY_NAME = "강원특별자치도 홍천군"

HONGCHEON_LIBRARY_PROVIDER = "MUNI_HONGCHEONLIB_GO_KR_17726A2C"
HONGCHEON_LIBRARY_CANDIDATE_ID = "MUNI_IR_6DAF3DB95540"
HONGCHEON_LIBRARY_HOST = "hongcheonlib.go.kr"
HONGCHEON_LIBRARY_URL = "https://hongcheonlib.go.kr/main/index.do"
HONGCHEON_LIBRARY_PARSER = (
    "hongcheon_library_official_six_partition_directory+single_page_catalogues+"
    "numeric_empty_sentinels+stable_rechecks+all_current_safe_details+"
    "identity_bound_application_controls_not_called+pii_structural_skip"
)

HONGCHEON_EXISTING_COURSE_PROVIDER = "MUNI_WWW_HONGCHEON_GO_KR_F5083BE8"
HONGCHEON_EXISTING_COURSE_CANDIDATE_ID = "MUNI_IR_EBF329238984"
HONGCHEON_EXISTING_COURSE_URL = (
    "https://www.hongcheon.go.kr/edu/selectCourseWebList.do?"
    "key=1196&srcEdu=&srcCategory=&srcStatus=&srcTitle="
)
HONGCHEON_EXISTING_COURSE_HOST = "www.hongcheon.go.kr"
HONGCHEON_EXISTING_COURSE_PARSER = (
    "hongcheon_integrated_education_pageunit100+declared_total+all_pages+"
    "empty_sentinel+stable_full_recheck+all_current_safe_details+"
    "application_controls_not_called+pii_structural_skip"
)
HONGCHEON_EXISTING_COURSE_PAGE_SIZE = 100
HONGCHEON_EXISTING_COURSE_RECOMMENDED_MAX_PAGES = 30
HONGCHEON_EXISTING_COURSE_RECOMMENDED_DETAIL_LIMIT = 200
HONGCHEON_GENERIC_HOME_PROVIDER = "MUNI_WWW_HONGCHEON_GO_KR_910402B0"
HONGCHEON_GENERIC_HOME_URL = "https://www.hongcheon.go.kr/"
HONGCHEON_YOUTH_RECRUITMENT_URL = "https://hcyc.kr/board/index.html?id=program"
HONGCHEON_CULTURAL_FOUNDATION_URL = (
    "https://www.hccf.or.kr/Home/H20000/H20200/cultureList?cult_type=E"
)
HONGCHEON_ARBORETUM_EXPERIENCE_URL = (
    "https://www.hongcheon.go.kr/mugunghwa/bbs/board.php?bo_table=sub02_2"
)

HONGCHEON_EMPTY_FILTER = "999999"
HONGCHEON_DEFAULT_DETAIL_LIMIT = 200
# Covers two directory checks, all 18 catalogue/sentinel requests, every
# configured detail slot, and bounded retry headroom.
HONGCHEON_DEFAULT_MAX_REQUESTS = 250
HONGCHEON_MAX_HTML_BYTES = 4_000_000
HONGCHEON_FETCH_ATTEMPTS = 3


@dataclass(frozen=True)
class HongcheonLibraryBranch:
    site: str
    homepage_id: str
    name: str
    page_title: str
    code: str
    address: str


HONGCHEON_LIBRARY_BRANCHES: tuple[HongcheonLibraryBranch, ...] = (
    HongcheonLibraryBranch(
        "yblib",
        "h2",
        "연봉도서관",
        "연봉도서관",
        "HC_LIB_YEONBONG",
        "강원특별자치도 홍천군 홍천읍 연봉중앙로 11-10",
    ),
    HongcheonLibraryBranch(
        "sslib",
        "h3",
        "서석도서관",
        "서석도서관",
        "HC_LIB_SEOSEOK",
        "강원특별자치도 홍천군 서석면 풍암길 7",
    ),
    HongcheonLibraryBranch(
        "nammyeon",
        "h4",
        "남면도서관",
        "남면도서관",
        "HC_LIB_NAMMYEON",
        "강원특별자치도 홍천군 남면 명덕길 28",
    ),
    HongcheonLibraryBranch(
        "naemyeon",
        "h5",
        "내면도서관",
        "내면도서관",
        "HC_LIB_NAEMYEON",
        "강원특별자치도 홍천군 내면 창촌로 57-11",
    ),
    HongcheonLibraryBranch(
        "naru",
        "h6",
        "별빛나루도서관",
        "별빛나루도서관",
        "HC_LIB_NARU",
        "강원특별자치도 홍천군 홍천읍 갈마로9길 13",
    ),
    HongcheonLibraryBranch(
        "children",
        "h7",
        "홍천어린이도서관",
        "홍천어린이도서관",
        "HC_LIB_CHILDREN",
        "강원특별자치도 홍천군 홍천읍 열산골길 10",
    ),
)

# One complete snapshot consumes, for every official branch, the catalogue,
# its proven-empty sentinel, and a catalogue stability recheck.  Keep two
# logical-page slots of headroom in the default scheduler bound.
HONGCHEON_REQUIRED_LIST_REQUESTS = len(HONGCHEON_LIBRARY_BRANCHES) * 3
HONGCHEON_DEFAULT_MAX_PAGES = HONGCHEON_REQUIRED_LIST_REQUESTS + 2

_BRANCH_BY_SITE: Mapping[str, HongcheonLibraryBranch] = {
    branch.site: branch for branch in HONGCHEON_LIBRARY_BRANCHES
}

HONGCHEON_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    HONGCHEON_LIBRARY_PROVIDER: {
        "decision": "canonical_current_county_library_six_partition_owner",
        "candidate_id": HONGCHEON_LIBRARY_CANDIDATE_ID,
        "url": HONGCHEON_LIBRARY_URL,
        "operator": "홍천군립도서관",
        "branches": tuple(branch.name for branch in HONGCHEON_LIBRARY_BRANCHES),
    },
    HONGCHEON_EXISTING_COURSE_PROVIDER: {
        "decision": "repair_existing_owner_complete_pagination",
        "candidate_id": HONGCHEON_EXISTING_COURSE_CANDIDATE_ID,
        "url": HONGCHEON_EXISTING_COURSE_URL,
        "reason": (
            "existing broad municipal education owner; retain its ownership and "
            "replace the ten-row generic sample with complete pageUnit=100 pagination"
        ),
    },
    HONGCHEON_GENERIC_HOME_PROVIDER: {
        "decision": "exclude_generic_navigation_shell_false_positive",
        "url": HONGCHEON_GENERIC_HOME_URL,
        "reason": "homepage cards are not a complete application ledger",
    },
    "OFFICIAL_HONGCHEON_YOUTH_RECRUITMENT_BOARD": {
        "decision": "exclude_mixed_notice_board_without_structured_course_boundary",
        "candidate_id": "MUNI_IR_53302DBB76C6",
        "url": HONGCHEON_YOUTH_RECRUITMENT_URL,
        "reason": (
            "six-page recruitment board; dates and application methods are free-form, "
            "image, attachment, email or third-party-form content"
        ),
    },
    "OFFICIAL_HONGCHEON_CULTURAL_FOUNDATION": {
        "decision": "keep_separate_performance_and_event_owner_outside_course_scope",
        "candidate_id": "MUNI_IR_1D011C7D035D",
        "url": HONGCHEON_CULTURAL_FOUNDATION_URL,
    },
    "OFFICIAL_HONGCHEON_MUGUNGHWA_ARBORETUM": {
        "decision": "exclude_static_recurring_experience_information_without_record_identity",
        "candidate_id": "MUNI_IR_4E851ACEAF00",
        "url": HONGCHEON_ARBORETUM_EXPERIENCE_URL,
        "reason": "telephone-only recurring information plus 2019 activity-photo posts",
    },
}

HONGCHEON_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-23",
    "canonical_url": HONGCHEON_LIBRARY_URL,
    "source_rows": 303,
    "current_or_future_rows": 15,
    "branch_source_counts": {
        "연봉도서관": 78,
        "서석도서관": 49,
        "남면도서관": 63,
        "내면도서관": 45,
        "별빛나루도서관": 45,
        "홍천어린이도서관": 23,
    },
    "branch_current_counts": {
        "연봉도서관": 2,
        "서석도서관": 3,
        "남면도서관": 3,
        "내면도서관": 1,
        "별빛나루도서관": 2,
        "홍천어린이도서관": 4,
    },
    "source_status_counts": {
        "수강종료": 288,
        "접수마감": 5,
        "수강신청": 6,
        "대기자신청": 4,
    },
    "current_detail_pages_verified": 15,
    "identity_bound_application_controls": 10,
    "application_endpoints_called": 0,
    "single_page_catalogues": 6,
    "empty_sentinels": 6,
    "sentinel_filter": f"searchCate1={HONGCHEON_EMPTY_FILTER}",
    "catalogue_snapshot_sha256": {
        "naru": "1d6b68a0cc9cc59ad290e26d8a4b2bec30650ee17ed5ba8106ad3f1bd09ccb87",
        "yblib": "1ddd79440a09b380dace28625059974f99aaa7ba0434bbd7cd3aac76b410b676",
        "sslib": "4a1f34d5762aa209614af85cf4734315ba8e70a4e68981b64e667c5fbd12abfa",
        "nammyeon": "ad084843d43b6d53822c6f13121ba6ea0cd65b82de81fa4cfbda1cc77eb9ece8",
        "naemyeon": "aaaf9cb48e1f15ee28d0e87799d1fb0c4761d6ade9fa9a18f250bb3d332eb067",
        "children": "50c19456dcfaeaf458b4fea6ff38ffeb009e8b728bd6e81ba27890ed3b279fd6",
    },
    "existing_owner_audit": {
        "checked_on": "2026-07-23",
        "source_rows": 1067,
        "current_or_future_rows": 49,
        "returned_rows": 49,
        "owner_branch_count": 1,
        "data_pages_at_page_unit_100": 11,
        "empty_sentinel_page": 12,
        "required_list_requests": 24,
        "configured_owner": HONGCHEON_EXISTING_COURSE_PROVIDER,
        "configured_last_quality_rows": 10,
        "recommended_max_pages": HONGCHEON_EXISTING_COURSE_RECOMMENDED_MAX_PAGES,
        "recommended_detail_limit": (
            HONGCHEON_EXISTING_COURSE_RECOMMENDED_DETAIL_LIMIT
        ),
        "two_run_live_equal": True,
        "source_catalogue_sha256": (
            "256dd01a53660f3b517103959b143cdc9a529fbdb24152b58be6ed61f054cbe2"
        ),
        "output_sha256": (
            "ee12971a97510fbcc2ff61dfc767518c131c637f688cecef49897bc183179de4"
        ),
    },
    "legacy_library_partitions": {
        "연봉도서관": {"rows": 168, "latest_end": "2024-06-12"},
        "서석도서관": {"rows": 77, "latest_end": "2024-05-22"},
        "남면도서관": {"rows": 83, "latest_end": "2024-06-05"},
        "내면도서관": {"rows": 11, "latest_end": "2024-06-05"},
    },
    "conclusion": (
        "collect the current six-branch county-library platform as one separate "
        "owner; retain the broad lifelong portal as its existing owner"
    ),
}


class HongcheonContractError(ValueError):
    """Raised when the audited Hongcheon source contract changes."""


class _TransientFetchError(RuntimeError):
    """Retryable response/transport failure whose body is never retained."""


SessionFactory = Callable[[], Any]
Sleeper = Callable[[float], None]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_POSITIVE_ID_RE = re.compile(r"[1-9]\d*")
_ZERO_OR_POSITIVE_ID_RE = re.compile(r"\d+")
_DATE_RE = re.compile(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)")
_COUNT_RE = re.compile(r"(온라인접수|오프라인접수)\s*(\d+)\s*/\s*(\d+)")
_WAIT_COUNT_RE = re.compile(r"\(후보자\s*(\d+)\s*/\s*(\d+)\)")
_DETAIL_COUNT_RE = re.compile(r"^(\d+)\s*명?\s*/\s*(\d+)\s*명?$")
_F508_DOTTED_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})[.]\s*(\d{1,2})[.]\s*(\d{1,2})(?!\d)"
)
_F508_KOREAN_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})년\s*(\d{1,2})월\s*(\d{1,2})일(?!\d)"
)
_F508_LIST_CAPTION = (
    "강좌소개 게시판 - 번호, 분야 ,강좌명, 대상, 교육기간, 접수인원/정원 ,상태 "
    "순으로 내용을 제공하고 있습니다."
)
_F508_LIST_HEADERS = (
    "번호",
    "분야",
    "강좌명",
    "대상",
    "교육기간",
    "접수인원/정원",
    "상태",
)
_F508_SOURCE_STATUS_MAP: Mapping[str, str] = {
    "온라인 접수중": "OPEN",
    "온라인 접수마감": "CLOSED",
    "온라인 방문접수 접수마감": "CLOSED",
    "방문접수 접수마감": "CLOSED",
}
_F508_DETAIL_LABELS = (
    "강좌명",
    "분야",
    "교육대상",
    "교육장소",
    "모집인원",
    "접수기간",
    "교육기간",
    "교육시간",
    "강사명",
    "수강료",
    "재료비",
    "교육내용",
    "문의전화",
    "첨부파일",
)
_F508_DETAIL_SAFE_FIELDS = frozenset(
    {
        "강좌명",
        "분야",
        "교육대상",
        "교육장소",
        "모집인원",
        "접수기간",
        "교육기간",
        "교육시간",
        "수강료",
    }
)
_F508_DETAIL_SKIPPED_FIELDS = frozenset(
    {"강사명", "재료비", "교육내용", "문의전화", "첨부파일"}
)
_F508_PII_DETAIL_FIELDS_NEVER_READ = tuple(sorted(_F508_DETAIL_SKIPPED_FIELDS))
_F508_TARGET_MARKERS = (
    "성인",
    "학생",
    "아동",
    "청소년",
    "군민",
    "주민",
    "누구나",
    "유아",
    "초등",
    "중등",
    "고등",
    "가족",
    "부모",
    "여성",
    "남성",
    "장애",
    "노인",
    "어르신",
    "관내",
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2})[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RESIDENT_ID_RE = re.compile(r"(?<!\d)\d{6}\s*[- ]\s*[1-4]\d{6}(?!\d)")

_LIST_CAPTION = "문화행사신청 목록 페이지"
_LIST_HEADERS = ("분류", "제목", "정원 및 신청현황", "행사기간", "접수기간", "접수상태")
_LIST_CELL_CLASSES = (
    frozenset({"list_cate_group", "sort"}),
    frozenset({"title"}),
    frozenset({"r_date"}),
    frozenset({"t_date"}),
    frozenset({"person"}),
    frozenset({"target"}),
    frozenset({"state"}),
)
_STATUS_MAP: Mapping[str, str] = {
    "수강신청": "OPEN",
    "대기자신청": "WAITING",
    "접수예정": "SCHEDULED",
    "접수마감": "CLOSED",
    "정원마감": "CLOSED",
    "수강종료": "CLOSED",
}
_ACTIVE_SOURCE_STATUSES = frozenset({"수강신청", "대기자신청"})
_DETAIL_ALLOWED_FIELDS = frozenset(
    {"접수기간", "강의대상", "강의기간", "강의시간", "강의장소"}
)
_DETAIL_SKIPPED_FIELDS = frozenset({"강사명", "강의계획서", "준비물/재료비"})
_PII_DETAIL_FIELDS_NEVER_READ = (
    "강사명 value",
    "강의계획서 value/link",
    "준비물/재료비 value",
    "안내사항 free text",
    "강좌(행사) 담당자 value",
    "application/applicant payload",
)

# Public intermediate omitted by hongcheonlib.go.kr during the 2026-07-23 audit.
# Subject: Sectigo Public Server Authentication CA DV R36
# Issuer:  Sectigo Public Server Authentication Root R46
HONGCHEON_SECTIGO_INTERMEDIATE_SHA256 = (
    "8c54c334b66ba4e426772af4a3f9136c19a1aec729fdb28c535c07a5a4ef22e0"
)
HONGCHEON_SECTIGO_INTERMEDIATE_PEM = """-----BEGIN CERTIFICATE-----
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


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(
        " ", html.unescape(str(value or "")).replace("\xa0", " ")
    ).strip()


def _text(node: Any) -> str:
    return _clean(node.get_text(" ", strip=True)) if isinstance(node, Tag) else ""


def _one(nodes: Iterable[Any], label: str) -> Any:
    values = list(nodes)
    if len(values) != 1:
        raise HongcheonContractError(f"expected one {label}, found {len(values)}")
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


def is_hongcheon_library_target(target: Any) -> bool:
    return (
        _clean(_target_value(target, "provider")) == HONGCHEON_LIBRARY_PROVIDER
        and _clean(_target_value(target, "url")) == HONGCHEON_LIBRARY_URL
    )


def is_hongcheon_existing_course_target(target: Any) -> bool:
    return (
        _clean(_target_value(target, "provider"))
        == HONGCHEON_EXISTING_COURSE_PROVIDER
        and _clean(_target_value(target, "url")) == HONGCHEON_EXISTING_COURSE_URL
    )


def is_hongcheon_education_target(target: Any) -> bool:
    return is_hongcheon_library_target(target) or is_hongcheon_existing_course_target(
        target
    )


def hongcheon_existing_course_list_url(page_index: Any) -> str:
    page = _clean(page_index)
    if not _POSITIVE_ID_RE.fullmatch(page):
        raise HongcheonContractError("integrated course page index must be positive")
    query = urlencode(
        (
            ("key", "1196"),
            ("pageUnit", str(HONGCHEON_EXISTING_COURSE_PAGE_SIZE)),
            ("srcStatus", ""),
            ("srcYear", ""),
            ("srcQuarter", ""),
            ("srcTitle", ""),
            ("srcCategory", ""),
            ("srcEdu", ""),
            ("pageIndex", page),
        )
    )
    return (
        f"https://{HONGCHEON_EXISTING_COURSE_HOST}/edu/"
        f"selectCourseWebList.do?{query}"
    )


def hongcheon_existing_course_detail_url(course_id: Any) -> str:
    course = _clean(course_id)
    if not _POSITIVE_ID_RE.fullmatch(course):
        raise HongcheonContractError("integrated course identity must be positive")
    query = urlencode(
        (
            ("key", "1196"),
            ("course", course),
            ("srcYear", ""),
            ("srcEdu", ""),
            ("srcCategory", ""),
            ("srcStatus", ""),
            ("srcTitle", ""),
            ("srcQuarter", ""),
        )
    )
    return (
        f"https://{HONGCHEON_EXISTING_COURSE_HOST}/edu/"
        f"courseWebView.do?{query}"
    )


def hongcheon_library_list_url(site: str, *, sentinel: bool = False) -> str:
    if site not in _BRANCH_BY_SITE:
        raise HongcheonContractError("unknown Hongcheon library site")
    pairs = [("menu_idx", "15")]
    if sentinel:
        pairs.append(("searchCate1", HONGCHEON_EMPTY_FILTER))
    return f"https://{HONGCHEON_LIBRARY_HOST}/{site}/module/teach/index.do?" + urlencode(
        pairs
    )


def hongcheon_library_detail_url(
    site: str, group_idx: Any, category_idx: Any, teach_idx: Any
) -> str:
    branch = _BRANCH_BY_SITE.get(site)
    group = _clean(group_idx)
    category = _clean(category_idx)
    teach = _clean(teach_idx)
    if branch is None:
        raise HongcheonContractError("unknown Hongcheon library site")
    if not _POSITIVE_ID_RE.fullmatch(group) or not _POSITIVE_ID_RE.fullmatch(teach):
        raise HongcheonContractError("detail group/teach identity must be positive integers")
    if not _ZERO_OR_POSITIVE_ID_RE.fullmatch(category):
        raise HongcheonContractError("detail category identity must be numeric")
    query = urlencode(
        (
            ("group_idx", group),
            ("category_idx", category),
            ("teach_idx", teach),
            ("menu_idx", "15"),
            ("large_category_idx", "0"),
            ("searchCate1", ""),
            ("homepage_id", branch.homepage_id),
        )
    )
    return (
        f"https://{HONGCHEON_LIBRARY_HOST}/{site}/module/teach/detail.do?{query}"
    )


def _query_dict(parsed: Any) -> dict[str, str]:
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if len(pairs) != len({key for key, _value in pairs}):
        raise HongcheonContractError("duplicate query fields are forbidden")
    return dict(pairs)


def _guard_url(url: str, method: str = "GET") -> str:
    parsed = urlparse(_clean(url))
    if (
        method != "GET"
        or parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port
        or parsed.fragment
    ):
        raise HongcheonContractError("refusing unaudited Hongcheon request destination")
    query = _query_dict(parsed)
    if parsed.hostname == HONGCHEON_EXISTING_COURSE_HOST:
        if parsed.path == "/edu/selectCourseWebList.do":
            expected_keys = {
                "key",
                "pageUnit",
                "srcStatus",
                "srcYear",
                "srcQuarter",
                "srcTitle",
                "srcCategory",
                "srcEdu",
                "pageIndex",
            }
            if (
                set(query) == expected_keys
                and query["key"] == "1196"
                and query["pageUnit"]
                == str(HONGCHEON_EXISTING_COURSE_PAGE_SIZE)
                and not any(
                    query[key]
                    for key in (
                        "srcStatus",
                        "srcYear",
                        "srcQuarter",
                        "srcTitle",
                        "srcCategory",
                        "srcEdu",
                    )
                )
                and _POSITIVE_ID_RE.fullmatch(query["pageIndex"])
            ):
                return "integrated_list"
            raise HongcheonContractError(
                "integrated course list query contract changed"
            )
        if parsed.path == "/edu/courseWebView.do":
            expected_keys = {
                "key",
                "course",
                "srcYear",
                "srcEdu",
                "srcCategory",
                "srcStatus",
                "srcTitle",
                "srcQuarter",
            }
            if (
                set(query) == expected_keys
                and query["key"] == "1196"
                and _POSITIVE_ID_RE.fullmatch(query["course"])
                and not any(query[key] for key in expected_keys - {"key", "course"})
            ):
                return "integrated_detail"
            raise HongcheonContractError(
                "integrated course detail query contract changed"
            )
        raise HongcheonContractError(
            "refusing integrated-course application or account route"
        )

    if parsed.hostname != HONGCHEON_LIBRARY_HOST:
        raise HongcheonContractError("refusing unaudited Hongcheon request destination")
    if parsed.path == "/main/index.do" and not query:
        return "directory"

    match = re.fullmatch(
        r"/(yblib|sslib|nammyeon|naemyeon|naru|children)/module/teach/"
        r"(index|detail)\.do",
        parsed.path,
    )
    if not match:
        raise HongcheonContractError("refusing non-public or application route")
    site, route = match.groups()
    branch = _BRANCH_BY_SITE[site]
    if route == "index":
        if query == {"menu_idx": "15"}:
            return "list"
        if query == {"menu_idx": "15", "searchCate1": HONGCHEON_EMPTY_FILTER}:
            return "sentinel"
        raise HongcheonContractError("library list query contract changed")

    expected_keys = {
        "group_idx",
        "category_idx",
        "teach_idx",
        "menu_idx",
        "large_category_idx",
        "searchCate1",
        "homepage_id",
    }
    if set(query) != expected_keys:
        raise HongcheonContractError("library detail query contract changed")
    if (
        not _POSITIVE_ID_RE.fullmatch(query["group_idx"])
        or not _POSITIVE_ID_RE.fullmatch(query["teach_idx"])
        or not _ZERO_OR_POSITIVE_ID_RE.fullmatch(query["category_idx"])
        or query["menu_idx"] != "15"
        or query["large_category_idx"] != "0"
        or query["searchCate1"]
        or query["homepage_id"] != branch.homepage_id
    ):
        raise HongcheonContractError("library detail identity contract changed")
    return "detail"


@lru_cache(maxsize=1)
def build_hongcheon_tls_context() -> ssl.SSLContext:
    der = ssl.PEM_cert_to_DER_cert(HONGCHEON_SECTIGO_INTERMEDIATE_PEM)
    if hashlib.sha256(der).hexdigest() != HONGCHEON_SECTIGO_INTERMEDIATE_SHA256:
        raise RuntimeError("embedded Sectigo intermediate fingerprint changed")
    context = ssl.create_default_context(cafile=certifi.where())
    context.load_verify_locations(cadata=HONGCHEON_SECTIGO_INTERMEDIATE_PEM)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise RuntimeError("verified TLS defaults unexpectedly unavailable")
    return context


class _HongcheonTLSAdapter(HTTPAdapter):
    def __init__(self, context: ssl.SSLContext, *args: Any, **kwargs: Any) -> None:
        self.ssl_context = context
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        kwargs["ssl_context"] = self.ssl_context
        super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, proxy: str, **proxy_kwargs: Any) -> Any:
        proxy_kwargs["ssl_context"] = self.ssl_context
        return super().proxy_manager_for(proxy, **proxy_kwargs)


class _HongcheonPinnedAdapter(_PinnedHTTPAdapter):
    """Complete the chain while retaining SafeSession DNS/address pinning."""

    def get_connection_with_tls_context(
        self,
        request: requests.PreparedRequest,
        verify: Any,
        proxies: Optional[dict[str, str]] = None,
        cert: Any = None,
    ) -> Any:
        if proxies and any(proxies.values()):
            raise OutboundRequestBlocked("Outbound HTTP proxies are not permitted")
        selected = getattr(request, "_mooncen_selected_address", "")
        original = getattr(request, "_mooncen_original_hostname", "")
        if not selected or original != HONGCHEON_LIBRARY_HOST:
            raise OutboundRequestBlocked("Hongcheon library destination was not validated")
        host_params, pool_kwargs = self.build_connection_pool_key_attributes(
            request, verify, cert
        )
        host_params["host"] = selected
        pool_kwargs["assert_hostname"] = original
        pool_kwargs["server_hostname"] = original
        pool_kwargs["ssl_context"] = build_hongcheon_tls_context()
        return self.poolmanager.connection_from_host(
            **host_params, pool_kwargs=pool_kwargs
        )


def _prepare_session(session: Any) -> Any:
    if isinstance(session, SafeSession):
        session.mount(
            f"https://{HONGCHEON_LIBRARY_HOST}/",
            _HongcheonPinnedAdapter(max_retries=0),
        )
    elif isinstance(session, requests.Session):
        session.trust_env = False
        session.mount(
            f"https://{HONGCHEON_LIBRARY_HOST}/",
            _HongcheonTLSAdapter(build_hongcheon_tls_context(), max_retries=0),
        )
    headers = getattr(session, "headers", None)
    if isinstance(headers, dict) or hasattr(headers, "update"):
        headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/140 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ko-KR,ko;q=0.9",
            }
        )
    return session


def _raw_session_factory() -> requests.Session:
    return _prepare_session(requests.Session())


def _response_html(response: Any, requested_url: str) -> str:
    status = int(getattr(response, "status_code", 200))
    if status != 200:
        raise _TransientFetchError(f"unexpected HTTP {status}")
    if getattr(response, "history", []):
        raise _TransientFetchError("redirected responses are forbidden")
    final_url = _clean(getattr(response, "url", ""))
    if final_url and final_url != requested_url:
        raise _TransientFetchError("response URL changed")
    headers = getattr(response, "headers", None)
    if headers:
        content_type = _clean(headers.get("Content-Type")).casefold()
        if content_type and "html" not in content_type:
            raise _TransientFetchError("response is not HTML")
    content = getattr(response, "content", None)
    if content is None:
        text = getattr(response, "text", "")
        content = str(text).encode("utf-8")
    if isinstance(content, str):
        content = content.encode("utf-8")
    payload = bytes(content)
    if not payload or len(payload) > HONGCHEON_MAX_HTML_BYTES:
        raise _TransientFetchError("empty or oversized HTML response")
    lowered = payload[:65_536].lower()
    if any(
        marker in lowered
        for marker in (b"access denied", b"request blocked", b"captcha", b"cf-chl-")
    ):
        raise _TransientFetchError("access-block page returned")
    try:
        return payload.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise HongcheonContractError("Hongcheon library response is no longer UTF-8") from exc


def _close_quietly(value: Any) -> None:
    try:
        if value is not None and hasattr(value, "close"):
            value.close()
    except Exception:
        pass


class _Client:
    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        timeout: int,
        max_requests: int,
        attempts: int,
        sleeper: Sleeper,
    ) -> None:
        self.session_factory = session_factory
        self.timeout = timeout
        self.max_requests = max_requests
        self.attempts = attempts
        self.sleeper = sleeper
        self.session: Any = None
        self.http_attempts = 0
        self.retry_count = 0
        self.sessions_created = 0
        self.route_counts: Counter[str] = Counter()

    def _session(self) -> Any:
        if self.session is None:
            self.session = _prepare_session(self.session_factory())
            self.sessions_created += 1
        return self.session

    def get_soup(self, url: str) -> BeautifulSoup:
        route = _guard_url(url, "GET")
        last_error = "unknown"
        for attempt in range(1, self.attempts + 1):
            if self.http_attempts >= self.max_requests:
                raise HongcheonContractError("request budget exhausted")
            try:
                self.http_attempts += 1
                response = self._session().get(
                    url, timeout=self.timeout, allow_redirects=False
                )
                text = _response_html(response, url)
                self.route_counts[route] += 1
                return BeautifulSoup(text, "html.parser")
            except HongcheonContractError:
                raise
            except (
                _TransientFetchError,
                requests.RequestException,
                ssl.SSLError,
                ConnectionError,
                TimeoutError,
                OSError,
            ) as exc:
                last_error = type(exc).__name__
                _close_quietly(self.session)
                self.session = None
                if attempt >= self.attempts:
                    break
                self.retry_count += 1
                self.sleeper(min(2.0, 0.35 * attempt))
        raise HongcheonContractError(
            f"public fetch failed after {self.attempts} attempts ({last_error})"
        )

    def close(self) -> None:
        _close_quietly(self.session)
        self.session = None


def _directory_signature(soup: BeautifulSoup) -> tuple[tuple[str, str], ...]:
    title = _one(soup.select("title"), "library intro title")
    if _text(title) != "홍천군립도서관":
        raise HongcheonContractError("official library intro title changed")
    wrapper = _one(soup.select("div.libarary_btn_wrap"), "official branch directory")
    links = wrapper.find_all("a", href=True, recursive=False)
    actual = tuple((_text(node), _clean(node.get("href"))) for node in links)
    expected = tuple(
        (
            branch.name.replace("홍천어린이도서관", "어린이도서관").replace(
                "도서관", " 도서관"
            ),
            f"/{branch.site}/index.do",
        )
        for branch in HONGCHEON_LIBRARY_BRANCHES
    )
    if actual != expected:
        raise HongcheonContractError("official six-branch directory changed")
    return actual


def _date_range(value: str, label: str) -> tuple[str, str]:
    values = _DATE_RE.findall(_clean(value))
    if len(values) not in {1, 2}:
        raise HongcheonContractError(f"{label} must contain one or two ISO dates")
    start = date.fromisoformat(values[0])
    end = date.fromisoformat(values[-1])
    if end < start:
        raise HongcheonContractError(f"{label} end precedes start")
    return start.isoformat(), end.isoformat()


def _capacity(value: str, identity: str) -> tuple[int, int, int, int]:
    text = _clean(value)
    matches = _COUNT_RE.findall(text)
    labels = [label for label, _current, _total in matches]
    if not matches or labels.count("온라인접수") != 1 or len(labels) != len(set(labels)):
        raise HongcheonContractError(f"course {identity}: capacity contract changed")
    current = sum(int(item) for _label, item, _total in matches)
    total = sum(int(item) for _label, _item, item in matches)
    wait_matches = _WAIT_COUNT_RE.findall(text)
    if len(wait_matches) > 1:
        raise HongcheonContractError(f"course {identity}: wait capacity is ambiguous")
    wait_current, wait_total = (
        (int(wait_matches[0][0]), int(wait_matches[0][1]))
        if wait_matches
        else (0, 0)
    )
    if min(current, total, wait_current, wait_total) < 0:
        raise HongcheonContractError(f"course {identity}: capacity is invalid")
    return current, total, wait_current, wait_total


def _application_url(
    branch: HongcheonLibraryBranch,
    control: Tag,
    *,
    group_idx: str,
    category_idx: str,
    teach_idx: str,
) -> str:
    expected = {
        "keyvalue1": branch.homepage_id,
        "keyvalue2": group_idx,
        "keyvalue3": category_idx,
        "keyvalue4": teach_idx,
    }
    if any(_clean(control.get(key)) != value for key, value in expected.items()):
        raise HongcheonContractError(
            f"course {branch.site}:{teach_idx}: application identity mismatch"
        )
    large_category = _clean(control.get("keyvalue5"))
    apply_status = _clean(control.get("apply_status"))
    if (
        _clean(control.get("href"))
        or not _POSITIVE_ID_RE.fullmatch(large_category)
        or apply_status not in {"1", "2"}
    ):
        raise HongcheonContractError(
            f"course {branch.site}:{teach_idx}: application control changed"
        )
    query = urlencode(
        (
            ("editMode", "ADD"),
            ("homepage_id", branch.homepage_id),
            ("group_idx", group_idx),
            ("category_idx", category_idx),
            ("teach_idx", teach_idx),
            ("large_category_idx", large_category),
            ("apply_status", apply_status),
            ("menu_idx", "15"),
        )
    )
    # This URL is deliberately outside _guard_url's public read-only allowlist.
    return (
        f"https://{HONGCHEON_LIBRARY_HOST}/{branch.site}/module/teach/"
        f"student/edit.do?{query}"
    )


def _parse_catalogue(
    soup: BeautifulSoup,
    branch: HongcheonLibraryBranch,
    *,
    sentinel: bool,
) -> list[dict[str, Any]]:
    title = _one(soup.select("title"), f"{branch.site} title")
    if _text(title) != f"{branch.page_title} > 문화공간 > 프로그램":
        raise HongcheonContractError(f"{branch.site}: catalogue title changed")
    table = _one(soup.select("table.culture_table"), f"{branch.site} programme table")
    caption = _one(table.select("caption"), f"{branch.site} table caption")
    if _text(caption) != _LIST_CAPTION:
        raise HongcheonContractError(f"{branch.site}: table caption changed")
    headers = tuple(_text(node) for node in table.select("thead th"))
    if headers != _LIST_HEADERS:
        raise HongcheonContractError(f"{branch.site}: table headers changed")
    body = _one(table.select("tbody"), f"{branch.site} table body")
    tr_nodes = body.find_all("tr", recursive=False)
    if sentinel:
        if tr_nodes or table.select("a.teach_title, a.add"):
            raise HongcheonContractError(f"{branch.site}: empty sentinel contains records")
        return []
    if not tr_nodes:
        raise HongcheonContractError(f"{branch.site}: catalogue unexpectedly empty")

    rows: list[dict[str, Any]] = []
    identities: set[tuple[str, str, str]] = set()
    for tr in tr_nodes:
        cells = tr.find_all("td", recursive=False)
        if len(cells) != len(_LIST_CELL_CLASSES):
            raise HongcheonContractError(f"{branch.site}: programme row width changed")
        classes = tuple(frozenset(cell.get("class") or ()) for cell in cells)
        if classes != _LIST_CELL_CLASSES:
            raise HongcheonContractError(f"{branch.site}: programme cell contract changed")
        anchor = _one(cells[1].select("a.detail-btn.teach_title"), "detail control")
        group_idx = _clean(anchor.get("keyvalue1"))
        category_idx = _clean(anchor.get("keyvalue2"))
        teach_idx = _clean(anchor.get("keyvalue3"))
        identity = (group_idx, category_idx, teach_idx)
        if (
            not _POSITIVE_ID_RE.fullmatch(group_idx)
            or not _ZERO_OR_POSITIVE_ID_RE.fullmatch(category_idx)
            or not _POSITIVE_ID_RE.fullmatch(teach_idx)
            or identity in identities
            or _clean(anchor.get("href")) != "#"
            or _clean(anchor.get("title")) != "강좌 상세정보 보기"
        ):
            raise HongcheonContractError(f"{branch.site}: invalid or duplicate course identity")
        identities.add(identity)
        title_text = _text(anchor)
        category_name = _text(cells[0])
        apply_period = _text(cells[2])
        event_period = _text(cells[3])
        capacity_text = _text(cells[4])
        target = _text(cells[5])
        source_status = _text(cells[6])
        if not title_text or not category_name or source_status not in _STATUS_MAP:
            raise HongcheonContractError(
                f"course {branch.site}:{teach_idx}: required list fields changed"
            )
        apply_start, apply_end = _date_range(
            apply_period, f"course {branch.site}:{teach_idx} application period"
        )
        start, end = _date_range(
            event_period, f"course {branch.site}:{teach_idx} event period"
        )
        capacity_current, capacity_total, wait_current, wait_total = _capacity(
            capacity_text, f"{branch.site}:{teach_idx}"
        )
        controls = cells[6].select("a.add.reg")
        active = source_status in _ACTIVE_SOURCE_STATUSES
        if active and len(controls) != 1:
            raise HongcheonContractError(
                f"course {branch.site}:{teach_idx}: active application control missing"
            )
        if not active and controls:
            raise HongcheonContractError(
                f"course {branch.site}:{teach_idx}: inactive row gained application control"
            )
        application_url = (
            _application_url(
                branch,
                controls[0],
                group_idx=group_idx,
                category_idx=category_idx,
                teach_idx=teach_idx,
            )
            if controls
            else ""
        )
        detail_url = hongcheon_library_detail_url(
            branch.site, group_idx, category_idx, teach_idx
        )
        rows.append(
            {
                "provider": HONGCHEON_LIBRARY_PROVIDER,
                "provider_course_id": (
                    f"{HONGCHEON_LIBRARY_PROVIDER}:{branch.site}:"
                    f"{group_idx}:{category_idx}:{teach_idx}"
                ),
                "prefer_incoming_provider_course_id": True,
                "title": title_text,
                "description": title_text,
                "branch": branch.name,
                "branch_id": branch.code,
                "branch_code": branch.code,
                "preserve_branch": True,
                "category": "교육",
                "program_type": "교육",
                "raw_url": detail_url,
                "application_url": application_url,
                "application_type": "online" if application_url else "",
                "application_method_raw": "온라인" if application_url else "",
                "reservation_available": active,
                "status": _STATUS_MAP[source_status],
                "period": event_period,
                "start_date": start,
                "end_date": end,
                "apply_period": apply_period,
                "apply_start_date": apply_start,
                "apply_end_date": apply_end,
                "schedule_raw": event_period,
                "target": target,
                "capacity_current": capacity_current,
                "capacity_total": capacity_total,
                "venue_address": branch.address,
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "operator_type": "지자체/공공기관",
                "source_group": "municipal_reservation",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "classification_locked": True,
                "collection_type": "static_html+detail_html",
                "municipality_code": HONGCHEON_MUNICIPALITY_CODE,
                "municipality_name": HONGCHEON_MUNICIPALITY_NAME,
                "raw_fields": {
                    "parser": HONGCHEON_LIBRARY_PARSER,
                    "source_identity": f"{branch.site}:{group_idx}:{category_idx}:{teach_idx}",
                    "source_site": branch.site,
                    "source_homepage_id": branch.homepage_id,
                    "source_group_idx": group_idx,
                    "source_category_idx": category_idx,
                    "source_teach_idx": teach_idx,
                    "source_category": category_name,
                    "source_status": source_status,
                    "source_application_period": apply_period,
                    "source_event_period": event_period,
                    "source_wait_current": wait_current,
                    "source_wait_total": wait_total,
                    "application_control_present": bool(application_url),
                    "application_endpoint_called": False,
                    "detail_verified": False,
                    "pii_detail_fields_skipped": _PII_DETAIL_FIELDS_NEVER_READ,
                },
                "_site": branch.site,
                "_homepage_id": branch.homepage_id,
                "_group_idx": group_idx,
                "_category_idx": category_idx,
                "_teach_idx": teach_idx,
                "_source_status": source_status,
                "_wait_current": wait_current,
                "_wait_total": wait_total,
            }
        )
    return rows


def _rows_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            row.get("provider_course_id"),
            row.get("title"),
            row.get("start_date"),
            row.get("end_date"),
            row.get("apply_start_date"),
            row.get("apply_end_date"),
            row.get("status"),
            row.get("capacity_current"),
            row.get("capacity_total"),
            row.get("target"),
            bool(row.get("application_url")),
        )
        for row in rows
    )


def _f508_date_values(value: str, label: str) -> tuple[str, ...]:
    text = _clean(value)
    located: list[tuple[int, date]] = []
    for pattern in (_F508_DOTTED_DATE_RE, _F508_KOREAN_DATE_RE):
        for match in pattern.finditer(text):
            try:
                parsed = date(*(int(part) for part in match.groups()))
            except ValueError as exc:
                raise HongcheonContractError(f"{label} contains an invalid date") from exc
            located.append((match.start(), parsed))
    located.sort(key=lambda item: item[0])
    values = tuple(item.isoformat() for _offset, item in located)
    if len(values) < 2 or len(values) % 2:
        raise HongcheonContractError(f"{label} must contain complete date ranges")
    if any(values[index] > values[index + 1] for index in range(len(values) - 1)):
        raise HongcheonContractError(f"{label} dates are not chronological")
    return values


def _f508_public_target(value: str) -> tuple[str, bool]:
    text = _clean(value)
    sensitive = bool(
        _PHONE_RE.search(text)
        or _EMAIL_RE.search(text)
        or _RESIDENT_ID_RE.search(text)
        or (
            re.fullmatch(r"[가-힣]{2,4}", text)
            and not any(marker in text for marker in _F508_TARGET_MARKERS)
        )
    )
    return ("", True) if sensitive else (text, False)


def _f508_capacity(value: str, identity: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d+)\s*/\s*(\d+)", _clean(value))
    if not match:
        raise HongcheonContractError(
            f"integrated course {identity}: list capacity changed"
        )
    return int(match.group(1)), int(match.group(2))


def _f508_total(soup: BeautifulSoup) -> int:
    title = _one(soup.select("title"), "integrated education title")
    if _text(title) != "일반교육 목록 - 교육신청 - 교육정보 - 평생학습관":
        raise HongcheonContractError("integrated education list title changed")
    count_box = _one(soup.select(".bbs_count"), "integrated education total")
    totals = [
        int(match.group(1))
        for text in (_text(node) for node in count_box.select("strong"))
        if (match := re.fullmatch(r"(\d+)\s*(?:개)?", text))
    ]
    if len(totals) != 1 or totals[0] < 1:
        raise HongcheonContractError("integrated education total changed")
    return totals[0]


def _f508_validate_pagination(
    soup: BeautifulSoup, page_index: int, *, sentinel: bool
) -> None:
    pagination = _one(
        soup.select("div.p-pagination"), "integrated education pagination"
    )
    active = pagination.select("strong.p-page__link.active")
    if sentinel:
        if active:
            raise HongcheonContractError(
                "integrated education sentinel unexpectedly became a data page"
            )
    elif len(active) != 1 or _text(active[0]) != str(page_index):
        raise HongcheonContractError(
            f"integrated education page {page_index}: active pagination changed"
        )
    for link in pagination.select("a[href]"):
        absolute = urljoin(
            f"https://{HONGCHEON_EXISTING_COURSE_HOST}/edu/", link.get("href")
        )
        if _guard_url(absolute) != "integrated_list":
            raise HongcheonContractError(
                f"integrated education page {page_index}: pagination route changed"
            )


def _f508_application_url(raw_href: Any, course_id: str) -> str:
    absolute = urljoin(
        f"https://{HONGCHEON_EXISTING_COURSE_HOST}/edu/", _clean(raw_href)
    )
    parsed = urlparse(absolute)
    if (
        parsed.scheme != "https"
        or parsed.hostname != HONGCHEON_EXISTING_COURSE_HOST
        or parsed.username
        or parsed.password
        or parsed.port
        or parsed.fragment
    ):
        raise HongcheonContractError(
            f"integrated course {course_id}: application destination changed"
        )
    path_parts = parsed.path.split(";", 1)
    if path_parts[0] != "/edu/courseWebAppRegist.do":
        raise HongcheonContractError(
            f"integrated course {course_id}: application route changed"
        )
    if len(path_parts) == 2 and not re.fullmatch(
        r"jsessionid=[A-Fa-f0-9]{16,128}", path_parts[1]
    ):
        raise HongcheonContractError(
            f"integrated course {course_id}: application session marker changed"
        )
    query = _query_dict(parsed)
    expected_keys = {
        "key",
        "course",
        "srcEdu",
        "srcYear",
        "srcQuarter",
        "srcCategory",
        "srcTitle",
        "srcStatus",
        "pageIndex",
    }
    if (
        set(query) != expected_keys
        or query["key"] != "1196"
        or query["course"] != course_id
        or (
            query["srcYear"]
            and not re.fullmatch(r"20\d{2}", query["srcYear"])
        )
        or not _POSITIVE_ID_RE.fullmatch(query["pageIndex"])
        or any(
            query[key]
            for key in expected_keys
            - {"key", "course", "srcYear", "pageIndex"}
        )
    ):
        raise HongcheonContractError(
            f"integrated course {course_id}: application identity changed"
        )
    # Strip the server session path parameter and navigation-only filters.  The
    # application endpoint remains outside the read-only request allowlist.
    return (
        f"https://{HONGCHEON_EXISTING_COURSE_HOST}/edu/"
        f"courseWebAppRegist.do?{urlencode((('key', '1196'), ('course', course_id)))}"
    )


def _parse_f508_catalogue_page(
    soup: BeautifulSoup,
    *,
    page_index: int,
    expected_total: Optional[int],
    owner_branch: str,
    sentinel: bool,
) -> tuple[int, list[dict[str, Any]]]:
    total = _f508_total(soup)
    if expected_total is not None and total != expected_total:
        raise HongcheonContractError(
            f"integrated education page {page_index}: declared total changed"
        )
    data_pages = (total + HONGCHEON_EXISTING_COURSE_PAGE_SIZE - 1) // (
        HONGCHEON_EXISTING_COURSE_PAGE_SIZE
    )
    expected_page = data_pages + 1 if sentinel else page_index
    if page_index != expected_page and sentinel:
        raise HongcheonContractError("integrated education sentinel page changed")
    if not sentinel and not 1 <= page_index <= data_pages:
        raise HongcheonContractError("integrated education data page is out of range")

    table = _one(
        soup.select("table.bbs_default.list"), "integrated education list table"
    )
    caption = _one(table.select("caption"), "integrated education list caption")
    if _text(caption) != _F508_LIST_CAPTION:
        raise HongcheonContractError("integrated education list caption changed")
    headers = tuple(_text(node) for node in table.select("thead th"))
    if headers != _F508_LIST_HEADERS:
        raise HongcheonContractError("integrated education list headers changed")
    _f508_validate_pagination(soup, page_index, sentinel=sentinel)
    body = _one(table.select("tbody"), "integrated education list body")
    tr_nodes = body.find_all("tr", recursive=False)
    if sentinel:
        if (
            len(tr_nodes) != 1
            or _text(tr_nodes[0]) != "등록된 게시물이 없습니다."
            or tr_nodes[0].select("a[href]")
        ):
            raise HongcheonContractError(
                "integrated education empty sentinel contains records"
            )
        return total, []

    expected_count = min(
        HONGCHEON_EXISTING_COURSE_PAGE_SIZE,
        total - (page_index - 1) * HONGCHEON_EXISTING_COURSE_PAGE_SIZE,
    )
    if len(tr_nodes) != expected_count:
        raise HongcheonContractError(
            f"integrated education page {page_index}: expected {expected_count} rows, "
            f"found {len(tr_nodes)}"
        )

    rows: list[dict[str, Any]] = []
    for offset, tr in enumerate(tr_nodes, start=1):
        cells = tr.find_all("td", recursive=False)
        if len(cells) != len(_F508_LIST_HEADERS):
            raise HongcheonContractError(
                f"integrated education page {page_index}: row width changed"
            )
        ordinal = (page_index - 1) * HONGCHEON_EXISTING_COURSE_PAGE_SIZE + offset
        if _text(cells[0]) != str(ordinal):
            raise HongcheonContractError(
                f"integrated education page {page_index}: row ordinal changed"
            )
        detail_control = _one(
            cells[2].select("a[href]"), f"integrated course row {ordinal} detail"
        )
        raw_detail_url = urljoin(
            f"https://{HONGCHEON_EXISTING_COURSE_HOST}/edu/",
            detail_control.get("href"),
        )
        if _guard_url(raw_detail_url) != "integrated_detail":
            raise HongcheonContractError(
                f"integrated education row {ordinal}: detail route changed"
            )
        course_id = _query_dict(urlparse(raw_detail_url))["course"]
        detail_url = hongcheon_existing_course_detail_url(course_id)
        title = _text(detail_control)
        category = _text(cells[1])
        source_target = _text(cells[3])
        target, target_redacted = _f508_public_target(source_target)
        period = _text(cells[4])
        period_dates = _f508_date_values(
            period, f"integrated course {course_id} education period"
        )
        capacity_current, capacity_total = _f508_capacity(
            _text(cells[5]), course_id
        )
        source_status = _text(cells[6])
        if not title or not category or source_status not in _F508_SOURCE_STATUS_MAP:
            raise HongcheonContractError(
                f"integrated course {course_id}: required list fields changed"
            )
        controls = cells[6].select("a[href*='courseWebAppRegist.do']")
        active = source_status == "온라인 접수중"
        if active and len(controls) != 1:
            raise HongcheonContractError(
                f"integrated course {course_id}: active application control missing"
            )
        if not active and controls:
            raise HongcheonContractError(
                f"integrated course {course_id}: closed row gained application control"
            )
        application_url = (
            _f508_application_url(controls[0].get("href"), course_id)
            if controls
            else ""
        )
        rows.append(
            {
                "provider": HONGCHEON_EXISTING_COURSE_PROVIDER,
                "provider_course_id": (
                    f"{HONGCHEON_EXISTING_COURSE_PROVIDER}:{course_id}"
                ),
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "description": title,
                "branch": owner_branch,
                "branch_id": "HC_INTEGRATED_EDUCATION",
                "branch_code": "HC_INTEGRATED_EDUCATION",
                "preserve_branch": True,
                "category": category,
                "program_type": "교육",
                "raw_url": detail_url,
                "application_url": application_url,
                "application_type": "online" if application_url else "",
                "application_method_raw": (
                    "온라인" if source_status.startswith("온라인") else "방문접수"
                ),
                "reservation_available": active,
                "status": _F508_SOURCE_STATUS_MAP[source_status],
                "period": period,
                "start_date": period_dates[0],
                "end_date": period_dates[-1],
                "schedule_raw": period,
                "target": target,
                "capacity_current": capacity_current,
                "capacity_total": capacity_total,
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "operator_type": "지자체/공공기관",
                "source_group": "municipal_reservation",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "classification_locked": True,
                "collection_type": "static_html+detail_html",
                "municipality_code": HONGCHEON_MUNICIPALITY_CODE,
                "municipality_name": HONGCHEON_MUNICIPALITY_NAME,
                "raw_fields": {
                    "parser": HONGCHEON_EXISTING_COURSE_PARSER,
                    "source_identity": course_id,
                    "source_page": page_index,
                    "source_ordinal": ordinal,
                    "source_category": category,
                    "source_status": source_status,
                    "source_period_dates": period_dates,
                    "target_redacted": target_redacted,
                    "application_control_present": bool(application_url),
                    "application_endpoint_called": False,
                    "detail_verified": False,
                    "pii_detail_fields_skipped": _F508_PII_DETAIL_FIELDS_NEVER_READ,
                },
                "_course_id": course_id,
                "_period_dates": period_dates,
                "_source_target_truncated": source_target.endswith("..."),
                "_target_redacted": target_redacted,
            }
        )
    return total, rows


def _f508_rows_signature(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            row.get("provider_course_id"),
            row.get("title"),
            row.get("category"),
            row.get("start_date"),
            row.get("end_date"),
            row.get("status"),
            row.get("capacity_current"),
            row.get("capacity_total"),
            row.get("target"),
            bool(row.get("application_url")),
            (row.get("raw_fields") or {}).get("source_page"),
            (row.get("raw_fields") or {}).get("source_ordinal"),
        )
        for row in rows
    )


def _stable_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _f508_output_signature(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            row.get("provider_course_id"),
            row.get("title"),
            row.get("category"),
            row.get("start_date"),
            row.get("end_date"),
            row.get("apply_start_date"),
            row.get("apply_end_date"),
            row.get("schedule_raw"),
            row.get("status"),
            row.get("target"),
            row.get("venue_name"),
            row.get("fee"),
            row.get("application_url"),
        )
        for row in rows
    )


def _f508_detail_fields(table: Tag, course_id: str) -> dict[str, str]:
    caption = _one(table.select("caption"), f"integrated course {course_id} caption")
    if _text(caption) != "교육정보":
        raise HongcheonContractError(
            f"integrated course {course_id}: detail caption changed"
        )
    rows = table.select("tbody > tr")
    labels: list[str] = []
    fields: dict[str, str] = {}
    for tr in rows:
        label_node = _one(
            tr.find_all("th", recursive=False),
            f"integrated course {course_id} detail label",
        )
        value_node = _one(
            tr.find_all("td", recursive=False),
            f"integrated course {course_id} detail value",
        )
        label = _text(label_node)
        labels.append(label)
        if label in _F508_DETAIL_SKIPPED_FIELDS:
            # The value subtree is deliberately never accessed.
            continue
        if label not in _F508_DETAIL_SAFE_FIELDS or label in fields:
            raise HongcheonContractError(
                f"integrated course {course_id}: unknown/duplicate detail field {label}"
            )
        fields[label] = _text(value_node)
    if tuple(labels) != _F508_DETAIL_LABELS:
        raise HongcheonContractError(
            f"integrated course {course_id}: detail field contract changed"
        )
    if set(fields) != _F508_DETAIL_SAFE_FIELDS:
        raise HongcheonContractError(
            f"integrated course {course_id}: safe detail fields are incomplete"
        )
    required_nonempty = _F508_DETAIL_SAFE_FIELDS - {"교육시간"}
    if any(not fields[label] for label in required_nonempty):
        raise HongcheonContractError(
            f"integrated course {course_id}: required safe detail value missing"
        )
    return fields


def _merge_f508_detail(row: dict[str, Any], soup: BeautifulSoup) -> dict[str, Any]:
    course_id = str(row["_course_id"])
    page_title = _one(soup.select("title"), f"integrated course {course_id} title")
    if _text(page_title) != "일반교육 상세 - 교육신청 - 교육정보 - 평생학습관":
        raise HongcheonContractError(
            f"integrated course {course_id}: detail page title changed"
        )
    table = _one(
        soup.select("table.bbs_default.view"),
        f"integrated course {course_id} detail table",
    )
    fields = _f508_detail_fields(table, course_id)
    if fields["강좌명"] != row["title"] or fields["분야"] != row["category"]:
        raise HongcheonContractError(
            f"integrated course {course_id}: detail/list identity mismatch"
        )
    detail_period_dates = _f508_date_values(
        fields["교육기간"], f"integrated course {course_id} detail education period"
    )
    if detail_period_dates != tuple(row["_period_dates"]):
        raise HongcheonContractError(
            f"integrated course {course_id}: detail/list education period mismatch"
        )
    capacity_match = re.fullmatch(
        r"(\d+)명 접수\s*/\s*총 (\d+)명 모집\s*[(]"
        r"(?:(?:온라인: \d+명, 대기인원: \d+명, )?방문접수 \d+ 명)[)]",
        fields["모집인원"],
    )
    if not capacity_match or (
        int(capacity_match.group(1)), int(capacity_match.group(2))
    ) != (row["capacity_current"], row["capacity_total"]):
        raise HongcheonContractError(
            f"integrated course {course_id}: detail/list capacity mismatch"
        )
    detail_target, detail_target_redacted = _f508_public_target(fields["교육대상"])
    if row["_target_redacted"] != detail_target_redacted:
        raise HongcheonContractError(
            f"integrated course {course_id}: target privacy classification changed"
        )
    if row["target"]:
        if row["_source_target_truncated"]:
            if not detail_target.startswith(str(row["target"])[:-3]):
                raise HongcheonContractError(
                    f"integrated course {course_id}: truncated target mismatch"
                )
        elif detail_target != row["target"]:
            raise HongcheonContractError(
                f"integrated course {course_id}: detail/list target mismatch"
            )
    application_controls = soup.select("a[href*='courseWebAppRegist.do']")
    canonical_controls = {
        _f508_application_url(control.get("href"), course_id)
        for control in application_controls
    }
    expected_controls = {row["application_url"]} if row["application_url"] else set()
    if canonical_controls != expected_controls:
        raise HongcheonContractError(
            f"integrated course {course_id}: detail application control mismatch"
        )
    apply_dates = _f508_date_values(
        fields["접수기간"], f"integrated course {course_id} application period"
    )
    merged = dict(row)
    merged.update(
        {
            "target": detail_target,
            "venue_name": fields["교육장소"],
            "period": fields["교육기간"],
            "apply_period": fields["접수기간"],
            "apply_start_date": apply_dates[0],
            "apply_end_date": apply_dates[-1],
            "schedule_raw": fields["교육시간"] or fields["교육기간"],
            "fee": fields["수강료"],
        }
    )
    merged["raw_fields"] = {
        **row["raw_fields"],
        "detail_verified": True,
        "application_control_verified": bool(canonical_controls),
        "target_redacted": detail_target_redacted,
        "sensitive_free_text_structurally_discarded": True,
    }
    return merged


def _detail_safe_fields(intro: Tag, identity: str) -> dict[str, str]:
    ul = _one(intro.find_all("ul", recursive=False), f"course {identity} detail fields")
    fields: dict[str, str] = {}
    for li in ul.find_all("li", recursive=False):
        label_node = li.find("span", class_="sub_title", recursive=False)
        if label_node is None:
            raise HongcheonContractError(f"course {identity}: unlabeled detail field")
        label = _text(label_node)
        if label in fields:
            raise HongcheonContractError(f"course {identity}: duplicate detail field {label}")
        if label in _DETAIL_SKIPPED_FIELDS:
            # The value subtree is deliberately never accessed.
            continue
        if label not in _DETAIL_ALLOWED_FIELDS:
            raise HongcheonContractError(f"course {identity}: unknown detail field {label}")
        value_node = li.find("span", class_="con", recursive=False)
        if value_node is None:
            raise HongcheonContractError(f"course {identity}: safe detail value missing")
        fields[label] = _text(value_node)
    missing = _DETAIL_ALLOWED_FIELDS - set(fields)
    if missing:
        raise HongcheonContractError(
            f"course {identity}: safe detail fields missing ({', '.join(sorted(missing))})"
        )
    return fields


def _detail_counts(intro: Tag, identity: str) -> tuple[int, int, int, int]:
    table = _one(
        intro.select("table.culture_view_table"), f"course {identity} capacity table"
    )
    headers = tuple(_text(node) for node in table.select("thead th"))
    if headers != (
        "현재 참여/모집",
        "현재 오프라인/오프라인",
        "현재 대기자/대기자",
    ):
        raise HongcheonContractError(f"course {identity}: detail capacity headers changed")
    cells = table.select("tbody td")
    if len(cells) != 3:
        raise HongcheonContractError(f"course {identity}: detail capacity width changed")
    values: list[tuple[int, int]] = []
    for cell in cells:
        match = _DETAIL_COUNT_RE.fullmatch(_text(cell))
        if not match:
            raise HongcheonContractError(f"course {identity}: detail capacity changed")
        values.append((int(match.group(1)), int(match.group(2))))
    return (
        values[0][0] + values[1][0],
        values[0][1] + values[1][1],
        values[2][0],
        values[2][1],
    )


def _merge_detail(row: dict[str, Any], soup: BeautifulSoup) -> dict[str, Any]:
    site = str(row["_site"])
    branch = _BRANCH_BY_SITE[site]
    identity = str(row["raw_fields"]["source_identity"])
    title_node = _one(soup.select("title"), f"course {identity} page title")
    if _text(title_node) != f"{branch.page_title} > 문화공간 > 프로그램":
        raise HongcheonContractError(f"course {identity}: detail page title changed")
    intro = _one(soup.select("div.culture_intro"), f"course {identity} safe detail")
    heading = _one(intro.find_all("h4", recursive=False), f"course {identity} title")
    category_node = _one(
        intro.find_all("strong", class_="teach_sort", recursive=False),
        f"course {identity} category",
    )
    if _text(heading) != row["title"]:
        raise HongcheonContractError(f"course {identity}: detail title mismatch")
    if _text(category_node) != row["raw_fields"]["source_category"]:
        raise HongcheonContractError(f"course {identity}: detail category mismatch")
    fields = _detail_safe_fields(intro, identity)
    detail_apply_start, detail_apply_end = _date_range(
        fields["접수기간"], f"course {identity} detail application period"
    )
    detail_start, detail_end = _date_range(
        fields["강의기간"], f"course {identity} detail course period"
    )
    if (
        detail_start != row["start_date"]
        or detail_end != row["end_date"]
        or detail_apply_start != row["apply_start_date"]
        or detail_apply_end != row["apply_end_date"]
    ):
        raise HongcheonContractError(f"course {identity}: detail/list period mismatch")
    detail_capacity = _detail_counts(intro, identity)
    if detail_capacity != (
        row["capacity_current"],
        row["capacity_total"],
        row["_wait_current"],
        row["_wait_total"],
    ):
        raise HongcheonContractError(f"course {identity}: detail/list capacity mismatch")

    controls = soup.select("a.reg_btn.add")
    source_status = str(row["_source_status"])
    active = source_status in _ACTIVE_SOURCE_STATUSES
    if active and len(controls) != 1:
        raise HongcheonContractError(f"course {identity}: detail application control missing")
    if not active and controls:
        raise HongcheonContractError(f"course {identity}: closed detail gained application control")
    if controls:
        control = controls[0]
        expected = {
            "keyvalue1": row["_homepage_id"],
            "keyvalue2": row["_group_idx"],
            "keyvalue3": row["_category_idx"],
            "keyvalue4": row["_teach_idx"],
            "apply_status": "0" if source_status == "수강신청" else "1",
        }
        if (
            _clean(control.get("href"))
            or any(_clean(control.get(key)) != str(value) for key, value in expected.items())
        ):
            raise HongcheonContractError(
                f"course {identity}: detail application identity mismatch"
            )

    merged = dict(row)
    merged.update(
        {
            "apply_period": fields["접수기간"],
            "period": fields["강의기간"],
            "schedule_raw": fields["강의시간"],
            "target": fields["강의대상"],
            "venue_name": fields["강의장소"],
        }
    )
    merged["raw_fields"] = {
        **row["raw_fields"],
        "detail_verified": True,
        "application_control_verified": bool(controls),
        "sensitive_free_text_structurally_discarded": True,
    }
    return merged


def _assert_no_pii(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).startswith("_"):
                continue
            _assert_no_pii(item)
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            _assert_no_pii(item)
        return
    text = str(value or "")
    if _PHONE_RE.search(text) or _EMAIL_RE.search(text) or _RESIDENT_ID_RE.search(text):
        raise HongcheonContractError("PII-like value escaped the Hongcheon allowlist")


def _finalize_rows(
    rows: list[dict[str, Any]], dedupe_fn: Optional[DedupeRows]
) -> list[dict[str, Any]]:
    public = [
        {key: value for key, value in row.items() if not key.startswith("_")}
        for row in rows
    ]
    identities = [row["provider_course_id"] for row in public]
    if len(identities) != len(set(identities)):
        raise HongcheonContractError("duplicate current course identity detected")
    if dedupe_fn is not None:
        deduped = list(dedupe_fn(public))
        if len(deduped) != len(public):
            raise HongcheonContractError("external dedupe removed an owned source identity")
        public = deduped
    for row in public:
        _assert_no_pii(row)
    return public


def _failure_meta(error: Exception, client: Optional[_Client]) -> dict[str, Any]:
    message = _clean(error)
    meta: dict[str, Any] = {
        "snapshot_complete": False,
        "parser": HONGCHEON_LIBRARY_PARSER,
        "provider": HONGCHEON_LIBRARY_PROVIDER,
        "candidate_id": HONGCHEON_LIBRARY_CANDIDATE_ID,
        "canonical_url": HONGCHEON_LIBRARY_URL,
        "collected": 0,
        "application_endpoints_called": 0,
        "error_kind": type(error).__name__,
        "error": message[:500],
    }
    if "session_factory" in message:
        meta["configured_collection_error"] = message[:500]
    if client is not None:
        meta.update(
            {
                "http_attempts": client.http_attempts,
                "retry_count": client.retry_count,
                "sessions_created": client.sessions_created,
                "route_counts": dict(client.route_counts),
            }
        )
    return meta


def _f508_failure_meta(
    error: Exception, client: Optional[_Client]
) -> dict[str, Any]:
    message = _clean(error) or type(error).__name__
    meta: dict[str, Any] = {
        "snapshot_complete": False,
        "parser": HONGCHEON_EXISTING_COURSE_PARSER,
        "provider": HONGCHEON_EXISTING_COURSE_PROVIDER,
        "candidate_id": HONGCHEON_EXISTING_COURSE_CANDIDATE_ID,
        "canonical_url": HONGCHEON_EXISTING_COURSE_URL,
        "collected": 0,
        "application_endpoints_called": 0,
        "configured_collection_error": message[:500],
        "error_kind": type(error).__name__,
        "error": message[:500],
    }
    if client is not None:
        meta.update(
            {
                "http_attempts": client.http_attempts,
                "retry_count": client.retry_count,
                "sessions_created": client.sessions_created,
                "route_counts": dict(client.route_counts),
            }
        )
    return meta


def collect_hongcheon_existing_education(
    target: Any,
    *,
    today: Optional[date | datetime | str] = None,
    max_pages: int = HONGCHEON_EXISTING_COURSE_RECOMMENDED_MAX_PAGES,
    detail_limit: int = HONGCHEON_EXISTING_COURSE_RECOMMENDED_DETAIL_LIMIT,
    max_requests: int = HONGCHEON_DEFAULT_MAX_REQUESTS,
    timeout: int = 30,
    attempts: int = HONGCHEON_FETCH_ATTEMPTS,
    session_factory: Optional[SessionFactory] = None,
    sleeper: Sleeper = time.sleep,
    dedupe_fn: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect the configured lifelong-learning owner as one atomic ledger."""

    client: Optional[_Client] = None
    try:
        if not is_hongcheon_existing_course_target(target):
            raise HongcheonContractError(
                "target is not the exact Hongcheon integrated-education owner"
            )
        if isinstance(max_pages, bool) or int(max_pages) < 1:
            raise HongcheonContractError("max_pages must be a positive integer")
        if isinstance(detail_limit, bool) or int(detail_limit) < 1:
            raise HongcheonContractError("detail_limit must be a positive integer")
        if isinstance(max_requests, bool) or int(max_requests) < 1:
            raise HongcheonContractError("max_requests must be a positive integer")
        if isinstance(attempts, bool) or not 1 <= int(attempts) <= 5:
            raise HongcheonContractError("attempts must be between 1 and 5")
        if session_factory is None:
            if not allow_raw_requests_for_tests:
                raise HongcheonContractError("managed session_factory is required")
            session_factory = _raw_session_factory

        audit_day = _today(today)
        owner_branch = (
            _clean(_target_value(target, "branch"))
            or _clean(_target_value(target, "name"))
            or HONGCHEON_MUNICIPALITY_NAME
        )
        client = _Client(
            session_factory=session_factory,
            timeout=int(timeout),
            max_requests=int(max_requests),
            attempts=int(attempts),
            sleeper=sleeper,
        )

        declared_total, first_rows = _parse_f508_catalogue_page(
            client.get_soup(hongcheon_existing_course_list_url(1)),
            page_index=1,
            expected_total=None,
            owner_branch=owner_branch,
            sentinel=False,
        )
        data_pages = (
            declared_total + HONGCHEON_EXISTING_COURSE_PAGE_SIZE - 1
        ) // HONGCHEON_EXISTING_COURSE_PAGE_SIZE
        sentinel_page = data_pages + 1
        required_list_requests = 2 * sentinel_page
        if int(max_pages) < required_list_requests:
            raise HongcheonContractError(
                f"max_pages {max_pages} is below required "
                f"{required_list_requests} catalogue/sentinel requests"
            )

        source_pages: dict[int, list[dict[str, Any]]] = {1: first_rows}
        for page_index in range(2, data_pages + 1):
            _total, page_rows = _parse_f508_catalogue_page(
                client.get_soup(hongcheon_existing_course_list_url(page_index)),
                page_index=page_index,
                expected_total=declared_total,
                owner_branch=owner_branch,
                sentinel=False,
            )
            source_pages[page_index] = page_rows
        _parse_f508_catalogue_page(
            client.get_soup(hongcheon_existing_course_list_url(sentinel_page)),
            page_index=sentinel_page,
            expected_total=declared_total,
            owner_branch=owner_branch,
            sentinel=True,
        )

        source_rows = [
            row
            for page_index in range(1, data_pages + 1)
            for row in source_pages[page_index]
        ]
        if len(source_rows) != declared_total:
            raise HongcheonContractError(
                "integrated education declared total does not match full pagination"
            )
        source_identities = [row["provider_course_id"] for row in source_rows]
        if len(source_identities) != len(set(source_identities)):
            raise HongcheonContractError(
                "integrated education course identities are not unique"
            )
        source_ordinals = [
            int(row["raw_fields"]["source_ordinal"]) for row in source_rows
        ]
        if source_ordinals != list(range(1, declared_total + 1)):
            raise HongcheonContractError(
                "integrated education source ordinals are not contiguous"
            )

        current_rows = [
            row
            for row in source_rows
            if date.fromisoformat(str(row["end_date"])) >= audit_day
        ]
        if len(current_rows) > int(detail_limit):
            raise HongcheonContractError(
                f"detail_limit {detail_limit} is below required "
                f"{len(current_rows)} details"
            )
        minimum_requests = required_list_requests + len(current_rows)
        if int(max_requests) < minimum_requests:
            raise HongcheonContractError(
                f"max_requests {max_requests} is below required minimum "
                f"{minimum_requests} requests"
            )

        # Repeat every data page and the empty boundary.  Comparing parsed,
        # privacy-filtered signatures makes the eventual save a single stable
        # snapshot rather than a blend of two changing catalogues.
        for page_index in range(1, data_pages + 1):
            _total, rechecked = _parse_f508_catalogue_page(
                client.get_soup(hongcheon_existing_course_list_url(page_index)),
                page_index=page_index,
                expected_total=declared_total,
                owner_branch=owner_branch,
                sentinel=False,
            )
            if _f508_rows_signature(rechecked) != _f508_rows_signature(
                source_pages[page_index]
            ):
                raise HongcheonContractError(
                    f"integrated education page {page_index} changed during collection"
                )
        _parse_f508_catalogue_page(
            client.get_soup(hongcheon_existing_course_list_url(sentinel_page)),
            page_index=sentinel_page,
            expected_total=declared_total,
            owner_branch=owner_branch,
            sentinel=True,
        )

        verified_rows = [
            _merge_f508_detail(row, client.get_soup(str(row["raw_url"])))
            for row in current_rows
        ]
        output = _finalize_rows(verified_rows, dedupe_fn)
        source_status_counts = Counter(
            str(row["raw_fields"]["source_status"]) for row in source_rows
        )
        current_status_counts = Counter(
            str(row["raw_fields"]["source_status"]) for row in output
        )
        application_controls = sum(bool(row.get("application_url")) for row in output)
        meta = {
            "snapshot_complete": True,
            "parser": HONGCHEON_EXISTING_COURSE_PARSER,
            "provider": HONGCHEON_EXISTING_COURSE_PROVIDER,
            "candidate_id": HONGCHEON_EXISTING_COURSE_CANDIDATE_ID,
            "canonical_url": HONGCHEON_EXISTING_COURSE_URL,
            "municipality_code": HONGCHEON_MUNICIPALITY_CODE,
            "municipality_name": HONGCHEON_MUNICIPALITY_NAME,
            "source_total": declared_total,
            "collected": len(output),
            "excluded_expired": declared_total - len(output),
            "source_catalogue_sha256": _stable_sha256(
                _f508_rows_signature(source_rows)
            ),
            "output_sha256": _stable_sha256(_f508_output_signature(output)),
            "source_status_counts": dict(source_status_counts),
            "current_status_counts": dict(current_status_counts),
            "page_size": HONGCHEON_EXISTING_COURSE_PAGE_SIZE,
            "data_pages": data_pages,
            "empty_sentinel_page": sentinel_page,
            "empty_sentinels": 2,
            "pages": required_list_requests,
            "list_requests": required_list_requests,
            "required_list_requests": required_list_requests,
            "pagination_detected": data_pages > 1,
            "pagination_complete": True,
            "pagination_exhausted": True,
            "source_cap_reached": False,
            "catalogue_stability_rechecks": sentinel_page,
            "detail_pages": len(verified_rows),
            "details_complete": len(verified_rows) == len(current_rows),
            "identity_bound_application_controls": application_controls,
            "application_endpoints_called": 0,
            "pii_detail_fields_never_read": _F508_PII_DETAIL_FIELDS_NEVER_READ,
            "classification_locked": True,
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "source_group": "municipal_reservation",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
            "no_current_data": not output,
            "recommended_max_pages": HONGCHEON_EXISTING_COURSE_RECOMMENDED_MAX_PAGES,
            "recommended_detail_limit": (
                HONGCHEON_EXISTING_COURSE_RECOMMENDED_DETAIL_LIMIT
            ),
            "http_attempts": client.http_attempts,
            "retry_count": client.retry_count,
            "sessions_created": client.sessions_created,
            "route_counts": dict(client.route_counts),
        }
        return output, HONGCHEON_EXISTING_COURSE_PARSER, meta
    except Exception as exc:
        return [], HONGCHEON_EXISTING_COURSE_PARSER, _f508_failure_meta(exc, client)
    finally:
        if client is not None:
            client.close()


def collect_hongcheon_library_education(
    target: Any,
    *,
    today: Optional[date | datetime | str] = None,
    max_pages: int = HONGCHEON_DEFAULT_MAX_PAGES,
    detail_limit: int = HONGCHEON_DEFAULT_DETAIL_LIMIT,
    max_requests: int = HONGCHEON_DEFAULT_MAX_REQUESTS,
    timeout: int = 30,
    attempts: int = HONGCHEON_FETCH_ATTEMPTS,
    session_factory: Optional[SessionFactory] = None,
    sleeper: Sleeper = time.sleep,
    dedupe_fn: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect an all-or-nothing current snapshot of all six library sites."""

    client: Optional[_Client] = None
    try:
        if not is_hongcheon_library_target(target):
            raise HongcheonContractError("target is not the exact Hongcheon library owner")
        if (
            isinstance(max_pages, bool)
            or int(max_pages) < HONGCHEON_REQUIRED_LIST_REQUESTS
        ):
            raise HongcheonContractError(
                f"max_pages {max_pages} is below required "
                f"{HONGCHEON_REQUIRED_LIST_REQUESTS} catalogue/sentinel requests"
            )
        if isinstance(detail_limit, bool) or int(detail_limit) < 1:
            raise HongcheonContractError("detail_limit must be a positive integer")
        if isinstance(max_requests, bool) or int(max_requests) < 1:
            raise HongcheonContractError("max_requests must be a positive integer")
        if isinstance(attempts, bool) or not 1 <= int(attempts) <= 5:
            raise HongcheonContractError("attempts must be between 1 and 5")
        if session_factory is None:
            if not allow_raw_requests_for_tests:
                raise HongcheonContractError("managed session_factory is required")
            session_factory = _raw_session_factory
        audit_day = _today(today)
        client = _Client(
            session_factory=session_factory,
            timeout=int(timeout),
            max_requests=int(max_requests),
            attempts=int(attempts),
            sleeper=sleeper,
        )

        directory = _directory_signature(client.get_soup(HONGCHEON_LIBRARY_URL))
        source_rows: list[dict[str, Any]] = []
        source_counts: dict[str, int] = {}
        source_signatures: dict[str, tuple[tuple[Any, ...], ...]] = {}
        for branch in HONGCHEON_LIBRARY_BRANCHES:
            rows = _parse_catalogue(
                client.get_soup(hongcheon_library_list_url(branch.site)),
                branch,
                sentinel=False,
            )
            _parse_catalogue(
                client.get_soup(
                    hongcheon_library_list_url(branch.site, sentinel=True)
                ),
                branch,
                sentinel=True,
            )
            source_counts[branch.name] = len(rows)
            source_signatures[branch.site] = _rows_signature(rows)
            source_rows.extend(rows)

        identities = [row["provider_course_id"] for row in source_rows]
        if len(identities) != len(set(identities)):
            raise HongcheonContractError("cross-branch course identities are not disjoint")

        for branch in HONGCHEON_LIBRARY_BRANCHES:
            rechecked = _parse_catalogue(
                client.get_soup(hongcheon_library_list_url(branch.site)),
                branch,
                sentinel=False,
            )
            if _rows_signature(rechecked) != source_signatures[branch.site]:
                raise HongcheonContractError(
                    f"{branch.site}: catalogue changed during collection"
                )
        if _directory_signature(client.get_soup(HONGCHEON_LIBRARY_URL)) != directory:
            raise HongcheonContractError("official branch directory changed during collection")

        current_rows = [
            row
            for row in source_rows
            if date.fromisoformat(str(row["end_date"])) >= audit_day
        ]
        if len(current_rows) > int(detail_limit):
            raise HongcheonContractError(
                f"detail_limit {detail_limit} is below required {len(current_rows)} details"
            )
        verified_rows = [
            _merge_detail(row, client.get_soup(str(row["raw_url"])))
            for row in current_rows
        ]
        output = _finalize_rows(verified_rows, dedupe_fn)
        current_counts = Counter(str(row["branch"]) for row in output)
        source_status_counts = Counter(
            str(row["raw_fields"]["source_status"]) for row in source_rows
        )
        current_status_counts = Counter(
            str(row["raw_fields"]["source_status"]) for row in output
        )
        application_controls = sum(bool(row.get("application_url")) for row in output)
        meta = {
            "snapshot_complete": True,
            "parser": HONGCHEON_LIBRARY_PARSER,
            "provider": HONGCHEON_LIBRARY_PROVIDER,
            "candidate_id": HONGCHEON_LIBRARY_CANDIDATE_ID,
            "canonical_url": HONGCHEON_LIBRARY_URL,
            "municipality_code": HONGCHEON_MUNICIPALITY_CODE,
            "municipality_name": HONGCHEON_MUNICIPALITY_NAME,
            "source_total": len(source_rows),
            "collected": len(output),
            "excluded_expired": len(source_rows) - len(output),
            "branch_count": len(HONGCHEON_LIBRARY_BRANCHES),
            "official_branch_directory": [branch.name for branch in HONGCHEON_LIBRARY_BRANCHES],
            "branch_source_counts": source_counts,
            "branch_current_counts": dict(current_counts),
            "source_status_counts": dict(source_status_counts),
            "current_status_counts": dict(current_status_counts),
            "single_page_catalogues": len(HONGCHEON_LIBRARY_BRANCHES),
            "pages": HONGCHEON_REQUIRED_LIST_REQUESTS,
            "list_requests": HONGCHEON_REQUIRED_LIST_REQUESTS,
            "required_list_requests": HONGCHEON_REQUIRED_LIST_REQUESTS,
            "pagination_detected": False,
            "empty_sentinels": len(HONGCHEON_LIBRARY_BRANCHES),
            "sentinel_filter": f"searchCate1={HONGCHEON_EMPTY_FILTER}",
            "catalogue_stability_rechecks": len(HONGCHEON_LIBRARY_BRANCHES),
            "directory_stability_rechecks": 1,
            "detail_pages": len(verified_rows),
            "details_complete": len(verified_rows) == len(current_rows),
            "pagination_complete": True,
            "full_snapshot_validated": True,
            "no_current_data": not output,
            "no_current_reason": (
                "complete owner ledger has no current/future courses"
                if not output
                else ""
            ),
            "identity_bound_application_controls": application_controls,
            "application_endpoints_called": 0,
            "pii_detail_fields_never_read": _PII_DETAIL_FIELDS_NEVER_READ,
            "classification_locked": True,
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "source_group": "municipal_reservation",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
            "tls_intermediate_sha256": HONGCHEON_SECTIGO_INTERMEDIATE_SHA256,
            "tls_verification_disabled": False,
            "http_attempts": client.http_attempts,
            "retry_count": client.retry_count,
            "sessions_created": client.sessions_created,
            "route_counts": dict(client.route_counts),
        }
        return output, HONGCHEON_LIBRARY_PARSER, meta
    except Exception as exc:
        return [], HONGCHEON_LIBRARY_PARSER, _failure_meta(exc, client)
    finally:
        if client is not None:
            client.close()


def collect_hongcheon_education_courses(
    target: Any, **kwargs: Any
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    if is_hongcheon_library_target(target):
        return collect_hongcheon_library_education(target, **kwargs)
    if is_hongcheon_existing_course_target(target):
        return collect_hongcheon_existing_education(target, **kwargs)
    error = HongcheonContractError("target is not an exact Hongcheon education owner")
    return [], HONGCHEON_LIBRARY_PARSER, _failure_meta(error, None)


collect = collect_hongcheon_education_courses
