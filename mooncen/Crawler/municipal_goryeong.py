"""Fail-closed collector for Goryeong County's current education ledger.

The retained provider was discovered through one stale course detail.  Its
canonical source is the official ``진행중강좌`` list.  That page also embeds a
large K-MOOC catalogue; only the first, municipal ``오프라인 강좌`` ledger is
owned here.

Completeness is established from the advertised education pages, an exact
post-last empty sentinel, all fifteen institution partitions, the five local
course-type partitions, the source's available/future partitions, every
current detail, and a full-ledger recheck after those details.  Application,
member, attachment, and other PII-bearing endpoints are never requested.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


GORYEONG_PROVIDER = "MUNI_WWW_GORYEONG_GO_KR_8F708B74"
GORYEONG_MUNICIPALITY_CODE = "4783000000"
GORYEONG_MUNICIPALITY_NAME = "경상북도 고령군"

GORYEONG_HOST = "www.goryeong.go.kr"
GORYEONG_LIST_PATH = "/lifelong/eduProgram/list.do"
GORYEONG_DETAIL_PATH = "/lifelong/eduProgram/detail.do"
GORYEONG_APPLICATION_PATH = "/lifelong/member/myApplyList.do"
GORYEONG_IDX = "35"
GORYEONG_CANONICAL_URL = (
    f"https://{GORYEONG_HOST}{GORYEONG_LIST_PATH}?IDX={GORYEONG_IDX}"
)
GORYEONG_LEGACY_URL = (
    f"https://{GORYEONG_HOST}{GORYEONG_DETAIL_PATH}?IDX=35&epIdx=75"
)
GORYEONG_CANONICAL_URL_SHA256 = (
    "458a8d7f1d64249d5a6c9f444470c5d437176b99fd795d97ba51d67f431b137a"
)
GORYEONG_CANONICAL_CANDIDATE_ID = "MUNI_IR_458A8D7F1D64"
GORYEONG_LEGACY_CANDIDATE_ID = "MUNI_IR_C83F8397BC7D"
GORYEONG_NOTICE_CANDIDATE_ID = "MUNI_IR_3E88A0974A83"
GORYEONG_PAST_CANDIDATE_ID = "MUNI_IR_538886A9616C"

GORYEONG_PAGE_SIZE = 5
GORYEONG_RECOMMENDED_MAX_PAGES = 100
GORYEONG_RECOMMENDED_DETAIL_LIMIT = 100
GORYEONG_FETCH_ATTEMPTS = 2
GORYEONG_MAX_HTML_BYTES = 2_000_000
GORYEONG_PARSER = (
    "goryeong_complete_current_offline_education+advertised_pages+"
    "exact_post_last_sentinel+fifteen_institution_partition+"
    "five_local_type_partition+available_future_partition+"
    "all_current_details+stable_full_recheck+source_status_controls+"
    "kmooc_owner_exclusion+application_attachment_and_pii_no_fetch"
)
GORYEONG_OWNERSHIP_SCOPE = (
    "official_goryeong_lifelong_portal_current_offline_education_ledger"
)

GORYEONG_INSTITUTIONS: tuple[str, ...] = (
    "평생학습관",
    "도시과",
    "보건소",
    "농업기술센터",
    "대가야읍",
    "덕곡면",
    "운수면",
    "성산면",
    "다산면",
    "개진면",
    "우곡면",
    "쌍림면",
    "고령문화원",
    "다산도서관",
    "고령도서관",
)
GORYEONG_INTERNAL_TYPES: tuple[str, ...] = (
    "생활취미",
    "외국어",
    "정보화",
    "자격증",
    "기타",
)
GORYEONG_STATUS_FILTERS: tuple[tuple[str, str], ...] = (
    ("AI", "신청가능"),
    ("AA", "접수예정"),
)


@dataclass(frozen=True)
class GoryeongBranch:
    source_name: str
    public_name: str
    code: str
    address: str = ""
    public_url: str = GORYEONG_CANONICAL_URL


GORYEONG_BRANCHES: tuple[GoryeongBranch, ...] = (
    GoryeongBranch(
        "평생학습관",
        "고령군평생학습관",
        "GORYEONG_LIFELONG_LEARNING_CENTER",
        "경상북도 고령군 왕릉로 30",
        "https://www.goryeong.go.kr/lifelong/contents.do?IDX=8",
    ),
    GoryeongBranch("도시과", "도시과", "GORYEONG_URBAN_DIVISION"),
    GoryeongBranch("보건소", "고령군보건소", "GORYEONG_HEALTH_CENTER"),
    GoryeongBranch(
        "농업기술센터", "고령군농업기술센터", "GORYEONG_AGRICULTURAL_CENTER"
    ),
    GoryeongBranch("대가야읍", "대가야읍", "GORYEONG_DAEGAYA_EUP"),
    GoryeongBranch("덕곡면", "덕곡면", "GORYEONG_DEOKGOK_MYEON"),
    GoryeongBranch("운수면", "운수면", "GORYEONG_UNSU_MYEON"),
    GoryeongBranch("성산면", "성산면", "GORYEONG_SEONGSAN_MYEON"),
    GoryeongBranch("다산면", "다산면", "GORYEONG_DASAN_MYEON"),
    GoryeongBranch("개진면", "개진면", "GORYEONG_GAEJIN_MYEON"),
    GoryeongBranch("우곡면", "우곡면", "GORYEONG_UGOK_MYEON"),
    GoryeongBranch("쌍림면", "쌍림면", "GORYEONG_SSANGNIM_MYEON"),
    GoryeongBranch(
        "고령문화원",
        "고령문화원",
        "GORYEONG_CULTURE_CENTER",
        "경상북도 고령군 대가야읍 왕릉로 30",
        "https://culture.goryeong.go.kr",
    ),
    GoryeongBranch(
        "다산도서관",
        "다산도서관",
        "GORYEONG_DASAN_LIBRARY",
        "경상북도 고령군 다산면 다산로 681",
        "https://www.goryeong.go.kr/lifelong/contents.do?IDX=13",
    ),
    GoryeongBranch(
        "고령도서관",
        "경상북도교육청 고령도서관",
        "GBELIB_GORYEONG_LIBRARY",
        "경상북도 고령군 대가야읍 연조1길 10",
        "https://www.gbelib.kr/gr/",
    ),
)
_BRANCH_BY_SOURCE = {item.source_name: item for item in GORYEONG_BRANCHES}

GORYEONG_CANDIDATE_AUDIT: Mapping[str, Mapping[str, str]] = {
    GORYEONG_CANONICAL_CANDIDATE_ID: {
        "provider": GORYEONG_PROVIDER,
        "url": GORYEONG_CANONICAL_URL,
        "decision": "retain_incumbent_provider_and_retarget_to_complete_current_list",
    },
    GORYEONG_LEGACY_CANDIDATE_ID: {
        "provider": GORYEONG_PROVIDER,
        "url": GORYEONG_LEGACY_URL,
        "decision": "legacy_stale_single_detail_alias_of_retained_provider",
    },
    GORYEONG_NOTICE_CANDIDATE_ID: {
        "provider": "MUNI_WWW_GORYEONG_GO_KR_A92BACC0",
        "url": "https://www.goryeong.go.kr/lifelong/boardView.do?BOARD_IDX=133&IDX=18",
        "decision": "inactive_notice_alias_without_course_identity_ledger",
    },
    GORYEONG_PAST_CANDIDATE_ID: {
        "provider": "MUNI_WWW_GORYEONG_GO_KR_1E3A14DA",
        "url": "https://www.goryeong.go.kr/lifelong/eduProgram/detail.do?IDX=4&epIdx=561",
        "decision": "inactive_past_single_course_alias",
    },
}

GORYEONG_OWNER_BOUNDARIES: tuple[Mapping[str, str], ...] = (
    {
        "url": "https://www.goryeong.go.kr/lifelong/eduProgram/list.do?IDX=36",
        "decision": "ended_history_not_current_collection_scope",
    },
    {
        "url": "https://www.goryeong.go.kr/lifelong/eduProgram/detail2.do",
        "decision": "embedded_external_kmooc_catalogue_excluded",
    },
    {
        "url": "https://www.gbelib.kr/gr/",
        "decision": "separate_education_office_library_owner; portal rows_keep_source_branch_only",
    },
    {
        "url": "https://lib.goryeong.go.kr",
        "decision": "separate_library_homepage; portal_rows_keep_exact_dasan_branch",
    },
    {
        "url": "https://goryeonggun.familynet.or.kr/",
        "decision": "separate_family_center_program_owner",
    },
    {
        "url": "https://culture.goryeong.go.kr",
        "decision": "separate_culture_center_owner_outside_portal_rows",
    },
    {
        "url": "https://www.goryeong.go.kr/daegaya/",
        "decision": "separate_museum_and_experience_owner",
    },
    {
        "url": "https://mall.goryeong.go.kr/",
        "decision": "separate_tourism_experience_and_accommodation_owner",
    },
)

GORYEONG_LIVE_AUDIT_BASELINE: Mapping[str, Any] = {
    "cutoff": "2026-07-23",
    "source_rows": 3,
    "current_rows": 3,
    "pages": 1,
    "post_last_page": 2,
    "list_requests": 31,
    "detail_pages": 3,
    "source_requests": 34,
    "status_counts": {"OPEN": 1, "CLOSED": 2},
    "source_institution_counts": {"평생학습관": 1, "다산도서관": 2},
    "public_branch_counts": {"고령군평생학습관": 1, "다산도서관": 2},
    "type_counts": {"생활취미": 2, "기타": 1},
    "status_filter_counts": {"AI": 3, "AA": 0},
    "application_controls": 1,
    "ep_indices": ["718", "719", "720"],
    "requests_per_snapshot": 34,
    "two_snapshot_requests": 68,
}


class GoryeongContractError(ValueError):
    """Raised when the audited public contract no longer holds."""


@dataclass(frozen=True)
class _Filter:
    name: str
    value: str


@dataclass(frozen=True)
class _ListedCourse:
    ep_idx: str
    title: str
    source_institution: str
    category: str
    target: str
    apply_period: str
    event_period: str
    apply_start: date
    apply_end: date
    event_start: date
    event_end: date
    capacity: int
    capacity_current: int
    source_status: str
    status: str
    reservation_available: bool
    page: int
    sequence: int

    @property
    def detail_url(self) -> str:
        return _detail_url(self.ep_idx)


@dataclass(frozen=True)
class _Page:
    requested_page: int
    effective_page: int
    advertised_last_page: int
    rows: tuple[_ListedCourse, ...]


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"[1-9]\d*")
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)")
_TITLE_CAPACITY_RE = re.compile(
    r"^(?P<title>.+?)\s*\(정원\s*(?P<capacity>[\d,]+)명\s*/\s*"
    r"신청\s*(?P<current>[\d,]+)명\)$"
)
_COUNT_RE = re.compile(r"^([\d,]+)명$")
_DETAIL_ONCLICK_RE = re.compile(r"^doApply\('(\d+)'\)$")
_PAGE_ONCLICK_RE = re.compile(r"^fnEduLinkPage\((\d+)\)$")
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,3}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RESIDENT_RE = re.compile(r"(?<!\d)\d{6}\s*[- ]\s*[1-4]\d{6}(?!\d)")

_TABLE_HEADERS = (
    "번호",
    "분야",
    "기관",
    "교육강좌명 (정원/신청)",
    "모집대상",
    "접수 및 교육기간",
    "수강신청",
)
_DETAIL_ROWS = (
    ("위치",),
    ("접수기간", "교육기간"),
    ("접수처", "교육시간"),
    ("수강료", "준비물"),
    ("모집대상", "모집방법"),
    ("모집인원", "신청인원"),
    ("교육과정",),
    ("수강안내",),
    ("첨부파일",),
)
_OTHER_DETAIL_ROWS = (("편의제공",), ("기타사항",))
_STATUS_MAP = {"신청하기": "OPEN", "접수종료": "CLOSED", "접수예정": "SCHEDULED"}
_FORBIDDEN_ROW_KEYS = frozenset(
    {
        "detail_description",
        "source_html",
        "raw_html",
        "phone",
        "email",
        "contact",
        "contacts",
        "instructor",
        "instructor_name",
        "attachments",
        "attachment_urls",
        "application_endpoint",
        "applicant_name",
        "applicant_phone",
        "password",
    }
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def is_goryeong_education_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")) == GORYEONG_PROVIDER
        and _clean(_target_value(target, "url"))
        in {GORYEONG_CANONICAL_URL, GORYEONG_LEGACY_URL}
    )


is_target = is_goryeong_education_target


def _raw_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MooncenMunicipalCrawler/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=False)


def _list_url(
    filter_name: Optional[str] = None,
    filter_value: Optional[str] = None,
    page: int = 1,
) -> str:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("invalid Goryeong page")
    pairs: list[tuple[str, str]] = [("IDX", GORYEONG_IDX)]
    if filter_name is None:
        if filter_value is not None:
            raise ValueError("filter value without a filter name")
    else:
        allowed: Mapping[str, frozenset[str]] = {
            "searchInst": frozenset(GORYEONG_INSTITUTIONS),
            "searchType": frozenset(GORYEONG_INTERNAL_TYPES),
            "searchStatus": frozenset(code for code, _ in GORYEONG_STATUS_FILTERS),
        }
        if filter_name not in allowed or filter_value not in allowed[filter_name]:
            raise ValueError("unsupported Goryeong filter")
        pairs.append((filter_name, str(filter_value)))
    if page > 1:
        pairs.append(("eduPageIndex", str(page)))
    return f"https://{GORYEONG_HOST}{GORYEONG_LIST_PATH}?{urlencode(pairs)}"


def _detail_url(ep_idx: str) -> str:
    value = _clean(ep_idx)
    if _IDENTITY_RE.fullmatch(value) is None:
        raise ValueError("invalid Goryeong course identity")
    return f"https://{GORYEONG_HOST}{GORYEONG_DETAIL_PATH}?" + urlencode(
        (("IDX", GORYEONG_IDX), ("epIdx", value))
    )


def _allowed_request_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        port = parsed.port
    except ValueError:
        return False
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == GORYEONG_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and not parsed.params
        and not parsed.fragment
    ):
        return False
    if parsed.path == GORYEONG_DETAIL_PATH:
        return bool(
            len(pairs) == 2
            and pairs[0] == ("IDX", GORYEONG_IDX)
            and pairs[1][0] == "epIdx"
            and _IDENTITY_RE.fullmatch(pairs[1][1])
        )
    if parsed.path != GORYEONG_LIST_PATH or not pairs or pairs[0] != ("IDX", GORYEONG_IDX):
        return False
    index = 1
    if index < len(pairs) and pairs[index][0] in {"searchInst", "searchType", "searchStatus"}:
        name, value = pairs[index]
        allowed = {
            "searchInst": GORYEONG_INSTITUTIONS,
            "searchType": GORYEONG_INTERNAL_TYPES,
            "searchStatus": tuple(code for code, _ in GORYEONG_STATUS_FILTERS),
        }
        if value not in allowed[name]:
            return False
        index += 1
    if index < len(pairs):
        if (
            pairs[index][0] != "eduPageIndex"
            or _IDENTITY_RE.fullmatch(pairs[index][1]) is None
        ):
            return False
        index += 1
    return index == len(pairs)


def _fetch_soup(
    session: Any,
    url: str,
    timeout: int,
    fetcher: Fetcher,
    meta: dict[str, Any],
) -> BeautifulSoup:
    if not _allowed_request_url(url):
        raise GoryeongContractError("request left the audited list/detail allowlist")
    meta["source_requests"] += 1
    last_error: Optional[Exception] = None
    for _ in range(GORYEONG_FETCH_ATTEMPTS):
        meta["request_attempts"] += 1
        try:
            response = fetcher(session, url, timeout)
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            if int(getattr(response, "status_code", 200)) != 200:
                raise GoryeongContractError("unexpected HTTP status")
            if getattr(response, "history", None):
                raise GoryeongContractError("redirect is not allowed")
            final_url = _clean(getattr(response, "url", url)) or url
            if final_url != url or not _allowed_request_url(final_url):
                raise GoryeongContractError("response URL changed")
            headers = getattr(response, "headers", {}) or {}
            content_type = _clean(headers.get("content-type") or headers.get("Content-Type"))
            if content_type and (
                "text/html" not in content_type.lower()
                or "utf-8" not in content_type.lower()
            ):
                raise GoryeongContractError("official page is no longer UTF-8 HTML")
            content = getattr(response, "content", None)
            if content is None:
                content = str(getattr(response, "text", response)).encode("utf-8")
            if not isinstance(content, (bytes, bytearray)):
                content = bytes(content)
            if not content or len(content) > GORYEONG_MAX_HTML_BYTES:
                raise GoryeongContractError("empty or oversized official HTML")
            try:
                html = bytes(content).decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise GoryeongContractError("official page is no longer strict UTF-8") from exc
            return BeautifulSoup(html, "html.parser")
        except (requests.RequestException, TimeoutError) as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise GoryeongContractError("official page fetch failed")


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        return value.astimezone(ZoneInfo("Asia/Seoul")).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value)
    raise ValueError("today must be an ISO date")


def _date_range(value: str, label: str) -> tuple[date, date]:
    matches = list(_DATE_RE.finditer(_clean(value)))
    if len(matches) != 2:
        raise GoryeongContractError(f"{label}: exact two-date range missing")
    try:
        values = tuple(date(int(x.group(1)), int(x.group(2)), int(x.group(3))) for x in matches)
    except ValueError as exc:
        raise GoryeongContractError(f"{label}: invalid date") from exc
    if values[1] < values[0]:
        raise GoryeongContractError(f"{label}: reversed date range")
    return values[0], values[1]


def _owner_contract(soup: BeautifulSoup) -> None:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != "고령군 평생교육포털 - 진행중강좌":
        raise GoryeongContractError(f"official owner/title changed: {title}")
    logos = [_clean(x.get_text(" ", strip=True)) for x in soup.select("h1.logo")]
    if logos != ["고령군 평생교육포털"]:
        raise GoryeongContractError("official portal logo changed")
    page_titles = [_clean(x.get_text(" ", strip=True)) for x in soup.select("h3.pageTit")]
    if page_titles != ["진행중강좌", "진행중강좌"]:
        raise GoryeongContractError("current-course page heading changed")
    footer_nodes = soup.select("footer.footer")
    footer = _clean(footer_nodes[0].get_text(" ", strip=True)) if len(footer_nodes) == 1 else ""
    if "고령군평생학습관" not in footer or "경상북도 고령군 왕릉로 30" not in footer:
        raise GoryeongContractError("official owner name/address evidence missing")


def _radio_registry(soup: BeautifulSoup, name: str) -> tuple[tuple[str, str, str], ...]:
    output: list[tuple[str, str, str]] = []
    for node in soup.select(f"form#frm input[type='radio'][name='{name}']"):
        identity = _clean(node.get("id"))
        labels = soup.select(f"form#frm label[for='{identity}']")
        if len(labels) != 1:
            raise GoryeongContractError(f"{name} label binding changed")
        output.append(
            (
                identity,
                _clean(node.get("value")),
                _clean(labels[0].get_text(" ", strip=True)),
            )
        )
    return tuple(output)


def _form_contract(soup: BeautifulSoup, filter_value: Optional[_Filter], page: int) -> int:
    forms = soup.select("form#frm[name='frm']")
    if (
        len(forms) != 1
        or _clean(forms[0].get("method")).lower() != "post"
        or _clean(forms[0].get("action")) != "?"
    ):
        raise GoryeongContractError("course search form changed")
    form = forms[0]
    hidden_nodes = form.select("input[type='hidden'][name]")
    hidden = {_clean(x.get("name")): _clean(x.get("value")) for x in hidden_nodes}
    if set(hidden) != {
        "pageIndex",
        "eduPage",
        "kmoocPage",
        "eduPageIndex",
        "kmoocPageIndex",
        "IDX",
    }:
        raise GoryeongContractError("course search hidden fields changed")
    if (
        hidden["pageIndex"]
        or hidden["kmoocPage"]
        or hidden["kmoocPageIndex"] != "1"
        or hidden["IDX"] != GORYEONG_IDX
        or hidden["eduPage"] != ("" if page == 1 else str(page))
        or not hidden["eduPageIndex"].isdigit()
    ):
        raise GoryeongContractError("course search hidden binding changed")
    expected_inst = tuple(
        (f"searchInst{value}", name, name)
        for value, name in (
            ("1", "평생학습관"),
            ("3", "도시과"),
            ("4", "보건소"),
            ("5", "농업기술센터"),
            ("6", "대가야읍"),
            ("7", "덕곡면"),
            ("8", "운수면"),
            ("9", "성산면"),
            ("10", "다산면"),
            ("11", "개진면"),
            ("12", "우곡면"),
            ("13", "쌍림면"),
            ("14", "고령문화원"),
            ("2", "다산도서관"),
            ("15", "고령도서관"),
        )
    )
    expected_type = tuple(
        (f"searchType{index}", value, value)
        for index, value in enumerate((*GORYEONG_INTERNAL_TYPES, "K-MOOC"), 1)
    )
    expected_status = (
        ("searchStatus1", "", "전체검색"),
        ("searchStatus2", "AI", "신청가능"),
        ("searchStatus3", "AA", "접수예정"),
    )
    if _radio_registry(soup, "searchInst") != expected_inst:
        raise GoryeongContractError("institution registry changed")
    if _radio_registry(soup, "searchType") != expected_type:
        raise GoryeongContractError("course type registry changed")
    if _radio_registry(soup, "searchStatus") != expected_status:
        raise GoryeongContractError("status registry changed")
    checked = form.select("input[type='radio'][checked]")
    if filter_value is None:
        if checked:
            raise GoryeongContractError("unfiltered page unexpectedly selected a filter")
    elif (
        len(checked) != 1
        or _clean(checked[0].get("name")) != filter_value.name
        or _clean(checked[0].get("value")) != filter_value.value
    ):
        raise GoryeongContractError("requested partition selection changed")
    keyword = form.select("input#boardSearch[name='pageKeyword']")
    if len(keyword) != 1 or _clean(keyword[0].get("value")):
        raise GoryeongContractError("keyword boundary changed")
    return int(hidden["eduPageIndex"])


def _pager_contract(board: Any) -> int:
    pager = board.find_next_sibling("div", class_="pageNav")
    if pager is None:
        raise GoryeongContractError("offline-course pager missing")
    pc = pager.select("ul.pcVer")
    scope = pc[0] if len(pc) == 1 else pager
    values = {1}
    strong = scope.select("strong")
    if len(strong) != 1 or not _clean(strong[0].get_text(" ", strip=True)).isdigit():
        raise GoryeongContractError("active education page changed")
    values.add(int(_clean(strong[0].get_text(" ", strip=True))))
    for anchor in scope.select("a[onclick]"):
        match = _PAGE_ONCLICK_RE.fullmatch(_clean(anchor.get("onclick")))
        if match is None or _clean(anchor.get("href")) != "javascript:;":
            raise GoryeongContractError("education pagination control changed")
        values.add(int(match.group(1)))
    return max(values)


def _period_cell(cell: Any, label: str) -> str:
    nodes = [x for x in cell.select("p") if _clean(x.select_one("span").get_text(" ", strip=True) if x.select_one("span") else "") == label]
    if len(nodes) != 1:
        raise GoryeongContractError(f"{label} list field changed")
    value = _clean(nodes[0].get_text(" ", strip=True))
    return _clean(value[len(label) :])


def _parse_status(cell: Any, ep_idx: str) -> tuple[str, str, bool]:
    controls = cell.select(":scope > .btn")
    if len(controls) != 1:
        raise GoryeongContractError(f"course {ep_idx}: status control changed")
    control = controls[0]
    label = _clean(control.get_text(" ", strip=True))
    if label not in _STATUS_MAP:
        raise GoryeongContractError(f"course {ep_idx}: unknown status {label}")
    if label == "신청하기":
        match = _DETAIL_ONCLICK_RE.fullmatch(_clean(control.get("onclick")))
        if (
            control.name != "a"
            or _clean(control.get("href")) != "javascript:"
            or match is None
            or match.group(1) != ep_idx
            or frozenset(control.get("class") or ()) != frozenset({"btn", "apply"})
        ):
            raise GoryeongContractError(f"course {ep_idx}: application identity drift")
        return label, _STATUS_MAP[label], True
    if control.name == "a" or control.get("href") or control.get("onclick"):
        raise GoryeongContractError(f"course {ep_idx}: inactive status became actionable")
    return label, _STATUS_MAP[label], False


def _parse_row(node: Any, page: int, sequence: int) -> _ListedCourse:
    cells = node.find_all("td", recursive=False)
    if len(cells) != 7:
        raise GoryeongContractError(f"page {page}: course cell count changed")
    if not _clean(cells[0].get_text(" ", strip=True)).replace(",", "").isdigit():
        raise GoryeongContractError(f"page {page}: source ordinal changed")
    links = cells[3].select(":scope > a[href]")
    if len(links) != 1:
        raise GoryeongContractError(f"page {page}: detail link count changed")
    href = _clean(links[0].get("href"))
    parsed = urlparse(href)
    try:
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise GoryeongContractError("malformed detail identity") from exc
    if (
        parsed.path != GORYEONG_DETAIL_PATH
        or query[:1] != [("IDX", GORYEONG_IDX)]
        or len(query) != 2
        or query[1][0] != "epIdx"
        or _IDENTITY_RE.fullmatch(query[1][1]) is None
    ):
        raise GoryeongContractError("detail identity binding changed")
    ep_idx = query[1][1]
    if href != urlparse(_detail_url(ep_idx)).path + "?" + urlparse(_detail_url(ep_idx)).query:
        raise GoryeongContractError(f"course {ep_idx}: non-canonical detail URL")
    title_match = _TITLE_CAPACITY_RE.fullmatch(_clean(links[0].get_text(" ", strip=True)))
    if title_match is None:
        raise GoryeongContractError(f"course {ep_idx}: title/capacity suffix changed")
    title = _clean(title_match.group("title"))
    capacity = int(title_match.group("capacity").replace(",", ""))
    capacity_current = int(title_match.group("current").replace(",", ""))
    category = _clean(cells[1].get_text(" ", strip=True))
    institution = _clean(cells[2].get_text(" ", strip=True))
    target = _clean(cells[4].get_text(" ", strip=True))
    if not title or category not in GORYEONG_INTERNAL_TYPES or institution not in GORYEONG_INSTITUTIONS:
        raise GoryeongContractError(f"course {ep_idx}: title/category/institution changed")
    apply_period = _period_cell(cells[5], "접수기간")
    event_period = _period_cell(cells[5], "교육기간")
    apply_start, apply_end = _date_range(apply_period, f"course {ep_idx} application")
    event_start, event_end = _date_range(event_period, f"course {ep_idx} event")
    source_status, status, available = _parse_status(cells[6], ep_idx)
    public = " ".join((title, category, institution, target))
    if _PHONE_RE.search(public) or _EMAIL_RE.search(public) or _RESIDENT_RE.search(public):
        raise GoryeongContractError(f"course {ep_idx}: list fields contain contact/PII")
    return _ListedCourse(
        ep_idx=ep_idx,
        title=title,
        source_institution=institution,
        category=category,
        target=target,
        apply_period=apply_period,
        event_period=event_period,
        apply_start=apply_start,
        apply_end=apply_end,
        event_start=event_start,
        event_end=event_end,
        capacity=capacity,
        capacity_current=capacity_current,
        source_status=source_status,
        status=status,
        reservation_available=available,
        page=page,
        sequence=sequence,
    )


def _parse_page(
    soup: BeautifulSoup,
    filter_value: Optional[_Filter],
    page: int,
) -> _Page:
    _owner_contract(soup)
    effective = _form_contract(soup, filter_value, page)
    boards = soup.select("#content .boardList") or soup.select(".boardList")
    if len(boards) != 2:
        raise GoryeongContractError("offline/K-MOOC ledger boundary changed")
    if boards[0].select("a[href*='/lifelong/eduProgram/detail2.do']"):
        raise GoryeongContractError("K-MOOC detail leaked into offline ledger")
    if boards[1].select("a[href*='/lifelong/eduProgram/detail.do?']"):
        raise GoryeongContractError("municipal detail leaked into K-MOOC ledger")
    for field in boards[1].select("tbody tr td.field"):
        if _clean(field.get_text(" ", strip=True)) != "K-MOOC":
            raise GoryeongContractError("external K-MOOC owner boundary changed")
    board = boards[0]
    table = board.select("table.dataTable")
    if len(table) != 1:
        raise GoryeongContractError("offline course table changed")
    headers = tuple(_clean(x.get_text(" ", strip=True)) for x in table[0].select("thead th"))
    if headers != _TABLE_HEADERS:
        raise GoryeongContractError("offline course headers changed")
    headings = [_clean(x.get_text(" ", strip=True)) for x in table[0].select("tbody > h3")]
    if headings != ["오프라인 강좌"]:
        raise GoryeongContractError("offline course heading changed")
    row_nodes = table[0].select("tbody > tr")
    empty = board.select(":scope > .noData")
    if row_nodes:
        if empty:
            raise GoryeongContractError("rows and empty sentinel appeared together")
        rows = tuple(_parse_row(node, page, index) for index, node in enumerate(row_nodes, 1))
    else:
        if len(empty) != 1 or _clean(empty[0].get_text(" ", strip=True)) != "등록된 교육강좌가 없습니다.":
            raise GoryeongContractError("exact offline empty sentinel changed")
        rows = ()
    if len(rows) > GORYEONG_PAGE_SIZE:
        raise GoryeongContractError(f"page {page}: page-size overflow")
    ids = [row.ep_idx for row in rows]
    if len(ids) != len(set(ids)):
        raise GoryeongContractError(f"page {page}: duplicate course identity")
    return _Page(page, effective, _pager_contract(board), rows)


def _listed_signature(row: _ListedCourse) -> tuple[Any, ...]:
    return (
        row.ep_idx,
        row.title,
        row.source_institution,
        row.category,
        row.target,
        row.apply_period,
        row.event_period,
        row.capacity,
        row.capacity_current,
        row.source_status,
        row.reservation_available,
        row.page,
        row.sequence,
    )


def _ledger_signature(rows: Iterable[_ListedCourse]) -> tuple[Any, ...]:
    return tuple(_listed_signature(row) for row in rows)


def _collect_pages(
    session: Any,
    filter_value: Optional[_Filter],
    timeout: int,
    max_pages: int,
    fetcher: Fetcher,
    meta: dict[str, Any],
) -> tuple[list[_ListedCourse], int]:
    def fetch(page: int) -> _Page:
        meta["list_requests"] += 1
        soup = _fetch_soup(
            session,
            _list_url(
                filter_value.name if filter_value else None,
                filter_value.value if filter_value else None,
                page,
            ),
            timeout,
            fetcher,
            meta,
        )
        return _parse_page(soup, filter_value, page)

    first = fetch(1)
    last = first.advertised_last_page
    if last < 1 or last > max_pages:
        meta["source_cap_reached"] = True
        raise GoryeongContractError(f"advertised last page {last} exceeds max_pages={max_pages}")
    if first.effective_page != 1:
        raise GoryeongContractError("first page effective binding changed")
    if not first.rows:
        if last != 1:
            raise GoryeongContractError("empty first page advertises later data")
        return [], 1
    pages = {1: first}
    for page in range(2, last + 1):
        current = fetch(page)
        if current.advertised_last_page != last or current.effective_page != page:
            raise GoryeongContractError(f"page {page}: pagination binding changed")
        pages[page] = current
    for page, current in pages.items():
        if page < last and len(current.rows) != GORYEONG_PAGE_SIZE:
            raise GoryeongContractError(f"page {page}: premature short page")
    if not pages[last].rows:
        raise GoryeongContractError("advertised last page is empty")
    sentinel = fetch(last + 1)
    meta["post_last_requests"] += 1
    if (
        sentinel.rows
        or sentinel.advertised_last_page != last
        or sentinel.effective_page != last
    ):
        raise GoryeongContractError("post-last page is not the exact empty boundary")
    rows = [row for number in sorted(pages) for row in pages[number].rows]
    identities = [row.ep_idx for row in rows]
    if len(identities) != len(set(identities)):
        raise GoryeongContractError("duplicate course identity across pages")
    return rows, last


def _table_fields(table: Any, expected: tuple[tuple[str, ...], ...], label: str) -> dict[str, str]:
    rows = table.select("tbody > tr") or table.select("tr")
    if len(rows) != len(expected):
        raise GoryeongContractError(f"{label}: detail row count changed")
    output: dict[str, str] = {}
    actual_shape: list[tuple[str, ...]] = []
    for row in rows:
        cells = row.find_all(["th", "td"], recursive=False)
        labels: list[str] = []
        index = 0
        while index < len(cells):
            cell = cells[index]
            if "th" not in (cell.get("class") or ()) and cell.name != "th":
                raise GoryeongContractError(f"{label}: detail label cell changed")
            if index + 1 >= len(cells):
                raise GoryeongContractError(f"{label}: detail value cell missing")
            field = _clean(cell.get_text(" ", strip=True))
            if field in output:
                raise GoryeongContractError(f"{label}: duplicate detail field {field}")
            output[field] = _clean(cells[index + 1].get_text(" ", strip=True))
            labels.append(field)
            index += 2
        actual_shape.append(tuple(labels))
    if tuple(actual_shape) != expected:
        raise GoryeongContractError(f"{label}: detail field vocabulary changed")
    return output


def _count(value: str, label: str) -> int:
    match = _COUNT_RE.fullmatch(_clean(value))
    if match is None:
        raise GoryeongContractError(f"{label}: count shape changed")
    return int(match.group(1).replace(",", ""))


def _target_extra(target: Any) -> Mapping[str, Any]:
    value = _target_value(target, "extra")
    return value if isinstance(value, Mapping) else {}


def _parse_detail(
    target: Any,
    listed: _ListedCourse,
    soup: BeautifulSoup,
) -> dict[str, Any]:
    _owner_contract(soup)
    wrappers = soup.select(".viewCourse")
    if len(wrappers) != 1:
        raise GoryeongContractError(f"course {listed.ep_idx}: detail wrapper changed")
    wrapper = wrappers[0]
    titles = wrapper.select(".viewTop h3.topTit")
    title = _clean(titles[0].get_text(" ", strip=True)) if len(titles) == 1 else ""
    if title != listed.title:
        raise GoryeongContractError(f"course {listed.ep_idx}: list/detail title mismatch")
    tables = wrapper.select("table.conTable")
    if len(tables) != 2:
        raise GoryeongContractError(f"course {listed.ep_idx}: detail table count changed")
    fields = _table_fields(tables[0], _DETAIL_ROWS, f"course {listed.ep_idx}")
    _table_fields(tables[1], _OTHER_DETAIL_ROWS, f"course {listed.ep_idx} other")
    apply_start, apply_end = _date_range(fields["접수기간"], f"course {listed.ep_idx} detail application")
    event_start, event_end = _date_range(fields["교육기간"], f"course {listed.ep_idx} detail event")
    if (
        (apply_start, apply_end) != (listed.apply_start, listed.apply_end)
        or (event_start, event_end) != (listed.event_start, listed.event_end)
        or fields["모집대상"] != listed.target
        or _count(fields["모집인원"], f"course {listed.ep_idx} capacity") != listed.capacity
        or _count(fields["신청인원"], f"course {listed.ep_idx} current capacity")
        != listed.capacity_current
    ):
        raise GoryeongContractError(f"course {listed.ep_idx}: list/detail fields disagree")
    controls = wrapper.select("a.submitBtn")
    control_match = (
        _DETAIL_ONCLICK_RE.fullmatch(_clean(controls[0].get("onclick")))
        if len(controls) == 1
        else None
    )
    if (
        len(controls) != 1
        or _clean(controls[0].get("href")) != "javascript:"
        or _clean(controls[0].get_text(" ", strip=True)) != "신청하기"
        or control_match is None
        or control_match.group(1) != listed.ep_idx
    ):
        raise GoryeongContractError(f"course {listed.ep_idx}: detail identity control changed")
    unsafe_inputs = wrapper.select(
        "input[name], textarea[name], select[name], form[action*='apply'], form[action*='member']"
    )
    if unsafe_inputs:
        raise GoryeongContractError(f"course {listed.ep_idx}: applicant fields entered public detail")
    schedule = re.sub(r"(?<=\d);(?=\d)", ":", fields["교육시간"])
    safe_text = " ".join(
        (
            listed.title,
            listed.target,
            fields["위치"],
            schedule,
            fields["수강료"],
            fields["준비물"],
        )
    )
    if _PHONE_RE.search(safe_text) or _EMAIL_RE.search(safe_text) or _RESIDENT_RE.search(safe_text):
        raise GoryeongContractError(f"course {listed.ep_idx}: persisted detail fields contain contact/PII")
    branch = _BRANCH_BY_SOURCE[listed.source_institution]
    extra = _target_extra(target)
    output: dict[str, Any] = {
        "provider": GORYEONG_PROVIDER,
        "provider_course_id": f"{GORYEONG_PROVIDER}:education:{listed.ep_idx}",
        "title": listed.title,
        "description": listed.title,
        "branch": branch.public_name,
        "branch_code": branch.code,
        "source_branch": listed.source_institution,
        "preserve_branch": True,
        "branch_url": GORYEONG_CANONICAL_URL,
        "branch_address": branch.address,
        "raw_url": listed.detail_url,
        "application_url": listed.detail_url if listed.reservation_available else "",
        "application_type": (
            "ONLINE_LOGIN_REQUIRED"
            if listed.reservation_available
            else "INFO_ONLY_DISABLED_SOURCE_CONTROL"
        ),
        "reservation_available": listed.reservation_available,
        "status": listed.status,
        "raw_status": listed.source_status,
        "period": listed.event_period,
        "apply_period": listed.apply_period,
        "schedule_raw": schedule,
        "start_date": listed.event_start.isoformat(),
        "end_date": listed.event_end.isoformat(),
        "apply_start_date": listed.apply_start.isoformat(),
        "apply_end_date": listed.apply_end.isoformat(),
        "target": listed.target,
        "capacity": listed.capacity,
        "capacity_total": listed.capacity,
        "capacity_current": listed.capacity_current,
        "fee": fields["수강료"],
        "preparation": fields["준비물"],
        "venue_name": fields["위치"],
        "room": fields["위치"],
        "address": branch.address,
        "venue_address": "",
        "category": listed.category,
        "collection_category": _clean(extra.get("collection_category") or "공공예약"),
        "domain_category": _clean(extra.get("domain_category") or "교육·강좌"),
        "operator_type": _clean(extra.get("operator_type") or "지자체/공공기관"),
        "source_group": _clean(extra.get("source_group") or "municipal_reservation"),
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": "complete_current_html+all_detail_html",
        "program_type": "교육",
        "municipality_code": GORYEONG_MUNICIPALITY_CODE,
        "municipality_name": GORYEONG_MUNICIPALITY_NAME,
        "municipality_full_name": GORYEONG_MUNICIPALITY_NAME,
        "raw_fields": {
            "parser": GORYEONG_PARSER,
            "identity": listed.ep_idx,
            "source_page": listed.page,
            "source_institution": listed.source_institution,
            "source_category": listed.category,
            "source_status": listed.source_status,
            "source_apply_period": listed.apply_period,
            "source_event_period": listed.event_period,
            "source_schedule": schedule,
            "detail_verified": True,
            "list_detail_binding": "epIdx+title+dates+target+capacity",
            "detail_application_control_verified": True,
            "list_status_is_authoritative": True,
            "application_endpoint_fetched": False,
            "attachment_endpoint_fetched": False,
            "pii_endpoint_fetched": False,
            "discarded_fields": [
                "접수처",
                "모집방법",
                "교육과정",
                "수강안내",
                "첨부파일",
                "편의제공",
                "기타사항",
            ],
            "embedded_kmooc_excluded": True,
        },
    }
    return output


def _partition_ids(rows: Iterable[_ListedCourse]) -> set[str]:
    return {row.ep_idx for row in rows}


def _reconcile_disjoint(
    canonical: list[_ListedCourse],
    partitions: Mapping[str, list[_ListedCourse]],
    kind: str,
) -> None:
    canonical_ids = _partition_ids(canonical)
    seen: set[str] = set()
    for value, rows in partitions.items():
        ids = _partition_ids(rows)
        if len(ids) != len(rows) or seen & ids:
            raise GoryeongContractError(f"{kind} partitions overlap or duplicate identities")
        for row in rows:
            if kind == "institution" and row.source_institution != value:
                raise GoryeongContractError(f"institution partition {value} leaked another branch")
            if kind == "type" and row.category != value:
                raise GoryeongContractError(f"type partition {value} leaked another category")
        seen.update(ids)
    if seen != canonical_ids:
        raise GoryeongContractError(f"{kind} partitions do not cover canonical ledger")


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity not in seen:
            seen.add(identity)
            output.append(row)
    return output


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors = [f"forbidden key {key}" for key in _FORBIDDEN_ROW_KEYS if key in row]
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping):
        errors.append("raw_fields missing")
        return errors
    serialized = " ".join(_clean(value) for value in row.values() if isinstance(value, str))
    if _PHONE_RE.search(serialized) or _EMAIL_RE.search(serialized) or _RESIDENT_RE.search(serialized):
        errors.append("public row contains contact/PII")
    forbidden_url_tokens = ("/front/downFile.do", "/file/readFile", "forms.gle", "myApplyList")
    for key, value in row.items():
        if isinstance(value, str) and any(token.lower() in value.lower() for token in forbidden_url_tokens):
            errors.append(f"unsafe endpoint persisted in {key}")
    return errors


def _initial_meta() -> dict[str, Any]:
    return {
        "municipality_code": GORYEONG_MUNICIPALITY_CODE,
        "municipality_full_name": GORYEONG_MUNICIPALITY_NAME,
        "owner_provider": GORYEONG_PROVIDER,
        "canonical_provider": GORYEONG_PROVIDER,
        "canonical_candidate_id": GORYEONG_CANONICAL_CANDIDATE_ID,
        "canonical_url": GORYEONG_CANONICAL_URL,
        "canonical_url_sha256": GORYEONG_CANONICAL_URL_SHA256,
        "legacy_target_url": GORYEONG_LEGACY_URL,
        "provider_decision": "retain incumbent provider and retarget stale detail to complete current list",
        "ownership_scope": GORYEONG_OWNERSHIP_SCOPE,
        "candidate_audit": {key: dict(value) for key, value in GORYEONG_CANDIDATE_AUDIT.items()},
        "owner_boundaries": [dict(value) for value in GORYEONG_OWNER_BOUNDARIES],
        "parser": GORYEONG_PARSER,
        "recommended_max_pages": GORYEONG_RECOMMENDED_MAX_PAGES,
        "recommended_detail_limit": GORYEONG_RECOMMENDED_DETAIL_LIMIT,
        "recommended_timeout_seconds": 30,
        "fetch_attempts": GORYEONG_FETCH_ATTEMPTS,
        "max_html_bytes": GORYEONG_MAX_HTML_BYTES,
        "live_audit_baseline": dict(GORYEONG_LIVE_AUDIT_BASELINE),
        "source_requests": 0,
        "request_attempts": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "post_last_requests": 0,
        "source_rows": 0,
        "current_source_count": 0,
        "expired_source_count": 0,
        "returned_count": 0,
        "source_cap_reached": False,
        "pagination_complete": False,
        "institution_partition_complete": False,
        "type_partition_complete": False,
        "status_partition_complete": False,
        "partition_overlap_count": 0,
        "full_ledger_rechecked_after_details": False,
        "details_complete": False,
        "privacy_violations": 0,
        "semantic_duplicate_count": 0,
        "application_endpoints_called": 0,
        "attachment_endpoints_called": 0,
        "pii_endpoints_called": 0,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "configured_collection_error": "",
    }


def collect_goryeong_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = GORYEONG_RECOMMENDED_MAX_PAGES,
    detail_limit: int = GORYEONG_RECOMMENDED_DETAIL_LIMIT,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one atomic, complete current Goryeong education snapshot."""

    meta = _initial_meta()
    if not is_goryeong_education_target(target):
        meta["configured_collection_error"] = "target does not match exact retained Goryeong owner"
        return [], GORYEONG_PARSER, meta
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta["configured_collection_error"] = "managed session_factory injection is required"
            return [], GORYEONG_PARSER, meta
        session_factory = _raw_session
    try:
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
        ):
            raise ValueError("timeout/max_pages/detail_limit are invalid")
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], GORYEONG_PARSER, meta

    current_fetcher = fetcher or _default_fetcher
    session = session_factory()
    try:
        canonical, last_page = _collect_pages(
            session, None, timeout, max_pages, current_fetcher, meta
        )
        canonical_ids = _partition_ids(canonical)
        if len(canonical_ids) != len(canonical):
            raise GoryeongContractError("canonical ledger duplicated course identities")

        institution_partitions: dict[str, list[_ListedCourse]] = {}
        for value in GORYEONG_INSTITUTIONS:
            institution_partitions[value], _ = _collect_pages(
                session,
                _Filter("searchInst", value),
                timeout,
                max_pages,
                current_fetcher,
                meta,
            )
        type_partitions: dict[str, list[_ListedCourse]] = {}
        for value in GORYEONG_INTERNAL_TYPES:
            type_partitions[value], _ = _collect_pages(
                session,
                _Filter("searchType", value),
                timeout,
                max_pages,
                current_fetcher,
                meta,
            )
        status_partitions: dict[str, list[_ListedCourse]] = {}
        for code, _ in GORYEONG_STATUS_FILTERS:
            status_partitions[code], _ = _collect_pages(
                session,
                _Filter("searchStatus", code),
                timeout,
                max_pages,
                current_fetcher,
                meta,
            )
        _reconcile_disjoint(canonical, institution_partitions, "institution")
        _reconcile_disjoint(canonical, type_partitions, "type")
        _reconcile_disjoint(canonical, status_partitions, "status")

        current = [row for row in canonical if row.event_end >= cutoff]
        if len(current) > detail_limit:
            meta["source_cap_reached"] = True
            raise GoryeongContractError(
                f"detail_limit {detail_limit} below required {len(current)}"
            )
        meta.update(
            {
                "cutoff": cutoff.isoformat(),
                "pages": last_page,
                "post_last_page": last_page + 1 if canonical else None,
                "source_rows": len(canonical),
                "source_total": len(canonical),
                "source_identity_count": len(canonical_ids),
                "source_ep_indices": sorted(canonical_ids, key=int),
                "current_source_count": len(current),
                "expired_source_count": len(canonical) - len(current),
                "source_status_counts": dict(Counter(row.source_status for row in canonical)),
                "source_institution_counts": dict(Counter(row.source_institution for row in canonical)),
                "source_type_counts": dict(Counter(row.category for row in canonical)),
                "institution_filter_counts": {key: len(value) for key, value in institution_partitions.items()},
                "type_filter_counts": {key: len(value) for key, value in type_partitions.items()},
                "status_filter_counts": {key: len(value) for key, value in status_partitions.items()},
                "institution_partition_union_count": len(set().union(*(_partition_ids(value) for value in institution_partitions.values()))),
                "type_partition_union_count": len(set().union(*(_partition_ids(value) for value in type_partitions.values()))),
                "status_partition_union_count": len(set().union(*(_partition_ids(value) for value in status_partitions.values()))),
                "empty_partition_count": sum(
                    not rows
                    for partitions in (institution_partitions, type_partitions, status_partitions)
                    for rows in partitions.values()
                ),
                "institution_partition_complete": True,
                "type_partition_complete": True,
                "status_partition_complete": True,
                "pagination_complete": True,
            }
        )

        rows: list[dict[str, Any]] = []
        for listed in current:
            meta["detail_pages"] += 1
            soup = _fetch_soup(
                session,
                listed.detail_url,
                timeout,
                current_fetcher,
                meta,
            )
            rows.append(_parse_detail(target, listed, soup))

        checked, checked_last = _collect_pages(
            session, None, timeout, max_pages, current_fetcher, meta
        )
        if checked_last != last_page or _ledger_signature(checked) != _ledger_signature(canonical):
            raise GoryeongContractError("full current ledger stability recheck changed")
        meta["full_ledger_rechecked_after_details"] = True

        rows.sort(key=lambda row: (row["start_date"], row["provider_course_id"]))
        rows = list((dedupe_rows or _dedupe)(rows))
        expected_ids = {
            f"{GORYEONG_PROVIDER}:education:{listed.ep_idx}" for listed in current
        }
        if len(rows) != len(current) or {
            _clean(row.get("provider_course_id")) for row in rows
        } != expected_ids:
            raise GoryeongContractError("dedupe changed the current course identity set")
        privacy_errors = [error for row in rows for error in _privacy_errors(row)]
        meta["privacy_violations"] = len(privacy_errors)
        if privacy_errors:
            raise GoryeongContractError("; ".join(privacy_errors[:5]))
        semantic_counts = Counter(
            (
                _clean(row.get("title")).casefold(),
                _clean(row.get("branch")),
                _clean(row.get("start_date")),
                _clean(row.get("end_date")),
                _clean(row.get("venue_name")),
            )
            for row in rows
        )
        semantic_duplicates = sum(count - 1 for count in semantic_counts.values() if count > 1)
        meta["semantic_duplicate_count"] = semantic_duplicates
        if semantic_duplicates:
            raise GoryeongContractError("semantic duplicate current courses detected")
        meta.update(
            {
                "returned_count": len(rows),
                "status_counts": dict(Counter(_clean(row.get("status")) for row in rows)),
                "raw_status_counts": dict(Counter(_clean(row.get("raw_status")) for row in rows)),
                "branch_counts": dict(Counter(_clean(row.get("branch")) for row in rows)),
                "source_branch_counts": dict(Counter(_clean(row.get("source_branch")) for row in rows)),
                "category_counts": dict(Counter(_clean(row.get("category")) for row in rows)),
                "application_control_count": sum(bool(row.get("reservation_available")) for row in rows),
                "actionable_application_count": sum(bool(row.get("reservation_available")) for row in rows),
                "details_complete": meta["detail_pages"] == len(current),
                "no_current_data": not rows,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
            }
        )
        return rows, GORYEONG_PARSER, meta
    except Exception as exc:
        meta.update(
            {
                "configured_collection_error": f"{type(exc).__name__}: {_clean(exc)}",
                "pagination_complete": False,
                "institution_partition_complete": False,
                "type_partition_complete": False,
                "status_partition_complete": False,
                "details_complete": False,
                "snapshot_complete": False,
                "full_snapshot_validated": False,
                "returned_count": 0,
            }
        )
        return [], GORYEONG_PARSER, meta
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


