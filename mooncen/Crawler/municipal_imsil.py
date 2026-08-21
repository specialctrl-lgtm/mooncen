"""Fail-closed collector for Imsil-gun public-library education programmes.

The former Imsil municipal target is not an education source.  It renders the
county ``고시/공고`` Saeol notice iframe and its ten generic cards were false
positives.  Course identities instead belong to the separately branded
official Imsil County Library programme ledger, so this module deliberately
uses a new provider and never accepts or retargets the incumbent provider.

The library publishes one complete ledger plus two overlapping official
partitions (county libraries and small libraries).  A snapshot is released
only when every page, an immediate empty sentinel, stable boundary rechecks,
and the two partitions reconcile.  Details are fetched only for raw
current/future identities.  Applicant/login/application/attachment endpoints
are never fetched and current applicant, wait-list, contact, attachment, and
free-text body data are never returned.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


IMSIL_MUNICIPALITY_CODE = "5275000000"
IMSIL_MUNICIPALITY_NAME = "전북특별자치도 임실군"

IMSIL_INCUMBENT_PROVIDER = "MUNI_WWW_IMSIL_GO_KR_D29DB99C"
IMSIL_INCUMBENT_URL = (
    "https://www.imsil.go.kr/index.imsil?"
    "menuCd=DOM_000000103001005000"
)
IMSIL_INCUMBENT_URL_SHA1 = (
    "D29DB99C11D12E5C5800C4D4A5BFD66B6442F7F2"
)
IMSIL_INCUMBENT_URL_SHA256 = (
    "D04B4438E82FEE74CE4BB0FE1E676595297ABD8956E4363BEEBBAB66BF5EDB5C"
)
IMSIL_INCUMBENT_DECISION = (
    "deactivate MUNI_WWW_IMSIL_GO_KR_D29DB99C; the URL is the county "
    "고시/공고 Saeol notice iframe, not an education identity ledger; do not "
    "retarget it across the separately branded library-owner boundary"
)

IMSIL_HOST = "lib.imsil.go.kr"
IMSIL_PROVIDER = "MUNI_LIB_IMSIL_GO_KR_C73F4E31"
IMSIL_LIST_PATH = "/commonlib/uce/board/openProgramList.do"
IMSIL_DETAIL_PATH = "/commonlib/uce/board/openProgramDetail.do"
IMSIL_APPLICATION_PATH = "/commonlib/uce/board/openAppHistoryWrite.do"
IMSIL_LOGIN_PATH = "/commonlib/uce/member/memberLogin.do"
IMSIL_URL = f"https://{IMSIL_HOST}{IMSIL_LIST_PATH}?mi=MN0131"
IMSIL_PROVIDER_URL_SHA1 = (
    "C73F4E31D7C674C469C8CD65A8FB43CF181F69DA"
)
IMSIL_CANONICAL_URL_SHA256 = (
    "AB2A463F004E1FA7207EC27BBDE8A7B345C8679D22E9AEED66D998E9C378EA3F"
)
IMSIL_CANONICAL_CANDIDATE_ID = "MUNI_IR_AB2A463F004E"
IMSIL_INCUMBENT_CANDIDATE_ID = "MUNI_IR_D04B4438E82F"
IMSIL_BBS_ID = "PROGRAM_0000001"

IMSIL_CANDIDATE_DECISIONS: Mapping[str, str] = {
    IMSIL_INCUMBENT_CANDIDATE_ID: (
        "deactivate_false_positive_government_notice_iframe"
    ),
    IMSIL_CANONICAL_CANDIDATE_ID: (
        "add_new_separate_county_library_complete_program_owner"
    ),
}

IMSIL_FILTERS: Mapping[str, str] = {
    "Z": "전체",
    "A": "군립",
    "B": "작은",
}
IMSIL_BRANCHES: Mapping[str, str] = {
    "임실": "임실군립도서관",
    "오수": "임실군립오수도서관",
    "지사랑": "지사랑 작은도서관",
    "무지개빛": "무지개빛 작은도서관",
    "필봉": "필봉작은도서관",
    "아낌없이": "아낌없이주는나무 작은도서관",
    "전체": "임실군립도서관 통합",
}
IMSIL_BRANCH_CLASSES: Mapping[str, str] = {
    "임실": "bulIs",
    "오수": "bulOs",
    "지사랑": "bulSm4",
    "무지개빛": "bulSm1",
    "필봉": "bulSm2",
    "아낌없이": "bulSm3",
    "전체": "bulAll",
}
IMSIL_COUNTY_BRANCHES = frozenset({"임실", "오수"})
IMSIL_SMALL_BRANCHES = frozenset(
    {"지사랑", "무지개빛", "필봉", "아낌없이"}
)

# These are the complete, audited set of identities intentionally published
# only under the global tab.  The six Bookstart rows are package receipt
# requests and 549 is a performance, not a course.  Exact title binding makes
# the exclusion fail closed if an identity is edited or repurposed.
IMSIL_NONCOURSE_IDENTITIES: Mapping[str, str] = {
    "760": "26년 북스타트 수령 신청(36개월~취학 전)",
    "759": "26년 북스타트 수령 신청(19~35개월)",
    "758": "26년 북스타트 수령 신청(임신부, 0~18개월)",
    "701": "북스타트 수령 신청(36개월-취학 전)",
    "700": "북스타트 수령 신청(19-35개월)",
    "699": "북스타트 수령 신청(0-18개월)",
    "549": "2022년 임실군립도서관 도서관 주간 행사: 클래식 공연",
}

# Exact historical source anomalies.  Missing receipt dates are harmless for
# expired rows; the two one-sided operation dates are normalized to one day.
IMSIL_EMPTY_APPLICATION_PERIODS: Mapping[str, str] = {
    "764": "무지개빛 작은도서관 <사랑을 전하는 꽃바구니>",
    "556": "아낌없이주는나무 작은도서관 <샴푸바 만들기>",
}
IMSIL_ONE_SIDED_EVENT_PERIODS: Mapping[str, str] = {
    "550": "2022년 임실군립도서관 도서관 주간 행사: 인문학 특강",
    "549": "2022년 임실군립도서관 도서관 주간 행사: 클래식 공연",
}

IMSIL_OWNER_BOUNDARIES: Mapping[str, str] = {
    IMSIL_INCUMBENT_URL: (
        "county_government_single_notice_iframe_false_positive_excluded"
    ),
    IMSIL_URL: "canonical_county_library_program_identity_owner_included",
    (
        "https://www.imsil.go.kr/town/board/view.imsil?"
        "menuCd=DOM_000000704004001000&boardId=BBS_0000003&dataSid=138317"
    ): "single_resident_autonomy_operator_recruitment_notice_excluded",
    (
        "https://www.imsil.go.kr/agri/schedule/list.imsil?"
        "menuCd=DOM_000000606002000000&boardId=BBS_0000044"
    ): "separate_mixed_agriculture_editorial_calendar_owner",
    (
        "https://www.imsil.go.kr/injae/index.imsil?"
        "menuCd=DOM_000001102006001000"
    ): "separate_scholarship_and_education_support_owner",
    "https://www.imsil.go.kr/ytc/index.imsil": (
        "separate_youth_facility_and_experience_owner"
    ),
    "https://lib.jbe.go.kr/islib/index.do": (
        "separate_provincial_education_office_library_owner"
    ),
    "https://www.cheesepark.kr/": "separate_tourism_experience_owner",
}

IMSIL_PAGE_SIZE = 10
IMSIL_FETCH_ATTEMPTS = 2
IMSIL_MAX_WORKERS = 4
IMSIL_PARSER = (
    "imsil_library_complete_program_owner+all_county_small_partition+"
    "empty_post_last_sentinel+stable_first_final_sentinel_rechecks+"
    "current_future_detail_binding+application_control_no_endpoint_fetch+"
    "exact_noncourse_exclusions+pii_allowlist"
)
IMSIL_OWNERSHIP_SCOPE = (
    "official_imsil_county_library_program_application_identity_ledger"
)


class ImsilContractError(RuntimeError):
    """Raised when the audited Imsil public-source contract changes."""


@dataclass(frozen=True)
class _ListedProgram:
    identity: str
    title: str
    branch_label: str
    branch: str
    receipt_status: str
    event_status: str
    apply_start: Optional[date]
    apply_end: Optional[date]
    raw_apply_period: str
    event_start: date
    event_end: date
    raw_event_period: str
    target: str
    capacity_total: int
    page: int
    source_filter: str
    detail_url: str


@dataclass(frozen=True)
class _Detail:
    venue: str
    schedule: str
    fee: str
    target: str
    method: str
    control: str
    current_applicants: int


Fetcher = Callable[..., Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"[1-9]\d*")
_TOTAL_RE = re.compile(
    r"^전체\s+([\d,]+)\s*건\s*·\s*현재페이지\s+(\d+)/1$"
)
_PERIOD_RE = re.compile(
    r"^(20\d{2})\.(\d{2})\.(\d{2})\s*~\s*"
    r"(?:(20\d{2})\.(\d{2})\.(\d{2}))?$"
)
_CAPACITY_RE = re.compile(r"^(\d+)(?:\((\d+)\))?\s*/\s*(\d+)$")
_DETAIL_CAPACITY_RE = re.compile(
    r"^(\d+)\s*/\s*(\d+)\s*\(신청\s*/\s*정원\)$"
)
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,3}[-\s)]?\d{3,4}[-\s]?\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_LIST_FIELDS = ("접수", "수강", "접수현황", "대상")
_DETAIL_FIELDS = (
    "접수기간",
    "운영기간",
    "운영장소",
    "운영시간",
    "수강료 및 재료비",
    "신청대상",
    "접수방법",
    "신청인원",
    "문의처",
    "대기인원",
    "강의계획서",
)
_STATUS_CLASSES: Mapping[tuple[str, str], tuple[str, str]] = {
    ("접수대기", "행사대기"): ("eventBtn1", "eventBtn1"),
    ("접수진행", "행사대기"): ("eventBtn2", "eventBtn1"),
    ("접수진행", "행사진행"): ("eventBtn2", "eventBtn2"),
    ("접수종료", "행사진행"): ("eventBtn3", "eventBtn2"),
    ("접수종료", "행사종료"): ("eventBtn3", "eventBtn3"),
}


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _target_extra(target: Any) -> Mapping[str, Any]:
    value = _target_value(target, "extra")
    return value if isinstance(value, Mapping) else {}


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def _safe_source_url(url: str, *, path: str) -> bool:
    parsed = urlparse(url)
    return bool(
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == IMSIL_HOST
        and parsed.port is None
        and parsed.path == path
        and not parsed.params
        and not parsed.username
        and not parsed.password
        and not parsed.fragment
    )


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query, keep_blank_values=True)


def is_imsil_education_target(target: Any) -> bool:
    url = _target_url(target)
    return bool(
        _provider(target) == IMSIL_PROVIDER
        and _safe_source_url(url, path=IMSIL_LIST_PATH)
        and _query(url) == {"mi": ["MN0131"]}
    )


is_target = is_imsil_education_target


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


def imsil_list_url(source_filter: str = "Z") -> str:
    if source_filter not in IMSIL_FILTERS:
        return ""
    return IMSIL_URL if source_filter == "Z" else f"{IMSIL_URL}&acc={source_filter}"


def imsil_detail_url(identity: str) -> str:
    if not _IDENTITY_RE.fullmatch(_clean(identity)):
        return ""
    return (
        f"https://{IMSIL_HOST}{IMSIL_DETAIL_PATH}?"
        + urlencode(
            (
                ("mi", "MN0131"),
                ("acc", "Z"),
                ("ik", _clean(identity)),
                ("bbsId", IMSIL_BBS_ID),
            )
        )
    )


def _list_payload(source_filter: str, page: int) -> dict[str, str]:
    return {
        "pageNo": str(page),
        "searchFiled": "",
        "searchValue": "",
        "agencyClassCd": source_filter,
        "agencyCd": "000000",
    }


def _same_request_url(actual: str, requested: str) -> bool:
    left, right = urlparse(actual), urlparse(requested)
    return bool(
        _safe_source_url(actual, path=right.path)
        and left.path == right.path
        and parse_qs(left.query, keep_blank_values=True)
        == parse_qs(right.query, keep_blank_values=True)
    )


def _response_soup(response: Any, requested: str) -> tuple[BeautifulSoup, str]:
    try:
        status = int(getattr(response, "status_code", 0))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise ImsilContractError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise ImsilContractError("HTTP redirects are not accepted")
    final_url = _clean(getattr(response, "url", ""))
    if not _same_request_url(final_url, requested):
        raise ImsilContractError("source response URL changed")
    content = getattr(response, "content", None)
    if content is None:
        content = getattr(response, "text", None)
    if not content:
        raise ImsilContractError("empty HTML response")
    return BeautifulSoup(content, "lxml"), final_url


def _request_soup(
    current: Any,
    method: str,
    url: str,
    *,
    timeout: int,
    data: Mapping[str, str],
    fetcher: Optional[Fetcher],
) -> tuple[BeautifulSoup, str, int]:
    messages: list[str] = []
    for attempt in range(1, IMSIL_FETCH_ATTEMPTS + 1):
        try:
            if fetcher is not None:
                result = fetcher(current, method, url, timeout=timeout, data=dict(data))
                if (
                    isinstance(result, tuple)
                    and len(result) == 2
                    and isinstance(result[0], BeautifulSoup)
                ):
                    soup, final_url = result
                    final_url = _clean(final_url or url)
                    if not _same_request_url(final_url, url):
                        raise ImsilContractError("source response URL changed")
                    return soup, final_url, attempt
                if isinstance(result, BeautifulSoup):
                    return result, url, attempt
                if isinstance(result, (str, bytes, bytearray)):
                    if not result:
                        raise ImsilContractError("empty HTML response")
                    return BeautifulSoup(result, "lxml"), url, attempt
                soup, final_url = _response_soup(result, url)
                return soup, final_url, attempt
            if method == "POST":
                response = current.post(url, data=dict(data), timeout=timeout)
            elif method == "GET":
                response = current.get(url, timeout=timeout)
            else:
                raise ImsilContractError("unsupported request method")
            soup, final_url = _response_soup(response, url)
            return soup, final_url, attempt
        except Exception as exc:
            messages.append(f"attempt {attempt}: {type(exc).__name__}: {_clean(exc)}")
    raise ImsilContractError("; ".join(messages))


def _digest(value: Any) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def _date_value(year: str, month: str, day: str, identity: str) -> date:
    try:
        return date(int(year), int(month), int(day))
    except ValueError as exc:
        raise ImsilContractError(f"program {identity}: invalid source date") from exc


def _period(
    value: Any,
    identity: str,
    title: str,
    *,
    kind: str,
) -> tuple[Optional[date], Optional[date]]:
    text = _clean(value)
    if kind == "application" and text == "~":
        if IMSIL_EMPTY_APPLICATION_PERIODS.get(identity) != title:
            raise ImsilContractError(
                f"program {identity}: unaudited empty application period"
            )
        return None, None
    match = _PERIOD_RE.fullmatch(text)
    if match is None:
        raise ImsilContractError(f"program {identity}: malformed {kind} period {text!r}")
    values = match.groups()
    start = _date_value(*values[:3], identity)
    if values[3] is None:
        if (
            kind != "event"
            or IMSIL_ONE_SIDED_EVENT_PERIODS.get(identity) != title
        ):
            raise ImsilContractError(
                f"program {identity}: unaudited one-sided {kind} period"
            )
        end = start
    else:
        end = _date_value(*values[3:], identity)
    if start > end:
        raise ImsilContractError(f"program {identity}: reversed {kind} period")
    return start, end


def _validate_tabs(soup: BeautifulSoup, source_filter: str) -> None:
    nodes = soup.select(".sub_tab_nav > li")
    values: list[tuple[str, str, bool]] = []
    for node in nodes:
        anchors = node.select(":scope > a[name='libClassClick']")
        hidden = node.select(":scope > input#AGENCY_CLASS_CD")
        if len(anchors) != 1 or len(hidden) != 1:
            raise ImsilContractError("library-class tabs changed")
        if (
            _clean(anchors[0].get("href")) != "#this"
            or _clean(anchors[0].get("onclick")) != "return false;"
        ):
            raise ImsilContractError("library-class tab control changed")
        values.append(
            (
                _clean(anchors[0].get_text(" ", strip=True)),
                _clean(hidden[0].get("value")),
                "active" in node.get("class", []),
            )
        )
    expected = [
        (label, code, code == source_filter)
        for code, label in IMSIL_FILTERS.items()
    ]
    if values != expected:
        raise ImsilContractError("official library-class vocabulary changed")


def _validate_form(soup: BeautifulSoup, source_filter: str, page: int) -> None:
    forms = soup.select(".boardSearch form[name='frm']")
    if len(forms) != 1:
        raise ImsilContractError("expected one unfiltered programme search form")
    form = forms[0]
    if _clean(form.get("method")) or _clean(form.get("action")):
        raise ImsilContractError("JavaScript search form transport changed")
    expected = _list_payload(source_filter, page)
    for name, value in expected.items():
        nodes = form.select(f"input[name='{name}']")
        if len(nodes) != 1 or _clean(nodes[0].get("value")) != value:
            raise ImsilContractError(f"programme form field {name} changed")
    selects = form.select("select[name='searchS']")
    if len(selects) != 1:
        raise ImsilContractError("programme search selector changed")
    options = [
        (_clean(node.get("value")), _clean(node.get_text(" ", strip=True)))
        for node in selects[0].select(":scope > option")
    ]
    if options != [("SUBJECT", "제목"), ("CONTENT", "내용")]:
        raise ImsilContractError("programme search vocabulary changed")
    search_inputs = form.select("input[name='searchI']")
    if len(search_inputs) != 1 or _clean(search_inputs[0].get("value")):
        raise ImsilContractError("unfiltered programme form contains a search")


def _declared_total(soup: BeautifulSoup, requested_page: int) -> tuple[int, int]:
    nodes = soup.select(".boardSearch > p")
    if len(nodes) != 1:
        raise ImsilContractError("expected one declared programme total")
    match = _TOTAL_RE.fullmatch(_clean(nodes[0].get_text(" ", strip=True)))
    if match is None or int(match.group(2)) != requested_page:
        raise ImsilContractError("declared total/current-page contract changed")
    total = int(match.group(1).replace(",", ""))
    return total, max(1, math.ceil(total / IMSIL_PAGE_SIZE))


def _validate_pagination(
    soup: BeautifulSoup,
    *,
    requested_page: int,
    last_page: int,
    sentinel: bool,
) -> None:
    pagers = soup.select(".paging")
    if len(pagers) != 1:
        raise ImsilContractError("expected one programme pagination block")
    focus = pagers[0].select("a.focus")
    if sentinel:
        if focus:
            raise ImsilContractError("post-last sentinel unexpectedly has page focus")
    elif len(focus) != 1 or _clean(focus[0].get_text(" ", strip=True)) != str(
        requested_page
    ):
        raise ImsilContractError("pagination current page mismatch")
    for anchor in pagers[0].select("a"):
        material = f"{_clean(anchor.get('onclick'))} {_clean(anchor.get('href'))}"
        matches = re.findall(r"fn_movePage\('([1-9]\d*)'\)", material)
        if len(matches) != 1 or not 1 <= int(matches[0]) <= last_page:
            raise ImsilContractError("malformed pagination control")


def _card_fields(card: Any, identity: str) -> dict[str, str]:
    output: dict[str, str] = {}
    for block in card.select(":scope > a > ol > li > dl"):
        labels, values = block.select(":scope > dt"), block.select(":scope > dd")
        if len(labels) != 1 or len(values) != 1:
            raise ImsilContractError(f"program {identity}: malformed list field")
        label = _clean(labels[0].get_text(" ", strip=True))
        if not label or label in output:
            raise ImsilContractError(f"program {identity}: duplicate list field")
        output[label] = _clean(values[0].get_text(" ", strip=True))
    if tuple(output) != _LIST_FIELDS:
        raise ImsilContractError(f"program {identity}: list fields changed")
    return output


def _title_without_new(node: Any) -> str:
    for marker in node.select(":scope > span.new"):
        if _clean(marker.get_text(" ", strip=True)) != "NEW":
            raise ImsilContractError("new-program marker changed")
    return _clean(" ".join(str(value) for value in node.find_all(string=True, recursive=False)))


def _parse_page(
    soup: BeautifulSoup,
    *,
    source_filter: str,
    requested_page: int,
    sentinel: bool = False,
) -> tuple[int, int, list[_ListedProgram], str]:
    _validate_tabs(soup, source_filter)
    _validate_form(soup, source_filter, requested_page)
    total, last_page = _declared_total(soup, requested_page)
    _validate_pagination(
        soup,
        requested_page=requested_page,
        last_page=last_page,
        sentinel=sentinel,
    )
    containers = soup.select("ul.eventListBox")
    if len(containers) != 1:
        raise ImsilContractError("expected one canonical programme list")
    cards = containers[0].select(":scope > li")
    expected = (
        0
        if sentinel
        else min(
            IMSIL_PAGE_SIZE,
            max(0, total - (requested_page - 1) * IMSIL_PAGE_SIZE),
        )
    )
    if len(cards) != expected:
        raise ImsilContractError(
            f"{source_filter} page {requested_page}: expected {expected} cards, "
            f"found {len(cards)}"
        )

    rows: list[_ListedProgram] = []
    for card in cards:
        ids = card.select(":scope > input#IDX")
        boards = card.select(":scope > input#BBS_ID")
        if len(ids) != 1 or len(boards) != 1:
            raise ImsilContractError("program identity controls changed")
        identity = _clean(ids[0].get("value"))
        if not _IDENTITY_RE.fullmatch(identity):
            raise ImsilContractError("program identity is missing")
        if _clean(boards[0].get("value")) != IMSIL_BBS_ID:
            raise ImsilContractError(f"program {identity}: BBS identity changed")
        anchors = card.select(":scope > a[name='title']")
        if len(anchors) != 1:
            raise ImsilContractError(f"program {identity}: title control changed")
        anchor = anchors[0]
        if (
            _clean(anchor.get("href")) != "#this"
            or _clean(anchor.get("onclick")) != "return false;"
        ):
            raise ImsilContractError(f"program {identity}: title route changed")
        titles = anchor.select(":scope > h3")
        branches = anchor.select(":scope > span")
        badges = anchor.select(":scope > b.eventBtn")
        if len(titles) != 1 or len(branches) != 1 or len(badges) != 2:
            raise ImsilContractError(f"program {identity}: card header changed")
        title = _title_without_new(titles[0])
        if not title:
            raise ImsilContractError(f"program {identity}: empty title")
        branch_label = _clean(branches[0].get_text(" ", strip=True))
        if branch_label not in IMSIL_BRANCHES:
            raise ImsilContractError(f"program {identity}: unknown branch label")
        if set(branches[0].get("class", [])) != {
            IMSIL_BRANCH_CLASSES[branch_label]
        }:
            raise ImsilContractError(f"program {identity}: branch badge changed")
        receipt_status = _clean(badges[0].get_text(" ", strip=True))
        event_status = _clean(badges[1].get_text(" ", strip=True))
        status_pair = (receipt_status, event_status)
        expected_classes = _STATUS_CLASSES.get(status_pair)
        actual_classes = tuple(
            next(
                (
                    name
                    for name in badge.get("class", [])
                    if name.startswith("eventBtn") and name != "eventBtn"
                ),
                "",
            )
            for badge in badges
        )
        if (
            expected_classes is None
            or actual_classes != expected_classes
            or any("eventBtn" not in badge.get("class", []) for badge in badges)
        ):
            raise ImsilContractError(f"program {identity}: source status changed")

        fields = _card_fields(card, identity)
        apply_start, apply_end = _period(
            fields["접수"], identity, title, kind="application"
        )
        event_start, event_end = _period(
            fields["수강"], identity, title, kind="event"
        )
        if event_start is None or event_end is None:
            raise ImsilContractError(f"program {identity}: missing event period")
        capacity_match = _CAPACITY_RE.fullmatch(fields["접수현황"])
        if capacity_match is None:
            raise ImsilContractError(f"program {identity}: malformed aggregate capacity")
        capacity_total = int(capacity_match.group(3))
        rows.append(
            _ListedProgram(
                identity=identity,
                title=title,
                branch_label=branch_label,
                branch=IMSIL_BRANCHES[branch_label],
                receipt_status=receipt_status,
                event_status=event_status,
                apply_start=apply_start,
                apply_end=apply_end,
                raw_apply_period=fields["접수"],
                event_start=event_start,
                event_end=event_end,
                raw_event_period=fields["수강"],
                target=fields["대상"],
                capacity_total=capacity_total,
                page=requested_page,
                source_filter=source_filter,
                detail_url=imsil_detail_url(identity),
            )
        )
    signature = _digest(tuple(_row_key(row) for row in rows))
    return total, last_page, rows, signature


def _row_key(row: _ListedProgram) -> tuple[Any, ...]:
    return (
        row.identity,
        row.title,
        row.branch_label,
        row.receipt_status,
        row.event_status,
        row.raw_apply_period,
        row.raw_event_period,
        row.target,
        row.capacity_total,
    )


def _detail_fields(table: Any, identity: str) -> dict[str, str]:
    output: dict[str, str] = {}
    sequence: list[str] = []
    for heading in table.select("tbody tr > th"):
        label = _clean(heading.get_text(" ", strip=True))
        value = heading.find_next_sibling("td")
        if value is None or not label or label in output:
            raise ImsilContractError(f"program {identity}: malformed detail field")
        sequence.append(label)
        # Contact, wait-list and attachment payloads are contract-checked by
        # label but intentionally never copied into the parsed value mapping.
        output[label] = (
            ""
            if label in {"문의처", "대기인원", "강의계획서"}
            else _clean(value.get_text(" ", strip=True))
        )
    if tuple(sequence) != _DETAIL_FIELDS:
        raise ImsilContractError(f"program {identity}: detail fields changed")
    return output


def _script_value(script: str, key: str, identity: str) -> str:
    matches = re.findall(rf"\bvar\s+{re.escape(key)}\s*=\s*'([^']*)'\s*;", script)
    if len(matches) != 1:
        raise ImsilContractError(f"program {identity}: detail variable {key} changed")
    return _clean(matches[0])


def _parse_detail(soup: BeautifulSoup, program: _ListedProgram) -> _Detail:
    tables = soup.select(".contents .tableBox > table.tbView")
    if len(tables) != 1:
        raise ImsilContractError(f"program {program.identity}: detail table changed")
    table = tables[0]
    titles = table.select("tbody tr > td.PrTitle")
    if len(titles) != 1:
        raise ImsilContractError(f"program {program.identity}: detail title changed")
    title_cell = titles[0]
    title = _clean(
        " ".join(
            str(value) for value in title_cell.find_all(string=True, recursive=False)
        )
    )
    branch_nodes = title_cell.select(":scope > span.viewBul")
    badges = title_cell.select(":scope > b.eventBtn")
    if len(branch_nodes) != 1 or len(badges) != 2:
        raise ImsilContractError(f"program {program.identity}: detail header changed")
    branch_label = _clean(branch_nodes[0].get_text(" ", strip=True))
    status_pair = tuple(_clean(node.get_text(" ", strip=True)) for node in badges)
    if (
        title != program.title
        or branch_label != program.branch_label
        or status_pair != (program.receipt_status, program.event_status)
        or set(branch_nodes[0].get("class", []))
        != {"viewBul", IMSIL_BRANCH_CLASSES[branch_label]}
    ):
        raise ImsilContractError(f"program {program.identity}: list/detail mismatch")
    expected_classes = _STATUS_CLASSES[status_pair]
    actual_classes = tuple(
        next(
            (
                name
                for name in badge.get("class", [])
                if name.startswith("eventBtn") and name != "eventBtn"
            ),
            "",
        )
        for badge in badges
    )
    if actual_classes != expected_classes:
        raise ImsilContractError(f"program {program.identity}: detail status drift")

    fields = _detail_fields(table, program.identity)
    apply_range = _period(
        fields["접수기간"], program.identity, program.title, kind="application"
    )
    event_range = _period(
        fields["운영기간"], program.identity, program.title, kind="event"
    )
    if apply_range != (program.apply_start, program.apply_end):
        raise ImsilContractError(
            f"program {program.identity}: list/detail application period mismatch"
        )
    if event_range != (program.event_start, program.event_end):
        raise ImsilContractError(
            f"program {program.identity}: list/detail event period mismatch"
        )
    if fields["신청대상"] != program.target:
        raise ImsilContractError(f"program {program.identity}: target mismatch")
    capacity_match = _DETAIL_CAPACITY_RE.fullmatch(fields["신청인원"])
    if capacity_match is None or int(capacity_match.group(2)) != program.capacity_total:
        raise ImsilContractError(f"program {program.identity}: capacity mismatch")

    button_boxes = soup.select(".contents .rightBox")
    if len(button_boxes) != 1:
        raise ImsilContractError(f"program {program.identity}: detail buttons changed")
    buttons = button_boxes[0].select(":scope > a")
    list_controls = [node for node in buttons if _clean(node.get("id")) == "list"]
    if (
        len(list_controls) != 1
        or set(list_controls[0].get("class", [])) != {"btn2"}
        or _clean(list_controls[0].get("href")) != "#this"
        or _clean(list_controls[0].get_text(" ", strip=True)) != "목록"
    ):
        raise ImsilContractError(f"program {program.identity}: list control changed")
    others = [node for node in buttons if node not in list_controls]
    control = "none"
    if others:
        if len(others) != 1:
            raise ImsilContractError(f"program {program.identity}: extra detail control")
        node = others[0]
        classes = set(node.get("class", []))
        label = _clean(node.get_text(" ", strip=True))
        href = _clean(node.get("href"))
        if (
            _clean(node.get("id")) == "applyLogin"
            and classes == {"btn1"}
            and label == "신청"
            and href == "#"
        ):
            control = "login"
        elif (
            not _clean(node.get("id"))
            and classes == {"btn3"}
            and label == "마감"
            and href == "#"
        ):
            control = "closed"
        else:
            raise ImsilContractError(
                f"program {program.identity}: unknown application control"
            )

    script = "\n".join(node.get_text("\n") for node in soup.find_all("script"))
    expected_vars = {
        "mi": "MN0131",
        "acc": "Z",
        "ik": program.identity,
        "bbsId": IMSIL_BBS_ID,
    }
    for key, expected in expected_vars.items():
        if _script_value(script, key, program.identity) != expected:
            raise ImsilContractError(
                f"program {program.identity}: detail variable {key} mismatch"
            )
    if IMSIL_APPLICATION_PATH not in re.sub(r";jsessionid=[^?\"']+", "", script):
        raise ImsilContractError(f"program {program.identity}: application route changed")
    if IMSIL_LOGIN_PATH not in re.sub(r";jsessionid=[^?\"']+", "", script):
        raise ImsilContractError(f"program {program.identity}: login route changed")
    return _Detail(
        venue=fields["운영장소"],
        schedule=fields["운영시간"],
        fee=fields["수강료 및 재료비"],
        target=fields["신청대상"],
        method=fields["접수방법"],
        control=control,
        current_applicants=int(capacity_match.group(1)),
    )


def _application_state(
    program: _ListedProgram, detail: _Detail, audit_date: date
) -> tuple[str, str, str, bool]:
    if program.apply_start is None or program.apply_end is None:
        effective = "CLOSED"
    elif audit_date < program.apply_start:
        effective = "SCHEDULED"
    elif (
        audit_date > program.apply_end
        or program.receipt_status == "접수종료"
        or detail.current_applicants >= program.capacity_total
    ):
        effective = "CLOSED"
    else:
        effective = "OPEN"

    internet = "인터넷" in detail.method
    offline = any(word in detail.method for word in ("방문", "접수")) and not internet
    if effective == "OPEN" and internet:
        if detail.control != "login":
            raise ImsilContractError(
                f"program {program.identity}: open online application control missing"
            )
        return "OPEN", "ONLINE_APPLICATION", program.detail_url, True
    if effective == "OPEN" and offline:
        if detail.control not in {"none", "closed"}:
            raise ImsilContractError(
                f"program {program.identity}: offline programme exposes login control"
            )
        return "OPEN", "OFFLINE_APPLICATION", "", False
    if effective == "CLOSED" and detail.current_applicants >= program.capacity_total:
        if detail.control != "closed":
            raise ImsilContractError(
                f"program {program.identity}: full programme is not closed"
            )
    elif effective == "CLOSED" and detail.control not in {"closed", "login", "none"}:
        raise ImsilContractError(
            f"program {program.identity}: invalid closed application control"
        )
    elif effective == "SCHEDULED" and detail.control not in {
        "none",
        "closed",
        # The public detail page exposes its login shell before receipt opens.
        # Scheduled rows remain information-only and never publish that route.
        "login",
    }:
        raise ImsilContractError(
            f"program {program.identity}: scheduled programme exposes application"
        )
    return effective, "INFORMATION_ONLY", "", False


def _branch_code(branch: str) -> str:
    suffix = hashlib.sha1(branch.encode("utf-8")).hexdigest().upper()[:8]
    return f"{IMSIL_PROVIDER}:program:{suffix}"


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
        "municipality_code": IMSIL_MUNICIPALITY_CODE,
        "municipality_name": IMSIL_MUNICIPALITY_NAME,
        "municipality_full_name": IMSIL_MUNICIPALITY_NAME,
    }


def _output_row(
    target: Any,
    program: _ListedProgram,
    detail: _Detail,
    audit_date: date,
) -> dict[str, Any]:
    status, application_type, application_url, available = _application_state(
        program, detail, audit_date
    )
    output: dict[str, Any] = {
        "provider": IMSIL_PROVIDER,
        "provider_course_id": (
            f"{IMSIL_PROVIDER}:education:program:{program.identity}"
        ),
        "prefer_incoming_provider_course_id": True,
        "title": program.title,
        "branch": program.branch,
        "branch_code": _branch_code(program.branch),
        "preserve_branch": True,
        "branch_url": IMSIL_URL,
        "raw_url": program.detail_url,
        "application_url": application_url,
        "application_type": application_type,
        "application_method_raw": detail.method,
        "reservation_available": available,
        "status": status,
        "period": f"{program.event_start.isoformat()} ~ {program.event_end.isoformat()}",
        "start_date": program.event_start.isoformat(),
        "end_date": program.event_end.isoformat(),
        "apply_period": (
            f"{program.apply_start.isoformat()} ~ {program.apply_end.isoformat()}"
            if program.apply_start is not None and program.apply_end is not None
            else ""
        ),
        "apply_start_date": (
            program.apply_start.isoformat() if program.apply_start is not None else ""
        ),
        "apply_end_date": (
            program.apply_end.isoformat() if program.apply_end is not None else ""
        ),
        "schedule_raw": detail.schedule,
        "target": detail.target,
        "capacity": f"{program.capacity_total}명",
        "capacity_total": program.capacity_total,
        "fee": detail.fee,
        "venue_name": detail.venue or program.branch,
        "room": detail.venue or program.branch,
        "category": "도서관 교육·문화프로그램",
        "collection_type": IMSIL_PARSER,
        "raw_fields": {
            "parser": IMSIL_PARSER,
            "source_kind": "library_program",
            "source_identity": program.identity,
            "source_page": program.page,
            "source_branch_label": program.branch_label,
            "source_receipt_status": program.receipt_status,
            "source_event_status": program.event_status,
            "source_event_period": program.raw_event_period,
            "source_apply_period": program.raw_apply_period,
            "detail_verified": True,
            "application_control_present": detail.control == "login",
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
        "municipality_code": IMSIL_MUNICIPALITY_CODE,
        "municipality_full_name": IMSIL_MUNICIPALITY_NAME,
        "provider": IMSIL_PROVIDER,
        "canonical_url": IMSIL_URL,
        "provider_url_sha1": IMSIL_PROVIDER_URL_SHA1,
        "canonical_url_sha256": IMSIL_CANONICAL_URL_SHA256,
        "canonical_candidate_id": IMSIL_CANONICAL_CANDIDATE_ID,
        "incumbent_provider": IMSIL_INCUMBENT_PROVIDER,
        "incumbent_url": IMSIL_INCUMBENT_URL,
        "incumbent_url_sha1": IMSIL_INCUMBENT_URL_SHA1,
        "incumbent_url_sha256": IMSIL_INCUMBENT_URL_SHA256,
        "incumbent_decision": IMSIL_INCUMBENT_DECISION,
        "candidate_decisions": dict(IMSIL_CANDIDATE_DECISIONS),
        "ownership_scope": IMSIL_OWNERSHIP_SCOPE,
        "ledger_totals": {code: 0 for code in IMSIL_FILTERS},
        "ledger_pages": {code: 0 for code in IMSIL_FILTERS},
        "source_total": 0,
        "source_unique_total": 0,
        "education_total": 0,
        "source_status_counts": {},
        "branch_counts": {},
        "filter_branch_counts": {code: {} for code in IMSIL_FILTERS},
        "global_only_ids": [],
        "noncourse_excluded_ids": [],
        "noncourse_excluded_count": 0,
        "current_raw_count": 0,
        "current_count": 0,
        "excluded_current_count": 0,
        "expired_count": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_login_control_count": 0,
        "stale_login_control_count": 0,
        "excluded_noncourse_login_control_count": 0,
        "capacity_close_override_count": 0,
        "online_application_count": 0,
        "offline_application_count": 0,
        "application_endpoint_fetches": 0,
        "list_requests": 0,
        "logical_requests": 0,
        "physical_requests": 0,
        "request_retry_count": 0,
        "sentinel_mode": "immediate_empty_post_last_page",
        "sentinel_pages": {code: 0 for code in IMSIL_FILTERS},
        "sentinel_counts": {code: 0 for code in IMSIL_FILTERS},
        "stable_rechecks": {
            f"{code}_{boundary}": False
            for code in IMSIL_FILTERS
            for boundary in ("first", "final", "sentinel")
        },
        "empty_application_period_ids": [],
        "one_sided_event_period_ids": [],
        "pagination_complete": False,
        "partition_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "pii_payload_persisted": False,
        "source_cap_reached": False,
        "configured_collection_error": "",
    }


def collect_imsil_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 30,
    detail_limit: int = 10,
    max_workers: int = IMSIL_MAX_WORKERS,
    today: Optional[date | datetime | str] = None,
    session_factory: SessionFactory = _default_session_factory,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future Imsil library-owner snapshot."""

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
        return [], IMSIL_PARSER, meta
    if not is_imsil_education_target(target):
        meta["configured_collection_error"] = "target is outside canonical Imsil scope"
        return [], IMSIL_PARSER, meta

    main_session = session_factory()
    try:
        def request_list(source_filter: str, page: int) -> BeautifulSoup:
            soup, _, attempts = _request_soup(
                main_session,
                "POST",
                imsil_list_url(source_filter),
                timeout=timeout_value,
                data=_list_payload(source_filter, page),
                fetcher=fetcher,
            )
            meta["logical_requests"] += 1
            meta["physical_requests"] += attempts
            meta["list_requests"] += 1
            return soup

        def traverse(source_filter: str) -> list[_ListedProgram]:
            first_soup = request_list(source_filter, 1)
            total, last_page, first_rows, first_signature = _parse_page(
                first_soup, source_filter=source_filter, requested_page=1
            )
            if last_page > allowed_pages:
                meta["source_cap_reached"] = True
                raise ImsilContractError(
                    f"max_pages cap {allowed_pages} is below {source_filter} final "
                    f"page {last_page}"
                )
            meta["ledger_totals"][source_filter] = total
            meta["ledger_pages"][source_filter] = last_page
            pages: dict[int, list[_ListedProgram]] = {1: first_rows}
            signatures: dict[int, str] = {1: first_signature}
            for page in range(2, last_page + 1):
                soup = request_list(source_filter, page)
                page_total, page_last, rows, signature = _parse_page(
                    soup, source_filter=source_filter, requested_page=page
                )
                if (page_total, page_last) != (total, last_page):
                    raise ImsilContractError(
                        f"{source_filter}: total/final page changed during traversal"
                    )
                pages[page], signatures[page] = rows, signature
            rows = [row for page in range(1, last_page + 1) for row in pages[page]]
            if len(rows) != total:
                raise ImsilContractError(
                    f"{source_filter}: traversal does not equal declared total"
                )
            ids = [int(row.identity) for row in rows]
            if len(ids) != len(set(ids)) or ids != sorted(ids, reverse=True):
                raise ImsilContractError(
                    f"{source_filter}: identities are duplicate or not descending"
                )

            sentinel_page = last_page + 1
            sentinel_soup = request_list(source_filter, sentinel_page)
            sentinel_total, sentinel_last, sentinel_rows, sentinel_signature = _parse_page(
                sentinel_soup,
                source_filter=source_filter,
                requested_page=sentinel_page,
                sentinel=True,
            )
            if (
                (sentinel_total, sentinel_last) != (total, last_page)
                or sentinel_rows
                or sentinel_signature != _digest(())
            ):
                raise ImsilContractError(
                    f"{source_filter}: immediate post-last sentinel is not empty"
                )
            meta["sentinel_pages"][source_filter] = sentinel_page
            meta["sentinel_counts"][source_filter] = len(sentinel_rows)

            recheck_first = request_list(source_filter, 1)
            rt, rl, _, signature = _parse_page(
                recheck_first, source_filter=source_filter, requested_page=1
            )
            stable = (rt, rl, signature) == (total, last_page, signatures[1])
            meta["stable_rechecks"][f"{source_filter}_first"] = stable
            if not stable:
                raise ImsilContractError(f"{source_filter}: first page changed on recheck")

            if last_page == 1:
                meta["stable_rechecks"][f"{source_filter}_final"] = True
            else:
                recheck_final = request_list(source_filter, last_page)
                rt, rl, _, signature = _parse_page(
                    recheck_final,
                    source_filter=source_filter,
                    requested_page=last_page,
                )
                stable = (rt, rl, signature) == (
                    total,
                    last_page,
                    signatures[last_page],
                )
                meta["stable_rechecks"][f"{source_filter}_final"] = stable
                if not stable:
                    raise ImsilContractError(
                        f"{source_filter}: final page changed on recheck"
                    )

            recheck_sentinel = request_list(source_filter, sentinel_page)
            rt, rl, re_rows, signature = _parse_page(
                recheck_sentinel,
                source_filter=source_filter,
                requested_page=sentinel_page,
                sentinel=True,
            )
            stable = (
                (rt, rl) == (total, last_page)
                and not re_rows
                and signature == sentinel_signature
            )
            meta["stable_rechecks"][f"{source_filter}_sentinel"] = stable
            if not stable:
                raise ImsilContractError(
                    f"{source_filter}: sentinel changed on recheck"
                )
            return rows

        ledgers = {code: traverse(code) for code in IMSIL_FILTERS}
        all_rows, county_rows, small_rows = (
            ledgers["Z"],
            ledgers["A"],
            ledgers["B"],
        )
        all_by_id = {row.identity: row for row in all_rows}
        county_by_id = {row.identity: row for row in county_rows}
        small_by_id = {row.identity: row for row in small_rows}
        if set(county_by_id).intersection(small_by_id):
            raise ImsilContractError("county/small partitions overlap")
        if any(row.branch_label not in IMSIL_COUNTY_BRANCHES for row in county_rows):
            raise ImsilContractError("county partition contains a non-county branch")
        if any(row.branch_label not in IMSIL_SMALL_BRANCHES for row in small_rows):
            raise ImsilContractError("small partition contains a non-small branch")
        for identity, filtered in {**county_by_id, **small_by_id}.items():
            canonical = all_by_id.get(identity)
            if canonical is None or _row_key(filtered) != _row_key(canonical):
                raise ImsilContractError(
                    f"program {identity}: filtered/canonical row mismatch"
                )
        global_only = set(all_by_id).difference(county_by_id, small_by_id)
        if global_only != set(IMSIL_NONCOURSE_IDENTITIES):
            raise ImsilContractError("global-only/non-course identity boundary changed")
        for identity, expected_title in IMSIL_NONCOURSE_IDENTITIES.items():
            row = all_by_id[identity]
            if row.title != expected_title or row.branch_label != "전체":
                raise ImsilContractError(
                    f"program {identity}: non-course exclusion binding changed"
                )

        meta["source_total"] = len(all_rows)
        meta["source_unique_total"] = len(all_rows)
        meta["education_total"] = len(all_rows) - len(IMSIL_NONCOURSE_IDENTITIES)
        meta["global_only_ids"] = sorted(global_only, key=int, reverse=True)
        meta["noncourse_excluded_ids"] = sorted(
            IMSIL_NONCOURSE_IDENTITIES, key=int, reverse=True
        )
        meta["noncourse_excluded_count"] = len(IMSIL_NONCOURSE_IDENTITIES)
        meta["source_status_counts"] = dict(
            sorted(
                Counter(
                    f"{row.receipt_status}/{row.event_status}" for row in all_rows
                ).items()
            )
        )
        meta["branch_counts"] = dict(
            sorted(Counter(row.branch_label for row in all_rows).items())
        )
        meta["filter_branch_counts"] = {
            code: dict(sorted(Counter(row.branch_label for row in rows).items()))
            for code, rows in ledgers.items()
        }
        meta["empty_application_period_ids"] = sorted(
            (
                row.identity
                for row in all_rows
                if row.apply_start is None and row.apply_end is None
            ),
            key=int,
            reverse=True,
        )
        meta["one_sided_event_period_ids"] = sorted(
            (
                row.identity
                for row in all_rows
                if row.identity in IMSIL_ONE_SIDED_EVENT_PERIODS
            ),
            key=int,
            reverse=True,
        )
        if set(meta["empty_application_period_ids"]) != set(
            IMSIL_EMPTY_APPLICATION_PERIODS
        ):
            raise ImsilContractError("empty application-period anomaly set changed")
        if set(meta["one_sided_event_period_ids"]) != set(
            IMSIL_ONE_SIDED_EVENT_PERIODS
        ):
            raise ImsilContractError("one-sided event-period anomaly set changed")

        current_raw = [row for row in all_rows if row.event_end >= audit_date]
        current_courses = [
            row
            for row in current_raw
            if row.identity not in IMSIL_NONCOURSE_IDENTITIES
        ]
        meta["current_raw_count"] = len(current_raw)
        meta["excluded_current_count"] = len(current_raw) - len(current_courses)
        meta["expired_count"] = meta["education_total"] - len(current_courses)
        if len(current_raw) > allowed_details:
            meta["source_cap_reached"] = True
            raise ImsilContractError(
                f"detail_limit cap {allowed_details} is below raw current count "
                f"{len(current_raw)}"
            )

        def fetch_detail(program: _ListedProgram) -> tuple[_ListedProgram, _Detail, int]:
            session = session_factory()
            try:
                soup, _, attempts = _request_soup(
                    session,
                    "GET",
                    program.detail_url,
                    timeout=timeout_value,
                    data={},
                    fetcher=fetcher,
                )
                return program, _parse_detail(soup, program), attempts
            finally:
                _close_quietly(session)

        detail_results: list[tuple[_ListedProgram, _Detail, int]] = []
        if current_raw:
            if workers == 1:
                detail_results = [fetch_detail(row) for row in current_raw]
            else:
                with ThreadPoolExecutor(
                    max_workers=min(workers, len(current_raw))
                ) as executor:
                    detail_results = list(executor.map(fetch_detail, current_raw))
        meta["detail_attempts"] = len(current_raw)
        meta["detail_pages"] = len(detail_results)
        meta["logical_requests"] += len(detail_results)
        meta["physical_requests"] += sum(item[2] for item in detail_results)
        meta["detail_login_control_count"] = sum(
            detail.control == "login" for _, detail, _ in detail_results
        )
        meta["excluded_noncourse_login_control_count"] = sum(
            program.identity in IMSIL_NONCOURSE_IDENTITIES
            and detail.control == "login"
            for program, detail, _ in detail_results
        )

        results: list[dict[str, Any]] = []
        for program, detail, _ in detail_results:
            state, _, _, _ = _application_state(program, detail, audit_date)
            if (
                state == "CLOSED"
                and detail.control == "login"
                and program.identity not in IMSIL_NONCOURSE_IDENTITIES
            ):
                meta["stale_login_control_count"] += 1
            if (
                state == "CLOSED"
                and program.receipt_status == "접수진행"
                and detail.current_applicants >= program.capacity_total
                and detail.control == "closed"
            ):
                meta["capacity_close_override_count"] += 1
            if program.identity in IMSIL_NONCOURSE_IDENTITIES:
                continue
            results.append(_output_row(target, program, detail, audit_date))

        ids = [_clean(row.get("provider_course_id")) for row in results]
        if len(ids) != len(set(ids)) or any(not value for value in ids):
            raise ImsilContractError("duplicate or empty output provider_course_id")
        if dedupe_rows is not None:
            deduped = list(dedupe_rows(results))
            deduped_ids = [_clean(row.get("provider_course_id")) for row in deduped]
            if len(deduped) != len(results) or set(deduped_ids) != set(ids):
                raise ImsilContractError("external dedupe changed complete identity snapshot")
            results = deduped
        if len(results) != len(current_courses):
            raise ImsilContractError("current/future output count does not reconcile")
        if not _privacy_valid(results):
            raise ImsilContractError("PII or excluded detail payload reached output rows")

        meta["current_count"] = len(results)
        meta["online_application_count"] = sum(
            row["application_type"] == "ONLINE_APPLICATION" for row in results
        )
        meta["offline_application_count"] = sum(
            row["application_type"] == "OFFLINE_APPLICATION" for row in results
        )
        meta["pagination_complete"] = True
        meta["partition_complete"] = True
        meta["details_complete"] = meta["detail_pages"] == len(current_raw)
        meta["snapshot_complete"] = bool(
            meta["pagination_complete"]
            and meta["partition_complete"]
            and meta["details_complete"]
            and all(meta["stable_rechecks"].values())
            and meta["current_count"] == len(current_courses)
        )
        meta["full_snapshot_validated"] = meta["snapshot_complete"]
        meta["request_retry_count"] = (
            meta["physical_requests"] - meta["logical_requests"]
        )
        return results, IMSIL_PARSER, meta
    except Exception as exc:
        meta["configured_collection_error"] = _clean(exc)
        meta["snapshot_complete"] = False
        meta["full_snapshot_validated"] = False
        meta["request_retry_count"] = max(
            0, meta["physical_requests"] - meta["logical_requests"]
        )
        return [], IMSIL_PARSER, meta
    finally:
        _close_quietly(main_session)


collect = collect_imsil_education
