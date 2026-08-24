"""Fail-closed collector for Sokcho's official education catalogues.

The search candidate ``/index.php?device=mobile`` is only a navigation shell.
The municipal lifelong-learning centre publishes three real catalogue
partitions below ``/lecture/class_list.php``.  The daytime partition is
paginated; the legacy YAML collector read only its first page and therefore
returned 26 of the 67 courses visible on 2026-07-21.

Sokcho also publishes education through two municipal-library branches.  Two
separate Gangwon Office of Education branches in Sokcho use the common GWE
``lecture-event`` platform.  They are retained as distinct branches but are
owned by this canonical municipal snapshot so that the candidate-only GWE
homepage is not scheduled as a duplicate provider.

Every catalogue proves its complete page range, an immediately empty
post-last page, and a stable first-page recheck.  Every current/future library
course is checked against its detail page.  The lifelong-learning centre has
full inline details; each public course-bound application link is additionally
checked through the anonymous identity-verification page.  Instructor names,
staff contacts, free-form descriptions, attachments, applicant fields, and
source HTML are never returned or retained in ``raw_fields``.  Any incomplete
contract returns an empty result.

``edu.sokcho.go.kr`` currently omits its Sectigo intermediate certificate and
requires OpenSSL security level 1.  The adapter below pins that public
intermediate while keeping CA-chain and hostname verification enabled.  It
never disables TLS verification.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
import ssl
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import certifi
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter


SOKCHO_PROVIDER = "MUNI_EDU_SOKCHO_GO_KR_8E237F28"
SOKCHO_CANONICAL_CANDIDATE_ID = "MUNI_IR_26D69AC2DCC7"
SOKCHO_MUNICIPALITY_CODE = "5121000000"
SOKCHO_MUNICIPALITY_NAME = "강원특별자치도 속초시"
SOKCHO_CENTER_HOST = "edu.sokcho.go.kr"
SOKCHO_LIBRARY_HOST = "library.sokcho.go.kr"
SOKCHO_GWE_HOST = "lib.gwe.go.kr"
SOKCHO_CANONICAL_URL = (
    "https://edu.sokcho.go.kr/lecture/class_list.php?lc_type=0"
)
SOKCHO_DISCOVERY_SHELL_URL = "https://edu.sokcho.go.kr/index.php?device=mobile"
SOKCHO_CENTER_BRANCH = "속초시평생교육문화센터"
SOKCHO_CENTER_SITE_TITLE = "속초평생교육문화센터"
SOKCHO_CENTER_ADDRESS = "강원특별자치도 속초시 수복로 46"
SOKCHO_PAGE_SIZE = 15
SOKCHO_LECTURE_PAGE_SIZE = 10
SOKCHO_MAX_WORKERS = 6
SOKCHO_PARSER = (
    "sokcho_center_three_partitions+municipal_library_two_branches+"
    "gwe_two_branches+exclude_application_practice_shell+declared_totals+empty_post_last_pages+"
    "page1_rechecks+all_current_details+verified_application_controls+"
    "pinned_verified_legacy_tls+pii_allowlist"
)

# This is the official missing intermediate served by Sectigo's AIA endpoint.
# SHA-256: 8c54c334b66ba4e426772af4a3f9136c19a1aec729fdb28c535c07a5a4ef22e0
SOKCHO_SECTIGO_INTERMEDIATE_SHA256 = (
    "8c54c334b66ba4e426772af4a3f9136c19a1aec729fdb28c535c07a5a4ef22e0"
)
SOKCHO_SECTIGO_INTERMEDIATE_PEM = """-----BEGIN CERTIFICATE-----
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


@dataclass(frozen=True)
class SokchoCenterPartition:
    code: str
    name: str
    query_name: str
    query_value: str
    identity_name: str

    def list_url(self, page: Optional[int] = None) -> str:
        query: dict[str, str] = {self.query_name: self.query_value}
        if page is not None:
            query["page"] = str(page)
        return "https://edu.sokcho.go.kr/lecture/class_list.php?" + urlencode(query)


SOKCHO_CENTER_PARTITIONS: tuple[SokchoCenterPartition, ...] = (
    SokchoCenterPartition("center_day", "주간반", "lc_type", "0", "lc_idx"),
    SokchoCenterPartition("center_night", "야간반", "lc_type", "1", "lc_idx"),
    SokchoCenterPartition(
        "center_special", "특별프로그램", "lco_type", "3", "lco_idx"
    ),
)
_CENTER_BY_CODE = {item.code: item for item in SOKCHO_CENTER_PARTITIONS}


@dataclass(frozen=True)
class SokchoLectureSource:
    code: str
    branch: str
    list_url: str
    layout: str
    authority: str
    operator_type: str
    address: str

    @property
    def host(self) -> str:
        return (urlparse(self.list_url).hostname or "").lower()

    @property
    def path_prefix(self) -> str:
        parts = [part for part in urlparse(self.list_url).path.split("/") if part]
        return f"/{parts[0]}/" if parts else "/"

    def page_url(self, index: int) -> str:
        parsed = urlparse(self.list_url)
        query = parse_qs(parsed.query)
        query["page"] = [str(index)]
        return parsed._replace(query=urlencode(query, doseq=True)).geturl()


SOKCHO_LECTURE_SOURCES: tuple[SokchoLectureSource, ...] = (
    SokchoLectureSource(
        "city_library",
        "속초시립도서관",
        "https://library.sokcho.go.kr/sokcho/menu/379/lecture-event/list/all",
        "city",
        "속초시",
        "지자체/공공기관",
        "강원특별자치도 속초시 조양로 89",
    ),
    SokchoLectureSource(
        "english_library",
        "어린이영어도서관",
        "https://library.sokcho.go.kr/eng/menu/391/lecture-event/list/all",
        "english",
        "속초시",
        "지자체/공공기관",
        "강원특별자치도 속초시 엑스포로 132",
    ),
    SokchoLectureSource(
        "gwe_education_library",
        "속초교육도서관",
        "https://lib.gwe.go.kr/sclib/menu/3904/lecture-event/list/all",
        "gwe",
        "강원특별자치도교육청",
        "교육청/공공기관",
        "강원특별자치도 속초시 번영로 15",
    ),
    SokchoLectureSource(
        "gwe_education_culture_center",
        "속초교육문화관",
        "https://lib.gwe.go.kr/sokecc/menu/4559/lecture-event/list/all",
        "gwe",
        "강원특별자치도교육청",
        "교육청/공공기관",
        "강원특별자치도 속초시 번영로 82",
    ),
)
_LECTURE_BY_CODE = {item.code: item for item in SOKCHO_LECTURE_SOURCES}

# Candidate-only or discovery URLs folded into the canonical snapshot.  The
# general GWE collector supports the layout, but no Sokcho branch target is
# active in crawl_targets; scheduling these aliases would duplicate this run.
SOKCHO_OWNERSHIP_ALIAS_URLS: tuple[str, ...] = (
    SOKCHO_DISCOVERY_SHELL_URL,
    "https://library.sokcho.go.kr/sokcho/main",
    "https://library.sokcho.go.kr/eng/main",
    "https://lib.gwe.go.kr/sclib/main",
    "https://lib.gwe.go.kr/sokecc/main",
)
SOKCHO_SUPERSEDED_PROVIDER_CANDIDATES: tuple[str, ...] = (
    "MUNI_LIB_GWE_GO_KR_6A5F40FB",
)
SOKCHO_EXCLUDED_PRACTICE_TITLES: tuple[str, ...] = (
    "【강좌 신청 미리 연습해 보기】",
)
SOKCHO_EXCLUDED_NON_COURSE_URLS: tuple[str, ...] = (
    "https://edu.sokcho.go.kr/bbs/board.php?bo_table=notice",
    "https://library.sokcho.go.kr/toy/main",
    "https://library.sokcho.go.kr/sokcho/menu/286/field_trip/calendar",
)