collect = collect_goryeong_education


__all__ = [
    "GORYEONG_PROVIDER",
    "GORYEONG_MUNICIPALITY_CODE",
    "GORYEONG_MUNICIPALITY_NAME",
    "GORYEONG_CANONICAL_URL",
    "GORYEONG_LEGACY_URL",
    "GORYEONG_CANONICAL_URL_SHA256",
    "GORYEONG_CANONICAL_CANDIDATE_ID",
    "GORYEONG_LEGACY_CANDIDATE_ID",
    "GORYEONG_NOTICE_CANDIDATE_ID",
    "GORYEONG_PAST_CANDIDATE_ID",
    "GORYEONG_INSTITUTIONS",
    "GORYEONG_INTERNAL_TYPES",
    "GORYEONG_STATUS_FILTERS",
    "GORYEONG_BRANCHES",
    "GORYEONG_CANDIDATE_AUDIT",
    "GORYEONG_OWNER_BOUNDARIES",
    "GORYEONG_LIVE_AUDIT_BASELINE",
    "GORYEONG_RECOMMENDED_MAX_PAGES",
    "GORYEONG_RECOMMENDED_DETAIL_LIMIT",
    "GORYEONG_PARSER",
    "GoryeongContractError",
    "collect",
    "collect_goryeong_education",
    "is_target",
    "is_goryeong_education_target",
]
