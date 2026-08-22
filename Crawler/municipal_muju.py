"""Complete, fail-closed collector for Muju-gun public education courses.

The incumbent provider already points at the county lifelong-learning
application ledger.  That ledger is canonical for application identities, but
it is not the whole public programme catalogue: the same official portal also
publishes a current regular-programme plan and a current ``모두배움터`` plan.
The regular plan is reconciled against (and yields to) the application ledger;
the ``모두배움터`` rows are separate information-only identities.

The application ledger exposes five rows per page and clamps every request
after the advertised final page to the final five identities.  Consequently a
snapshot is released only after the exact post-last clamp and stable first,
final, and static-page rechecks agree.  Detail pages are fetched only for
current/future rows.  Public application controls are validated, but
application, authentication, personal-library, download, and applicant pages
are never requested.

Returned rows deliberately omit instructors, contacts, attachments, free-text
descriptions, current applicant/wait-list counts, and all applicant data.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from difflib import SequenceMatcher
import hashlib
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


MUJU_HOST = "www.muju.go.kr"
MUJU_PROVIDER = "MUNI_WWW_MUJU_GO_KR_953B498D"
MUJU_MUNICIPALITY_CODE = "5273000000"
MUJU_MUNICIPALITY_NAME = "전북특별자치도 무주군"
MUJU_BRANCH = "무주군평생교육원"

MUJU_LIST_PATH = "/lifelongedu/main/edusat/list.do"
MUJU_DETAIL_PATH = "/lifelongedu/main/edusat/view.do"
MUJU_APPLICATION_PATH = "/lifelongedu/main/edusat/regist.do"
MUJU_CONTENTS_PATH = "/lifelongedu/main/contents.do"
MUJU_URL = f"https://{MUJU_HOST}{MUJU_LIST_PATH}"
MUJU_REGULAR_PLAN_URL = (
    f"https://{MUJU_HOST}{MUJU_CONTENTS_PATH}?idx=4363"
)
MUJU_ALL_LEARNING_URL = (
    f"https://{MUJU_HOST}{MUJU_CONTENTS_PATH}?idx=4364"
)
MUJU_NOTICE_EVIDENCE_URL = (
    f"https://{MUJU_HOST}{MUJU_CONTENTS_PATH}?"
    "proc_type=view&a_num=51641524&b_num=779"
)
MUJU_AGRICULTURE_CANDIDATE_URL = (
    "https://www.muju.go.kr/index.9is?"
    "contentUid=ff8080816c5f9d47016cbd872754027e"
)

MUJU_PROVIDER_URL_SHA1 = "953B498DFCC0"
MUJU_CANONICAL_URL_SHA256 = "8EDCCB379970"
MUJU_AGRICULTURE_URL_SHA1 = "9EFA917B8145"
MUJU_AGRICULTURE_URL_SHA256 = "18D70C921457"
MUJU_CANONICAL_CANDIDATE_ID = "MUNI_IR_8EDCCB379970"
MUJU_AGRICULTURE_CANDIDATE_ID = "MUNI_IR_18D70C921457"
MUJU_AGRICULTURE_REVIEW_PROVIDER = "MUNI_WWW_MUJU_GO_KR_9EFA917B"

MUJU_CANDIDATE_DECISIONS: Mapping[str, str] = {
    MUJU_CANONICAL_CANDIDATE_ID: (
        "keep_incumbent_same_canonical_url_and_upgrade_complete_owner"
    ),
    MUJU_AGRICULTURE_CANDIDATE_ID: (
        "exclude_agriculture_section_landing_not_course_identity_ledger"
    ),
}
MUJU_INCUMBENT_DECISION = (
    "retain MUNI_WWW_MUJU_GO_KR_953B498D; URL is already canonical; "
    "replace partial first-page collection with the complete three-ledger audit"
)

MUJU_PAGE_SIZE = 5
MUJU_FETCH_ATTEMPTS = 2
MUJU_MAX_WORKERS = 4
MUJU_PARSER = (
    "muju_complete_lifelong_owner+125_identity_paginated_edusat+"
    "exact_post_last_final_identity_clamp+stable_first_final_static_boundaries+"
    "regular_plan_mirror_reconciliation+all_learning_district_partition+"
    "current_future_detail_binding+official_source_date_corrections+"
    "application_control_no_endpoint_fetch+pii_allowlist"
)
MUJU_OWNERSHIP_SCOPE = (
    "official_muju_lifelong_application_regular_plan_and_all_learning_programmes"
)

MUJU_CATEGORY_OPTIONS: Mapping[str, str] = {
    "105": "기초문해",
    "106": "학력보완",
    "107": "직업능력",
    "108": "문화예술",
    "109": "인문교양",
    "110": "시민참여",
    "111": "원데이클래스",
    "112": "지역으뜸인재육성사업",
    "113": "평생교육활성화",
}
MUJU_ALL_LEARNING_DISTRICTS = (
    "무주읍",
    "무풍면",
    "설천면",
    "적상면",
    "안성면",
    "부남면",
)

MUJU_OWNER_BOUNDARIES: Mapping[str, str] = {
    MUJU_URL: "canonical_application_identity_ledger_included",
    MUJU_REGULAR_PLAN_URL: "same_owner_regular_plan_reconciled_to_canonical_ids",
    MUJU_ALL_LEARNING_URL: "same_owner_information_only_programme_ledger_included",
    MUJU_AGRICULTURE_CANDIDATE_URL: (
        "general_agriculture_section_landing_without_course_identities"
    ),
    (
        "https://www.muju.go.kr/index.9is?"
        "contentUid=ff8080817a2ded9b017ba47020b63b1a"
    ): "separate_agriculture_department_static_annual_information_family",
    "https://www.muju.go.kr/lifelongedu/main/contents.do?idx=4384": (
        "unlinked_2025_resident_autonomy_archive_no_current_rows"
    ),
    "https://www.muju.go.kr/lifelongedu/main/contents.do?idx=4425": (
        "unlinked_2025_one_day_plan_archive_mirrored_by_canonical_ledger"
    ),
    "https://www.muju.go.kr/lifelongedu/main/req_room/list.do": (
        "room_rental_owner_not_course_identity_ledger"
    ),
    "https://library.muju.go.kr/main/edusat/list.do?sh_ct_idx=4": (
        "separate_county_library_application_identity_owner"
    ),
    "https://lib.jbe.go.kr/mjl/index.do": (
        "separate_provincial_education_office_library_owner"
    ),
    "https://www.mujuyouth.net/": "separate_youth_center_owner",
    "https://www.mujucc.or.kr/": "separate_culture_center_owner",
    "https://www.mujubokji.or.kr/": "separate_social_welfare_owner",
    "https://tour.muju.go.kr/art/index.do": "separate_museum_programme_owner",
}


@dataclass(frozen=True)
class MujuDateCorrection:
    identity: str
    source_period: str
    corrected_start: date
    corrected_end: date
    evidence: str


# Three 2025 records use the impossible date June 31.  They are expired, but
# their exact source anomalies still have to be acknowledged for a complete
# 125-identity traversal.  The live 2026 additional-recruitment record reverses
# the year of its end date; official notice 779 states Aug 2026 through Jun
# 2027, while the source's day component is 30.
MUJU_SOURCE_DATE_CORRECTIONS = (
    MujuDateCorrection(
        "93",
        "2025-03-04 ~ 2026-06-31",
        date(2025, 3, 4),
        date(2026, 6, 30),
        "calendar_normalization_of_impossible_2026-06-31",
    ),
    MujuDateCorrection(
        "114",
        "2025-03-19 ~ 2026-06-31",
        date(2025, 3, 19),
        date(2026, 6, 30),
        "calendar_normalization_of_impossible_2026-06-31",
    ),
    MujuDateCorrection(
        "146",
        "2025-09-22 ~ 2026-06-31",
        date(2025, 9, 22),
        date(2026, 6, 30),
        "calendar_normalization_of_impossible_2026-06-31",
    ),
    MujuDateCorrection(
        "202",
        "2026-08-15 ~ 2026-06-30",
        date(2026, 8, 15),
        date(2027, 6, 30),
        MUJU_NOTICE_EVIDENCE_URL,
    ),
)
_CORRECTION_BY_ID = {item.identity: item for item in MUJU_SOURCE_DATE_CORRECTIONS}


@dataclass(frozen=True)
class _OnlineCourse:
    identity: str
    title: str
    category: str
    detail_url: str
    list_control_url: str
    source_status: str
    event_start: date
    event_end: date
    raw_event_period: str
    apply_start: date
    apply_end: date
    raw_apply_period: str
    target: str
    capacity_total: Optional[int]
    page: int
    correction: Optional[MujuDateCorrection]


@dataclass(frozen=True)
class _StaticCourse:
    source_key: str
    identity: str
    title: str
    category: str
    branch: str
    venue: str
    target: str
    capacity_total: Optional[int]
    event_start: date
    event_end: date
    raw_event_period: str
    year: int
    raw_url: str


@dataclass(frozen=True)
class _StaticSource:
    key: str
    label: str
    url: str
    heading_phrase: str
    headers: tuple[str, ...]


MUJU_STATIC_SOURCES = (
    _StaticSource(
        "regular_plan",
        "평생교육원 프로그램",
        MUJU_REGULAR_PLAN_URL,
        "무주군평생교육원 프로그램 운영계획",
        (
            "프로그램명",
            "대상",
            "정원(명)",
            "운영기간",
            "횟수 (교육차시)",
            "내용",
            "강의실",
        ),
    ),
    _StaticSource(
        "all_learning",
        "모두배움터 프로그램",
        MUJU_ALL_LEARNING_URL,
        "모두배움터 운영 현황",
        ("해당 읍면", "교육장소", "프로그램명", "대상", "운영일정", "내용"),
    ),
)


class MujuContractError(RuntimeError):
    """Raised when the audited Muju public-source contract changes."""


Fetcher = Callable[..., Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"[1-9]\d*")
_TOTAL_RE = re.compile(r"총\s*([\d,]+)\s*개의 프로그램이 등록되어 있습니다\.")
_TITLE_RE = re.compile(r"^\[([^\]]+)\]\s*(.+)$")
_EVENT_RE = re.compile(
    r"^(20\d{2})-(\d{2})-(\d{2})"
    r"(?:\s*~\s*(20\d{2})-(\d{2})-(\d{2}))?$"
)
_LIST_APPLY_RE = re.compile(
    r"^(20\d{2})-(\d{2})-(\d{2})\s+([01]\d|2[0-3]):([0-5]\d)"
    r"\s*~\s*(20\d{2})-(\d{2})-(\d{2})\s+"
    r"([01]\d|2[0-3]):([0-5]\d)$"
)
_CAPACITY_RE = re.compile(
    r"^\[선착순\]\s*(\d+)\s*/\s*(\d+|제한없음)\s*(?:명)?"
    r"(?:\s*\(대기:\d+명\))?$"
)
_STATIC_RANGE_RE = re.compile(
    r"^(\d{1,2})\.\s*(\d{1,2})\.?\s*~\s*"
    r"(\d{1,2})\.\s*(\d{1,2})\.?$"
)
_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})년")
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,3}[-\s)]?\d{3,4}[-\s]?\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_LIST_FIELDS = ("신청기간", "운영기간", "수강대상", "모집인원")
_LIST_FIELDS_WITHOUT_TARGET = ("신청기간", "운영기간", "모집인원")
_DETAIL_REQUIRED = frozenset({"강좌기간", "신청기간", "모집인원"})
_DETAIL_ALLOWED = frozenset(
    {
        "강좌기간",
        "강좌시간",
        "신청기간",
        "수강대상",
        "모집인원",
        "강의실",
        "참가비",
        "강사",
        "첨부파일",
    }
)
_STATUS_CONTRACT: Mapping[str, tuple[str, str, str]] = {
    "수강신청": ("OPEN", "btn_ing", "신청중"),
    "신청준비": ("SCHEDULED", "btn_prepare", "신청준비"),
    "신청마감": ("CLOSED", "btn_end", "신청마감"),
    "기간종료": ("CLOSED", "btn_close", "기간종료"),
}


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def _target_extra(target: Any) -> Mapping[str, Any]:
    value = _target_value(target, "extra")
    return value if isinstance(value, Mapping) else {}


def _safe_base_url(url: str, *, path: str) -> bool:
    parsed = urlparse(url)
    return bool(
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == MUJU_HOST
        and parsed.port is None
        and parsed.path == path
        and not parsed.params
        and not parsed.username
        and not parsed.password
    )


def is_muju_education_target(target: Any) -> bool:
    parsed = urlparse(_target_url(target))
    return bool(
        _provider(target) == MUJU_PROVIDER
        and _safe_base_url(_target_url(target), path=MUJU_LIST_PATH)
        and not parsed.query
        and not parsed.fragment
    )


is_target = is_muju_education_target


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is not None:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _default_session_factory() -> requests.Session:
    current = requests.Session()
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
    return current


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def muju_list_url(page: int = 1) -> str:
    if page < 1:
        return ""
    if page == 1:
        return MUJU_URL
    return f"{MUJU_URL}?{urlencode((('v_page', str(page)),))}"


def _same_request_url(actual: str, requested: str) -> bool:
    left, right = urlparse(actual), urlparse(requested)
    return bool(
        _safe_base_url(actual, path=right.path)
        and left.path == right.path
        and parse_qs(left.query, keep_blank_values=True)
        == parse_qs(right.query, keep_blank_values=True)
        and not left.fragment
    )


def _response_soup(response: Any, requested: str) -> tuple[BeautifulSoup, str]:
    try:
        status = int(getattr(response, "status_code", 0))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise MujuContractError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise MujuContractError("HTTP redirects are not accepted")
    final_url = _clean(getattr(response, "url", ""))
    if not _same_request_url(final_url, requested):
        raise MujuContractError("source response URL changed")
    content = getattr(response, "content", None)
    if content is None:
        content = getattr(response, "text", None)
    if not content:
        raise MujuContractError("empty HTML response")
    return BeautifulSoup(content, "lxml"), final_url


def _request_soup(
    current: Any,
    url: str,
    *,
    timeout: int,
    fetcher: Optional[Fetcher],
) -> tuple[BeautifulSoup, str, int]:
    messages: list[str] = []
    for attempt in range(1, MUJU_FETCH_ATTEMPTS + 1):
        try:
            if fetcher is not None:
                result = fetcher(current, "GET", url, timeout=timeout, data={})
                if (
                    isinstance(result, tuple)
                    and len(result) == 2
                    and isinstance(result[0], BeautifulSoup)
                ):
                    soup, final_url = result
                    final_url = _clean(final_url or url)
                    if not _same_request_url(final_url, url):
                        raise MujuContractError("source response URL changed")
                    return soup, final_url, attempt
                if isinstance(result, BeautifulSoup):
                    return result, url, attempt
                if isinstance(result, (str, bytes, bytearray)):
                    if not result:
                        raise MujuContractError("empty HTML response")
                    return BeautifulSoup(result, "lxml"), url, attempt
                soup, final_url = _response_soup(result, url)
                return soup, final_url, attempt
            soup, final_url = _response_soup(current.get(url, timeout=timeout), url)
            return soup, final_url, attempt
        except Exception as exc:
            messages.append(f"attempt {attempt}: {type(exc).__name__}: {_clean(exc)}")
    raise MujuContractError("; ".join(messages))


def _digest(value: Any) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def _date_value(year: str, month: str, day: str) -> date:
    try:
        return date(int(year), int(month), int(day))
    except ValueError as exc:
        raise MujuContractError(f"invalid source date {year}-{month}-{day}") from exc


def _event_range(
    value: Any, identity: str
) -> tuple[date, date, Optional[MujuDateCorrection]]:
    text = _clean(value)
    correction = _CORRECTION_BY_ID.get(identity)
    if correction is not None and text == correction.source_period:
        return correction.corrected_start, correction.corrected_end, correction
    match = _EVENT_RE.fullmatch(text)
    if match is None:
        raise MujuContractError(f"course {identity}: malformed operation period {text!r}")
    values = match.groups()
    start = _date_value(*values[:3])
    end = start if values[3] is None else _date_value(*values[3:])
    if start > end:
        raise MujuContractError(f"course {identity}: reversed operation period")
    return start, end, None


def _list_apply_range(value: Any, identity: str) -> tuple[date, date]:
    text = _clean(value)
    match = _LIST_APPLY_RE.fullmatch(text)
    if match is None:
        raise MujuContractError(f"course {identity}: malformed application period")
    values = match.groups()
    start = _date_value(*values[:3])
    end = _date_value(*values[5:8])
    if start > end:
        raise MujuContractError(f"course {identity}: reversed application period")
    return start, end


def _detail_apply_range(value: Any, identity: str) -> tuple[date, date]:
    text = _clean(value)
    match = _EVENT_RE.fullmatch(text)
    if match is None or match.group(4) is None:
        raise MujuContractError(f"course {identity}: malformed detail application period")
    start = _date_value(*match.groups()[:3])
    end = _date_value(*match.groups()[3:])
    if start > end:
        raise MujuContractError(f"course {identity}: reversed detail application period")
    return start, end


def _capacity(value: Any, identity: str) -> tuple[int, Optional[int]]:
    match = _CAPACITY_RE.fullmatch(_clean(value))
    if match is None:
        raise MujuContractError(f"course {identity}: malformed aggregate capacity")
    current = int(match.group(1))
    total = None if match.group(2) == "제한없음" else int(match.group(2))
    if total is not None and current > total:
        raise MujuContractError(f"course {identity}: aggregate capacity is inconsistent")
    return current, total


def _validate_form(soup: BeautifulSoup) -> None:
    forms = soup.select("#board form[name='frm_edu']")
    if len(forms) != 1:
        raise MujuContractError("expected one unfiltered frm_edu form")
    form = forms[0]
    action = urlparse(urljoin(MUJU_URL, _clean(form.get("action"))))
    if (
        _clean(form.get("method")).lower() != "get"
        or action.path != MUJU_LIST_PATH
        or action.query
    ):
        raise MujuContractError("frm_edu method/action changed")
    category = form.select("select[name='sh_ct_idx2']")
    if len(category) != 1:
        raise MujuContractError("category selector changed")
    options = [
        (_clean(node.get("value")), _clean(node.get_text(" ", strip=True)))
        for node in category[0].select("option")
    ]
    expected = [("", "분류선택"), *MUJU_CATEGORY_OPTIONS.items()]
    if options != expected:
        raise MujuContractError("official category vocabulary changed")
    if any(
        _clean(node.get("value")) and node.has_attr("selected")
        for node in category[0].select("option")
    ):
        raise MujuContractError("unfiltered category form became filtered")
    search = form.select("select[name='v_search']")
    search_options = (
        [
            (_clean(node.get("value")), _clean(node.get_text(" ", strip=True)))
            for node in search[0].select("option")
        ]
        if len(search) == 1
        else []
    )
    if search_options != [
        ("", "검색항목"),
        ("edu_subject", "교육명"),
        ("edu_content", "내용"),
    ]:
        raise MujuContractError("search field vocabulary changed")
    hidden = form.select("input[name='sh_ct_idx']")
    keyword = form.select("input[name='v_keyword']")
    if (
        len(hidden) != 1
        or _clean(hidden[0].get("value"))
        or len(keyword) != 1
        or _clean(keyword[0].get("value"))
    ):
        raise MujuContractError("unfiltered search form contains a filter")


def _declared_total(soup: BeautifulSoup) -> tuple[int, int]:
    nodes = soup.select("#board .board_total_left")
    if len(nodes) != 1:
        raise MujuContractError("expected one declared programme total")
    match = _TOTAL_RE.fullmatch(_clean(nodes[0].get_text(" ", strip=True)))
    if match is None:
        raise MujuContractError("declared programme total changed")
    total = int(match.group(1).replace(",", ""))
    return total, max(1, math.ceil(total / MUJU_PAGE_SIZE))


def _validate_pagination(
    soup: BeautifulSoup, *, requested_page: int, last_page: int, sentinel: bool
) -> None:
    pagers = soup.select("#board .board_paginate")
    if len(pagers) != 1:
        raise MujuContractError("expected one pagination block")
    current = pagers[0].select(":scope > strong")
    if sentinel:
        if current:
            raise MujuContractError("post-last page unexpectedly declares a current page")
    elif len(current) != 1 or _clean(current[0].get_text(" ", strip=True)) != str(
        requested_page
    ):
        raise MujuContractError("pagination current page mismatch")
    for anchor in pagers[0].select("a[href]"):
        parsed = urlparse(urljoin(MUJU_URL, _clean(anchor.get("href"))))
        query = parse_qs(parsed.query, keep_blank_values=True)
        if (
            not _safe_base_url(parsed.geturl(), path=MUJU_LIST_PATH)
            or set(query) != {"v_page"}
            or len(query["v_page"]) != 1
            or not query["v_page"][0].isdigit()
            or not 1 <= int(query["v_page"][0]) <= last_page
        ):
            raise MujuContractError("malformed pagination link")


def _validate_prepage(value: str, requested_page: int) -> bool:
    parsed = urlparse(value)
    query = parse_qs(parsed.query, keep_blank_values=True)
    expected = {} if requested_page == 1 else {"v_page": [str(requested_page)]}
    return bool(
        parsed.path == MUJU_LIST_PATH
        and query == expected
        and not parsed.scheme
        and not parsed.netloc
        and not parsed.fragment
    )


def _validated_identity_url(
    value: Any, *, path: str, identity: str, prepage_kind: str, page: int
) -> str:
    raw = _clean(value)
    parsed = urlparse(urljoin(MUJU_URL, raw))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        not _safe_base_url(parsed.geturl(), path=path)
        or set(query) != {"edu_idx", "prepage"}
        or query.get("edu_idx") != [identity]
        or any(len(values) != 1 for values in query.values())
    ):
        raise MujuContractError(f"course {identity}: malformed {prepage_kind} route")
    prepage = query["prepage"][0]
    if prepage_kind == "detail" and not _validate_prepage(prepage, page):
        raise MujuContractError(f"course {identity}: detail prepage is not identity-bound")
    if prepage_kind == "application":
        source = urlparse(prepage)
        source_query = parse_qs(source.query, keep_blank_values=True)
        if (
            source.path != MUJU_DETAIL_PATH
            or source_query.get("edu_idx") != [identity]
            or not set(source_query).issubset({"edu_idx", "prepage"})
            or source.scheme
            or source.netloc
        ):
            raise MujuContractError(
                f"course {identity}: application prepage is not identity-bound"
            )
    return parsed.geturl()


def _fields(card: Any) -> dict[str, str]:
    output: dict[str, str] = {}
    for block in card.select(".sm_box dl"):
        labels, values = block.select(":scope > dt"), block.select(":scope > dd")
        if len(labels) != 1 or len(values) != 1:
            raise MujuContractError("malformed online list field")
        label = _clean(labels[0].get_text(" ", strip=True))
        if not label or label in output:
            raise MujuContractError("duplicate or empty online list field")
        output[label] = _clean(values[0].get_text(" ", strip=True))
    if tuple(output) not in (_LIST_FIELDS, _LIST_FIELDS_WITHOUT_TARGET):
        raise MujuContractError("online list field vocabulary changed")
    return output


def _online_signature(rows: Iterable[_OnlineCourse]) -> str:
    return _digest(
        tuple(
            (
                row.identity,
                row.title,
                row.category,
                row.source_status,
                row.raw_event_period,
                row.raw_apply_period,
                row.target,
                row.capacity_total,
                bool(row.list_control_url),
            )
            for row in rows
        )
    )


def _parse_online_page(
    soup: BeautifulSoup, *, requested_page: int, sentinel: bool = False
) -> tuple[int, int, list[_OnlineCourse], str]:
    _validate_form(soup)
    total, last_page = _declared_total(soup)
    _validate_pagination(
        soup, requested_page=requested_page, last_page=last_page, sentinel=sentinel
    )
    containers = soup.select("#board .lesson > ul")
    if len(containers) != 1:
        raise MujuContractError("expected one canonical online course list")
    nodes = containers[0].select(":scope > li")
    course_nodes = [node for node in nodes if node.select_one(".tit a[href]")]
    if total and len(nodes) != len(course_nodes):
        raise MujuContractError("non-course placeholder mixed into nonempty list")
    expected = (
        min(MUJU_PAGE_SIZE, max(0, total - (requested_page - 1) * MUJU_PAGE_SIZE))
        if not sentinel
        else min(
            MUJU_PAGE_SIZE,
            max(0, total - (last_page - 1) * MUJU_PAGE_SIZE),
        )
    )
    if len(course_nodes) != expected:
        raise MujuContractError(
            f"page {requested_page}: expected {expected} cards, found {len(course_nodes)}"
        )

    rows: list[_OnlineCourse] = []
    for card in course_nodes:
        anchors = card.select(".tit a[href]")
        if len(anchors) != 1:
            raise MujuContractError("expected one detail anchor per course")
        preliminary = urlparse(urljoin(MUJU_URL, _clean(anchors[0].get("href"))))
        preliminary_query = parse_qs(preliminary.query, keep_blank_values=True)
        identity = _clean((preliminary_query.get("edu_idx") or [""])[0])
        if not _IDENTITY_RE.fullmatch(identity):
            raise MujuContractError("online course identity is missing")
        detail_url = _validated_identity_url(
            anchors[0].get("href"),
            path=MUJU_DETAIL_PATH,
            identity=identity,
            prepage_kind="detail",
            page=requested_page,
        )
        labelled_title = _clean(anchors[0].get_text(" ", strip=True))
        title_match = _TITLE_RE.fullmatch(labelled_title)
        if title_match is None:
            raise MujuContractError(f"course {identity}: category/title contract changed")
        category, title = (_clean(value) for value in title_match.groups())
        if category not in MUJU_CATEGORY_OPTIONS.values() or not title:
            raise MujuContractError(f"course {identity}: unknown source category")

        values = _fields(card)
        apply_start, apply_end = _list_apply_range(values["신청기간"], identity)
        event_start, event_end, correction = _event_range(values["운영기간"], identity)
        _, capacity_total = _capacity(values["모집인원"], identity)

        boxes = card.select(":scope > .btn_box")
        if len(boxes) != 1:
            raise MujuContractError(f"course {identity}: button box changed")
        buttons = boxes[0].select(":scope > a[href]")
        checks = [button for button in buttons if "btn_check" in button.get("class", [])]
        controls = [button for button in buttons if "btn_check" not in button.get("class", [])]
        if len(checks) != 1 or len(controls) != 1 or len(buttons) != 2:
            raise MujuContractError(f"course {identity}: list controls changed")
        check = urlparse(urljoin(MUJU_URL, _clean(checks[0].get("href"))))
        if (
            not _safe_base_url(
                check.geturl(), path="/lifelongedu/main/site/mylib/myEdu.do"
            )
            or set(parse_qs(check.query, keep_blank_values=True)) != {"prepage"}
        ):
            raise MujuContractError(f"course {identity}: personal-library control changed")
        control = controls[0]
        source_status = _clean(control.get_text(" ", strip=True))
        if source_status not in _STATUS_CONTRACT:
            raise MujuContractError(f"course {identity}: unknown source status")
        _, expected_class, _ = _STATUS_CONTRACT[source_status]
        classes = set(control.get("class", []))
        if classes != {"btn_sm", expected_class}:
            raise MujuContractError(f"course {identity}: status class changed")
        list_control_url = ""
        if source_status == "수강신청":
            list_control_url = _validated_identity_url(
                control.get("href"),
                path=MUJU_APPLICATION_PATH,
                identity=identity,
                prepage_kind="detail",
                page=requested_page,
            )
        elif _clean(control.get("href")) != "#javascript:;":
            raise MujuContractError(f"course {identity}: inactive control became navigable")

        rows.append(
            _OnlineCourse(
                identity=identity,
                title=title,
                category=category,
                detail_url=detail_url,
                list_control_url=list_control_url,
                source_status=source_status,
                event_start=event_start,
                event_end=event_end,
                raw_event_period=values["운영기간"],
                apply_start=apply_start,
                apply_end=apply_end,
                raw_apply_period=values["신청기간"],
                target=_clean(values.get("수강대상")),
                capacity_total=capacity_total,
                page=requested_page,
                correction=correction,
            )
        )
    return total, last_page, rows, _online_signature(rows)


def _static_range(value: Any, year: int, source_key: str) -> tuple[date, date]:
    text = _clean(value)
    match = _STATIC_RANGE_RE.fullmatch(text)
    if match is None:
        raise MujuContractError(f"{source_key}: malformed static operation period {text!r}")
    try:
        start = date(year, int(match.group(1)), int(match.group(2)))
        end = date(year, int(match.group(3)), int(match.group(4)))
    except ValueError as exc:
        raise MujuContractError(f"{source_key}: invalid static operation date") from exc
    if start > end:
        raise MujuContractError(f"{source_key}: reversed static operation period")
    return start, end


def _static_identity(source_key: str, values: Iterable[Any]) -> str:
    material = "|".join(_clean(value) for value in values)
    suffix = hashlib.sha1(material.encode("utf-8")).hexdigest().upper()[:12]
    return f"{source_key}:{suffix}"


def _parse_static_page(
    soup: BeautifulSoup, source: _StaticSource
) -> tuple[list[_StaticCourse], str, int]:
    root = soup.select("#contents")
    if len(root) != 1:
        raise MujuContractError(f"{source.key}: canonical contents root changed")
    root_node = root[0]
    h2 = root_node.select(":scope h2")
    if len(h2) != 1 or _clean(h2[0].get_text(" ", strip=True)) != source.label:
        raise MujuContractError(f"{source.key}: page title changed")
    headings = [
        _clean(node.get_text(" ", strip=True))
        for node in root_node.select("h3")
        if source.heading_phrase in _clean(node.get_text(" ", strip=True))
    ]
    if len(headings) != 1:
        raise MujuContractError(f"{source.key}: dated programme heading changed")
    years = _YEAR_RE.findall(headings[0])
    if len(years) != 1:
        raise MujuContractError(f"{source.key}: expected one programme year")
    year = int(years[0])
    tables = root_node.select("table.table1")
    if len(tables) != 1:
        raise MujuContractError(f"{source.key}: expected one programme table")
    table = tables[0]
    headers = tuple(
        _clean(node.get_text(" ", strip=True)) for node in table.select("thead th")
    )
    if headers != source.headers:
        raise MujuContractError(f"{source.key}: table headers changed")
    if root_node.select(
        f"a[href*='{MUJU_DETAIL_PATH}'], a[href*='{MUJU_APPLICATION_PATH}']"
    ):
        raise MujuContractError(f"{source.key}: static page gained identity/application routes")

    rows: list[_StaticCourse] = []
    raw_signature: list[tuple[str, ...]] = []
    for table_row in table.select("tbody > tr"):
        cells = tuple(
            _clean(node.get_text(" ", strip=True))
            for node in table_row.find_all(["th", "td"], recursive=False)
        )
        if len(cells) != len(source.headers):
            raise MujuContractError(f"{source.key}: malformed programme table row")
        raw_signature.append(cells)
        values = dict(zip(source.headers, cells))
        if source.key == "regular_plan":
            title = values["프로그램명"]
            target = values["대상"]
            capacity = values["정원(명)"]
            raw_period = values["운영기간"]
            venue = values["강의실"]
            if not values["내용"]:
                raise MujuContractError("regular_plan: empty programme description")
            branch = (
                MUJU_BRANCH
                if venue == "강당" or re.fullmatch(r"\d+호", venue)
                else venue
            )
            category = source.label
            capacity_total = int(capacity) if capacity.isdigit() else None
            identity_values = (source.key, year, title, raw_period, venue)
        else:
            district = values["해당 읍면"]
            venue = values["교육장소"]
            title = values["프로그램명"]
            target = values["대상"]
            raw_period = values["운영일정"]
            if (
                district not in MUJU_ALL_LEARNING_DISTRICTS
                or not venue
                or not values["내용"]
            ):
                raise MujuContractError("all_learning: district/venue/content changed")
            branch = f"{district} {venue}"
            category = source.label
            capacity_total = None
            identity_values = (source.key, year, district, venue, title, raw_period)
        if not title or not target or not venue:
            raise MujuContractError(f"{source.key}: required public field is empty")
        event_start, event_end = _static_range(raw_period, year, source.key)
        identity = _static_identity(source.key, identity_values)
        rows.append(
            _StaticCourse(
                source_key=source.key,
                identity=identity,
                title=title,
                category=category,
                branch=branch,
                venue=venue,
                target=target,
                capacity_total=capacity_total,
                event_start=event_start,
                event_end=event_end,
                raw_event_period=raw_period,
                year=year,
                raw_url=source.url,
            )
        )
    if not rows:
        raise MujuContractError(f"{source.key}: programme table is unexpectedly empty")
    identities = [row.identity for row in rows]
    if len(identities) != len(set(identities)):
        raise MujuContractError(f"{source.key}: duplicate static source identities")
    return rows, _digest(tuple(raw_signature)), year


def _identity_text(value: Any) -> str:
    return "".join(character.lower() for character in _clean(value) if character.isalnum())


def _regular_plan_mirrors(
    online: Iterable[_OnlineCourse], regular: Iterable[_StaticCourse]
) -> tuple[dict[str, str], int]:
    online_rows = list(online)
    mirrors: dict[str, str] = {}
    date_drifts = 0
    used_online: set[str] = set()
    for row in regular:
        candidates: list[tuple[float, int, _OnlineCourse]] = []
        left = _identity_text(row.title)
        for candidate in online_rows:
            if candidate.event_start.year != row.year:
                continue
            ratio = SequenceMatcher(None, left, _identity_text(candidate.title)).ratio()
            if ratio < 0.84:
                continue
            distance = abs((candidate.event_start - row.event_start).days) + abs(
                (candidate.event_end - row.event_end).days
            )
            candidates.append((ratio, -distance, candidate))
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        if not candidates:
            continue
        best = candidates[0][2]
        if best.identity in used_online:
            raise MujuContractError("regular plan mirror maps twice to one canonical identity")
        used_online.add(best.identity)
        mirrors[row.identity] = best.identity
        if (row.event_start, row.event_end) != (best.event_start, best.event_end):
            date_drifts += 1
    return mirrors, date_drifts


def _detail_direct_title(header: Any) -> str:
    values = [
        _clean(value)
        for value in header.find_all(string=True, recursive=False)
        if _clean(value)
    ]
    return values[0] if len(values) == 1 else ""


def _detail_fields(root: Any, identity: str) -> dict[str, str]:
    output: dict[str, str] = {}
    for block in root.select("table tbody dl.info"):
        labels, values = block.select(":scope > dt"), block.select(":scope > dd")
        if len(labels) != 1 or len(values) != 1:
            raise MujuContractError(f"course {identity}: malformed detail field")
        label = _clean(labels[0].get_text(" ", strip=True))
        if not label or label in output:
            raise MujuContractError(f"course {identity}: duplicate detail field")
        output[label] = _clean(values[0].get_text(" ", strip=True))
    if not _DETAIL_REQUIRED.issubset(output) or not set(output).issubset(_DETAIL_ALLOWED):
        raise MujuContractError(f"course {identity}: detail field vocabulary changed")
    return output


def _parse_detail(
    soup: BeautifulSoup, course: _OnlineCourse
) -> dict[str, Any]:
    roots = soup.select("#board")
    if len(roots) != 1:
        raise MujuContractError(f"course {course.identity}: detail root changed")
    root = roots[0]
    tables = root.select(":scope .table_bview > table")
    headers = root.select(":scope .table_bview > table > thead th.th_none")
    if len(tables) != 1 or len(headers) != 1:
        raise MujuContractError(f"course {course.identity}: detail table changed")
    header = headers[0]
    title = _detail_direct_title(header)
    if title != course.title:
        raise MujuContractError(f"course {course.identity}: list/detail title mismatch")
    badges = header.select(":scope > a.btn_sm[href]")
    if len(badges) != 1:
        raise MujuContractError(f"course {course.identity}: detail status badge changed")
    expected_status, _, expected_badge = _STATUS_CONTRACT[course.source_status]
    badge = badges[0]
    expected_badge_class = {
        "OPEN": "btn_receipt",
        "SCHEDULED": "btn_prepare",
        "CLOSED": "btn_close",
    }[expected_status]
    if course.source_status == "신청마감":
        expected_badge_class = "btn_end"
    if (
        _clean(badge.get_text(" ", strip=True)) != expected_badge
        or set(badge.get("class", [])) != {"btn_sm", expected_badge_class}
        or _clean(badge.get("href")) != "#javascript:;"
    ):
        raise MujuContractError(f"course {course.identity}: detail status drift")

    fields = _detail_fields(root, course.identity)
    start, end, correction = _event_range(fields["강좌기간"], course.identity)
    if (start, end) != (course.event_start, course.event_end):
        raise MujuContractError(
            f"course {course.identity}: list/detail operation period mismatch"
        )
    if bool(correction) != bool(course.correction):
        raise MujuContractError(f"course {course.identity}: correction binding drift")
    apply_start, apply_end = _detail_apply_range(fields["신청기간"], course.identity)
    if (apply_start, apply_end) != (course.apply_start, course.apply_end):
        raise MujuContractError(
            f"course {course.identity}: list/detail application period mismatch"
        )
    _, capacity_total = _capacity(fields["모집인원"], course.identity)
    if capacity_total != course.capacity_total:
        raise MujuContractError(f"course {course.identity}: list/detail capacity mismatch")
    target = _clean(fields.get("수강대상"))
    if course.target and target != course.target:
        raise MujuContractError(f"course {course.identity}: list/detail target mismatch")

    button_boxes = root.select(":scope > .btn_w")
    if len(button_boxes) != 1:
        raise MujuContractError(f"course {course.identity}: detail buttons changed")
    buttons = button_boxes[0].select(":scope > a[href]")
    list_buttons = [button for button in buttons if "gray" in button.get("class", [])]
    apply_buttons = [button for button in buttons if "btn_receipt" in button.get("class", [])]
    if len(list_buttons) != 1:
        raise MujuContractError(f"course {course.identity}: detail list control changed")
    back = urlparse(urljoin(MUJU_URL, _clean(list_buttons[0].get("href"))))
    back_query = parse_qs(back.query, keep_blank_values=True)
    expected_back_query = (
        {} if course.page == 1 else {"v_page": [str(course.page)]}
    )
    if (
        not _safe_base_url(back.geturl(), path=MUJU_LIST_PATH)
        or back_query != expected_back_query
        or _clean(list_buttons[0].get_text(" ", strip=True)) != "목록"
    ):
        raise MujuContractError(f"course {course.identity}: detail back route changed")
    application_url = ""
    if expected_status == "OPEN":
        if len(apply_buttons) != 1 or len(buttons) != 2:
            raise MujuContractError(f"course {course.identity}: open application missing")
        if _clean(apply_buttons[0].get_text(" ", strip=True)) != "신청":
            raise MujuContractError(f"course {course.identity}: application label changed")
        application_url = _validated_identity_url(
            apply_buttons[0].get("href"),
            path=MUJU_APPLICATION_PATH,
            identity=course.identity,
            prepage_kind="application",
            page=course.page,
        )
        if not course.list_control_url:
            raise MujuContractError(f"course {course.identity}: list application missing")
    elif apply_buttons or len(buttons) != 1 or course.list_control_url:
        raise MujuContractError(f"course {course.identity}: inactive application exposed")
    return {
        "target": target or course.target,
        "schedule": _clean(fields.get("강좌시간")),
        "venue": _clean(fields.get("강의실")),
        "fee": _clean(fields.get("참가비")),
        "application_url": application_url,
        "application_control": bool(application_url),
        "status": expected_status,
    }


def _branch_code(source_key: str, branch: str) -> str:
    suffix = hashlib.sha1(branch.encode("utf-8")).hexdigest().upper()[:8]
    return f"{MUJU_PROVIDER}:{source_key}:{suffix}"


def _base_output(target: Any) -> dict[str, Any]:
    extra = _target_extra(target)
    return {
        "collection_category": _clean(extra.get("collection_category") or "공공예약"),
        "domain_category": _clean(extra.get("domain_category") or "교육·강좌"),
        "operator_type": _clean(extra.get("operator_type") or "지자체/공공기관"),
        "source_group": _clean(extra.get("source_group") or "municipal_reservation"),
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "program_type": "교육",
        "municipality_code": MUJU_MUNICIPALITY_CODE,
        "municipality_name": MUJU_MUNICIPALITY_NAME,
        "municipality_full_name": MUJU_MUNICIPALITY_NAME,
    }


def _online_row(
    target: Any, course: _OnlineCourse, detail: Mapping[str, Any]
) -> dict[str, Any]:
    status = _clean(detail.get("status"))
    application_url = _clean(detail.get("application_url"))
    venue = _clean(detail.get("venue")) or MUJU_BRANCH
    output: dict[str, Any] = {
        "provider": MUJU_PROVIDER,
        "provider_course_id": f"{MUJU_PROVIDER}:education:edusat:{course.identity}",
        "prefer_incoming_provider_course_id": True,
        "title": course.title,
        "branch": MUJU_BRANCH,
        "branch_code": _branch_code("edusat", MUJU_BRANCH),
        "preserve_branch": True,
        "branch_url": MUJU_URL,
        "raw_url": course.detail_url,
        "application_url": application_url,
        "application_type": "ONLINE_APPLICATION" if application_url else "INFO_ONLY",
        "application_method_raw": "온라인 수강신청" if application_url else "정보 제공",
        "reservation_available": bool(application_url and status == "OPEN"),
        "status": status,
        "period": f"{course.event_start.isoformat()} ~ {course.event_end.isoformat()}",
        "start_date": course.event_start.isoformat(),
        "end_date": course.event_end.isoformat(),
        "apply_period": f"{course.apply_start.isoformat()} ~ {course.apply_end.isoformat()}",
        "apply_start_date": course.apply_start.isoformat(),
        "apply_end_date": course.apply_end.isoformat(),
        "schedule_raw": _clean(detail.get("schedule")),
        "target": _clean(detail.get("target")),
        "capacity": (
            f"{course.capacity_total}명" if course.capacity_total is not None else "제한없음"
        ),
        "capacity_total": course.capacity_total,
        "fee": _clean(detail.get("fee")),
        "venue_name": venue,
        "room": venue,
        "category": course.category,
        "collection_type": MUJU_PARSER,
        "raw_fields": {
            "parser": MUJU_PARSER,
            "source_kind": "edusat",
            "source_identity": course.identity,
            "source_page": course.page,
            "source_status": course.source_status,
            "source_category": course.category,
            "source_event_period": course.raw_event_period,
            "source_apply_period": course.raw_apply_period,
            "event_end_corrected": bool(course.correction),
            "correction_evidence": course.correction.evidence if course.correction else "",
            "detail_verified": True,
            "application_control_present": bool(application_url),
            "application_form_fetched": False,
            "service_family": "education",
        },
    }
    output.update(_base_output(target))
    return output


def _static_row(target: Any, course: _StaticCourse, audit_date: date) -> dict[str, Any]:
    status = "SCHEDULED" if audit_date < course.event_start else "CLOSED"
    output: dict[str, Any] = {
        "provider": MUJU_PROVIDER,
        "provider_course_id": (
            f"{MUJU_PROVIDER}:education:{course.source_key}:{course.identity.split(':')[-1]}"
        ),
        "prefer_incoming_provider_course_id": True,
        "title": course.title,
        "branch": course.branch,
        "branch_code": _branch_code(course.source_key, course.branch),
        "preserve_branch": True,
        "branch_url": course.raw_url,
        "raw_url": course.raw_url,
        "application_url": "",
        "application_type": "INFORMATION_ONLY",
        "application_method_raw": "공식 프로그램 운영표",
        "reservation_available": False,
        "status": status,
        "period": f"{course.event_start.isoformat()} ~ {course.event_end.isoformat()}",
        "start_date": course.event_start.isoformat(),
        "end_date": course.event_end.isoformat(),
        "apply_period": "",
        "schedule_raw": "",
        "target": course.target,
        "capacity": (
            f"{course.capacity_total}명" if course.capacity_total is not None else ""
        ),
        "capacity_total": course.capacity_total,
        "fee": "",
        "venue_name": course.venue,
        "room": course.venue,
        "category": course.category,
        "collection_type": MUJU_PARSER,
        "raw_fields": {
            "parser": MUJU_PARSER,
            "source_kind": course.source_key,
            "source_identity": course.identity,
            "source_year": course.year,
            "source_event_period": course.raw_event_period,
            "detail_verified": False,
            "application_control_present": False,
            "application_form_fetched": False,
            "service_family": "education",
        },
    }
    output.update(_base_output(target))
    return output


def _privacy_valid(rows: Iterable[Mapping[str, Any]]) -> bool:
    forbidden_keys = {
        "instructor",
        "teacher",
        "contact",
        "phone",
        "email",
        "attachment",
        "content",
        "body",
        "capacity_current",
        "waitlist_current",
        "applicants",
    }
    material = repr(list(rows))
    if _PHONE_RE.search(material) or _EMAIL_RE.search(material):
        return False
    for row in rows:
        raw_fields = row.get("raw_fields", {})
        if not isinstance(raw_fields, Mapping):
            return False
        if forbidden_keys.intersection(str(key).lower() for key in raw_fields):
            return False
    return True


def _empty_meta() -> dict[str, Any]:
    return {
        "municipality_code": MUJU_MUNICIPALITY_CODE,
        "municipality_full_name": MUJU_MUNICIPALITY_NAME,
        "provider": MUJU_PROVIDER,
        "canonical_url": MUJU_URL,
        "provider_url_sha1": MUJU_PROVIDER_URL_SHA1,
        "canonical_url_sha256": MUJU_CANONICAL_URL_SHA256,
        "ownership_scope": MUJU_OWNERSHIP_SCOPE,
        "incumbent_decision": MUJU_INCUMBENT_DECISION,
        "candidate_decisions": dict(MUJU_CANDIDATE_DECISIONS),
        "ledger_totals": {"edusat": 0, "regular_plan": 0, "all_learning": 0},
        "ledger_pages": {"edusat": 0, "regular_plan": 1, "all_learning": 1},
        "ledger_years": {},
        "source_total": 0,
        "source_unique_total": 0,
        "source_status_counts": {},
        "category_counts": {label: 0 for label in MUJU_CATEGORY_OPTIONS.values()},
        "all_learning_district_counts": {},
        "regular_plan_mirror_count": 0,
        "regular_plan_date_drift_count": 0,
        "regular_plan_mirror_bindings": {},
        "source_date_correction_ids": [],
        "current_count": 0,
        "expired_count": 0,
        "ledger_current_counts": {},
        "detail_attempts": 0,
        "detail_pages": 0,
        "application_control_count": 0,
        "application_endpoint_fetches": 0,
        "list_requests": 0,
        "static_requests": 0,
        "logical_requests": 0,
        "physical_requests": 0,
        "request_retry_count": 0,
        "sentinel_mode": "exact_post_last_repeated_final_identity_page",
        "sentinel_page": 0,
        "sentinel_count": 0,
        "stable_rechecks": {
            "edusat_first": False,
            "edusat_final": False,
            "regular_plan": False,
            "all_learning": False,
        },
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "pii_payload_persisted": False,
        "source_cap_reached": False,
        "configured_collection_error": "",
    }


def collect_muju_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 30,
    detail_limit: int = 20,
    max_workers: int = MUJU_MAX_WORKERS,
    today: Optional[date | datetime | str] = None,
    session_factory: SessionFactory = _default_session_factory,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future Muju lifelong-owner snapshot."""

    meta = _empty_meta()
    try:
        timeout_value = int(timeout)
        allowed_pages = int(max_pages)
        allowed_details = int(detail_limit)
        workers = int(max_workers)
        audit_date = _today(today)
        if (
            timeout_value < 1
            or allowed_pages < 1
            or allowed_details < 0
            or not 1 <= workers <= 16
        ):
            raise ValueError
    except (TypeError, ValueError):
        meta["configured_collection_error"] = (
            "timeout/max_pages/detail_limit/max_workers/today are invalid"
        )
        return [], MUJU_PARSER, meta
    if not is_muju_education_target(target):
        meta["configured_collection_error"] = "target is outside canonical Muju scope"
        return [], MUJU_PARSER, meta

    main_session = session_factory()
    try:
        def request_main(url: str, kind: str) -> BeautifulSoup:
            soup, _, attempts = _request_soup(
                main_session, url, timeout=timeout_value, fetcher=fetcher
            )
            meta["logical_requests"] += 1
            meta["physical_requests"] += attempts
            meta[f"{kind}_requests"] += 1
            return soup

        first_soup = request_main(muju_list_url(1), "list")
        total, last_page, first_rows, first_signature = _parse_online_page(
            first_soup, requested_page=1
        )
        if last_page > allowed_pages:
            meta["source_cap_reached"] = True
            raise MujuContractError(
                f"max_pages cap {allowed_pages} is below declared final page {last_page}"
            )
        meta["ledger_totals"]["edusat"] = total
        meta["ledger_pages"]["edusat"] = last_page
        pages: dict[int, list[_OnlineCourse]] = {1: first_rows}
        signatures: dict[int, str] = {1: first_signature}
        for page in range(2, last_page + 1):
            soup = request_main(muju_list_url(page), "list")
            page_total, page_last, page_rows, signature = _parse_online_page(
                soup, requested_page=page
            )
            if (page_total, page_last) != (total, last_page):
                raise MujuContractError("declared total/final page changed during traversal")
            pages[page] = page_rows
            signatures[page] = signature

        online_rows = [row for page in range(1, last_page + 1) for row in pages[page]]
        if len(online_rows) != total:
            raise MujuContractError("full online traversal does not equal declared total")
        online_ids = [row.identity for row in online_rows]
        if len(online_ids) != len(set(online_ids)):
            raise MujuContractError("duplicate source identities in online ledger")

        sentinel_page = last_page + 1
        sentinel_soup = request_main(muju_list_url(sentinel_page), "list")
        sentinel_total, sentinel_last, sentinel_rows, sentinel_signature = (
            _parse_online_page(
                sentinel_soup, requested_page=sentinel_page, sentinel=True
            )
        )
        if (
            (sentinel_total, sentinel_last) != (total, last_page)
            or sentinel_signature != signatures[last_page]
        ):
            raise MujuContractError("immediate post-last clamp does not repeat final identities")
        meta["sentinel_page"] = sentinel_page
        meta["sentinel_count"] = len(sentinel_rows)

        recheck_first = request_main(muju_list_url(1), "list")
        _, _, _, recheck_first_signature = _parse_online_page(
            recheck_first, requested_page=1
        )
        meta["stable_rechecks"]["edusat_first"] = (
            recheck_first_signature == signatures[1]
        )
        if not meta["stable_rechecks"]["edusat_first"]:
            raise MujuContractError("online first page changed on recheck")
        if last_page == 1:
            meta["stable_rechecks"]["edusat_final"] = True
        else:
            recheck_final = request_main(muju_list_url(last_page), "list")
            _, _, _, recheck_final_signature = _parse_online_page(
                recheck_final, requested_page=last_page
            )
            meta["stable_rechecks"]["edusat_final"] = (
                recheck_final_signature == signatures[last_page]
            )
            if not meta["stable_rechecks"]["edusat_final"]:
                raise MujuContractError("online final page changed on recheck")

        category_counts = Counter(row.category for row in online_rows)
        meta["category_counts"] = {
            label: category_counts.get(label, 0)
            for label in MUJU_CATEGORY_OPTIONS.values()
        }
        if sum(meta["category_counts"].values()) != total:
            raise MujuContractError("online category partition does not reconcile")
        meta["source_status_counts"] = dict(
            sorted(Counter(row.source_status for row in online_rows).items())
        )
        meta["source_date_correction_ids"] = sorted(
            (row.identity for row in online_rows if row.correction), key=int
        )

        static_rows: dict[str, list[_StaticCourse]] = {}
        for source in MUJU_STATIC_SOURCES:
            soup = request_main(source.url, "static")
            rows, signature, year = _parse_static_page(soup, source)
            recheck = request_main(source.url, "static")
            recheck_rows, recheck_signature, recheck_year = _parse_static_page(
                recheck, source
            )
            stable = (
                signature == recheck_signature
                and year == recheck_year
                and len(rows) == len(recheck_rows)
            )
            meta["stable_rechecks"][source.key] = stable
            if not stable:
                raise MujuContractError(f"{source.key}: static page changed on recheck")
            static_rows[source.key] = rows
            meta["ledger_totals"][source.key] = len(rows)
            meta["ledger_years"][source.key] = year

        districts = Counter(
            row.branch.split(" ", 1)[0]
            for row in static_rows["all_learning"]
        )
        if set(districts) != set(MUJU_ALL_LEARNING_DISTRICTS):
            raise MujuContractError("all_learning official district vocabulary changed")
        meta["all_learning_district_counts"] = {
            district: districts[district] for district in MUJU_ALL_LEARNING_DISTRICTS
        }

        mirrors, date_drifts = _regular_plan_mirrors(
            online_rows, static_rows["regular_plan"]
        )
        meta["regular_plan_mirror_count"] = len(mirrors)
        meta["regular_plan_date_drift_count"] = date_drifts
        meta["regular_plan_mirror_bindings"] = dict(sorted(mirrors.items()))
        meta["source_total"] = sum(meta["ledger_totals"].values())
        meta["source_unique_total"] = meta["source_total"] - len(mirrors)

        online_current = [row for row in online_rows if row.event_end >= audit_date]
        regular_unique = [
            row for row in static_rows["regular_plan"] if row.identity not in mirrors
        ]
        regular_current = [row for row in regular_unique if row.event_end >= audit_date]
        all_learning_current = [
            row
            for row in static_rows["all_learning"]
            if row.event_end >= audit_date
        ]
        meta["ledger_current_counts"] = {
            "edusat": len(online_current),
            "regular_plan": len(regular_current),
            "all_learning": len(all_learning_current),
        }
        expected_current = sum(meta["ledger_current_counts"].values())
        meta["expired_count"] = meta["source_unique_total"] - expected_current
        if len(online_current) > allowed_details:
            meta["source_cap_reached"] = True
            raise MujuContractError(
                f"detail_limit cap {allowed_details} is below current online count "
                f"{len(online_current)}"
            )

        def fetch_detail(course: _OnlineCourse) -> tuple[_OnlineCourse, dict[str, Any], int]:
            session = session_factory()
            try:
                soup, _, attempts = _request_soup(
                    session,
                    course.detail_url,
                    timeout=timeout_value,
                    fetcher=fetcher,
                )
                return course, _parse_detail(soup, course), attempts
            finally:
                _close_quietly(session)

        detail_results: list[tuple[_OnlineCourse, dict[str, Any], int]] = []
        if online_current:
            if workers == 1:
                detail_results = [fetch_detail(row) for row in online_current]
            else:
                with ThreadPoolExecutor(
                    max_workers=min(workers, len(online_current))
                ) as executor:
                    detail_results = list(executor.map(fetch_detail, online_current))
        meta["detail_attempts"] = len(online_current)
        meta["detail_pages"] = len(detail_results)
        meta["logical_requests"] += len(detail_results)
        meta["physical_requests"] += sum(item[2] for item in detail_results)
        meta["application_control_count"] = sum(
            bool(item[1].get("application_control")) for item in detail_results
        )

        result = [
            _online_row(target, course, detail)
            for course, detail, _ in detail_results
        ]
        result.extend(_static_row(target, row, audit_date) for row in regular_current)
        result.extend(
            _static_row(target, row, audit_date) for row in all_learning_current
        )
        ids = [_clean(row.get("provider_course_id")) for row in result]
        if len(ids) != len(set(ids)) or any(not value for value in ids):
            raise MujuContractError("duplicate or empty output provider_course_id")
        if dedupe_rows is not None:
            deduped = list(dedupe_rows(result))
            deduped_ids = [_clean(row.get("provider_course_id")) for row in deduped]
            if len(deduped) != len(result) or set(deduped_ids) != set(ids):
                raise MujuContractError("external dedupe changed complete identity snapshot")
            result = deduped
        if len(result) != expected_current:
            raise MujuContractError("current/future output count does not reconcile")
        if not _privacy_valid(result):
            raise MujuContractError("PII or excluded detail payload reached output rows")

        meta["current_count"] = len(result)
        meta["pagination_complete"] = True
        meta["details_complete"] = meta["detail_pages"] == len(online_current)
        meta["snapshot_complete"] = bool(
            meta["pagination_complete"]
            and meta["details_complete"]
            and all(meta["stable_rechecks"].values())
            and meta["current_count"] == expected_current
        )
        meta["full_snapshot_validated"] = meta["snapshot_complete"]
        meta["request_retry_count"] = (
            meta["physical_requests"] - meta["logical_requests"]
        )
        return result, MUJU_PARSER, meta
    except Exception as exc:
        meta["configured_collection_error"] = _clean(exc)
        meta["snapshot_complete"] = False
        meta["full_snapshot_validated"] = False
        meta["request_retry_count"] = max(
            0, meta["physical_requests"] - meta["logical_requests"]
        )
        return [], MUJU_PARSER, meta
    finally:
        _close_quietly(main_session)


collect = collect_muju_education