@dataclass(frozen=True)
class FetchedPage:
    soup: BeautifulSoup
    requested_url: str
    final_url: str
    redirected: bool = False


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})(?!\d)"
)
_TIME_RE = re.compile(r"(?<!\d)(\d{1,2})\s*[:시]\s*(\d{2})?(?!\d)")
_CENTER_SEQUENCE_RE = re.compile(r"^(\d+)\.\s*(.+)$")
_CENTER_ID_RE = re.compile(r"^\d+$")
_LECTURE_DETAIL_RE = re.compile(r"/lecture-event/(\d+)(?:/)?$")
_CITY_TOTAL_RE = re.compile(
    r"총\s*([0-9,]+)\s*건\s*\(\s*([0-9,]+)\s*/\s*([0-9,]+)\s*PAGE\s*\)",
    re.IGNORECASE,
)
_GWE_TOTAL_RE = re.compile(r"전체\s*([0-9,]+)\s*건")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2})[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_CENTER_REQUIRED_LABELS = frozenset(
    {
        "년도",
        "강의기간",
        "기수",
        "강의시간",
        "접수기간",
        "정원/신청",
        "선발기준",
        "강의구분",
        "강의장소",
        "납부기간",
        "모집제한",
        "수강료",
    }
)
_ACTIVE_LABELS = frozenset({"수강신청", "신청", "접수중"})
_WAITLIST_LABELS = frozenset({"대기자신청", "대기자접수", "대기접수"})
_SCHEDULED_LABELS = frozenset({"접수예정", "신청예정"})
_FULL_LABELS = frozenset({"신청인원초과", "정원마감"})
_CLOSED_LABELS = frozenset(
    {"접수마감", "신청마감", "신청종료", "접수종료", "마감", "운영종료"}
)
_CITY_EMPTY_TEXT = "조회되는 문화강좌가 없습니다."

_ALLOWED_ROW_KEYS = frozenset(
    {
        "provider",
        "provider_course_id",
        "title",
        "branch",
        "branch_code",
        "preserve_branch",
        "category",
        "raw_url",
        "application_url",
        "application_type",
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
        "capacity",
        "capacity_current",
        "capacity_total",
        "room",
        "venue_name",
        "address",
        "venue_address",
        "program_type",
        "collection_category",
        "domain_category",
        "source_group",
        "operator_type",
        "collection_type",
        "application_method_raw",
        "municipality_code",
        "municipality_name",
        "raw_fields",
    }
)
_ALLOWED_RAW_KEYS = frozenset(
    {
        "parser",
        "source_kind",
        "source_authority",
        "identity",
        "application_identity",
        "source_identity_contract",
        "source_page",
        "source_sequence",
        "source_status",
        "source_control_label",
        "fee_evidence",
        "target_evidence",
        "schedule_evidence",
        "inline_detail_verified",
        "detail_verified",
        "application_control_verified",
    }
)


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return _SPACE_RE.sub(" ", str(value).replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", _clean(value)).lower()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _canonical_url(value: Any) -> str:
    parsed = urlparse(_clean(value))
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return ""
    query = urlencode(sorted((key, item) for key, values in parse_qs(parsed.query).items() for item in values))
    return parsed._replace(
        scheme="https",
        netloc=parsed.hostname.lower(),
        path=parsed.path or "/",
        query=query,
        fragment="",
    ).geturl()


def is_sokcho_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != SOKCHO_PROVIDER:
        return False
    value = _canonical_url(_target_value(target, "url"))
    allowed = {_canonical_url(SOKCHO_CANONICAL_URL), _canonical_url(SOKCHO_DISCOVERY_SHELL_URL)}
    return value in allowed


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        return value.astimezone(ZoneInfo("Asia/Seoul")).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(_clean(value)[:10])


def _course_id(source_kind: str, identity: str) -> str:
    token = f"{SOKCHO_PROVIDER}|{source_kind}|{identity}"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:32].upper()


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> str:
    values = [
        "|".join(
            (
                _clean(row.get("raw_fields", {}).get("identity")),
                _clean(row.get("title")),
                _clean(row.get("end_date")),
                _clean(row.get("raw_fields", {}).get("source_status")),
            )
        )
        for row in rows
    ]
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()


def _parse_dates(value: Any) -> list[date]:
    result: list[date] = []
    for year, month, day in _DATE_RE.findall(_clean(value)):
        try:
            result.append(date(int(year), int(month), int(day)))
        except ValueError:
            return []
    return result


def _date_bounds(value: Any, label: str) -> tuple[date, date]:
    values = _parse_dates(value)
    if len(values) != 2:
        raise ValueError(f"{label} must contain exactly two dates")
    if values[0] > values[1]:
        raise ValueError(f"{label} is reversed")
    return values[0], values[1]


def _range_text(start: date, end: date) -> str:
    return f"{start.isoformat()} ~ {end.isoformat()}"


def _fee(value: Any) -> str:
    text = _clean(value).replace(" ", "")
    if not text or text in {"0", "-", "없음", "무료"}:
        return "무료"
    if re.fullmatch(r"\d[\d,]*", text):
        return f"{int(text.replace(',', '')):,}원"
    if "원" not in text and re.search(r"\d", text):
        return text + "원"
    return text


def _capacity(value: Any) -> tuple[Optional[int], Optional[int], Optional[int], Optional[int]]:
    text = _clean(value).replace(",", "")
    match = re.search(r"(?:정원\s*:\s*)?(\d+)\s*/\s*(\d+)", text)
    wait = re.search(r"대기자\s*:\s*(\d+)\s*/\s*(\d+)", text)
    current = int(match.group(1)) if match else None
    total = int(match.group(2)) if match else None
    wait_current = int(wait.group(1)) if wait else None
    wait_total = int(wait.group(2)) if wait else None
    return current, total, wait_current, wait_total


def _center_capacity(value: Any) -> tuple[Optional[int], Optional[int]]:
    match = re.search(r"(\d+)\s*/\s*(\d+)", _clean(value).replace(",", ""))
    if not match:
        return None, None
    return int(match.group(2)), int(match.group(1))


def _dl_pairs(root: Any) -> dict[str, str]:
    pairs: dict[str, str] = {}
    if root is None or not hasattr(root, "select"):
        return pairs
    for dl in root.select("dl"):
        children = [child for child in dl.children if getattr(child, "name", None) in {"dt", "dd"}]
        index = 0
        while index < len(children) - 1:
            if children[index].name == "dt" and children[index + 1].name == "dd":
                label = _clean(children[index].get_text(" ", strip=True)).rstrip(":")
                value = _clean(children[index + 1].get_text(" ", strip=True))
                if label and label not in pairs:
                    pairs[label] = value
                index += 2
            else:
                index += 1
    return pairs


def _center_pairs(root: Any) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for tr in root.select("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        index = 0
        while index < len(cells):
            if cells[index].name != "th":
                index += 1
                continue
            label = _clean(cells[index].get_text(" ", strip=True)).rstrip(":")
            value = _clean(cells[index + 1].get_text(" ", strip=True)) if index + 1 < len(cells) else ""
            if label and value and label not in pairs:
                pairs[label] = value
            index += 2
    return pairs


class _PinnedTLSAdapter(HTTPAdapter):
    def __init__(self, context: ssl.SSLContext, *args: Any, **kwargs: Any) -> None:
        self._context = context
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        kwargs["ssl_context"] = self._context
        super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, proxy: str, **proxy_kwargs: Any) -> Any:
        proxy_kwargs["ssl_context"] = self._context
        return super().proxy_manager_for(proxy, **proxy_kwargs)


def _sokcho_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=certifi.where())
    context.load_verify_locations(cadata=SOKCHO_SECTIGO_INTERMEDIATE_PEM)
    context.set_ciphers("DEFAULT:@SECLEVEL=1")
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def configure_sokcho_verified_session(current: requests.Session) -> requests.Session:
    """Mount the verified legacy-TLS adapter on an existing safe session."""

    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    current.mount(
        f"https://{SOKCHO_CENTER_HOST}/",
        _PinnedTLSAdapter(_sokcho_ssl_context(), max_retries=0),
    )
    return current


def _default_session_factory() -> requests.Session:
    return configure_sokcho_verified_session(requests.Session())


def _default_fetcher(current: Any, url: str, timeout: int) -> Any:
    last_error: Optional[Exception] = None
    for _attempt in range(3):
        try:
            response = current.get(url, timeout=timeout, allow_redirects=True)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _safe_https_url(value: Any) -> bool:
    parsed = urlparse(_clean(value))
    return bool(
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").lower()
        in {SOKCHO_CENTER_HOST, SOKCHO_LIBRARY_HOST, SOKCHO_GWE_HOST}
        and not parsed.username
        and not parsed.password
    )


def _coerce_page(value: Any, requested_url: str, *, allow_redirect: bool) -> FetchedPage:
    if isinstance(value, FetchedPage):
        page = value
    elif isinstance(value, BeautifulSoup):
        page = FetchedPage(value, requested_url, requested_url, False)
    elif isinstance(value, (str, bytes, bytearray)):
        if not value:
            raise ValueError("empty HTML response")
        page = FetchedPage(BeautifulSoup(value, "lxml"), requested_url, requested_url, False)
    else:
        status = int(getattr(value, "status_code", 200))
        if status != 200:
            raise ValueError(f"unexpected HTTP status {status}")
        content = getattr(value, "content", None)
        if content is None:
            content = getattr(value, "text", None)
        if not content:
            raise ValueError("empty HTML response")
        final_url = _clean(getattr(value, "url", "")) or requested_url
        redirected = bool(getattr(value, "history", None)) or _canonical_url(final_url) != _canonical_url(requested_url)
        page = FetchedPage(
            BeautifulSoup(content, "lxml"), requested_url, final_url, redirected
        )
    if not _safe_https_url(page.requested_url) or not _safe_https_url(page.final_url):
        raise ValueError("unsafe response URL")
    requested_host = (urlparse(page.requested_url).hostname or "").lower()
    final_host = (urlparse(page.final_url).hostname or "").lower()
    if requested_host != final_host:
        raise ValueError("cross-host redirect is not accepted")
    if page.redirected and not allow_redirect:
        raise ValueError("unexpected HTTP redirect")
    return page


