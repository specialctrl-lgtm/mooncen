"""Fail-closed collector for Ansan's official education catalogues.

Ansan publishes municipal education through two official systems.  The
Lifelong Learning Center owns four real course catalogues (``nor``, ``reg``,
``mul`` and ``road``); its instructor bank and road-learning *place*
directory are not courses.  The integrated reservation system owns seven
education categories and also republishes some Lifelong Learning Center
courses.  This collector scans both systems completely and reconciles only
strong current/future semantic matches, preferring the originating lifelong
record while retaining every distinct reservation-system course.

Completeness is proved by advertised totals, every declared page, an
immediately empty sentinel and a stable page-one recheck for every catalogue.
The road-place directory is read only as official district evidence.  Every
   current/future course detail must agree with its list identity and dates.
   The sole exception is a non-open numeric legacy import for which the
   official site returns its exact unpublished/retired shell; that row remains
   information-only and never receives an application URL.  Open rows never
   receive this exception.

Both Ansan hosts currently need OpenSSL's legacy-server-connect option.  The
adapter below relaxes protocol interoperability only: CA and hostname
verification remain enabled.  Free-form descriptions, instructor names,
applicant data, staff contacts and attachments are deliberately not returned
or retained in ``raw_fields``.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
import gc
import hashlib
from html import unescape
import math
import re
import ssl
from threading import Lock, local
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter


ANSAN_PROVIDER = "MUNI_LLL_ANSAN_GO_KR_691646BE"
ANSAN_CANONICAL_CANDIDATE_ID = "MUNI_IR_348E08281517"
ANSAN_CANONICAL_URL = "https://lll.ansan.go.kr/web/cop/norEduList.do"
ANSAN_LLL_HOST = "lll.ansan.go.kr"
ANSAN_RESERVE_HOST = "reserve.ansan.go.kr"

# 4115000000 is Uijeongbu, not Ansan.  These are the official codes already
# used by the repository's municipal registry.
ANSAN_CITY_CODE = "4127000000"
ANSAN_SANGNOK_CODE = "4127100000"
ANSAN_DANWON_CODE = "4127300000"
ANSAN_MUNICIPALITY_NAMES = {
    ANSAN_CITY_CODE: "경기도 안산시",
    ANSAN_SANGNOK_CODE: "경기도 안산시 상록구",
    ANSAN_DANWON_CODE: "경기도 안산시 단원구",
}
ANSAN_COVERED_MUNICIPALITIES: tuple[dict[str, str], ...] = (
    {
        "code": ANSAN_CITY_CODE,
        "sido": "경기도",
        "sigungu": "안산시",
        "full_name": ANSAN_MUNICIPALITY_NAMES[ANSAN_CITY_CODE],
    },
    {
        "code": ANSAN_SANGNOK_CODE,
        "sido": "경기도",
        "sigungu": "안산시 상록구",
        "full_name": ANSAN_MUNICIPALITY_NAMES[ANSAN_SANGNOK_CODE],
    },
    {
        "code": ANSAN_DANWON_CODE,
        "sido": "경기도",
        "sigungu": "안산시 단원구",
        "full_name": ANSAN_MUNICIPALITY_NAMES[ANSAN_DANWON_CODE],
    },
)

ANSAN_LLL_PAGE_SIZE = 100
ANSAN_RESERVE_PAGE_SIZE = 10
ANSAN_MAX_WORKERS = 16
ANSAN_DETAIL_RETRY_WORKERS = 6
ANSAN_DETAIL_BATCH_SIZE = 64
ANSAN_FETCH_ATTEMPTS = 3
ANSAN_MAIN_CENTER = "안산시평생학습관"
ANSAN_MAIN_CENTER_ADDRESS = "경기도 안산시 상록구 차돌배기로 24-1(사동)"
ANSAN_PARSER = (
    "ansan_four_lifelong_catalogues+seven_reserve_categories+declared_totals+"
    "empty_sentinels+page1_rechecks+road_place_district_evidence+"
    "all_current_details+non_open_legacy_unpublished_shells+"
    "complete_open_legacy_list_only_fallback+"
    "bounded_detail_batches+strong_cross_source_reconciliation"
)


@dataclass(frozen=True)
class AnsanLifelongCatalogue:
    code: str
    name: str
    list_path: str
    detail_path: str
    identity_param: str
    identity_prefix: str

    @property
    def list_url(self) -> str:
        return f"https://{ANSAN_LLL_HOST}{self.list_path}"


ANSAN_LIFELONG_CATALOGUES: tuple[AnsanLifelongCatalogue, ...] = (
    AnsanLifelongCatalogue(
        "nor",
        "특별교육",
        "/web/cop/norEduList.do",
        "/web/cop/norEduDetail.do",
        "nId",
        "NOREDU_",
    ),
    AnsanLifelongCatalogue(
        "reg",
        "피움과정",
        "/web/cop/regEduList.do",
        "/web/cop/regEduDetail.do",
        "mId",
        "EDUMNG_",
    ),
    AnsanLifelongCatalogue(
        "mul",
        "다채움",
        "/web/cop/mulEduList.do",
        "/web/cop/mulEduDetail.do",
        "nId",
        "MULEDU_",
    ),
    AnsanLifelongCatalogue(
        "road",
        "길거리학습관/아파트학습관",
        "/web/cop/roadEduList.do",
        "/web/cop/roadEduDetail.do",
        "nId",
        "ROADMEDU_",
    ),
)
_LLL_BY_CODE = {item.code: item for item in ANSAN_LIFELONG_CATALOGUES}


@dataclass(frozen=True)
class AnsanReserveCategory:
    code: str
    name: str
    menu_no: str

    @property
    def list_path(self) -> str:
        return f"/edu/{self.code}/eduList.do"

    @property
    def detail_path(self) -> str:
        return f"/edu/{self.code}/eduView.do"


ANSAN_RESERVE_CATEGORIES: tuple[AnsanReserveCategory, ...] = (
    AnsanReserveCategory("E01", "외국어", "567"),
    AnsanReserveCategory("E02", "정보화", "603"),
    AnsanReserveCategory("E03", "음악", "604"),
    AnsanReserveCategory("E04", "미술", "614"),
    AnsanReserveCategory("E05", "체육", "615"),
    AnsanReserveCategory("E07", "과학", "703"),
    AnsanReserveCategory("E06", "기타", "616"),
)
_RESERVE_BY_CODE = {item.code: item for item in ANSAN_RESERVE_CATEGORIES}

# The reservation system republishes ``reg`` catalogue rows without the
# official lifelong catalogue's leading curriculum label.  The label is
# presentation metadata, not part of the course identity.  Keep this list
# deliberately closed so an arbitrary bracketed title cannot become a false
# cross-source match.
ANSAN_REG_TITLE_CATEGORY_PREFIXES = frozenset(
    {
        "기초생활문해",
        "직업능력",
        "문화예술",
        "인문교양",
    }
)


@dataclass(frozen=True)
class AnsanAlias:
    provider: str
    url: str
    reason: str
    ownership: str


ANSAN_NON_EXECUTING_ALIASES: tuple[AnsanAlias, ...] = (
    AnsanAlias(
        "MUNI_LLL_ANSAN_GO_KR_AE8DC75D",
        "https://lll.ansan.go.kr/web/cop/regEduList.do",
        "피움과정 is one of the canonical owner's four lifelong catalogues",
        "lifelong_catalogue_subset",
    ),
    AnsanAlias(
        "MUNI_RESERVE_ANSAN_GO_KR_8236CAF0",
        "https://reserve.ansan.go.kr/edu/E01/eduList.do?currentMenuNo=567",
        "the legacy owner fans out over the same seven reservation categories",
        "reserve_catalogue_component",
    ),
    AnsanAlias(
        "MUNI_RESERVE_ANSAN_GO_KR_5D6B8309",
        "https://reserve.ansan.go.kr/",
        "navigation shell for the reservation component of the canonical owner",
        "navigation_shell",
    ),
    AnsanAlias(
        "MUNI_RESERVE_ANSAN_GO_KR_4A4CC6B4",
        "https://reserve.ansan.go.kr/edu/E07/eduList.do?pageIndex=1",
        "science is one of the canonical owner's seven reservation categories",
        "reserve_category_subset",
    ),
)
ANSAN_EXCLUDED_NON_COURSE_URLS: tuple[tuple[str, str], ...] = (
    (
        "https://lll.ansan.go.kr/web/cop/roadEduPlaceList.do",
        "road_learning_place_directory_not_course_catalogue",
    ),
    (
        "https://lll.ansan.go.kr/web/cop/lectEduList.do",
        "instructor_bank_not_course_catalogue",
    ),
    (
        "https://reserve.ansan.go.kr/exp/X01/expList.do?currentMenuNo=667",
        "wrong_category_experience",
    ),
    (
        "https://reserve.ansan.go.kr/edu/E04/eduView.do?currentMenuNo=614&resrId=RESR_000000000002850",
        "single_historic_detail_not_catalogue",
    ),
)

ANSAN_CANDIDATE_AUDIT: Mapping[str, Mapping[str, str]] = {
    ANSAN_CANONICAL_CANDIDATE_ID: {
        "decision": "canonical_complete_owner",
        "provider": ANSAN_PROVIDER,
        "url": ANSAN_CANONICAL_URL,
        "owner": ANSAN_PROVIDER,
    },
    "MUNI_IR_201E1EBB44E3": {
        "decision": "lifelong_catalogue_subset",
        "provider": "MUNI_LLL_ANSAN_GO_KR_AE8DC75D",
        "url": "https://lll.ansan.go.kr/web/cop/regEduList.do",
        "owner": ANSAN_PROVIDER,
    },
    "MUNI_IR_C4AD132627A7": {
        "decision": "reservation_navigation_shell",
        "provider": "MUNI_RESERVE_ANSAN_GO_KR_5D6B8309",
        "url": "https://reserve.ansan.go.kr/",
        "owner": ANSAN_PROVIDER,
    },
    "MUNI_IR_6EFE5338C530": {
        "decision": "reservation_category_subset",
        "provider": "MUNI_RESERVE_ANSAN_GO_KR_4A4CC6B4",
        "url": "https://reserve.ansan.go.kr/edu/E07/eduList.do?pageIndex=1",
        "owner": ANSAN_PROVIDER,
    },
    "MUNI_IR_86CEDAFB8430": {
        "decision": "excluded_single_historic_detail",
        "provider": "MUNI_RESERVE_ANSAN_GO_KR_8236CAF0",
        "url": (
            "https://reserve.ansan.go.kr/edu/E04/eduView.do?"
            "currentMenuNo=614&resrId=RESR_000000000002850"
        ),
        "owner": ANSAN_PROVIDER,
    },
    "MUNI_IR_939B72D9FE14": {
        "decision": "road_place_directory_evidence_only",
        "provider": "MUNI_LLL_ANSAN_GO_KR_93F5BB79",
        "url": "https://lll.ansan.go.kr/web/cop/roadEduPlaceList.do",
        "owner": ANSAN_PROVIDER,
    },
    "MUNI_IR_452897AE0425": {
        "decision": "excluded_wrong_category_experience",
        "provider": "MUNI_RESERVE_ANSAN_GO_KR_02253999",
        "url": (
            "https://reserve.ansan.go.kr/exp/X01/expList.do?currentMenuNo=667"
        ),
        "owner": "MUNI_RESERVE_ANSAN_GO_KR_02253999",
    },
}


ANSAN_RAW_FIELD_ALLOWLIST = frozenset(
    {
        "parser",
        "source_kind",
        "source_catalogue",
        "source_category_code",
        "source_identity",
        "source_status",
        "source_page",
        "list_branch",
        "list_venue",
        "municipality_evidence",
        "application_control",
        "semantic_overlap_owner",
        "source_link_yn",
        "source_method",
        "terminal_excluded",
        "official_period_anomaly",
        "status_control_override",
        "target_source_omission",
        "schedule_source_omission",
    }
)

Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[./-](\d{1,2})[./-](\d{1,2})(?!\d)")
_TOTAL_RE = re.compile(r"전체\s*:?\s*([\d,]+)\s*건")
_LLL_PAGE_RE = re.compile(r"linkPage\(\s*(\d+)\s*\)")
_RESERVE_PAGE_RE = re.compile(r"fnSearch\(\s*(\d+)\s*\)")
_LLL_ID_RE = re.compile(r"fn_go_detail\(\s*['\"]([A-Z0-9_]+)['\"]\s*\)")
_RESERVE_ID_RE = re.compile(
    r"fnView\(\s*['\"](RESR_\d+|\d{6,20})['\"]\s*,\s*['\"]([NY])['\"]\s*\)"
)
_PLACE_NO_RE = re.compile(r"No\.\s*([\d,]+)")

_STATUS_MAP = {
    "교육접수중": "OPEN",
    "접수중": "OPEN",
    "신청가능": "OPEN",
    "대기자접수": "OPEN",
    "접수대기": "SCHEDULED",
    "교육접수대기": "SCHEDULED",
    "신청대기": "SCHEDULED",
    "접수예정": "SCHEDULED",
    "신청마감": "CLOSED",
    "접수마감": "CLOSED",
    "교육종료": "CLOSED",
    "교육진행중": "CLOSED",
    "교육마감": "CLOSED",
    "정원마감": "CLOSED",
    "마감": "CLOSED",
    "취소": "CLOSED",
    "폐강": "CLOSED",
}


class AnsanContractError(ValueError):
    """The official Ansan sources no longer match the audited contract."""


def _mark_required_source_omissions(rows: Iterable[dict[str, Any]]) -> None:
    for row in rows:
        raw_fields = row.get("raw_fields")
        if not isinstance(raw_fields, dict):
            continue
        if not _clean(row.get("target")):
            row["target"] = "공식 페이지 미기재"
            raw_fields["target_source_omission"] = True
        if not _clean(row.get("schedule_raw")):
            row["schedule_raw"] = "공식 페이지 시간 미기재"
            raw_fields["schedule_source_omission"] = True


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _decoded_text(value: Any) -> str:
    """Decode one extra official-source HTML-entity layer for display text."""

    return _clean(unescape(_clean(value)))


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", _decoded_text(value)).casefold()


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _dates(value: Any) -> list[date]:
    result: list[date] = []
    for year, month, day in _DATE_RE.findall(_clean(value)):
        try:
            result.append(date(int(year), int(month), int(day)))
        except ValueError as exc:
            raise AnsanContractError(f"invalid date in {_clean(value)}") from exc
    return result


def _date_range(value: Any) -> tuple[str, str, str]:
    values = _dates(value)
    if len(values) != 2:
        raise AnsanContractError(f"expected one date range in {_clean(value)}")
    if values[1] < values[0]:
        raise AnsanContractError(f"reversed date range in {_clean(value)}")
    return values[0].isoformat(), values[1].isoformat(), (
        f"{values[0].isoformat()} ~ {values[1].isoformat()}"
    )


def _normalize_status(value: Any) -> str:
    source = _clean(value)
    status = _STATUS_MAP.get(source)
    if not status:
        raise AnsanContractError(f"unknown source status {source}")
    return status


def _stable_id(source_kind: str, identity: str) -> str:
    token = f"{ANSAN_PROVIDER}|{source_kind}|{identity}"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:32]


def _single_query(parsed: Any, key: str) -> str:
    values = parse_qs(parsed.query, keep_blank_values=True).get(key) or []
    return _clean(values[0]) if len(values) == 1 else ""


def is_ansan_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != ANSAN_PROVIDER:
        return False
    parsed = urlparse(_clean(_target_value(target, "url")))
    return (
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == ANSAN_LLL_HOST
        and parsed.port is None
        and parsed.path == "/web/cop/norEduList.do"
        and not parsed.query
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


is_target = is_ansan_education_target


def ansan_lifelong_list_url(
    catalogue: AnsanLifelongCatalogue, page: int = 1
) -> str:
    if catalogue not in ANSAN_LIFELONG_CATALOGUES:
        raise ValueError("unknown Ansan lifelong catalogue")
    if not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    return catalogue.list_url + "?" + urlencode(
        (("pageUnit", ANSAN_LLL_PAGE_SIZE), ("pageIndex", page))
    )


def ansan_lifelong_detail_url(
    catalogue: AnsanLifelongCatalogue, identity: Any
) -> str:
    value = _clean(identity)
    if catalogue not in ANSAN_LIFELONG_CATALOGUES:
        raise ValueError("unknown Ansan lifelong catalogue")
    if not value.startswith(catalogue.identity_prefix) or not re.fullmatch(
        r"[A-Z]+_[0-9]{8,20}", value
    ):
        raise ValueError("invalid Ansan lifelong identity")
    return (
        f"https://{ANSAN_LLL_HOST}{catalogue.detail_path}?"
        + urlencode(((catalogue.identity_param, value),))
    )


def ansan_reserve_list_url(category: AnsanReserveCategory, page: int = 1) -> str:
    if category not in ANSAN_RESERVE_CATEGORIES:
        raise ValueError("unknown Ansan reservation category")
    if not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    return (
        f"https://{ANSAN_RESERVE_HOST}{category.list_path}?"
        + urlencode((("currentMenuNo", category.menu_no), ("pageIndex", page)))
    )


def ansan_reserve_detail_url(
    category: AnsanReserveCategory,
    identity: Any,
    link_yn: Any = "",
) -> str:
    value = _clean(identity)
    if category not in ANSAN_RESERVE_CATEGORIES:
        raise ValueError("unknown Ansan reservation category")
    native = bool(re.fullmatch(r"RESR_[0-9]{10,20}", value))
    legacy = bool(re.fullmatch(r"[0-9]{6,20}", value))
    if not native and not legacy:
        raise ValueError("invalid Ansan reservation identity")
    expected_link = "N" if native else "Y"
    supplied_link = _clean(link_yn).upper()
    if supplied_link and supplied_link != expected_link:
        raise ValueError("reservation identity/linkYn mismatch")
    query: list[tuple[str, str]] = [
        ("currentMenuNo", category.menu_no),
        ("resrId", value),
    ]
    if legacy:
        query.append(("linkYn", "Y"))
    return (
        f"https://{ANSAN_RESERVE_HOST}{category.detail_path}?"
        + urlencode(query)
    )


def ansan_road_place_list_url(page: int = 1) -> str:
    if not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    return (
        f"https://{ANSAN_LLL_HOST}/web/cop/roadEduPlaceList.do?"
        + urlencode((("pageUnit", ANSAN_LLL_PAGE_SIZE), ("pageIndex", page)))
    )


class _AnsanLegacyTLSAdapter(HTTPAdapter):
    """CA-validating adapter compatible with the two legacy Ansan hosts."""

    @staticmethod
    def context() -> ssl.SSLContext:
        context = ssl.create_default_context()
        context.set_ciphers("DEFAULT@SECLEVEL=1")
        context.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
        return context

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        kwargs["ssl_context"] = self.context()
        super().init_poolmanager(*args, **kwargs)


def ansan_session_factory() -> requests.Session:
    """Return a strict-verification session that negotiates Ansan TLS."""

    current = requests.Session()
    current.mount("https://", _AnsanLegacyTLSAdapter())
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
            "Referer": ANSAN_CANONICAL_URL,
        }
    )
    return current


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        if not value:
            raise AnsanContractError("empty HTML response")
        return BeautifulSoup(value, "lxml")
    status = getattr(value, "status_code", None)
    if status is not None and int(status) != 200:
        raise AnsanContractError(f"unexpected HTTP status {status}")
    headers = getattr(value, "headers", {}) or {}
    if headers.get("Location"):
        raise AnsanContractError("redirect response is not accepted")
    content = getattr(value, "content", None)
    if content is None:
        content = getattr(value, "text", None)
    if not content:
        raise AnsanContractError("empty HTML response")
    return BeautifulSoup(content, "lxml")


def _take_soup(values: dict[Any, Any], key: Any) -> Optional[BeautifulSoup]:
    value = values.pop(key, None)
    return _coerce_soup(value) if value is not None else None


def _fetch(
    fetcher: Optional[Fetcher], current_session: Any, url: str, timeout: int
) -> BeautifulSoup:
    last_error: Optional[Exception] = None
    for _attempt in range(ANSAN_FETCH_ATTEMPTS):
        try:
            if fetcher is None:
                response = current_session.get(
                    url, timeout=timeout, allow_redirects=False
                )
            else:
                response = fetcher(current_session, url, timeout)
            return _coerce_soup(response)
        except Exception as exc:  # bounded retries are part of the source contract
            last_error = exc
    raise AnsanContractError(
        f"request failed after {ANSAN_FETCH_ATTEMPTS} attempts"
    ) from last_error


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _pairs(node: Any, label_selector: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in node.select("li") if hasattr(node, "select") else []:
        label = item.select_one(label_selector)
        key = _clean(label.get_text(" ", strip=True) if label else "")
        if not key:
            continue
        value_node = item.select_one(".txt")
        if value_node is not None:
            value = _clean(value_node.get_text(" ", strip=True))
        else:
            clone = BeautifulSoup(str(item), "lxml")
            cloned_label = clone.select_one(label_selector)
            if cloned_label is not None:
                cloned_label.decompose()
            value = _clean(clone.get_text(" ", strip=True))
        if key in result and result[key] != value:
            raise AnsanContractError(f"duplicate conflicting label {key}")
        result[key] = value
    return result


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            row.get("raw_fields", {}).get("source_identity"),
            _clean(row.get("title")),
            _clean(row.get("start_date")),
            _clean(row.get("end_date")),
            row.get("raw_fields", {}).get("source_status"),
        )
        for row in rows
    )


def _contract_total_last(
    soup: BeautifulSoup, page_size: int, page_pattern: re.Pattern[str]
) -> tuple[int, int]:
    text = _clean(soup.get_text(" ", strip=True))
    match = _TOTAL_RE.search(text)
    if not match:
        raise AnsanContractError("missing advertised total")
    total = int(match.group(1).replace(",", ""))
    calculated = max(1, math.ceil(total / page_size))
    advertised = max([int(value) for value in page_pattern.findall(str(soup))] or [1])
    if advertised != calculated:
        raise AnsanContractError(
            f"advertised final page {advertised} != calculated {calculated}"
        )
    return total, advertised


def _observed_total(soup: BeautifulSoup) -> int:
    match = _TOTAL_RE.search(_clean(soup.get_text(" ", strip=True)))
    if not match:
        raise AnsanContractError("missing advertised total")
    return int(match.group(1).replace(",", ""))


def _main_directory_contract(soup: BeautifulSoup) -> None:
    public_course_paths: set[str] = set()
    for anchor in soup.select("a[href*='EduList.do']"):
        parsed = urlparse(_clean(anchor.get("href")))
        path = parsed.path
        if path.startswith("/web/member/"):
            continue
        if path.startswith("/web/cop/"):
            public_course_paths.add(path)
    expected = {item.list_path for item in ANSAN_LIFELONG_CATALOGUES} | {
        "/web/cop/lectEduList.do"
    }
    if public_course_paths != expected:
        raise AnsanContractError(
            f"lifelong public directory drift: {sorted(public_course_paths)}"
        )


def _reserve_directory_contract(soup: BeautifulSoup) -> None:
    options = [
        (_clean(option.get("value")), _clean(option.get_text(" ", strip=True)))
        for option in soup.select("select[name='searchClsfCd'] option")
    ]
    expected = [("all", "전체")] + [
        (item.code, item.name) for item in ANSAN_RESERVE_CATEGORIES
    ]
    if options != expected:
        raise AnsanContractError(f"reservation category directory drift: {options}")


def _lll_rows(
    soup: BeautifulSoup,
    catalogue: AnsanLifelongCatalogue,
    page: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for position, card in enumerate(soup.select(".list-board > .board_section"), 1):
        try:
            link = card.select_one(".info .tp > a")
            title = _decoded_text(
                link.get_text(" ", strip=True) if link else ""
            )
            if not title:
                raise AnsanContractError("missing lifelong title")
            status_node = card.select_one(".cate .cate_border") or card.select_one(
                ".cate .cate_bg"
            )
            status_source = _clean(
                status_node.get_text(" ", strip=True) if status_node else ""
            )
            status = _normalize_status(status_source)
            terminal_excluded = status_source in {"폐강", "취소"}
            values = _pairs(card.select_one(".bm") or card, "strong")
            period_source = _clean(values.get("교육기간 :") or values.get("교육기간"))
            period_anomaly = ""
            try:
                start_date, end_date, period = _date_range(period_source)
            except AnsanContractError:
                displayed_dates = _dates(period_source)
                if not terminal_excluded or len(displayed_dates) != 2:
                    raise
                lower, upper = min(displayed_dates), max(displayed_dates)
                start_date, end_date = lower.isoformat(), upper.isoformat()
                period = f"{start_date} ~ {end_date}"
                period_anomaly = "terminal_cancelled_reversed_display_period"
            schedule = _clean(
                " ".join(
                    value
                    for value in (
                        values.get("수강일 :") or values.get("수강일"),
                        values.get("시간 :") or values.get("시간"),
                    )
                    if value
                )
            )
            status_pairs = _pairs(card.select_one(".edu_status") or card, ".f")
            applied = _clean(status_pairs.get("신청"))
            capacity = _clean(status_pairs.get("정원"))
            venue = _clean(values.get("장소 :") or values.get("장소"))
            match = _LLL_ID_RE.search(_clean(link.get("href") if link else ""))
            identity = _clean(match.group(1) if match else "")
            source_method = "linked_detail"
            if not identity:
                if not (
                    terminal_excluded
                    and link
                    and "line-through" in (link.get("class") or [])
                    and _clean(link.get("onclick")) == "return false;"
                ):
                    raise AnsanContractError("missing lifelong detail identity")
                token = "|".join(
                    (
                        catalogue.code,
                        _normalized(title),
                        start_date,
                        end_date,
                        _normalized(schedule),
                        _normalized(venue),
                    )
                )
                identity = "CANCELLED_" + hashlib.sha256(
                    token.encode("utf-8")
                ).hexdigest()[:20].upper()
                source_method = "cancelled_list_identity"
            elif not identity.startswith(catalogue.identity_prefix) or not re.fullmatch(
                r"[A-Z]+_[0-9]{8,20}", identity
            ):
                raise AnsanContractError("invalid lifelong identity shape")
            apply = card.select_one(".btn_apply")
            apply_href = _clean(apply.get("href") if apply else "")
            expected_apply = f"fn_go_reply('{identity}')"
            has_application_control = expected_apply in apply_href
            if apply and not has_application_control:
                raise AnsanContractError("spoofed lifelong application control")
            detail_url = (
                ansan_lifelong_detail_url(catalogue, identity)
                if source_method == "linked_detail"
                else ansan_lifelong_list_url(catalogue, page)
            )
            branch = venue if catalogue.code == "road" and venue else ANSAN_MAIN_CENTER
            row = {
                "provider": ANSAN_PROVIDER,
                "provider_course_id": _stable_id("lifelong", identity),
                "title": title,
                "branch": branch,
                "branch_code": catalogue.code,
                "category": catalogue.name,
                "category_raw": catalogue.code,
                "raw_url": detail_url,
                "application_url": "",
                "status": status,
                "fee": "",
                "period": period,
                "start_date": start_date,
                "end_date": end_date,
                "apply_period": "",
                "schedule_raw": schedule,
                "target": _clean(
                    values.get("수강대상자 :") or values.get("수강대상자")
                ),
                "capacity": capacity,
                "capacity_current": int(re.sub(r"\D", "", applied) or 0),
                "capacity_total": int(re.sub(r"\D", "", capacity) or 0),
                "room": venue,
                "venue_name": branch,
                "venue_address": "",
                "address": "",
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "operator_type": "지자체/공공기관",
                "source_group": "municipal_reservation",
                "collection_type": "static_html",
                "program_type": "교육",
                "application_type": "INFORMATION_ONLY",
                "reservation_available": False,
                "raw_fields": {
                    "parser": ANSAN_PARSER,
                    "source_kind": "lifelong",
                    "source_catalogue": catalogue.code,
                    "source_identity": identity,
                    "source_status": status_source,
                    "source_page": page,
                    "source_method": source_method,
                    "terminal_excluded": terminal_excluded,
                    "official_period_anomaly": period_anomaly,
                    "list_branch": branch,
                    "list_venue": venue,
                    "application_control": {
                        "listed": has_application_control,
                        "type": "LOGIN_REQUIRED" if has_application_control else "NONE",
                    },
                },
                "_listed_application_control": has_application_control,
            }
            rows.append(row)
        except Exception as exc:
            errors.append(
                f"lifelong {catalogue.code} page {page} row {position}: "
                f"{type(exc).__name__}: {exc}"
            )
    return rows, errors


def _reserve_rows(
    soup: BeautifulSoup,
    category: AnsanReserveCategory,
    page: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for position, card in enumerate(soup.select("ul.blog.reserv > li"), 1):
        try:
            link = card.select_one("a[onclick*='fnView']")
            match = _RESERVE_ID_RE.search(_clean(link.get("onclick") if link else ""))
            identity = _clean(match.group(1) if match else "")
            link_yn = _clean(match.group(2) if match else "")
            title_node = card.select_one(".txtW .tit")
            title = _decoded_text(
                title_node.get_text(" ", strip=True) if title_node else ""
            )
            if not identity:
                raise AnsanContractError("missing reservation identity")
            status_source = _clean(
                card.select_one(".label").get_text(" ", strip=True)
                if card.select_one(".label")
                else ""
            )
            status = _normalize_status(status_source)
            source_method = (
                "native_reservation" if link_yn == "N" else "linked_external_legacy"
            )
            if not title:
                if status != "CLOSED":
                    raise AnsanContractError("non-closed reservation has blank title")
                title = f"[제목 미제공] {identity}"
                source_method = "historic_blank_title"
            values = _pairs(card.select_one(".txtW .etc") or card, ".em")
            start_date, end_date, period = _date_range(values.get("교육기간"))
            detail_url = ansan_reserve_detail_url(category, identity, link_yn)
            branch = _clean(values.get("기관/부서"))
            if not branch:
                raise AnsanContractError("missing reservation institution")
            capacity = ""
            row = {
                "provider": ANSAN_PROVIDER,
                "provider_course_id": _stable_id("reserve", identity),
                "title": title,
                "branch": branch,
                "branch_code": category.code,
                "category": category.name,
                "category_raw": category.code,
                "raw_url": detail_url,
                "application_url": "",
                "status": status,
                "fee": _clean(values.get("사용료")),
                "period": period,
                "start_date": start_date,
                "end_date": end_date,
                "apply_period": _clean(values.get("접수기간")),
                "schedule_raw": _clean(
                    " ".join(
                        value
                        for value in (values.get("요일"), values.get("교육시간"))
                        if value
                    )
                ),
                "target": _clean(values.get("대상")),
                "capacity": capacity,
                "capacity_current": None,
                "capacity_total": None,
                "room": _clean(values.get("위치")),
                "venue_name": _clean(values.get("위치")),
                "venue_address": "",
                "address": "",
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "operator_type": "지자체/공공기관",
                "source_group": "municipal_reservation",
                "collection_type": "static_html",
                "program_type": "교육",
                "application_type": "INFORMATION_ONLY",
                "reservation_available": False,
                "raw_fields": {
                    "parser": ANSAN_PARSER,
                    "source_kind": "reserve",
                    "source_category_code": category.code,
                    "source_identity": identity,
                    "source_link_yn": link_yn,
                    "source_method": source_method,
                    "terminal_excluded": False,
                    "official_period_anomaly": "",
                    "source_status": status_source,
                    "source_page": page,
                    "list_branch": branch,
                    "list_venue": _clean(values.get("위치")),
                    "application_control": {"listed": False, "type": "UNKNOWN"},
                },
            }
            rows.append(row)
        except Exception as exc:
            errors.append(
                f"reserve {category.code} page {page} row {position}: "
                f"{type(exc).__name__}: {exc}"
            )
    return rows, errors


def _road_place_rows(
    soup: BeautifulSoup, page: int
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for position, card in enumerate(
        soup.select(".board_section.board_single.map_view"), 1
    ):
        try:
            number_match = _PLACE_NO_RE.search(_clean(card.get_text(" ", strip=True)))
            number = int(number_match.group(1).replace(",", "")) if number_match else 0
            title_node = card.select_one(".info .tp a")
            title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
            values = _pairs(card.select_one(".bm") or card, "strong")
            address = _clean(values.get("주소 :") or values.get("주소"))
            if number < 1 or not title or not address:
                raise AnsanContractError("incomplete road-place identity")
            rows.append(
                {
                    "number": number,
                    "title": title,
                    "address": address,
                    "page": page,
                }
            )
        except Exception as exc:
            errors.append(
                f"road-place page {page} row {position}: "
                f"{type(exc).__name__}: {exc}"
            )
    return rows, errors


def _road_place_contract(first_soup: BeautifulSoup) -> tuple[int, int]:
    first_rows, errors = _road_place_rows(first_soup, 1)
    if errors or not first_rows:
        raise AnsanContractError("road-place directory has no valid rows")
    total = max(row["number"] for row in first_rows)
    calculated = max(1, math.ceil(total / ANSAN_LLL_PAGE_SIZE))
    advertised = max(
        [int(value) for value in _LLL_PAGE_RE.findall(str(first_soup))] or [1]
    )
    if calculated != advertised:
        raise AnsanContractError(
            f"road-place final page {advertised} != calculated {calculated}"
        )
    return total, advertised


def _place_keys(value: Any) -> tuple[str, ...]:
    text = _clean(value)
    without_type = re.sub(r"^\[[^\]]+\]\s*", "", text)
    keys = {_normalized(text), _normalized(without_type)}
    return tuple(sorted(key for key in keys if key))


def _road_place_map(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        payload = {
            "title": _clean(row.get("title")),
            "address": _clean(row.get("address")),
        }
        for key in _place_keys(payload["title"]):
            existing = result.get(key)
            if existing and existing != payload:
                raise AnsanContractError(f"ambiguous road-place key {key}")
            result[key] = payload
    return result


def _municipality(
    *, address: Any, branch: Any, source: str
) -> tuple[str, str, dict[str, str]]:
    address_text = _clean(address)
    branch_text = _clean(branch)
    combined = f"{address_text} {branch_text}"
    has_sangnok = "상록구" in combined
    has_danwon = "단원구" in combined
    if has_sangnok and has_danwon:
        raise AnsanContractError("conflicting district evidence")
    if has_sangnok:
        code = ANSAN_SANGNOK_CODE
        method = "official_address_or_branch_district_token"
    elif has_danwon:
        code = ANSAN_DANWON_CODE
        method = "official_address_or_branch_district_token"
    else:
        code = ANSAN_CITY_CODE
        method = "official_citywide_catalogue_without_district_token"
    return (
        code,
        ANSAN_MUNICIPALITY_NAMES[code],
        {
            "source": source,
            "method": method,
            "code": code,
            "full_name": ANSAN_MUNICIPALITY_NAMES[code],
        },
    )


def _basic_detail_pairs(soup: BeautifulSoup) -> dict[str, str]:
    section = next(
        (
            node.parent
            for node in soup.select("h4.tit")
            if _clean(node.get_text(" ", strip=True)) == "강의 기본정보"
        ),
        None,
    )
    if section is None:
        raise AnsanContractError("missing lifelong basic-information section")
    result: dict[str, str] = {}
    for row in section.select(".board_write .row"):
        label = row.select_one(".div_th")
        value = row.select_one(".div_td")
        key = _clean(label.get_text(" ", strip=True) if label else "")
        item = _clean(value.get_text(" ", strip=True) if value else "")
        if not key:
            continue
        if key in result and result[key] != item:
            raise AnsanContractError(f"conflicting lifelong detail label {key}")
        result[key] = item
    if "교육기간" not in result:
        raise AnsanContractError("missing lifelong detail education period")
    return result


def _enrich_lifelong_detail(
    row: dict[str, Any],
    soup: BeautifulSoup,
    road_places: Mapping[str, Mapping[str, str]],
) -> list[str]:
    identity = _clean(row.get("raw_fields", {}).get("source_identity"))
    catalogue_code = _clean(row.get("raw_fields", {}).get("source_catalogue"))
    catalogue = _LLL_BY_CODE.get(catalogue_code)
    errors: list[str] = []
    if catalogue is None:
        return [f"detail {identity}: unknown lifelong catalogue"]
    try:
        card = soup.select_one(".board_section")
        if card is None:
            raise AnsanContractError("missing lifelong detail identity card")
        title_node = card.select_one(".info .tp h4, .info .tp a")
        detail_title = _normalized(
            title_node.get_text(" ", strip=True) if title_node else ""
        )
        list_title = _normalized(row.get("title"))
        if not detail_title or not (
            detail_title == list_title or detail_title.endswith(list_title)
        ):
            raise AnsanContractError("lifelong detail title mismatch")
        detail_status_source = _clean(
            card.select_one(".cate .cate_border").get_text(" ", strip=True)
            if card.select_one(".cate .cate_border")
            else ""
        )
        if _normalize_status(detail_status_source) != row.get("status"):
            raise AnsanContractError("lifelong detail status mismatch")

        card_values = _pairs(card.select_one(".bm") or card, "strong")
        detail_start, detail_end, _detail_period = _date_range(
            card_values.get("교육기간 :") or card_values.get("교육기간")
        )
        if (detail_start, detail_end) != (
            row.get("start_date"),
            row.get("end_date"),
        ):
            raise AnsanContractError("lifelong detail period mismatch")

        pairs = _basic_detail_pairs(soup)
        basic_start, basic_end, _basic_period = _date_range(pairs.get("교육기간"))
        if (basic_start, basic_end) != (detail_start, detail_end):
            raise AnsanContractError("lifelong basic-information period mismatch")

        apply_periods = [
            value
            for key, value in pairs.items()
            if ("신청기간" in key or "접수기간" in key) and _clean(value)
        ]
        row["apply_period"] = " | ".join(apply_periods)
        row["target"] = _clean(
            pairs.get("교육대상") or pairs.get("수강대상자") or row.get("target")
        )
        room = _clean(pairs.get("강의장") or row.get("room"))
        row["room"] = room
        row["fee"] = _clean(pairs.get("수강료"))
        material_fee = _clean(pairs.get("재료비"))
        if material_fee:
            row["material_fee"] = material_fee

        detail_control = next(
            (
                anchor
                for anchor in soup.select("a[href*='fn_go_reply']")
                if f"fn_go_reply('{identity}')" in _clean(anchor.get("href"))
                and _clean(anchor.get_text(" ", strip=True)) == "수강신청"
            ),
            None,
        )
        has_control = detail_control is not None
        listed_control = bool(row.pop("_listed_application_control", False))
        if row.get("status") == "OPEN" and not (has_control and listed_control):
            raise AnsanContractError("open lifelong row lacks two public controls")
        if row.get("status") != "OPEN" and (has_control or listed_control):
            raise AnsanContractError("non-open lifelong row exposes active control")
        if has_control:
            row["application_url"] = _clean(row.get("raw_url"))
            row["application_type"] = "ONLINE_LOGIN_REQUIRED"
            row["reservation_available"] = True

        if catalogue.code == "road":
            venue = _clean(
                pairs.get("강의장")
                or row.get("raw_fields", {}).get("list_venue")
                or row.get("branch")
            )
            matches = {
                key: road_places[key]
                for key in _place_keys(venue)
                if key in road_places
            }
            unique_places = {
                (_clean(item.get("title")), _clean(item.get("address")))
                for item in matches.values()
            }
            if len(unique_places) > 1:
                raise AnsanContractError("road course matches multiple places")
            if unique_places:
                place_title, address = next(iter(unique_places))
                branch = re.sub(r"^\[[^\]]+\]\s*", "", place_title)
                evidence_source = "roadEduPlaceList exact normalized venue"
            else:
                branch = venue
                address = ""
                evidence_source = "road course venue without place-directory match"
            row["branch"] = branch
            row["venue_name"] = branch
            row["venue_address"] = address
            row["address"] = address
            code, full_name, evidence = _municipality(
                address=address, branch=branch, source=evidence_source
            )
        else:
            footer_text = _clean(soup.get_text(" ", strip=True))
            if "상록구 차돌배기로 24-1" not in footer_text:
                raise AnsanContractError("lifelong main-center address evidence missing")
            row["branch"] = ANSAN_MAIN_CENTER
            row["venue_name"] = ANSAN_MAIN_CENTER
            row["venue_address"] = ANSAN_MAIN_CENTER_ADDRESS
            row["address"] = ANSAN_MAIN_CENTER_ADDRESS
            code, full_name, evidence = _municipality(
                address=ANSAN_MAIN_CENTER_ADDRESS,
                branch=ANSAN_MAIN_CENTER,
                source="official lifelong footer address",
            )
        row["municipality_code"] = code
        row["municipality_full_name"] = full_name
        raw_fields = row.get("raw_fields", {})
        raw_fields["municipality_evidence"] = evidence
        raw_fields["application_control"] = {
            "listed": listed_control,
            "detail": has_control,
            "type": "LOGIN_REQUIRED" if has_control else "NONE",
        }
    except Exception as exc:
        errors.append(f"detail {identity}: {type(exc).__name__}: {exc}")
    return errors


def _location_from_reserve_detail(soup: BeautifulSoup) -> str:
    for item in soup.select(".rsvPlace ul.loca > li"):
        label = item.select_one(".em")
        label_text = _clean(label.get_text(" ", strip=True) if label else "")
        if "위치" not in label_text:
            continue
        clone = BeautifulSoup(str(item), "lxml")
        cloned_label = clone.select_one(".em")
        if cloned_label is not None:
            cloned_label.decompose()
        return _clean(clone.get_text(" ", strip=True))
    return ""


def _capacity_pair(value: Any) -> tuple[Optional[int], Optional[int]]:
    numbers = [int(item.replace(",", "")) for item in re.findall(r"[\d,]+", _clean(value))]
    if len(numbers) == 1:
        return None, numbers[0]
    if len(numbers) != 2:
        return None, None
    return numbers[0], numbers[1]


def _safe_listed_external_control(value: Any) -> bool:
    parsed = urlparse(_clean(value))
    try:
        port = parsed.port
    except ValueError:
        return False
    hostname = (parsed.hostname or "").rstrip(".").lower()
    return bool(
        parsed.scheme.lower() == "https"
        and hostname
        and not hostname.startswith(("127.", "10.", "192.168."))
        and hostname != "localhost"
        and port in {None, 443}
        and not parsed.username
        and not parsed.password
        and not parsed.fragment
    )


def _is_exact_unpublished_legacy_detail_shell(
    row: Mapping[str, Any], soup: BeautifulSoup
) -> bool:
    """Recognize the exact official shell for a legacy linked identity."""

    raw_fields = row.get("raw_fields", {})
    identity = _clean(raw_fields.get("source_identity"))
    alerts = re.findall(
        r"alert\(\s*['\"]([^'\"]+)['\"]\s*\)",
        str(soup),
    )
    title = _clean(
        soup.title.get_text(" ", strip=True) if soup.title is not None else ""
    )
    page_text = _clean(soup.get_text(" ", strip=True))
    return bool(
        _clean(raw_fields.get("source_link_yn")) == "Y"
        and re.fullmatch(r"[0-9]{6,20}", identity)
        and soup.select_one(".listInfo .infoArea") is None
        and title == "안산시 통합예약시스템"
        and alerts == ["존재하지 않는 교육/강좌입니다."]
        and "경기도 안산시 단원구 화랑로 387" in page_text
        and "1666-1234" in page_text
    )


def _is_non_open_unpublished_legacy_detail(
    row: Mapping[str, Any], soup: BeautifulSoup
) -> bool:
    """Recognize the exact missing-detail shell for non-open legacy imports.

    The reservation catalogue contains numeric identities linked from other
    official Ansan systems.  Some scheduled identities have not been
    published yet and some closed identities have already been retired.  The
    list still supplies their structured identity, institution and periods.
    Only the exact official shell is accepted, and never for an open row.
    """

    return bool(
        row.get("status") in {"SCHEDULED", "CLOSED"}
        and _is_exact_unpublished_legacy_detail_shell(row, soup)
    )


def _is_complete_open_legacy_list_only_detail(
    row: Mapping[str, Any], soup: BeautifulSoup
) -> bool:
    """Accept an unavailable open detail only when its official list row is complete."""

    raw_fields = row.get("raw_fields", {})
    identity = _clean(raw_fields.get("source_identity"))
    category = _RESERVE_BY_CODE.get(_clean(row.get("branch_code")))
    expected_url = (
        ansan_reserve_detail_url(category, identity, "Y")
        if category is not None
        else ""
    )
    required_values = (
        row.get("title"),
        row.get("branch"),
        row.get("category_raw"),
        row.get("period"),
        row.get("start_date"),
        row.get("end_date"),
        row.get("apply_period"),
        row.get("schedule_raw"),
        row.get("target"),
        row.get("fee"),
        row.get("venue_name"),
    )
    return bool(
        row.get("status") == "OPEN"
        and _clean(raw_fields.get("source_status")) == "접수중"
        and _clean(raw_fields.get("source_kind")) == "reserve"
        and _clean(raw_fields.get("source_method")) == "linked_external_legacy"
        and _clean(raw_fields.get("source_link_yn")) == "Y"
        and not row.get("_listed_application_control")
        and all(_clean(value) for value in required_values)
        and _clean(row.get("raw_url")) == expected_url
        and _is_exact_unpublished_legacy_detail_shell(row, soup)
    )


def _enrich_reserve_detail(
    row: dict[str, Any],
    soup: BeautifulSoup,
    *,
    allow_open_legacy_semantic_replica: bool = False,
) -> list[str]:
    identity = _clean(row.get("raw_fields", {}).get("source_identity"))
    errors: list[str] = []
    try:
        source_link_yn = _clean(
            row.get("raw_fields", {}).get("source_link_yn")
        )
        info = soup.select_one(".listInfo .infoArea")
        if info is None:
            is_non_open_shell = _is_non_open_unpublished_legacy_detail(row, soup)
            is_open_replica_shell = bool(
                allow_open_legacy_semantic_replica
                and row.get("status") == "OPEN"
                and _is_exact_unpublished_legacy_detail_shell(row, soup)
            )
            is_open_list_only_shell = bool(
                not is_open_replica_shell
                and _is_complete_open_legacy_list_only_detail(row, soup)
            )
            if is_non_open_shell or is_open_replica_shell or is_open_list_only_shell:
                code, full_name, evidence = _municipality(
                    address="",
                    branch=row.get("branch"),
                    source=(
                        "official legacy list; exact unpublished-detail shell"
                    ),
                )
                row["municipality_code"] = code
                row["municipality_full_name"] = full_name
                row["application_url"] = ""
                row["application_type"] = "INFORMATION_ONLY"
                row["reservation_available"] = False
                raw_fields = row.get("raw_fields", {})
                raw_fields["municipality_evidence"] = evidence
                if is_open_replica_shell:
                    raw_fields["status_control_override"] = (
                        "open_legacy_semantic_replica_missing_detail"
                    )
                elif is_open_list_only_shell:
                    row["status"] = "CLOSED"
                    raw_fields["status_control_override"] = (
                        "source_open_legacy_detail_unpublished_without_public_control"
                    )
                else:
                    raw_fields["status_control_override"] = (
                        "scheduled_legacy_detail_not_yet_published"
                        if row.get("status") == "SCHEDULED"
                        else "closed_legacy_detail_retired"
                    )
                raw_fields["application_control"] = {
                    "detail": False,
                    "type": "NONE",
                }
                return []
            raise AnsanContractError(
                "missing reservation detail info "
                f"(status={_clean(row.get('status'))}, "
                "source_status="
                f"{_clean(row.get('raw_fields', {}).get('source_status'))}, "
                f"linkYn={source_link_yn})"
            )
        if source_link_yn == "N":
            favorite = soup.select_one(f"[onclick*=\"fnFavorite('{identity}')\"]")
            if favorite is None:
                raise AnsanContractError("reservation detail identity mismatch")
        elif source_link_yn != "Y" or not identity.isdigit():
            raise AnsanContractError("invalid reservation detail source method")
        detail_title = _clean(
            info.select_one(".tit").get_text(" ", strip=True)
            if info.select_one(".tit")
            else ""
        )
        if _normalized(detail_title) != _normalized(row.get("title")):
            raise AnsanContractError("reservation detail title mismatch")
        detail_status_source = _clean(
            info.select_one(".label").get_text(" ", strip=True)
            if info.select_one(".label")
            else ""
        )
        detail_status = _normalize_status(detail_status_source)
        status_mismatch = detail_status != row.get("status")
        values = _pairs(info.select_one(".itemList") or info, ".em")
        detail_start, detail_end, _detail_period = _date_range(
            values.get("교육기간") or values.get("이용기간")
        )
        if (detail_start, detail_end) != (
            row.get("start_date"),
            row.get("end_date"),
        ):
            raise AnsanContractError("reservation detail period mismatch")
        if _normalized(values.get("기관/부서")) != _normalized(row.get("branch")):
            raise AnsanContractError("reservation detail institution mismatch")
        row["apply_period"] = _clean(values.get("접수기간"))
        row["target"] = _clean(values.get("대상") or row.get("target"))
        row["fee"] = _clean(values.get("사용료") or row.get("fee"))
        listed_schedule = _clean(row.get("schedule_raw"))
        row["schedule_raw"] = _clean(
            " ".join(
                value
                for value in (
                    values.get("요일") or listed_schedule,
                    values.get("교육시간"),
                    next(
                        iter(
                            re.findall(
                                r"[0-2]\d:[0-5]\d\s*~\s*[0-2]\d:[0-5]\d",
                                _clean(values.get("이용기간")),
                            )
                        ),
                        "",
                    ),
                )
                if value
            )
        )
        capacity = _clean(values.get("모집정원"))
        current, total = _capacity_pair(capacity)
        row["capacity"] = capacity
        row["capacity_current"] = current
        row["capacity_total"] = total
        address = _location_from_reserve_detail(soup)
        row["venue_address"] = address
        row["address"] = address
        facility_name = _clean(values.get("시설명"))
        if facility_name:
            row["room"] = facility_name
            row["venue_name"] = facility_name
        if address:
            row["venue_name"] = facility_name or _clean(row.get("branch"))
        code, full_name, evidence = _municipality(
            address=address,
            branch=row.get("branch"),
            source=(
                "reservation structured place address"
                if address
                else "reservation official institution"
            ),
        )
        row["municipality_code"] = code
        row["municipality_full_name"] = full_name

        native_control = info.select_one("#resvRqstBtn")
        native_control_text = _clean(
            native_control.get_text(" ", strip=True) if native_control else ""
        )
        has_native_control = bool(
            native_control
            and source_link_yn == "N"
            and native_control_text in {"예약신청", "대기신청"}
            and _clean(native_control.get("href")) == "#none"
            and _clean(native_control.get("onclick")) == "checkInTracer();"
        )
        external_control = next(
            (
                anchor
                for anchor in info.select("a[onclick*='fnCmbResvView']")
                if _clean(anchor.get_text(" ", strip=True)) == "예약신청"
            ),
            None,
        )
        external_match = re.search(
            r"fnCmbResvView\(\s*'([^']+)'",
            _clean(external_control.get("onclick") if external_control else ""),
        )
        has_external_control = bool(
            external_control
            and source_link_yn == "Y"
            and _clean(external_control.get("href")) in {"#this", "#none"}
            and external_match
            and _safe_listed_external_control(external_match.group(1))
        )
        has_control = has_native_control or has_external_control
        control = native_control or external_control
        if control is not None and not has_control:
            raise AnsanContractError("spoofed reservation application control")
        if source_link_yn == "N" and has_external_control:
            raise AnsanContractError("native reservation exposes legacy control")
        if source_link_yn == "Y" and has_native_control:
            raise AnsanContractError("legacy reservation exposes native control")
        closed_list_open_full_detail = bool(
            status_mismatch
            and row.get("status") == "CLOSED"
            and detail_status == "OPEN"
            and _clean(row.get("raw_fields", {}).get("source_status"))
            in {"접수마감", "신청마감"}
            and source_link_yn == "N"
            and current is not None
            and total is not None
            and total > 0
            and current >= total
            and not has_control
        )
        if status_mismatch and not closed_list_open_full_detail:
            raise AnsanContractError("reservation detail status mismatch")
        if has_control and row.get("status") == "OPEN":
            row["application_url"] = _clean(row.get("raw_url"))
            row["application_type"] = (
                "ONLINE_EXTERNAL_LINK"
                if has_external_control
                else "ONLINE_LOGIN_REQUIRED"
            )
            row["reservation_available"] = True
        raw_fields = row.get("raw_fields", {})
        if closed_list_open_full_detail:
            raw_fields["status_control_override"] = (
                "list_closed_detail_open_full_without_application_control"
            )
        elif row.get("status") == "OPEN" and not has_control:
            row["status"] = "CLOSED"
            raw_fields["status_control_override"] = (
                "source_open_without_public_application_control"
            )
        elif native_control_text == "대기신청":
            raw_fields["status_control_override"] = "waitlist_control_available"
        else:
            raw_fields["status_control_override"] = ""
        raw_fields["municipality_evidence"] = evidence
        raw_fields["application_control"] = {
            "detail": has_control,
            "type": (
                "EXTERNAL_OFFICIAL_LINK"
                if has_external_control
                else "LOGIN_REQUIRED" if has_native_control else "NONE"
            ),
        }
    except Exception as exc:
        errors.append(f"detail {identity}: {type(exc).__name__}: {exc}")
    return errors


def _semantic_title(row: Mapping[str, Any]) -> str:
    title = _clean(row.get("title"))
    raw_fields = row.get("raw_fields", {})
    if (
        raw_fields.get("source_kind") == "lifelong"
        and raw_fields.get("source_catalogue") == "reg"
    ):
        match = re.fullmatch(r"\[([^\[\]]{1,20})\]\s*(.+)", title)
        if match and _clean(match.group(1)) in ANSAN_REG_TITLE_CATEGORY_PREFIXES:
            title = _clean(match.group(2))
    return _normalized(title)


def _semantic_key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        _semantic_title(row),
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
    )


def _reconcile_cross_source_overlaps(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, str]]]:
    by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_key[_semantic_key(row)].append(row)
    dropped: set[str] = set()
    errors: list[str] = []
    overlaps: list[dict[str, str]] = []
    for key, members in by_key.items():
        lifelong = [
            item
            for item in members
            if item.get("raw_fields", {}).get("source_kind") == "lifelong"
        ]
        reserve = [
            item
            for item in members
            if item.get("raw_fields", {}).get("source_kind") == "reserve"
        ]
        if not lifelong or not reserve:
            continue
        if len(lifelong) != 1 or len(reserve) != 1:
            errors.append(f"ambiguous cross-source semantic overlap {key}")
            continue
        origin = lifelong[0]
        replica = reserve[0]
        if _normalized(replica.get("branch")) not in {
            _normalized("평생학습관"),
            _normalized(ANSAN_MAIN_CENTER),
        }:
            errors.append(f"cross-source collision is not a learning-center replica {key}")
            continue
        left_schedule = _normalized(origin.get("schedule_raw"))
        right_schedule = _normalized(replica.get("schedule_raw"))
        if (
            left_schedule
            and right_schedule
            and left_schedule != right_schedule
            and left_schedule not in right_schedule
            and right_schedule not in left_schedule
        ):
            errors.append(f"cross-source overlap schedule mismatch {key}")
            continue
        replica_id = _clean(replica.get("provider_course_id"))
        dropped.add(replica_id)
        origin["raw_fields"]["semantic_overlap_owner"] = {
            "preferred_source": "lifelong",
            "replica_source": "reserve",
            "replica_identity": _clean(
                replica.get("raw_fields", {}).get("source_identity")
            ),
        }
        overlaps.append(
            {
                "title": _clean(origin.get("title")),
                "lifelong_identity": _clean(
                    origin.get("raw_fields", {}).get("source_identity")
                ),
                "reserve_identity": _clean(
                    replica.get("raw_fields", {}).get("source_identity")
                ),
            }
        )
    return (
        [row for row in rows if _clean(row.get("provider_course_id")) not in dropped],
        errors,
        overlaps,
    )


def _default_dedupe(rows: list[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "required_list_requests": 0,
        "data_pages": 0,
        "sentinel_requests": 0,
        "stability_rechecks": 0,
        "source_total": 0,
        "source_rows": 0,
        "lifelong_total": 0,
        "reserve_total": 0,
        "road_place_total": 0,
        "lifelong_catalogue_totals": {},
        "reserve_category_totals": {},
        "source_status_counts": {},
        "current_count": 0,
        "expired_count": 0,
        "returned_count": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_retry_pages": 0,
        "detail_errors": 0,
        "scheduled_detail_unpublished_count": 0,
        "closed_detail_retired_count": 0,
        "open_legacy_replica_shell_count": 0,
        "open_legacy_list_only_shell_count": 0,
        "closed_list_open_full_detail_count": 0,
        "non_open_detail_shell_count": 0,
        "application_control_count": 0,
        "cross_source_overlap_count": 0,
        "cross_source_overlaps": [],
        "duplicate_identity_count": 0,
        "duplicate_url_count": 0,
        "duplicate_count": 0,
        "municipality_counts": {},
        "branch_counts": {},
        "pagination_detected": False,
        "pagination_complete": False,
        "partitions_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": "",
        "covered_municipalities": [
            dict(item) for item in ANSAN_COVERED_MUNICIPALITIES
        ],
        "ownership_alias_providers": [
            item.provider for item in ANSAN_NON_EXECUTING_ALIASES
        ],
        "worker_limit": ANSAN_MAX_WORKERS,
        "detail_retry_worker_limit": ANSAN_DETAIL_RETRY_WORKERS,
        "detail_batch_size": ANSAN_DETAIL_BATCH_SIZE,
    }


def _parallel_fetches(
    tasks: Mapping[Any, str],
    *,
    fetcher: Optional[Fetcher],
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
) -> tuple[dict[Any, bytes], list[str]]:
    thread_state = local()
    sessions: list[Any] = []
    sessions_lock = Lock()

    def current_session() -> Any:
        value = getattr(thread_state, "session", None)
        if value is None:
            value = session_factory()
            thread_state.session = value
            with sessions_lock:
                sessions.append(value)
        return value

    def run(item: tuple[Any, str]) -> tuple[Any, bytes]:
        key, url = item
        soup = _fetch(fetcher, current_session(), url, timeout)
        try:
            return key, soup.encode("utf-8")
        finally:
            soup.decompose()

    soups: dict[Any, bytes] = {}
    errors: list[str] = []
    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(run, item): item[0] for item in tasks.items()}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    result_key, soup = future.result()
                    soups[result_key] = soup
                except Exception as exc:
                    errors.append(f"fetch {key} failed ({type(exc).__name__})")
    finally:
        for value in sessions:
            _close_quietly(value)
    return soups, errors


def _parallel_detail_validations(
    rows: Mapping[str, dict[str, Any]],
    *,
    fetcher: Optional[Fetcher],
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
    batch_size: int,
    validator: Callable[[dict[str, Any], BeautifulSoup], list[str]],
) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]], int]:
    """Fetch and validate details without retaining their HTML responses.

    Each validation works on a private row copy.  A failed first response can
    therefore be retried without mutations from the failed attempt (notably a
    consumed application-control marker) leaking into the next attempt.
    """

    valid: dict[str, dict[str, Any]] = {}
    failures: dict[str, list[str]] = {}
    response_pages = 0
    bounded_batch_size = max(1, min(int(batch_size), ANSAN_DETAIL_BATCH_SIZE))
    items = list(rows.items())
    for offset in range(0, len(items), bounded_batch_size):
        batch = items[offset : offset + bounded_batch_size]
        thread_state = local()
        sessions: list[Any] = []
        sessions_lock = Lock()

        def current_session() -> Any:
            value = getattr(thread_state, "session", None)
            if value is None:
                value = session_factory()
                thread_state.session = value
                with sessions_lock:
                    sessions.append(value)
            return value

        def run(
            item: tuple[str, dict[str, Any]],
        ) -> tuple[str, dict[str, Any], list[str]]:
            key, source_row = item
            soup = _fetch(
                fetcher,
                current_session(),
                _clean(source_row.get("raw_url")),
                timeout,
            )
            candidate = deepcopy(source_row)
            try:
                item_errors = validator(candidate, soup)
            finally:
                soup.decompose()
            return key, candidate, item_errors

        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(run, item): item[0] for item in batch}
                for future in as_completed(futures):
                    key = futures[future]
                    try:
                        result_key, candidate, item_errors = future.result()
                        response_pages += 1
                        if item_errors:
                            failures[result_key] = item_errors
                        else:
                            valid[result_key] = candidate
                    except Exception as exc:
                        failures[key] = [
                            f"fetch detail {key} failed ({type(exc).__name__})"
                        ]
        finally:
            for value in sessions:
                _close_quietly(value)
            batch.clear()
    return valid, failures, response_pages


def collect_ansan_education_courses(
    target: Any,
    timeout: int = 30,
    max_pages: int = 1200,
    detail_limit: int = 3000,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = ANSAN_MAX_WORKERS,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future Ansan education snapshot."""

    meta = _base_meta()
    if not is_ansan_education_target(target):
        meta["configured_collection_error"] = (
            "target is not the canonical Ansan education owner"
        )
        return [], ANSAN_PARSER, meta
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta["configured_collection_error"] = (
                "Ansan legacy-TLS session_factory injection is required"
            )
            return [], ANSAN_PARSER, meta
        session_factory = ansan_session_factory
    try:
        allowed_pages = max(0, int(max_pages))
        allowed_details = max(0, int(detail_limit))
        worker_count = max(1, min(int(max_workers), ANSAN_MAX_WORKERS))
        reference_day = _today(today)
    except (TypeError, ValueError):
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = (
            "max_pages/detail_limit/max_workers/today are invalid"
        )
        return [], ANSAN_PARSER, meta

    errors: list[str] = []
    source_cap_reached = False
    first_soups: dict[tuple[str, str], BeautifulSoup] = {}
    source_totals: dict[tuple[str, str], int] = {}
    source_lasts: dict[tuple[str, str], int] = {}
    first_rows: dict[tuple[str, str], list[dict[str, Any]]] = {}
    main_session: Any = None

    try:
        main_session = session_factory()
        for catalogue in ANSAN_LIFELONG_CATALOGUES:
            key = ("lifelong", catalogue.code)
            try:
                soup = _fetch(
                    fetcher,
                    main_session,
                    ansan_lifelong_list_url(catalogue),
                    timeout,
                )
                if catalogue.code == "nor":
                    _main_directory_contract(soup)
                total, last = _contract_total_last(
                    soup, ANSAN_LLL_PAGE_SIZE, _LLL_PAGE_RE
                )
                parsed, item_errors = _lll_rows(soup, catalogue, 1)
                errors.extend(item_errors)
                first_soups[key] = soup
                source_totals[key] = total
                source_lasts[key] = last
                first_rows[key] = parsed
            except Exception as exc:
                errors.append(
                    f"lifelong {catalogue.code} first page failed "
                    f"({type(exc).__name__}: {exc})"
                )

        for category in ANSAN_RESERVE_CATEGORIES:
            key = ("reserve", category.code)
            try:
                soup = _fetch(
                    fetcher,
                    main_session,
                    ansan_reserve_list_url(category),
                    timeout,
                )
                _reserve_directory_contract(soup)
                total, last = _contract_total_last(
                    soup, ANSAN_RESERVE_PAGE_SIZE, _RESERVE_PAGE_RE
                )
                parsed, item_errors = _reserve_rows(soup, category, 1)
                errors.extend(item_errors)
                first_soups[key] = soup
                source_totals[key] = total
                source_lasts[key] = last
                first_rows[key] = parsed
            except Exception as exc:
                errors.append(
                    f"reserve {category.code} first page failed "
                    f"({type(exc).__name__}: {exc})"
                )

        place_key = ("road_places", "directory")
        try:
            place_soup = _fetch(
                fetcher,
                main_session,
                ansan_road_place_list_url(),
                timeout,
            )
            place_total, place_last = _road_place_contract(place_soup)
            parsed_places, place_errors = _road_place_rows(place_soup, 1)
            errors.extend(place_errors)
            first_soups[place_key] = place_soup
            source_totals[place_key] = place_total
            source_lasts[place_key] = place_last
            first_rows[place_key] = parsed_places
        except Exception as exc:
            errors.append(
                f"road-place first page failed ({type(exc).__name__}: {exc})"
            )

        expected_source_count = (
            len(ANSAN_LIFELONG_CATALOGUES)
            + len(ANSAN_RESERVE_CATEGORIES)
            + 1
        )
        if len(source_lasts) != expected_source_count:
            meta["configured_collection_error"] = "; ".join(
                dict.fromkeys(errors)
            )
            return [], ANSAN_PARSER, meta

        required_list_requests = sum(last + 2 for last in source_lasts.values())
        meta["required_list_requests"] = required_list_requests
        meta["data_pages"] = sum(source_lasts.values())
        meta["sentinel_requests"] = len(source_lasts)
        meta["stability_rechecks"] = len(source_lasts)
        if allowed_pages < required_list_requests:
            source_cap_reached = True
            errors.append(
                f"max_pages {allowed_pages} < required {required_list_requests}"
            )
            meta["source_cap_reached"] = True
            meta["configured_collection_error"] = "; ".join(
                dict.fromkeys(errors)
            )
            return [], ANSAN_PARSER, meta

        page_tasks: dict[tuple[str, str, int], str] = {}
        for catalogue in ANSAN_LIFELONG_CATALOGUES:
            last = source_lasts[("lifelong", catalogue.code)]
            for page in range(2, last + 2):
                page_tasks[("lifelong", catalogue.code, page)] = (
                    ansan_lifelong_list_url(catalogue, page)
                )
        for category in ANSAN_RESERVE_CATEGORIES:
            last = source_lasts[("reserve", category.code)]
            for page in range(2, last + 2):
                page_tasks[("reserve", category.code, page)] = (
                    ansan_reserve_list_url(category, page)
                )
        for page in range(2, source_lasts[place_key] + 2):
            page_tasks[("road_places", "directory", page)] = (
                ansan_road_place_list_url(page)
            )

        page_soups, fetch_errors = _parallel_fetches(
            page_tasks,
            fetcher=fetcher,
            session_factory=session_factory,
            timeout=timeout,
            max_workers=worker_count,
        )
        errors.extend(fetch_errors)

        # Rechecks are deliberately issued only after the complete traversal.
        recheck_tasks: dict[tuple[str, str, str], str] = {}
        for catalogue in ANSAN_LIFELONG_CATALOGUES:
            recheck_tasks[("lifelong", catalogue.code, "recheck")] = (
                ansan_lifelong_list_url(catalogue)
            )
        for category in ANSAN_RESERVE_CATEGORIES:
            recheck_tasks[("reserve", category.code, "recheck")] = (
                ansan_reserve_list_url(category)
            )
        recheck_tasks[("road_places", "directory", "recheck")] = (
            ansan_road_place_list_url()
        )
        recheck_soups, recheck_errors = _parallel_fetches(
            recheck_tasks,
            fetcher=fetcher,
            session_factory=session_factory,
            timeout=timeout,
            max_workers=worker_count,
        )
        errors.extend(recheck_errors)

        all_course_rows: list[dict[str, Any]] = []
        all_place_rows: list[dict[str, Any]] = []

        for catalogue in ANSAN_LIFELONG_CATALOGUES:
            source_key = ("lifelong", catalogue.code)
            total = source_totals[source_key]
            last = source_lasts[source_key]
            source_rows: list[dict[str, Any]] = []
            for page in range(1, last + 1):
                soup = (
                    _take_soup(first_soups, source_key)
                    if page == 1
                    else _take_soup(
                        page_soups, ("lifelong", catalogue.code, page)
                    )
                )
                if soup is None:
                    errors.append(f"lifelong {catalogue.code} page {page} missing")
                    continue
                try:
                    observed_total = _observed_total(soup)
                    if observed_total != total:
                        raise AnsanContractError("lifelong page counter drift")
                    parsed, item_errors = _lll_rows(soup, catalogue, page)
                    errors.extend(item_errors)
                    expected = min(
                        ANSAN_LLL_PAGE_SIZE,
                        max(0, total - ((page - 1) * ANSAN_LLL_PAGE_SIZE)),
                    )
                    if len(parsed) != expected:
                        raise AnsanContractError(
                            f"page {page} rows {len(parsed)} != {expected}"
                        )
                    source_rows.extend(parsed)
                except Exception as exc:
                    errors.append(
                        f"lifelong {catalogue.code} page {page} contract "
                        f"({type(exc).__name__}: {exc})"
                    )
                finally:
                    soup.decompose()
            sentinel = _take_soup(
                page_soups, ("lifelong", catalogue.code, last + 1)
            )
            if sentinel is None:
                errors.append(f"lifelong {catalogue.code} sentinel missing")
            else:
                sentinel_rows, item_errors = _lll_rows(
                    sentinel, catalogue, last + 1
                )
                errors.extend(item_errors)
                if sentinel_rows:
                    errors.append(f"lifelong {catalogue.code} sentinel is not empty")
                sentinel.decompose()
            recheck = _take_soup(
                recheck_soups, ("lifelong", catalogue.code, "recheck")
            )
            if recheck is None:
                errors.append(f"lifelong {catalogue.code} recheck missing")
            else:
                reparsed, item_errors = _lll_rows(recheck, catalogue, 1)
                errors.extend(item_errors)
                if _page_signature(reparsed) != _page_signature(first_rows[source_key]):
                    errors.append(f"lifelong {catalogue.code} page-one drift")
                recheck.decompose()
            if len(source_rows) != total:
                errors.append(
                    f"lifelong {catalogue.code} total {len(source_rows)} != {total}"
                )
            all_course_rows.extend(source_rows)

        for category in ANSAN_RESERVE_CATEGORIES:
            source_key = ("reserve", category.code)
            total = source_totals[source_key]
            last = source_lasts[source_key]
            source_rows = []
            for page in range(1, last + 1):
                soup = (
                    _take_soup(first_soups, source_key)
                    if page == 1
                    else _take_soup(
                        page_soups, ("reserve", category.code, page)
                    )
                )
                if soup is None:
                    errors.append(f"reserve {category.code} page {page} missing")
                    continue
                try:
                    _reserve_directory_contract(soup)
                    observed_total = _observed_total(soup)
                    if observed_total != total:
                        raise AnsanContractError("reservation page counter drift")
                    parsed, item_errors = _reserve_rows(soup, category, page)
                    errors.extend(item_errors)
                    expected = min(
                        ANSAN_RESERVE_PAGE_SIZE,
                        max(0, total - ((page - 1) * ANSAN_RESERVE_PAGE_SIZE)),
                    )
                    if len(parsed) != expected:
                        raise AnsanContractError(
                            f"page {page} rows {len(parsed)} != {expected}"
                        )
                    source_rows.extend(parsed)
                except Exception as exc:
                    errors.append(
                        f"reserve {category.code} page {page} contract "
                        f"({type(exc).__name__}: {exc})"
                    )
                finally:
                    soup.decompose()
            sentinel = _take_soup(
                page_soups, ("reserve", category.code, last + 1)
            )
            if sentinel is None:
                errors.append(f"reserve {category.code} sentinel missing")
            else:
                sentinel_rows, item_errors = _reserve_rows(
                    sentinel, category, last + 1
                )
                errors.extend(item_errors)
                if sentinel_rows:
                    errors.append(f"reserve {category.code} sentinel is not empty")
                sentinel.decompose()
            recheck = _take_soup(
                recheck_soups, ("reserve", category.code, "recheck")
            )
            if recheck is None:
                errors.append(f"reserve {category.code} recheck missing")
            else:
                reparsed, item_errors = _reserve_rows(recheck, category, 1)
                errors.extend(item_errors)
                if _page_signature(reparsed) != _page_signature(first_rows[source_key]):
                    errors.append(f"reserve {category.code} page-one drift")
                recheck.decompose()
            if len(source_rows) != total:
                errors.append(
                    f"reserve {category.code} total {len(source_rows)} != {total}"
                )
            all_course_rows.extend(source_rows)

        place_total = source_totals[place_key]
        place_last = source_lasts[place_key]
        for page in range(1, place_last + 1):
            soup = (
                _take_soup(first_soups, place_key)
                if page == 1
                else _take_soup(
                    page_soups, ("road_places", "directory", page)
                )
            )
            if soup is None:
                errors.append(f"road-place page {page} missing")
                continue
            parsed, item_errors = _road_place_rows(soup, page)
            errors.extend(item_errors)
            expected = min(
                ANSAN_LLL_PAGE_SIZE,
                max(0, place_total - ((page - 1) * ANSAN_LLL_PAGE_SIZE)),
            )
            if len(parsed) != expected:
                errors.append(f"road-place page {page} rows {len(parsed)} != {expected}")
            all_place_rows.extend(parsed)
            soup.decompose()
        place_sentinel = _take_soup(
            page_soups, ("road_places", "directory", place_last + 1)
        )
        if place_sentinel is None:
            errors.append("road-place sentinel missing")
        else:
            sentinel_rows, item_errors = _road_place_rows(
                place_sentinel, place_last + 1
            )
            errors.extend(item_errors)
            if sentinel_rows:
                errors.append("road-place sentinel is not empty")
            place_sentinel.decompose()
        place_recheck = _take_soup(
            recheck_soups, ("road_places", "directory", "recheck")
        )
        if place_recheck is None:
            errors.append("road-place recheck missing")
        else:
            reparsed, item_errors = _road_place_rows(place_recheck, 1)
            errors.extend(item_errors)
            before_signature = tuple(
                (row["number"], row["title"], row["address"])
                for row in first_rows[place_key]
            )
            after_signature = tuple(
                (row["number"], row["title"], row["address"])
                for row in reparsed
            )
            if after_signature != before_signature:
                errors.append("road-place page-one drift")
            place_recheck.decompose()
        place_numbers = [int(row["number"]) for row in all_place_rows]
        if (
            len(all_place_rows) != place_total
            or len(set(place_numbers)) != place_total
            or set(place_numbers) != set(range(1, place_total + 1))
        ):
            errors.append("road-place continuous numbering contract failed")

        identities = [
            _clean(row.get("raw_fields", {}).get("source_identity"))
            for row in all_course_rows
        ]
        raw_urls = [
            _clean(row.get("raw_url"))
            for row in all_course_rows
            if row.get("raw_fields", {}).get("source_method")
            != "cancelled_list_identity"
        ]
        duplicate_identities = len(identities) - len(set(identities))
        duplicate_urls = len(raw_urls) - len(set(raw_urls))
        if duplicate_identities:
            errors.append(f"duplicate source identities {duplicate_identities}")
        if duplicate_urls:
            errors.append(f"duplicate detail URLs {duplicate_urls}")

        current_rows = [
            row
            for row in all_course_rows
            if date.fromisoformat(_clean(row.get("end_date"))) >= reference_day
            and not bool(row.get("raw_fields", {}).get("terminal_excluded"))
        ]
        if len(current_rows) > allowed_details:
            source_cap_reached = True
            errors.append(
                f"detail_limit {allowed_details} < current rows {len(current_rows)}"
            )

        road_places: dict[str, dict[str, str]] = {}
        try:
            road_places = _road_place_map(all_place_rows)
        except Exception as exc:
            errors.append(f"road-place map failed ({type(exc).__name__}: {exc})")

        meta.update(
            {
                "pages": required_list_requests if not fetch_errors and not recheck_errors else 0,
                "source_total": len(all_course_rows),
                "source_rows": len(all_course_rows),
                "lifelong_total": sum(
                    source_totals[("lifelong", item.code)]
                    for item in ANSAN_LIFELONG_CATALOGUES
                ),
                "reserve_total": sum(
                    source_totals[("reserve", item.code)]
                    for item in ANSAN_RESERVE_CATEGORIES
                ),
                "road_place_total": place_total,
                "lifelong_catalogue_totals": {
                    item.code: source_totals[("lifelong", item.code)]
                    for item in ANSAN_LIFELONG_CATALOGUES
                },
                "reserve_category_totals": {
                    item.code: source_totals[("reserve", item.code)]
                    for item in ANSAN_RESERVE_CATEGORIES
                },
                "source_status_counts": dict(
                    sorted(
                        Counter(
                            _clean(row.get("raw_fields", {}).get("source_status"))
                            for row in all_course_rows
                        ).items()
                    )
                ),
                "current_count": len(current_rows),
                "expired_count": len(all_course_rows) - len(current_rows),
                "detail_attempts": len(current_rows),
                "duplicate_identity_count": duplicate_identities,
                "duplicate_url_count": duplicate_urls,
                "pagination_detected": any(
                    last > 1 for last in source_lasts.values()
                ),
                "source_cap_reached": source_cap_reached,
            }
        )

        if errors or source_cap_reached:
            meta["configured_collection_error"] = "; ".join(
                dict.fromkeys(errors)
            )
            return [], ANSAN_PARSER, meta

        # List HTML can be large.  It is no longer needed once all source rows
        # and road-place evidence have passed their contracts.
        first_soups.clear()
        page_soups.clear()
        recheck_soups.clear()
        gc.collect()

        row_by_key = {
            _clean(row.get("provider_course_id")): row for row in current_rows
        }

        # Plan exact cross-source ownership before fetching details.  Some
        # legacy numeric rows are live replicas of the lifelong catalogue but
        # their reservation detail endpoint intentionally returns the exact
        # official missing-detail shell.  Only a replica already proven by
        # the same strong title/date/schedule/institution contract may accept
        # that shell, and it must still be removed during final reconciliation.
        _planned_rows, _planned_errors, planned_overlaps = (
            _reconcile_cross_source_overlaps(deepcopy(current_rows))
        )
        planned_replica_keys = {
            _stable_id("reserve", item["reserve_identity"])
            for item in planned_overlaps
        }

        def validate_detail(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
            if row.get("raw_fields", {}).get("source_kind") == "lifelong":
                return _enrich_lifelong_detail(row, soup, road_places)
            return _enrich_reserve_detail(
                row,
                soup,
                allow_open_legacy_semantic_replica=(
                    _clean(row.get("provider_course_id")) in planned_replica_keys
                ),
            )

        valid_detail_keys: set[str] = set()
        last_detail_errors: dict[str, list[str]] = {}
        pending_keys = set(row_by_key)
        retry_pages = 0
        for round_index in range(3):
            candidates, round_errors, response_pages = _parallel_detail_validations(
                {key: row_by_key[key] for key in pending_keys},
                fetcher=fetcher,
                session_factory=session_factory,
                timeout=timeout,
                max_workers=(
                    worker_count
                    if round_index == 0
                    else min(worker_count, ANSAN_DETAIL_RETRY_WORKERS)
                ),
                batch_size=ANSAN_DETAIL_BATCH_SIZE,
                validator=validate_detail,
            )
            if round_index:
                retry_pages += response_pages
            for key, candidate in candidates.items():
                source_row = row_by_key[key]
                source_row.clear()
                source_row.update(candidate)
                valid_detail_keys.add(key)
            pending_keys.difference_update(candidates)
            last_detail_errors = {
                key: round_errors.get(key, [f"detail {key} missing"])
                for key in pending_keys
            }
            if not pending_keys or round_index == 2:
                break
        detail_errors = [
            message
            for key in sorted(pending_keys)
            for message in last_detail_errors.get(key, [f"detail {key} missing"])
        ]
        errors.extend(detail_errors)

        reconciled, overlap_errors, overlaps = _reconcile_cross_source_overlaps(
            current_rows
        )
        errors.extend(overlap_errors)
        lingering_open_shells = [
            row
            for row in reconciled
            if row.get("raw_fields", {}).get("status_control_override")
            == "open_legacy_semantic_replica_missing_detail"
        ]
        if lingering_open_shells:
            errors.append(
                "open legacy missing-detail replica survived reconciliation"
            )
        _mark_required_source_omissions(reconciled)
        for row in reconciled:
            row.pop("_listed_application_control", None)
            raw_fields = row.get("raw_fields")
            if not isinstance(raw_fields, dict):
                errors.append("row raw_fields is not a mapping")
                continue
            unexpected = set(raw_fields) - ANSAN_RAW_FIELD_ALLOWLIST
            if unexpected:
                errors.append(f"raw-field allow-list violation {sorted(unexpected)}")

        deduper = dedupe_rows or _default_dedupe
        deduped = list(deduper(reconciled))
        duplicate_count = len(reconciled) - len(deduped)
        if duplicate_count:
            errors.append(f"post-reconciliation duplicate rows {duplicate_count}")
        deduped.sort(
            key=lambda row: (
                _clean(row.get("start_date")),
                _clean(row.get("title")),
                _clean(row.get("provider_course_id")),
            )
        )

        list_complete = (
            not fetch_errors
            and not recheck_errors
            and len(all_course_rows) == sum(
                source_totals[key]
                for key in source_totals
                if key[0] in {"lifelong", "reserve"}
            )
            and len(all_place_rows) == place_total
        )
        partitions_complete = (
            list_complete
            and not duplicate_identities
            and not duplicate_urls
            and sum(
                source_totals[("lifelong", item.code)]
                for item in ANSAN_LIFELONG_CATALOGUES
            )
            + sum(
                source_totals[("reserve", item.code)]
                for item in ANSAN_RESERVE_CATEGORIES
            )
            == len(all_course_rows)
        )
        details_complete = (
            not detail_errors
            and len(valid_detail_keys) == len(current_rows)
        )
        snapshot_complete = (
            not errors
            and list_complete
            and partitions_complete
            and details_complete
            and not source_cap_reached
            and len(deduped) == len(current_rows) - len(overlaps)
        )
        if not snapshot_complete:
            deduped = []

        meta.update(
            {
                "returned_count": len(deduped),
                "detail_pages": len(valid_detail_keys),
                "detail_retry_pages": retry_pages,
                "detail_errors": len(detail_errors),
                "scheduled_detail_unpublished_count": sum(
                    row.get("raw_fields", {}).get("status_control_override")
                    == "scheduled_legacy_detail_not_yet_published"
                    for row in current_rows
                ),
                "closed_detail_retired_count": sum(
                    row.get("raw_fields", {}).get("status_control_override")
                    == "closed_legacy_detail_retired"
                    for row in current_rows
                ),
                "open_legacy_replica_shell_count": sum(
                    row.get("raw_fields", {}).get("status_control_override")
                    == "open_legacy_semantic_replica_missing_detail"
                    for row in current_rows
                ),
                "open_legacy_list_only_shell_count": sum(
                    row.get("raw_fields", {}).get("status_control_override")
                    == "source_open_legacy_detail_unpublished_without_public_control"
                    for row in current_rows
                ),
                "closed_list_open_full_detail_count": sum(
                    row.get("raw_fields", {}).get("status_control_override")
                    == "list_closed_detail_open_full_without_application_control"
                    for row in current_rows
                ),
                "non_open_detail_shell_count": sum(
                    row.get("raw_fields", {}).get("status_control_override")
                    in {
                        "scheduled_legacy_detail_not_yet_published",
                        "closed_legacy_detail_retired",
                    }
                    for row in current_rows
                ),
                "application_control_count": sum(
                    bool(row.get("application_url")) for row in current_rows
                ),
                "target_source_omission_count": sum(
                    row.get("raw_fields", {}).get("target_source_omission") is True
                    for row in deduped
                ),
                "schedule_source_omission_count": sum(
                    row.get("raw_fields", {}).get("schedule_source_omission")
                    is True
                    for row in deduped
                ),
                "cross_source_overlap_count": len(overlaps),
                "cross_source_overlaps": overlaps,
                "duplicate_count": duplicate_count,
                "municipality_counts": dict(
                    sorted(
                        Counter(
                            _clean(row.get("municipality_full_name"))
                            for row in deduped
                        ).items()
                    )
                ),
                "branch_counts": dict(
                    sorted(
                        Counter(_clean(row.get("branch")) for row in deduped).items()
                    )
                ),
                "pagination_complete": list_complete,
                "partitions_complete": partitions_complete,
                "details_complete": details_complete,
                "snapshot_complete": snapshot_complete,
                "source_cap_reached": source_cap_reached,
                "no_current_data": snapshot_complete and not deduped,
                "no_current_reason": (
                    "all complete Ansan education catalogues contain only ended courses"
                    if snapshot_complete and not deduped
                    else ""
                ),
                "configured_collection_error": "; ".join(
                    dict.fromkeys(errors)
                ),
            }
        )
        return deduped, ANSAN_PARSER, meta
    finally:
        _close_quietly(main_session)


collect_ansan_target = collect_ansan_education_courses


__all__ = [
    "ANSAN_CANDIDATE_AUDIT",
    "ANSAN_CANONICAL_CANDIDATE_ID",
    "ANSAN_CANONICAL_URL",
    "ANSAN_CITY_CODE",
    "ANSAN_COVERED_MUNICIPALITIES",
    "ANSAN_DANWON_CODE",
    "ANSAN_DETAIL_BATCH_SIZE",
    "ANSAN_DETAIL_RETRY_WORKERS",
    "ANSAN_EXCLUDED_NON_COURSE_URLS",
    "ANSAN_FETCH_ATTEMPTS",
    "ANSAN_LIFELONG_CATALOGUES",
    "ANSAN_LLL_HOST",
    "ANSAN_LLL_PAGE_SIZE",
    "ANSAN_MAX_WORKERS",
    "ANSAN_MUNICIPALITY_NAMES",
    "ANSAN_NON_EXECUTING_ALIASES",
    "ANSAN_PARSER",
    "ANSAN_PROVIDER",
    "ANSAN_RAW_FIELD_ALLOWLIST",
    "ANSAN_REG_TITLE_CATEGORY_PREFIXES",
    "ANSAN_RESERVE_CATEGORIES",
    "ANSAN_RESERVE_HOST",
    "ANSAN_RESERVE_PAGE_SIZE",
    "ANSAN_SANGNOK_CODE",
    "ansan_lifelong_detail_url",
    "ansan_lifelong_list_url",
    "ansan_reserve_detail_url",
    "ansan_reserve_list_url",
    "ansan_road_place_list_url",
    "ansan_session_factory",
    "collect_ansan_education_courses",
    "collect_ansan_target",
    "is_ansan_education_target",
    "is_target",
]