def _fetch_many(
    items: list[tuple[Any, str, bool]],
    *,
    fetcher: Fetcher,
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
) -> tuple[dict[Any, FetchedPage], list[str]]:
    if not items:
        return {}, []
    workers = max(1, min(int(max_workers or 1), len(items)))
    chunks: list[list[tuple[Any, str, bool]]] = [[] for _ in range(workers)]
    for index, item in enumerate(items):
        chunks[index % workers].append(item)

    def run(chunk: list[tuple[Any, str, bool]]) -> tuple[dict[Any, FetchedPage], list[str]]:
        values: dict[Any, FetchedPage] = {}
        errors: list[str] = []
        current = session_factory()
        try:
            for key, url, allow_redirect in chunk:
                try:
                    raw = fetcher(current, url, timeout)
                    values[key] = _coerce_page(raw, url, allow_redirect=allow_redirect)
                except Exception as exc:
                    errors.append(f"{key}: {type(exc).__name__}: {_clean(exc)}")
        finally:
            _close_quietly(current)
        return values, errors

    values: dict[Any, FetchedPage] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(run, chunk) for chunk in chunks if chunk]
        for future in as_completed(futures):
            current_values, current_errors = future.result()
            values.update(current_values)
            errors.extend(current_errors)
    return values, errors


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _center_last_page(partition: SokchoCenterPartition, soup: BeautifulSoup) -> tuple[int, list[str]]:
    errors: list[str] = []
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if "강의목록" not in title or SOKCHO_CENTER_SITE_TITLE not in title:
        errors.append(f"{partition.code}: wrong catalogue shell")
    form = soup.select_one("form")
    if form is None:
        errors.append(f"{partition.code}: search form missing")
    else:
        control = form.select_one(f"[name='{partition.query_name}']")
        if control is None or _clean(control.get("value")) != partition.query_value:
            errors.append(f"{partition.code}: partition marker mismatch")
    current_node = soup.select_one("nav.pg_wrap .pg_current")
    if current_node is not None and _clean(current_node.get_text(" ", strip=True)) != "1":
        errors.append(f"{partition.code}: bootstrap is not page one")
    pages = [1]
    for link in soup.select("nav.pg_wrap a[href]"):
        parsed = parse_qs(urlparse(urljoin(partition.list_url(), link.get("href"))).query)
        value = _clean((parsed.get("page") or [""])[0])
        if value.isdigit() and int(value) >= 1:
            pages.append(int(value))
    return max(pages), errors


def _center_status(label: str, apply_start: date, apply_end: date, cutoff: date) -> str:
    normalized = _clean(label)
    if normalized in _FULL_LABELS:
        return "FULL"
    if normalized in _CLOSED_LABELS or any(token in normalized for token in ("마감", "종료")):
        return "CLOSED"
    if normalized not in _ACTIVE_LABELS and normalized not in _SCHEDULED_LABELS:
        raise ValueError(f"unknown centre application label {normalized!r}")
    if cutoff < apply_start:
        return "SCHEDULED"
    if cutoff > apply_end:
        return "CLOSED"
    return "OPEN"


def _center_application_identity(
    partition: SokchoCenterPartition, href: Any
) -> tuple[str, str]:
    value = urljoin(partition.list_url(), _clean(href))
    parsed = urlparse(value)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").lower() != SOKCHO_CENTER_HOST
        or parsed.path != "/lecture/reserve_write.php"
    ):
        raise ValueError("centre application link is not on the official endpoint")
    query = parse_qs(parsed.query)
    identity = _clean((query.get(partition.identity_name) or [""])[0])
    if not _CENTER_ID_RE.fullmatch(identity):
        raise ValueError("centre application identity missing")
    if _clean((query.get(partition.query_name) or [""])[0]) != partition.query_value:
        raise ValueError("centre application partition mismatch")
    other_identity = "lco_idx" if partition.identity_name == "lc_idx" else "lc_idx"
    if any(_clean(item) for item in query.get(other_identity, [])):
        raise ValueError("centre application contains conflicting identities")
    return identity, value


def _center_semantic_identity(
    partition: SokchoCenterPartition,
    title: str,
    start: date,
    end: date,
    schedule: str,
    venue: str,
) -> str:
    evidence = "|".join(
        (
            partition.code,
            _normalized(title),
            start.isoformat(),
            end.isoformat(),
            _normalized(schedule),
            _normalized(venue),
        )
    )
    return "semantic-" + hashlib.sha256(evidence.encode("utf-8")).hexdigest()[:24]


def _center_row(
    partition: SokchoCenterPartition,
    item: Any,
    *,
    page: int,
    cutoff: date,
) -> tuple[Optional[dict[str, Any]], list[str]]:
    errors: list[str] = []
    title_node = item.select_one(".list_div_title .title")
    numbered_title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
    match = _CENTER_SEQUENCE_RE.fullmatch(numbered_title)
    if not match:
        return None, [f"{partition.code} page {page}: invalid source sequence/title"]
    sequence = int(match.group(1))
    title = _clean(match.group(2))
    expected_min = (page - 1) * SOKCHO_PAGE_SIZE + 1
    expected_max = page * SOKCHO_PAGE_SIZE
    if not expected_min <= sequence <= expected_max:
        errors.append(f"{partition.code} page {page}: sequence outside page range")
    if not title or len(title) > 250:
        errors.append(f"{partition.code} page {page}: invalid title")

    pairs = _center_pairs(item)
    missing = _CENTER_REQUIRED_LABELS - set(pairs)
    if missing:
        errors.append(
            f"{partition.code} {sequence}: required inline detail fields missing"
        )
    if _clean(pairs.get("강의구분")) != partition.name:
        errors.append(f"{partition.code} {sequence}: inline category mismatch")
    try:
        start, end = _date_bounds(pairs.get("강의기간"), "centre course period")
        apply_start, apply_end = _date_bounds(
            pairs.get("접수기간"), "centre application period"
        )
    except ValueError as exc:
        errors.append(f"{partition.code} {sequence}: {_clean(exc)}")
        return None, errors

    action = item.select_one(".list_div_title .but a[href]")
    label = _clean(action.get_text(" ", strip=True) if action else "")
    if action is None:
        errors.append(f"{partition.code} {sequence}: course-bound application control missing")
        return None, errors
    try:
        status = _center_status(label, apply_start, apply_end, cutoff)
        application_identity = ""
        application_entry = ""
        source_identity_contract = ""
        if label in _CLOSED_LABELS | _FULL_LABELS:
            if (
                _clean(action.get("href")) != "#"
                or _clean(action.get("title")) != f"{label} 가기"
                or _clean(action.get("onclick"))
            ):
                raise ValueError("terminal centre application control changed")
            source_identity_contract = "semantic_inline_terminal_control"
        else:
            application_identity, application_entry = _center_application_identity(
                partition, action.get("href")
            )
            source_identity_contract = "semantic_inline_plus_application_identity"
    except ValueError as exc:
        errors.append(f"{partition.code} {sequence}: {_clean(exc)}")
        return None, errors
    current_capacity, total_capacity = _center_capacity(pairs.get("정원/신청"))
    if current_capacity is None or total_capacity is None or total_capacity <= 0:
        errors.append(f"{partition.code} {sequence}: invalid capacity")
    if not item.select_one("table[summary*='강의주요정보'], table[summary*='강의세부정보']"):
        errors.append(f"{partition.code} {sequence}: inline detail table missing")
    schedule = _clean(pairs.get("강의시간"))
    venue = _clean(pairs.get("강의장소"))
    identity = _center_semantic_identity(
        partition,
        title,
        start,
        end,
        schedule,
        venue,
    )
    raw_url = application_entry or f"{partition.list_url(page)}#list_{sequence}"
    terminal_control = not application_identity

    row: dict[str, Any] = {
        "provider": SOKCHO_PROVIDER,
        "provider_course_id": _course_id(partition.code, identity),
        "title": title,
        "branch": SOKCHO_CENTER_BRANCH,
        "branch_code": partition.code,
        "preserve_branch": True,
        "category": partition.name,
        "raw_url": raw_url,
        "application_url": "",
        "application_type": "INFO_ONLY",
        "reservation_available": False,
        "status": status,
        "fee": _fee(pairs.get("수강료")),
        "period": _range_text(start, end),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": _range_text(apply_start, apply_end),
        "apply_start_date": apply_start.isoformat(),
        "apply_end_date": apply_end.isoformat(),
        "schedule_raw": schedule,
        "target": _clean(pairs.get("모집제한")) or "제한 없음",
        "capacity": _clean(pairs.get("정원/신청")),
        "capacity_current": current_capacity,
        "capacity_total": total_capacity,
        "room": venue,
        "venue_name": " ".join(
            part for part in (SOKCHO_CENTER_BRANCH, venue) if part
        ),
        "address": SOKCHO_CENTER_ADDRESS,
        "venue_address": SOKCHO_CENTER_ADDRESS,
        "program_type": "강좌",
        "collection_category": "교육",
        "domain_category": "교육·강좌",
        "source_group": "municipal_reservation",
        "operator_type": "지자체/공공기관",
        "collection_type": "static_html",
        "application_method_raw": _clean(pairs.get("선발기준")),
        "municipality_code": SOKCHO_MUNICIPALITY_CODE,
        "municipality_name": SOKCHO_MUNICIPALITY_NAME,
        "raw_fields": {
            "parser": SOKCHO_PARSER,
            "source_kind": partition.code,
            "source_authority": "속초시",
            "identity": identity,
            "application_identity": application_identity,
            "source_identity_contract": source_identity_contract,
            "source_page": page,
            "source_sequence": sequence,
            "source_status": status,
            "source_control_label": label,
            "inline_detail_verified": True,
            "detail_verified": terminal_control,
            "application_control_verified": terminal_control,
        },
    }
    return row, errors


def _parse_center_page(
    partition: SokchoCenterPartition,
    soup: BeautifulSoup,
    *,
    page: int,
    cutoff: date,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if "강의목록" not in title or SOKCHO_CENTER_SITE_TITLE not in title:
        errors.append(f"{partition.code} page {page}: wrong catalogue shell")
    form = soup.select_one("form")
    marker = form.select_one(f"[name='{partition.query_name}']") if form else None
    if marker is None or _clean(marker.get("value")) != partition.query_value:
        errors.append(f"{partition.code} page {page}: partition marker mismatch")
    for item in soup.select(".list_div"):
        row, row_errors = _center_row(partition, item, page=page, cutoff=cutoff)
        errors.extend(row_errors)
        if row is not None:
            rows.append(row)
    sequences = [int(row["raw_fields"]["source_sequence"]) for row in rows]
    expected = list(
        range((page - 1) * SOKCHO_PAGE_SIZE + 1, (page - 1) * SOKCHO_PAGE_SIZE + 1 + len(rows))
    )
    if sequences != expected:
        errors.append(f"{partition.code} page {page}: source sequence gap/reorder")
    return rows, errors


def _center_nav_last(soup: BeautifulSoup) -> int:
    pages = [1]
    for link in soup.select("nav.pg_wrap a[href]"):
        value = _clean((parse_qs(urlparse(link.get("href", "")).query).get("page") or [""])[0])
        if value.isdigit() and int(value) >= 1:
            pages.append(int(value))
    current = soup.select_one("nav.pg_wrap .pg_current")
    current_value = _clean(current.get_text(" ", strip=True) if current else "")
    if current_value.isdigit():
        pages.append(int(current_value))
    return max(pages)


def _lecture_total(
    source: SokchoLectureSource, soup: BeautifulSoup, *, index: int
) -> tuple[int, int, int]:
    text = _clean(soup.get_text(" ", strip=True))
    if source.layout in {"city", "english"}:
        match = _CITY_TOTAL_RE.search(text)
        if not match:
            empty = soup.select(".list_box_wrap.culture > p.no_result")
            if (
                len(empty) == 1
                and _clean(empty[0].get_text(" ", strip=True)) == _CITY_EMPTY_TEXT
                and not _lecture_nodes(source, soup)
                and index == 0
            ):
                return 0, 1, 1
            raise ValueError(f"{source.code}: declared total/page marker missing")
        total, current, last = (
            int(item.replace(",", "")) for item in match.groups()
        )
        expected_last = max(1, math.ceil(total / SOKCHO_LECTURE_PAGE_SIZE))
        if current != index + 1 or last != expected_last:
            raise ValueError(f"{source.code}: declared page marker mismatch")
        return total, current, last
    match = _GWE_TOTAL_RE.search(text)
    if not match:
        raise ValueError(f"{source.code}: declared total missing")
    total = int(match.group(1).replace(",", ""))
    last = max(1, math.ceil(total / SOKCHO_LECTURE_PAGE_SIZE))
    return total, index + 1, last


def _lecture_nodes(source: SokchoLectureSource, soup: BeautifulSoup) -> list[Any]:
    selector = {
        "city": ".list_box",
        "english": ".lecture-item",
        "gwe": ".lecture_item",
    }[source.layout]
    return list(soup.select(selector))


def _lecture_title_and_url(
    source: SokchoLectureSource, item: Any
) -> tuple[str, str, str]:
    if source.layout == "city":
        title_node = item.select_one(".title .main_title") or item.select_one(".title")
        link = item.select_one(".title a[href*='/lecture-event/']")
    elif source.layout == "english":
        title_node = item.select_one(".lecture-item__title")
        link = item.select_one(".lecture-item__title a[href*='/lecture-event/']") or item.select_one(
            "a.lecture-item__cover[href*='/lecture-event/']"
        )
    else:
        title_node = item.select_one(".lecture_item__title")
        link = item.select_one(".lecture_item__title a[href*='/lecture-event/']")
    title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
    raw_url = urljoin(source.list_url, link.get("href") if link else "")
    parsed = urlparse(raw_url)
    match = _LECTURE_DETAIL_RE.search(parsed.path)
    identity = match.group(1) if match else ""
    if (
        not title
        or len(title) > 250
        or parsed.scheme.lower() != "https"
        or (parsed.hostname or "").lower() != source.host
        or not parsed.path.startswith(source.path_prefix)
        or not identity
    ):
        raise ValueError(f"{source.code}: invalid course identity/title link")
    return title, raw_url, identity


def _pair_value(pairs: Mapping[str, str], *labels: str) -> str:
    normalized = {_normalized(key): value for key, value in pairs.items()}
    for label in labels:
        value = _clean(normalized.get(_normalized(label)))
        if value:
            return value
    return ""


def _primary_control(source: SokchoLectureSource, item: Any) -> tuple[str, Optional[Any]]:
    selectors = {
        "city": ".applyButton, .reserveApplyButton, .btn--disabled",
        "english": ".applyButton, .reserveApplyButton, .btn--disabled",
        "gwe": ".applyStatusButton, .reserveStatusApplyButton, .prepare, .closed, .finish",
    }[source.layout]
    controls = []
    for node in item.select(selectors):
        label = _clean(node.get_text(" ", strip=True))
        if label in _ACTIVE_LABELS | _WAITLIST_LABELS | _SCHEDULED_LABELS | _CLOSED_LABELS:
            controls.append((label, node))
    if len(controls) > 1:
        raise ValueError(f"{source.code}: conflicting application controls")
    return controls[0] if controls else ("", None)


def _lecture_status(
    label: str,
    *,
    capacity_current: Optional[int],
    capacity_total: Optional[int],
    wait_current: Optional[int],
    wait_total: Optional[int],
    apply_start: date,
    apply_end: date,
    cutoff: date,
) -> str:
    if label in _ACTIVE_LABELS:
        status = "OPEN"
    elif label in _WAITLIST_LABELS:
        status = "WAITLIST"
    elif label in _SCHEDULED_LABELS:
        status = "SCHEDULED"
    elif label in _CLOSED_LABELS:
        status = "CLOSED"
    elif (
        capacity_current is not None
        and capacity_total is not None
        and capacity_current >= capacity_total
        and (
            wait_total in (None, 0)
            or (wait_current is not None and wait_current >= wait_total)
        )
    ):
        status = "FULL"
    else:
        raise ValueError("application state/control is ambiguous")
    if status in {"OPEN", "WAITLIST"} and not (apply_start <= cutoff <= apply_end):
        raise ValueError("active application control is outside source period")
    if status == "SCHEDULED" and cutoff > apply_start:
        raise ValueError("scheduled application control is after source start date")
    return status


def _lecture_row(
    source: SokchoLectureSource,
    item: Any,
    *,
    index: int,
    cutoff: date,
) -> tuple[Optional[dict[str, Any]], list[str]]:
    errors: list[str] = []
    try:
        title, raw_url, identity = _lecture_title_and_url(source, item)
        pairs = _dl_pairs(item)
        period_value = _pair_value(pairs, "강의기간", "운영기간")
        apply_value = _pair_value(pairs, "접수기간", "신청기간")
        start, end = _date_bounds(period_value, "library course period")
        apply_start, apply_end = _date_bounds(
            apply_value, "library application period"
        )
        capacity_text = _pair_value(pairs, "모집인원")
        current, total, wait_current, wait_total = _capacity(capacity_text)
        if current is None or total is None or total <= 0:
            raise ValueError("invalid library capacity")
        label, control = _primary_control(source, item)
        status = _lecture_status(
            label,
            capacity_current=current,
            capacity_total=total,
            wait_current=wait_current,
            wait_total=wait_total,
            apply_start=apply_start,
            apply_end=apply_end,
            cutoff=cutoff,
        )
        if control is not None and status in {"OPEN", "WAITLIST"}:
            control_identity = _clean(control.get("data-event-id"))
            if control_identity != identity:
                raise ValueError("list application control is not course-bound")
        if source.layout == "gwe":
            branch_node = item.select_one(".lecture_item__library")
            if _normalized(branch_node.get_text(" ", strip=True) if branch_node else "") != _normalized(source.branch):
                raise ValueError("GWE source branch mismatch")
    except ValueError as exc:
        return None, [f"{source.code} page {index + 1}: {_clean(exc)}"]

    room = _pair_value(pairs, "장소")
    target = _pair_value(pairs, "참가대상", "신청대상")
    fee_source = _pair_value(pairs, "참가비", "참가비/수강료")
    fee = _fee(fee_source) if fee_source else "요금 별도 안내"
    method = _pair_value(pairs, "모집방법", "신청방법")
    schedule = _pair_value(pairs, "운영주기", "운영시간", "강의시간")
    row: dict[str, Any] = {
        "provider": SOKCHO_PROVIDER,
        "provider_course_id": _course_id(source.code, identity),
        "title": title,
        "branch": source.branch,
        "branch_code": source.code,
        "preserve_branch": True,
        "category": "문화강좌" if source.layout != "gwe" else "교육프로그램",
        "raw_url": raw_url,
        "application_url": "",
        "application_type": "INFO_ONLY",
        "reservation_available": False,
        "status": status,
        "fee": fee,
        "period": _range_text(start, end),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": _range_text(apply_start, apply_end),
        "apply_start_date": apply_start.isoformat(),
        "apply_end_date": apply_end.isoformat(),
        "schedule_raw": schedule,
        "target": target or "대상 별도 안내",
        "capacity": capacity_text,
        "capacity_current": current,
        "capacity_total": total,
        "room": room,
        "venue_name": " ".join(part for part in (source.branch, room) if part),
        "address": source.address,
        "venue_address": source.address,
        "program_type": "강좌",
        "collection_category": "교육",
        "domain_category": "교육·강좌",
        "source_group": "library" if source.authority == "속초시" else "education_office",
        "operator_type": source.operator_type,
        "collection_type": "static_html",
        "application_method_raw": method,
        "municipality_code": SOKCHO_MUNICIPALITY_CODE,
        "municipality_name": SOKCHO_MUNICIPALITY_NAME,
        "raw_fields": {
            "parser": SOKCHO_PARSER,
            "source_kind": source.code,
            "source_authority": source.authority,
            "identity": identity,
            "source_page": index + 1,
            "source_sequence": 0,
            "source_status": status,
            "source_control_label": label or "FULL_NO_CONTROL",
            "fee_evidence": (
                "official_list"
                if fee_source
                else "official_list_field_absent"
            ),
            "target_evidence": (
                "official_list"
                if target
                else "official_list_field_absent"
            ),
            "schedule_evidence": (
                "official_list"
                if schedule
                else "official_list_field_absent"
            ),
            "inline_detail_verified": False,
            "detail_verified": False,
            "application_control_verified": False,
        },
    }
    return row, errors


def _parse_lecture_page(
    source: SokchoLectureSource,
    soup: BeautifulSoup,
    *,
    index: int,
    cutoff: date,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    expected_title = {
        "city": "속초시립도서관",
        "english": "어린이영어도서관",
        "gwe": "프로그램신청",
    }[source.layout]
    if expected_title not in title and not (source.layout == "gwe" and title == "전체"):
        errors.append(f"{source.code} page {index + 1}: wrong catalogue shell")
    for sequence, item in enumerate(_lecture_nodes(source, soup), start=1):
        row, row_errors = _lecture_row(source, item, index=index, cutoff=cutoff)
        errors.extend(row_errors)
        if row is not None:
            row["raw_fields"]["source_sequence"] = index * SOKCHO_LECTURE_PAGE_SIZE + sequence
            rows.append(row)
    return rows, errors


def _detail_title(source: SokchoLectureSource, soup: BeautifulSoup) -> str:
    selector = {
        "city": "h4.title .main_title, h4.title",
        "english": ".lecture-detail-title",
        "gwe": ".lecture_detail__title",
    }[source.layout]
    node = soup.select_one(selector)
    value = _clean(node.get_text(" ", strip=True) if node else "")
    if source.layout == "gwe":
        for label in _ACTIVE_LABELS | _WAITLIST_LABELS | _SCHEDULED_LABELS | _CLOSED_LABELS:
            if value.endswith(label):
                value = _clean(value[: -len(label)])
                break
    return value


def _detail_primary_control(
    source: SokchoLectureSource, soup: BeautifulSoup
) -> tuple[str, Optional[Any]]:
    selectors = {
        # The three live templates use different classes/IDs for disabled
        # states.  Selecting buttons and then applying the strict label
        # allowlist below is more stable and still excludes navigation.
        "city": "button",
        "english": "button",
        "gwe": "button",
    }[source.layout]
    controls: list[tuple[str, Any]] = []
    for node in soup.select(selectors):
        label = _clean(node.get_text(" ", strip=True))
        if label in _ACTIVE_LABELS | _WAITLIST_LABELS | _SCHEDULED_LABELS | _CLOSED_LABELS:
            controls.append((label, node))
    if len(controls) > 1:
        raise ValueError("conflicting detail application controls")
    return controls[0] if controls else ("", None)


def _validate_lecture_detail(
    source: SokchoLectureSource,
    row: dict[str, Any],
    page: FetchedPage,
    *,
    cutoff: date,
) -> list[str]:
    errors: list[str] = []
    identity = _clean(row.get("raw_fields", {}).get("identity"))
    parsed = urlparse(page.final_url)
    match = _LECTURE_DETAIL_RE.search(parsed.path)
    if (
        (parsed.hostname or "").lower() != source.host
        or not match
        or match.group(1) != identity
        or not parsed.path.startswith(source.path_prefix)
    ):
        return [f"{source.code}:{identity}: detail identity mismatch"]
    title = _detail_title(source, page.soup)
    if _normalized(title) != _normalized(row.get("title")):
        errors.append(f"{source.code}:{identity}: detail title mismatch")
    detail_root = (
        page.soup.select_one(".lecture_detail")
        or page.soup.select_one(".content-area")
        or page.soup.select_one("section.section")
        or page.soup
    )
    pairs = _dl_pairs(detail_root)
    detail_schedule = _pair_value(
        pairs,
        "운영주기",
        "운영시간",
        "강의시간",
    )
    if detail_schedule:
        row["schedule_raw"] = detail_schedule
        row["raw_fields"]["schedule_evidence"] = "official_detail"
    elif not _clean(row.get("schedule_raw")):
        row["schedule_raw"] = "시간 별도 안내"
        row["raw_fields"]["schedule_evidence"] = (
            "official_list_and_detail_field_absent"
        )
    try:
        start, end = _date_bounds(
            _pair_value(pairs, "강의기간", "운영기간"), "detail course period"
        )
        apply_start, apply_end = _date_bounds(
            _pair_value(pairs, "접수기간", "신청기간"),
            "detail application period",
        )
    except ValueError as exc:
        errors.append(f"{source.code}:{identity}: {_clean(exc)}")
        return errors
    if (start.isoformat(), end.isoformat()) != (
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
    ):
        errors.append(f"{source.code}:{identity}: detail course period mismatch")
    if (apply_start.isoformat(), apply_end.isoformat()) != (
        _clean(row.get("apply_start_date")),
        _clean(row.get("apply_end_date")),
    ):
        errors.append(f"{source.code}:{identity}: detail application period mismatch")
    capacity_text = _pair_value(pairs, "모집인원")
    current, total, wait_current, wait_total = _capacity(capacity_text)
    if (current, total) != (
        row.get("capacity_current"),
        row.get("capacity_total"),
    ):
        errors.append(f"{source.code}:{identity}: detail capacity mismatch")
    try:
        label, control = _detail_primary_control(source, page.soup)
        status = _lecture_status(
            label,
            capacity_current=current,
            capacity_total=total,
            wait_current=wait_current,
            wait_total=wait_total,
            apply_start=apply_start,
            apply_end=apply_end,
            cutoff=cutoff,
        )
    except ValueError as exc:
        errors.append(f"{source.code}:{identity}: {_clean(exc)}")
        return errors
    if status != _clean(row.get("status")):
        errors.append(f"{source.code}:{identity}: list/detail application-control mismatch")
    if control is not None and status in {"OPEN", "WAITLIST"}:
        control_identity = _clean(control.get("data-event-id"))
        if control_identity != identity:
            errors.append(f"{source.code}:{identity}: detail control is not course-bound")
        else:
            row["application_url"] = _clean(row.get("raw_url"))
            row["application_type"] = "ONLINE_RESERVATION"
            row["reservation_available"] = True
            row["raw_fields"]["application_control_verified"] = True
    else:
        row["application_url"] = ""
        row["application_type"] = "INFO_ONLY"
        row["reservation_available"] = False
        row["raw_fields"]["application_control_verified"] = status in {
            "SCHEDULED",
            "CLOSED",
            "FULL",
        }
    if source.layout == "gwe":
        detail_branch = _pair_value(pairs, "도서관")
        if _normalized(detail_branch) != _normalized(source.branch):
            errors.append(f"{source.code}:{identity}: detail branch mismatch")
    row["raw_fields"]["detail_verified"] = not errors
    return errors


def _validate_center_application(
    partition: SokchoCenterPartition,
    row: dict[str, Any],
    page: FetchedPage,
) -> list[str]:
    identity = _clean(row.get("raw_fields", {}).get("identity"))
    application_identity = _clean(
        row.get("raw_fields", {}).get("application_identity")
    )
    if not _CENTER_ID_RE.fullmatch(application_identity):
        return [f"{partition.code}:{identity}: application identity missing"]
    parsed = urlparse(page.final_url)
    if (parsed.hostname or "").lower() != SOKCHO_CENTER_HOST:
        return [f"{partition.code}:{identity}: application final host mismatch"]
    query = parse_qs(parsed.query)
    final_identity = _clean((query.get(partition.identity_name) or [""])[0])
    if final_identity != application_identity:
        return [f"{partition.code}:{identity}: application final identity mismatch"]
    if parsed.path not in {"/sci/pcc_seed.php", "/lecture/reserve_write.php"}:
        return [f"{partition.code}:{identity}: unexpected application endpoint"]
    form = None
    for candidate in page.soup.select("form"):
        action = urljoin(page.final_url, _clean(candidate.get("action")))
        if urlparse(action).path == "/lecture/reserve_write.php":
            form = candidate
            break
    hidden = form.select_one(f"input[name='{partition.identity_name}']") if form else None
    if hidden is None or _clean(hidden.get("value")) != application_identity:
        return [f"{partition.code}:{identity}: application identity form mismatch"]
    partition_input = form.select_one(f"input[name='{partition.query_name}']")
    if partition_input is None or _clean(partition_input.get("value")) != partition.query_value:
        return [f"{partition.code}:{identity}: application partition form mismatch"]
    text = _clean(page.soup.get_text(" ", strip=True))
    if "본인인증" not in text and parsed.path == "/sci/pcc_seed.php":
        return [f"{partition.code}:{identity}: identity-verification shell missing"]
    row["raw_fields"]["detail_verified"] = True
    row["raw_fields"]["application_control_verified"] = True
    if _clean(row.get("status")) in {"OPEN", "SCHEDULED"}:
        row["application_url"] = _clean(row.get("raw_url"))
        row["application_type"] = "ONLINE_RESERVATION"
        row["reservation_available"] = _clean(row.get("status")) == "OPEN"
    return []


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    identity = _clean(row.get("provider_course_id"))
    unknown = set(row) - _ALLOWED_ROW_KEYS
    if unknown:
        errors.append(f"{identity}: non-allowlisted row fields {sorted(unknown)!r}")
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping):
        errors.append(f"{identity}: raw_fields missing")
        return errors
    raw_unknown = set(raw) - _ALLOWED_RAW_KEYS
    if raw_unknown:
        errors.append(f"{identity}: non-allowlisted raw_fields {sorted(raw_unknown)!r}")
    blocked_keys = {
        "instructor",
        "phone",
        "email",
        "description",
        "attachments",
        "source_html",
        "applicant",
        "staff",
        "raw_text",
        "pairs",
    }
    if set(row) & blocked_keys or set(raw) & blocked_keys:
        errors.append(f"{identity}: PII/free-form field retained")

    human_text_keys = (
        "title",
        "branch",
        "category",
        "target",
        "room",
        "venue_name",
        "schedule_raw",
        "application_method_raw",
    )
    # Identifiers are hexadecimal and URLs/date ranges are numeric, so a
    # generic recursive phone regexp would produce false positives.  Scan only
    # human-facing allowlisted text; public institutional addresses are
    # explicitly allowed and no phone field exists in the contract.
    for value in (_clean(row.get(key)) for key in human_text_keys):
        if _EMAIL_RE.search(value):
            errors.append(f"{identity}: e-mail-like PII retained")
            break
        if _PHONE_RE.search(value):
            errors.append(f"{identity}: phone-like PII retained")
            break
    return errors


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "required_page_requests": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "source_total": 0,
        "source_rows": 0,
        "current_count": 0,
        "returned_count": 0,
        "expired_count": 0,
        "excluded_non_course_count": 0,
        "reservation_discovery_links": 0,
        "duplicate_identity_count": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "configured_collection_error": "",
        "municipality_code": SOKCHO_MUNICIPALITY_CODE,
        "municipality_name": SOKCHO_MUNICIPALITY_NAME,
        "canonical_candidate_id": SOKCHO_CANONICAL_CANDIDATE_ID,
        "canonical_url": SOKCHO_CANONICAL_URL,
        "discovery_shell_url": SOKCHO_DISCOVERY_SHELL_URL,
        "ownership_alias_urls": list(SOKCHO_OWNERSHIP_ALIAS_URLS),
        "superseded_provider_candidates": list(
            SOKCHO_SUPERSEDED_PROVIDER_CANDIDATES
        ),
        "excluded_non_course_urls": list(SOKCHO_EXCLUDED_NON_COURSE_URLS),
        "tls_verification": "CA+hostname+pinned_missing_intermediate+SECLEVEL1",
        "tls_intermediate_sha256": SOKCHO_SECTIGO_INTERMEDIATE_SHA256,
    }


def collect_sokcho_education_courses(
    target: Any,
    timeout: int = 30,
    max_pages: int = 200,
    detail_limit: int = 1000,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = SOKCHO_MAX_WORKERS,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    meta = _base_meta()
    errors: list[str] = []
    if not is_sokcho_education_target(target):
        meta["configured_collection_error"] = (
            "target is not the exact Sokcho canonical provider/ownership URL"
        )
        return [], SOKCHO_PARSER, meta
    cutoff = _today(today)
    request_timeout = max(1, int(timeout or 30))
    allowed_pages = max(0, int(max_pages or 0))
    allowed_details = max(0, int(detail_limit or 0))
    workers = max(1, min(int(max_workers or 1), SOKCHO_MAX_WORKERS))
    current_fetcher = fetcher or _default_fetcher
    current_factory = session_factory or _default_session_factory

    bootstrap_items: list[tuple[Any, str, bool]] = [
        (("center", item.code, 1, "data"), item.list_url(1), False)
        for item in SOKCHO_CENTER_PARTITIONS
    ]
    bootstrap_items.extend(
        (("lecture", item.code, 0, "data"), item.page_url(0), False)
        for item in SOKCHO_LECTURE_SOURCES
    )
    fetched, fetch_errors = _fetch_many(
        bootstrap_items,
        fetcher=current_fetcher,
        session_factory=current_factory,
        timeout=request_timeout,
        max_workers=workers,
    )
    errors.extend(fetch_errors)
    meta["pages"] += len(fetched)
    meta["list_requests"] += len(fetched)
    if errors or len(fetched) != len(bootstrap_items):
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], SOKCHO_PARSER, meta

    center_last: dict[str, int] = {}
    center_first_rows: dict[str, list[dict[str, Any]]] = {}
    center_first_signatures: dict[str, str] = {}
    for partition in SOKCHO_CENTER_PARTITIONS:
        page = fetched[("center", partition.code, 1, "data")]
        last, marker_errors = _center_last_page(partition, page.soup)
        center_last[partition.code] = last
        rows, row_errors = _parse_center_page(
            partition, page.soup, page=1, cutoff=cutoff
        )
        center_first_rows[partition.code] = rows
        center_first_signatures[partition.code] = _page_signature(rows)
        errors.extend(marker_errors)
        errors.extend(row_errors)
        if last > 1 and len(rows) != SOKCHO_PAGE_SIZE:
            errors.append(f"{partition.code}: non-last page size mismatch")
        if last < 1:
            errors.append(f"{partition.code}: invalid last page")

    lecture_totals: dict[str, int] = {}
    lecture_last: dict[str, int] = {}
    lecture_first_rows: dict[str, list[dict[str, Any]]] = {}
    lecture_first_signatures: dict[str, str] = {}
    for source in SOKCHO_LECTURE_SOURCES:
        page = fetched[("lecture", source.code, 0, "data")]
        try:
            total, current, last = _lecture_total(source, page.soup, index=0)
        except ValueError as exc:
            errors.append(_clean(exc))
            continue
        if current != 1:
            errors.append(f"{source.code}: bootstrap is not page one")
        rows, row_errors = _parse_lecture_page(
            source, page.soup, index=0, cutoff=cutoff
        )
        errors.extend(row_errors)
        expected = min(total, SOKCHO_LECTURE_PAGE_SIZE)
        if len(rows) != expected:
            errors.append(f"{source.code}: page-one row count mismatch")
        lecture_totals[source.code] = total
        lecture_last[source.code] = last
        lecture_first_rows[source.code] = rows
        lecture_first_signatures[source.code] = _page_signature(rows)

    if len(lecture_totals) != len(SOKCHO_LECTURE_SOURCES):
        errors.append("one or more library source totals are missing")
    required_page_requests = sum(last + 2 for last in center_last.values()) + sum(
        last + 2 for last in lecture_last.values()
    )
    meta["required_page_requests"] = required_page_requests
    if required_page_requests > allowed_pages:
        meta["source_cap_reached"] = True
        errors.append(
            f"max_pages cap allows {allowed_pages} of {required_page_requests} required source requests"
        )
    if errors:
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], SOKCHO_PARSER, meta

    remaining_items: list[tuple[Any, str, bool]] = []
    for partition in SOKCHO_CENTER_PARTITIONS:
        last = center_last[partition.code]
        remaining_items.extend(
            (("center", partition.code, page, "data"), partition.list_url(page), False)
            for page in range(2, last + 1)
        )
        remaining_items.extend(
            [
                (
                    ("center", partition.code, last + 1, "sentinel"),
                    partition.list_url(last + 1),
                    False,
                ),
                (
                    ("center", partition.code, 1, "recheck"),
                    partition.list_url(1),
                    False,
                ),
            ]
        )
    for source in SOKCHO_LECTURE_SOURCES:
        last = lecture_last[source.code]
        remaining_items.extend(
            (("lecture", source.code, index, "data"), source.page_url(index), False)
            for index in range(1, last)
        )
        remaining_items.extend(
            [
                (
                    ("lecture", source.code, last, "sentinel"),
                    source.page_url(last),
                    False,
                ),
                (
                    ("lecture", source.code, 0, "recheck"),
                    source.page_url(0),
                    False,
                ),
            ]
        )
    remaining, remaining_errors = _fetch_many(
        remaining_items,
        fetcher=current_fetcher,
        session_factory=current_factory,
        timeout=request_timeout,
        max_workers=workers,
    )
    fetched.update(remaining)
    errors.extend(remaining_errors)
    meta["pages"] += len(remaining)
    meta["list_requests"] += len(remaining)

    center_rows: list[dict[str, Any]] = []
    center_page_counts: dict[str, dict[int, int]] = {}
    center_source_counts: dict[str, int] = {}
    for partition in SOKCHO_CENTER_PARTITIONS:
        last = center_last[partition.code]
        page_counts: dict[int, int] = {}
        signatures: list[str] = []
        current_rows: list[dict[str, Any]] = []
        for page_number in range(1, last + 1):
            page = (
                fetched[("center", partition.code, 1, "data")]
                if page_number == 1
                else fetched.get(("center", partition.code, page_number, "data"))
            )
            if page is None:
                errors.append(f"{partition.code} page {page_number}: missing response")
                continue
            if _center_nav_last(page.soup) != last:
                errors.append(f"{partition.code} page {page_number}: last-page marker changed")
            rows = center_first_rows[partition.code] if page_number == 1 else _parse_center_page(
                partition, page.soup, page=page_number, cutoff=cutoff
            )[0]
            if page_number != 1:
                _, row_errors = _parse_center_page(
                    partition, page.soup, page=page_number, cutoff=cutoff
                )
                errors.extend(row_errors)
            expected = SOKCHO_PAGE_SIZE if page_number < last else len(rows)
            if page_number < last and len(rows) != expected:
                errors.append(f"{partition.code} page {page_number}: row count mismatch")
            if page_number == last and last > 1 and not (1 <= len(rows) <= SOKCHO_PAGE_SIZE):
                errors.append(f"{partition.code}: invalid final page size")
            page_counts[page_number] = len(rows)
            signature = _page_signature(rows)
            if rows:
                signatures.append(signature)
            current_rows.extend(rows)
        if len(signatures) != len(set(signatures)):
            errors.append(f"{partition.code}: duplicate non-empty page signature")
        sequences = [row["raw_fields"]["source_sequence"] for row in current_rows]
        if sequences != list(range(1, len(current_rows) + 1)):
            errors.append(f"{partition.code}: full source sequence gap/reorder")
        sentinel = fetched.get(("center", partition.code, last + 1, "sentinel"))
        recheck = fetched.get(("center", partition.code, 1, "recheck"))
        if sentinel is None or recheck is None:
            errors.append(f"{partition.code}: missing sentinel/recheck")
        else:
            sentinel_rows, sentinel_errors = _parse_center_page(
                partition, sentinel.soup, page=last + 1, cutoff=cutoff
            )
            recheck_rows, recheck_errors = _parse_center_page(
                partition, recheck.soup, page=1, cutoff=cutoff
            )
            errors.extend(sentinel_errors)
            errors.extend(recheck_errors)
            if sentinel_rows or _center_nav_last(sentinel.soup) != last:
                errors.append(f"{partition.code}: immediate post-last page is not empty")
            if (
                _center_nav_last(recheck.soup) != last
                or _page_signature(recheck_rows)
                != center_first_signatures[partition.code]
            ):
                errors.append(f"{partition.code}: page-one recheck changed")
        center_page_counts[partition.code] = page_counts
        center_source_counts[partition.code] = len(current_rows)
        center_rows.extend(current_rows)

    lecture_rows: list[dict[str, Any]] = []
    lecture_page_counts: dict[str, dict[int, int]] = {}
    for source in SOKCHO_LECTURE_SOURCES:
        total = lecture_totals[source.code]
        last = lecture_last[source.code]
        page_counts: dict[int, int] = {}
        current_rows: list[dict[str, Any]] = []
        signatures: list[str] = []
        for index in range(last):
            page = (
                fetched[("lecture", source.code, 0, "data")]
                if index == 0
                else fetched.get(("lecture", source.code, index, "data"))
            )
            if page is None:
                errors.append(f"{source.code} page {index + 1}: missing response")
                continue
            try:
                marker = _lecture_total(source, page.soup, index=index)
            except ValueError as exc:
                errors.append(_clean(exc))
                continue
            if marker != (total, index + 1, last):
                errors.append(f"{source.code} page {index + 1}: total/page marker changed")
            if index == 0:
                rows = lecture_first_rows[source.code]
            else:
                rows, row_errors = _parse_lecture_page(
                    source, page.soup, index=index, cutoff=cutoff
                )
                errors.extend(row_errors)
            expected = (
                SOKCHO_LECTURE_PAGE_SIZE
                if index < last - 1
                else total - SOKCHO_LECTURE_PAGE_SIZE * (last - 1)
            )
            if total == 0:
                expected = 0
            if len(rows) != expected:
                errors.append(f"{source.code} page {index + 1}: row count mismatch")
            page_counts[index + 1] = len(rows)
            signature = _page_signature(rows)
            if rows:
                signatures.append(signature)
            current_rows.extend(rows)
        if len(signatures) != len(set(signatures)):
            errors.append(f"{source.code}: duplicate non-empty page signature")
        if len(current_rows) != total:
            errors.append(f"{source.code}: declared total does not match parsed rows")
        sentinel = fetched.get(("lecture", source.code, last, "sentinel"))
        recheck = fetched.get(("lecture", source.code, 0, "recheck"))
        if sentinel is None or recheck is None:
            errors.append(f"{source.code}: missing sentinel/recheck")
        else:
            sentinel_rows, sentinel_errors = _parse_lecture_page(
                source, sentinel.soup, index=last, cutoff=cutoff
            )
            recheck_rows, recheck_errors = _parse_lecture_page(
                source, recheck.soup, index=0, cutoff=cutoff
            )
            errors.extend(sentinel_errors)
            errors.extend(recheck_errors)
            if sentinel_rows:
                errors.append(f"{source.code}: immediate post-last page is not empty")
            if source.layout == "gwe":
                try:
                    sentinel_total, _, _ = _lecture_total(
                        source, sentinel.soup, index=last
                    )
                except ValueError as exc:
                    errors.append(_clean(exc))
                else:
                    if sentinel_total != total:
                        errors.append(f"{source.code}: sentinel total changed")
            try:
                recheck_marker = _lecture_total(source, recheck.soup, index=0)
            except ValueError as exc:
                errors.append(_clean(exc))
            else:
                if (
                    recheck_marker != (total, 1, last)
                    or _page_signature(recheck_rows)
                    != lecture_first_signatures[source.code]
                ):
                    errors.append(f"{source.code}: page-one recheck changed")
        lecture_page_counts[source.code] = page_counts
        lecture_rows.extend(current_rows)

    identities = [
        f"{row['raw_fields']['source_kind']}:{row['raw_fields']['identity']}"
        for row in center_rows + lecture_rows
    ]
    duplicate_identity_count = len(identities) - len(set(identities))
    if duplicate_identity_count:
        errors.append(f"{duplicate_identity_count} duplicate source identities")
    excluded_non_course_rows = [
        row
        for row in lecture_rows
        if any(
            _normalized(title) == _normalized(row.get("title"))
            for title in SOKCHO_EXCLUDED_PRACTICE_TITLES
        )
    ]
    eligible_lecture_rows = [
        row for row in lecture_rows if row not in excluded_non_course_rows
    ]
    current_center_rows = [
        row
        for row in center_rows
        if date.fromisoformat(_clean(row.get("end_date"))) >= cutoff
    ]
    current_lecture_rows = [
        row
        for row in eligible_lecture_rows
        if date.fromisoformat(_clean(row.get("end_date"))) >= cutoff
    ]
    expired_count = (
        len(center_rows) + len(eligible_lecture_rows)
        - len(current_center_rows)
        - len(current_lecture_rows)
    )
    list_complete = not errors
    required_details = len(current_center_rows) + len(current_lecture_rows)
    if required_details > allowed_details:
        meta["source_cap_reached"] = True
        errors.append(
            f"detail_limit cap allows {allowed_details} of {required_details} required details"
        )

    detail_attempts = 0
    inline_terminal_detail_count = sum(
        not _clean(row.get("raw_fields", {}).get("application_identity"))
        for row in current_center_rows
    )
    detail_pages = inline_terminal_detail_count
    detail_errors: list[str] = []
    if list_complete and not errors:
        detail_items: list[tuple[Any, str, bool]] = []
        for row in current_center_rows:
            if not _clean(
                row.get("raw_fields", {}).get("application_identity")
            ):
                continue
            detail_items.append(
                (
                    (
                        "center_application",
                        row["raw_fields"]["source_kind"],
                        row["raw_fields"]["identity"],
                    ),
                    _clean(row.get("raw_url")),
                    True,
                )
            )
        for row in current_lecture_rows:
            detail_items.append(
                (
                    (
                        "lecture_detail",
                        row["raw_fields"]["source_kind"],
                        row["raw_fields"]["identity"],
                    ),
                    _clean(row.get("raw_url")),
                    False,
                )
            )
        detail_attempts = len(detail_items)
        details, fetch_detail_errors = _fetch_many(
            detail_items,
            fetcher=current_fetcher,
            session_factory=current_factory,
            timeout=request_timeout,
            max_workers=workers,
        )
        detail_errors.extend(fetch_detail_errors)
        center_by_key = {
            (row["raw_fields"]["source_kind"], row["raw_fields"]["identity"]): row
            for row in current_center_rows
        }
        lecture_by_key = {
            (row["raw_fields"]["source_kind"], row["raw_fields"]["identity"]): row
            for row in current_lecture_rows
        }
        for key, page in details.items():
            if key[0] == "center_application":
                row = center_by_key[(key[1], key[2])]
                item_errors = _validate_center_application(
                    _CENTER_BY_CODE[key[1]], row, page
                )
            else:
                row = lecture_by_key[(key[1], key[2])]
                item_errors = _validate_lecture_detail(
                    _LECTURE_BY_CODE[key[1]], row, page, cutoff=cutoff
                )
            if item_errors:
                detail_errors.extend(item_errors)
            else:
                detail_pages += 1
    errors.extend(detail_errors)
    details_complete = bool(
        list_complete
        and detail_pages == required_details
        and not detail_errors
    )

    current_rows = current_center_rows + current_lecture_rows
    result: list[dict[str, Any]] = []
    if list_complete and details_complete and not errors:
        for row in current_rows:
            errors.extend(_privacy_errors(row))
        if not errors:
            deduper = dedupe_rows or _dedupe_default
            result = list(deduper(current_rows))
            if len(result) != len(current_rows):
                errors.append(
                    f"dedupe changed complete row count {len(current_rows)} to {len(result)}"
                )
                result = []
    snapshot_complete = bool(list_complete and details_complete and not errors)
    if not snapshot_complete:
        result = []

    branch_counts = Counter(_clean(row.get("branch")) for row in result)
    status_counts = Counter(_clean(row.get("status")) for row in result)
    source_counts = Counter(
        _clean(row.get("raw_fields", {}).get("source_kind")) for row in result
    )
    source_total = len(center_rows) + sum(lecture_totals.values())
    meta.update(
        {
            "source_total": source_total,
            "source_rows": len(center_rows) + len(lecture_rows),
            "center_source_total": len(center_rows),
            "center_source_counts": center_source_counts,
            "lecture_source_totals": dict(lecture_totals),
            "center_last_pages": center_last,
            "lecture_last_pages": lecture_last,
            "center_page_counts": center_page_counts,
            "lecture_page_counts": lecture_page_counts,
            "current_count": len(current_rows),
            "returned_count": len(result),
            "expired_count": expired_count,
            "excluded_non_course_count": len(excluded_non_course_rows),
            "excluded_non_course_titles": [
                _clean(row.get("title")) for row in excluded_non_course_rows
            ],
            "detail_attempts": detail_attempts,
            "detail_pages": detail_pages,
            "inline_terminal_detail_count": inline_terminal_detail_count,
            "detail_errors": len(detail_errors),
            "reservation_discovery_links": sum(
                bool(row.get("application_url")) for row in result
            ),
            "duplicate_identity_count": duplicate_identity_count,
            "branch_count": len(branch_counts),
            "branch_counts": dict(branch_counts),
            "status_counts": dict(status_counts),
            "returned_source_counts": dict(source_counts),
            "pagination_detected": any(last > 1 for last in center_last.values())
            or any(last > 1 for last in lecture_last.values()),
            "pagination_complete": list_complete,
            "details_complete": details_complete,
            "snapshot_complete": snapshot_complete,
            "no_current_data": bool(snapshot_complete and not current_rows),
            "no_current_reason": (
                "all complete Sokcho education catalogues have ended"
                if snapshot_complete and not current_rows
                else ""
            ),
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
    )
    return result, SOKCHO_PARSER, meta


collect = collect_sokcho_education_courses
collect_sokcho_courses = collect_sokcho_education_courses


__all__ = [
    "FetchedPage",
    "SOKCHO_CANONICAL_CANDIDATE_ID",
    "SOKCHO_CANONICAL_URL",
    "SOKCHO_CENTER_PARTITIONS",
    "SOKCHO_DISCOVERY_SHELL_URL",
    "SOKCHO_EXCLUDED_NON_COURSE_URLS",
    "SOKCHO_EXCLUDED_PRACTICE_TITLES",
    "SOKCHO_LECTURE_SOURCES",
    "SOKCHO_MUNICIPALITY_CODE",
    "SOKCHO_MUNICIPALITY_NAME",
    "SOKCHO_OWNERSHIP_ALIAS_URLS",
    "SOKCHO_PARSER",
    "SOKCHO_PROVIDER",
    "SOKCHO_SECTIGO_INTERMEDIATE_SHA256",
    "SOKCHO_SUPERSEDED_PROVIDER_CANDIDATES",
    "SokchoCenterPartition",
    "SokchoLectureSource",
    "collect",
    "collect_sokcho_courses",
    "collect_sokcho_education_courses",
    "configure_sokcho_verified_session",
    "is_sokcho_education_target",
]
