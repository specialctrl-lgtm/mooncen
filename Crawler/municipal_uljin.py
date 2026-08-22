"""Fail-closed collector for Uljin-gun's integrated education ledger.

Uljin already has an active provider for the mixed reservation landing page.
The complete education owner is the provider's ``교육/강좌`` catalogue, not
the ten-card landing-page excerpt and not the municipal-library culture page.
This module therefore retains the incumbent provider while using the exact
education catalogue as its canonical URL.

The catalogue exposes five active facility filters.  Every advertised page is
walked, the five filters must be a disjoint exact union of the unfiltered
ledger, the public ``모집중`` filter must equal the rows with an active list
control, and an exact clamped page after the last page is verified.  All full
ledger pages and the clamp are fetched again after the filters, so a changing
snapshot is discarded atomically.

Course-title links lead to an authentication-gated application page rather
than a separate public detail.  Those links and active/inactive controls are
identity-validated but never requested.  Login, application, reservation
lookup, attachment, free-text, and applicant endpoints are never fetched or
persisted.
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


ULJIN_PROVIDER = "MUNI_WWW_ULJIN_GO_KR_3EFF1FF0"
ULJIN_MUNICIPALITY_CODE = "4793000000"
ULJIN_MUNICIPALITY_NAME = "경상북도 울진군"

ULJIN_HOST = "www.uljin.go.kr"
ULJIN_PATH = "/reserve/index.uljin"
ULJIN_LINK_PATH = "/index.uljin"
ULJIN_LIST_MENU = "DOM_000000401000000000"
ULJIN_APPLICATION_MENU = "DOM_000000401001000000"
ULJIN_LEGACY_TARGET_URL = f"https://{ULJIN_HOST}{ULJIN_PATH}"
ULJIN_CANONICAL_URL = (
    f"https://{ULJIN_HOST}{ULJIN_PATH}?menuCd={ULJIN_LIST_MENU}"
)
ULJIN_CANONICAL_URL_SHA256 = (
    "ab35d371f1e23a4aeb5cc3543113ed99bf0d5cb5350e90f43170cee163abedb4"
)
ULJIN_CANONICAL_CANDIDATE_ID = "MUNI_IR_AB35D371F1E2"
ULJIN_REVIEW_ROOT_CANDIDATE_ID = "MUNI_IR_8A11B6FAD65F"
ULJIN_LIBRARY_CANDIDATE_ID = "MUNI_IR_2D457FE1E0C4"
ULJIN_LIBRARY_PROVIDER = "MUNI_LIB_ULJIN_GO_KR_84BA0199"
ULJIN_EDUCATION_OFFICE_PROVIDER = "MUNI_WWW_GBE_KR_98673AC8"

ULJIN_PAGE_SIZE = 10
ULJIN_RECOMMENDED_MAX_PAGES = 100
ULJIN_RECOMMENDED_DETAIL_LIMIT = 0
ULJIN_FETCH_ATTEMPTS = 2
ULJIN_MAX_HTML_BYTES = 2_000_000
ULJIN_PARSER = (
    "uljin_integrated_complete_current_education_ledger+five_facility_"
    "partition+public_open_filter+advertised_pages_and_exact_last_clamp+"
    "stable_full_recheck+identity_bound_login_gated_application_no_fetch+"
    "exact_facility_branches+structured_list_allowlist"
)
ULJIN_OWNERSHIP_SCOPE = (
    "uljin_county_integrated_reservation_complete_education_course_ledger"
)


class UljinContractError(ValueError):
    """Raised when the official Uljin source violates its audited contract."""


@dataclass(frozen=True)
class UljinCategory:
    code: str
    label: str
    branch: str
    branch_code: str
    branch_url: str
    address: str

    @property
    def order_field(self) -> str:
        return "RE_NAME" if self.code == "02" else "reSdate"

    @property
    def order_sort(self) -> str:
        return "asc" if self.code == "02" else "desc"


ULJIN_CATEGORIES: tuple[UljinCategory, ...] = (
    UljinCategory(
        "02",
        "울진군 평생학습관",
        "울진군 평생학습관",
        "ULJIN_LIFELONG_LEARNING_CENTER",
        "https://www.uljin.go.kr/learning/index.uljin",
        "경상북도 울진군 울진읍 울진북로 496-11",
    ),
    UljinCategory(
        "03",
        "청소년수련시설",
        "",
        "",
        "https://www.uljin.go.kr/young/index.uljin",
        "",
    ),
    UljinCategory(
        "07",
        "울진문화예술회관",
        "울진문화예술회관",
        "ULJIN_CULTURE_AND_ARTS_CENTER",
        "https://www.uljin.go.kr/art/index.uljin",
        "경상북도 울진군 후포면 후포삼율로 194-14",
    ),
    UljinCategory(
        "05",
        "과학체험관",
        "울진과학체험관",
        "ULJIN_SCIENCE_EXPERIENCE_CENTER",
        "https://www.uljin.go.kr/science/index.uljin",
        "경상북도 울진군 울진읍 연지길 30",
    ),
    UljinCategory(
        "01",
        "농업기술센터",
        "울진군농업기술센터",
        "ULJIN_AGRICULTURAL_TECHNOLOGY_CENTER",
        "https://www.uljin.go.kr/agro/index.uljin",
        "경상북도 울진군 매화면 매화매실길 76",
    ),
)
ULJIN_CATEGORY_BY_CODE = {category.code: category for category in ULJIN_CATEGORIES}

ULJIN_YOUTH_BRANCHES: Mapping[str, Mapping[str, str]] = {
    "ULJIN_YOUTH_TRAINING_CENTER": {
        "name": "울진군청소년수련관",
        "url": "https://www.uljin.go.kr/young/index.uljin",
        "address": "경상북도 울진군 울진읍 대나리항길 2",
    },
    "ULJIN_YOUTH_CULTURE_HOUSE": {
        "name": "울진군청소년문화의집",
        "url": "https://www.uljin.go.kr/young/index.uljin",
        "address": "경상북도 울진군 후포면 삼율로 194-13",
    },
}

ULJIN_CANDIDATE_AUDIT: Mapping[str, Mapping[str, Any]] = {
    ULJIN_CANONICAL_CANDIDATE_ID: {
        "provider": ULJIN_PROVIDER,
        "url": ULJIN_CANONICAL_URL,
        "url_sha256": ULJIN_CANONICAL_URL_SHA256,
        "decision": "reuse_incumbent_provider_and_retarget_to_complete_catalogue",
    },
    ULJIN_REVIEW_ROOT_CANDIDATE_ID: {
        "provider": ULJIN_PROVIDER,
        "url": "https://www.uljin.go.kr/",
        "decision": "discovery_alias_only_not_a_course_ledger",
    },
    ULJIN_LIBRARY_CANDIDATE_ID: {
        "provider": ULJIN_LIBRARY_PROVIDER,
        "url": "https://lib.uljin.go.kr/content/03culture/01_01.php",
        "decision": "preserve_as_separate_municipal_library_owner",
    },
}

ULJIN_OWNER_BOUNDARIES: tuple[Mapping[str, str], ...] = (
    {
        "provider": ULJIN_LIBRARY_PROVIDER,
        "url": "https://lib.uljin.go.kr/content/03culture/01_01.php",
        "owner": "울진군통합도서관 문화행사·강좌",
        "decision": "preserve_separate_municipal_library_owner",
    },
    {
        "provider": ULJIN_EDUCATION_OFFICE_PROVIDER,
        "url": "https://www.gbe.kr/uj/eq/view/selectEqList.do?mi=22841",
        "owner": "울진과학발명교육센터",
        "decision": "preserve_separate_education_office_owner",
    },
    {
        "provider": "NATIONAL_OCEAN_SCIENCE_MUSEUM",
        "url": "https://www.kosm.or.kr/kosm/bbs/board.do?bbsId=BSD0008&type=r",
        "owner": "국립해양과학관",
        "decision": "preserve_separate_national_institution_owner",
    },
    {
        "provider": ULJIN_PROVIDER,
        "url": "https://www.uljin.go.kr/learning/index.uljin",
        "owner": "울진군 평생학습관 안내·공지",
        "decision": "exclude_notice_alias_registration_returns_to_integrated_ledger",
    },
)

ULJIN_LIVE_AUDIT_BASELINE: Mapping[str, Any] = {
    "cutoff": "2026-07-23",
    "source_total": 21,
    "current_total": 21,
    "data_pages": 3,
    "category_counts": {
        "02": 2,
        "03": 14,
        "07": 0,
        "05": 5,
        "01": 0,
    },
    "source_status_counts": {"접수중": 2, "접수완료": 14, "교육중": 5},
    "branch_counts": {
        "울진군 평생학습관": 2,
        "울진군청소년수련관": 14,
        "울진과학체험관": 5,
    },
    "application_controls": 2,
    "source_requests": 15,
    "detail_requests": 0,
}

ULJIN_STATUS_MAP: Mapping[str, str] = {
    "접수예정": "SCHEDULED",
    "접수중": "OPEN",
    "대기자 접수중": "WAITLIST",
    "접수완료": "CLOSED",
    "교육중": "CLOSED",
    "교육종료": "CLOSED",
}
ULJIN_ACTIVE_SOURCE_STATUSES = frozenset({"접수중", "대기자 접수중"})
ULJIN_STATUS_CLASSES: Mapping[str, tuple[frozenset[str], ...]] = {
    "접수예정": (frozenset({"rec", "rec01"}), frozenset({"rec", "rec04"})),
    "접수중": (
        frozenset({"rec", "rec01"}),
        frozenset({"rec", "rec02"}),
        frozenset({"rec", "rec03"}),
    ),
    "대기자 접수중": (
        frozenset({"rec", "rec01"}),
        frozenset({"rec", "rec02"}),
        frozenset({"rec", "rec03"}),
    ),
    "접수완료": (frozenset({"rec", "rec03"}), frozenset({"rec", "rec04"})),
    "교육중": (frozenset({"rec", "rec04"}),),
    "교육종료": (frozenset({"rec", "rec04"}),),
}
ULJIN_LIST_LABELS = (
    "교육기간",
    "교육시간",
    "접수기간",
    "교육장",
    "수강료/재료비",
)

ULJIN_FIELDS_NEVER_PERSISTED = (
    "로그인·신청·예약확인 form payload",
    "신청자 이름·전화·주소·생년월일·비밀번호",
    "담당자·연락처·이메일",
    "강좌소개·공지·상세 자유본문",
    "첨부파일·이미지·원문 HTML",
)

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE = re.compile(r"\s+")
_IDENTITY = re.compile(r"^RE\d{7}$")
_DATE_RANGE = re.compile(
    r"^(20\d{2}-\d{2}-\d{2})\s*~\s*(20\d{2}-\d{2}-\d{2})$"
)
_CAPACITY = re.compile(r"^(\d[\d,]*)\s*/\s*(\d[\d,]*)$")
_RESULT_COUNT = re.compile(r"(\d[\d,]*)\s*건")
_PHONE = re.compile(r"(?<!\d)(?:0\d{1,2}[\s().-]*)?\d{3,4}[\s.-]+\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RESIDENT_ID = re.compile(r"(?<!\d)\d{6}\s*[- ]\s*[1-4]\d{6}(?!\d)")
_KOREAN_THOUSAND = re.compile(r"(\d+(?:\.\d+)?)\s*천\s*원")
_NUMBER_WON = re.compile(r"(\d[\d,]*)\s*원")

_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "source_category_code",
        "source_category_label",
        "source_page",
        "source_position",
        "source_status",
        "source_apply_period",
        "source_education_period",
        "source_schedule",
        "source_venue",
        "source_fee_material",
        "source_capacity_current",
        "source_capacity_total",
        "list_identity_verified",
        "facility_partition_verified",
        "application_control_present",
        "application_control_verified",
        "detail_endpoint_fetched",
        "login_endpoint_fetched",
        "application_endpoint_fetched",
        "application_form_submitted",
        "reservation_lookup_endpoint_fetched",
        "attachment_endpoint_fetched",
        "discarded_fields",
        "service_family",
    }
)
_FORBIDDEN_ROW_KEYS = frozenset(
    {
        "instructor",
        "instructor_name",
        "manager",
        "manager_name",
        "contact",
        "phone",
        "email",
        "attachments",
        "attachment_urls",
        "detail_description",
        "course_content",
        "source_html",
        "raw_html",
        "applicant_name",
        "applicant_phone",
        "applicant_birth_date",
        "applicant_address",
        "password",
    }
)


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _strict_target_url(url: str) -> bool:
    if url not in {ULJIN_LEGACY_TARGET_URL, ULJIN_CANONICAL_URL}:
        return False
    try:
        parsed = urlparse(url)
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        port = parsed.port
    except ValueError:
        return False
    expected_query = [] if url == ULJIN_LEGACY_TARGET_URL else [("menuCd", ULJIN_LIST_MENU)]
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == ULJIN_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == ULJIN_PATH
        and query == expected_query
        and not parsed.fragment
    )


def is_uljin_education_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")) == ULJIN_PROVIDER
        and _strict_target_url(_clean(_target_value(target, "url")))
    )


is_target = is_uljin_education_target


def _raw_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MooncenMunicipalCrawler/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        }
    )
    return session


def _request(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=False)


def _order_for(category_code: str) -> tuple[str, str]:
    if category_code == "02":
        return "RE_NAME", "asc"
    return "reSdate", "desc"


def _list_url(
    page: int = 1,
    *,
    category_code: str = "",
    date_mode: str = "3",
) -> str:
    order_field, order_sort = _order_for(category_code)
    return (
        f"https://{ULJIN_HOST}{ULJIN_PATH}?"
        + urlencode(
            (
                ("menuCd", ULJIN_LIST_MENU),
                ("searchCondition", "RE_NAME"),
                ("searchKeyword", ""),
                ("orderField", order_field),
                ("orderSort", order_sort),
                ("searchDateGubun", date_mode),
                ("gubun", category_code),
                ("startPage", str(page)),
            )
        )
    )


def _detail_url(identity: str) -> str:
    return (
        f"https://{ULJIN_HOST}{ULJIN_PATH}?"
        + urlencode(
            (("menuCd", ULJIN_APPLICATION_MENU), ("reUniqId", identity))
        )
    )


def _unique_query(url: str) -> tuple[Any, dict[str, str]]:
    try:
        parsed = urlparse(url)
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        _ = parsed.port
    except ValueError as exc:
        raise UljinContractError(f"malformed URL: {url}") from exc
    values: dict[str, str] = {}
    for key, value in pairs:
        if key in values:
            raise UljinContractError(f"duplicate query key {key}: {url}")
        values[key] = value
    return parsed, values


def _same_response_url(actual: str, expected: str) -> bool:
    try:
        left = urlparse(actual)
        right = urlparse(expected)
        return bool(
            left.scheme == right.scheme == "https"
            and (left.hostname or "").lower()
            == (right.hostname or "").lower()
            == ULJIN_HOST
            and left.port is None
            and right.port is None
            and left.username is None
            and left.password is None
            and left.path == right.path == ULJIN_PATH
            and parse_qsl(left.query, keep_blank_values=True, strict_parsing=True)
            == parse_qsl(right.query, keep_blank_values=True, strict_parsing=True)
            and not left.fragment
            and not right.fragment
        )
    except ValueError:
        return False


def _fetch_soup(
    session: Any,
    url: str,
    timeout: int,
    fetcher: Fetcher,
) -> tuple[BeautifulSoup, int]:
    last_error: Optional[Exception] = None
    for attempt in range(1, ULJIN_FETCH_ATTEMPTS + 1):
        try:
            response = fetcher(session, url, timeout)
            if getattr(response, "status_code", None) != 200:
                raise UljinContractError(
                    f"HTTP {getattr(response, 'status_code', None)} for {url}"
                )
            if getattr(response, "history", None):
                raise UljinContractError(f"redirect is not allowed for {url}")
            response_url = _clean(getattr(response, "url", ""))
            if not _same_response_url(response_url, url):
                raise UljinContractError(
                    f"response URL drift: expected {url}, received {response_url}"
                )
            content = getattr(response, "content", None)
            if content is None:
                content = str(getattr(response, "text", "")).encode("utf-8")
            if len(content) > ULJIN_MAX_HTML_BYTES:
                raise UljinContractError(f"HTML exceeds byte limit for {url}")
            # The official source emits literal angle-bracket text in some
            # course titles (for example ``<65세 이상 경제교육>``).  lxml's
            # recovery keeps the following sibling fields intact, whereas
            # html.parser can incorrectly nest the venue and fee under that
            # malformed title token.
            return BeautifulSoup(content, "lxml", from_encoding="utf-8"), attempt
        except Exception as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(ZoneInfo("Asia/Seoul")).date()
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise TypeError("today must be date, datetime, ISO date string, or None")


def _parse_count(node: Any, label: str) -> int:
    text = _clean(node.get_text(" ", strip=True))
    if label not in text:
        raise UljinContractError(f"result count label drift: expected {label}")
    match = _RESULT_COUNT.search(text)
    if match is None:
        raise UljinContractError(f"result count shape drift: {text}")
    return int(match.group(1).replace(",", ""))


def _category_registry(soup: BeautifulSoup, selected_code: str) -> None:
    nodes = soup.select("article.menu_tab > ul.col3 > li")
    expected = (("", "전체"),) + tuple(
        (category.code, category.label) for category in ULJIN_CATEGORIES
    )
    actual: list[tuple[str, str, bool]] = []
    for node in nodes:
        anchor = node.find("a", recursive=False)
        if anchor is None or _clean(anchor.get("href")) != "#":
            raise UljinContractError("facility registry link drift")
        onclick = _clean(anchor.get("onclick"))
        match = re.fullmatch(r"searchGuBun\('([0-9]*)'\);", onclick)
        if match is None:
            raise UljinContractError("facility registry onclick drift")
        actual.append(
            (
                match.group(1),
                _clean(anchor.get_text(" ", strip=True)),
                "on" in (node.get("class") or ()),
            )
        )
    if tuple((code, label) for code, label, _ in actual) != expected:
        raise UljinContractError("active facility registry vocabulary drift")
    selected = tuple(code for code, _, active in actual if active)
    if selected != (selected_code,):
        raise UljinContractError("active facility selection drift")


def _selected_option(select: Any) -> str:
    selected = select.select("option[selected]")
    if len(selected) != 1:
        raise UljinContractError("education-date ordering selection drift")
    return _clean(selected[0].get("value"))


def _validate_shell(
    soup: BeautifulSoup,
    *,
    requested_page: int,
    category_code: str,
    date_mode: str,
) -> tuple[int, int]:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    headings = soup.select("section.s_contents h3.s_tit_01")
    if title != "교육/강좌" or len(headings) != 1 or _clean(
        headings[0].get_text(" ", strip=True)
    ) != "교육/강좌":
        raise UljinContractError("education catalogue title/heading drift")
    _category_registry(soup, category_code)

    forms = soup.select('form[name="listForm"]')
    if len(forms) != 1:
        raise UljinContractError("expected one listForm")
    form = forms[0]
    if (
        _clean(form.get("method")).lower() != "get"
        or _clean(form.get("action")) != ULJIN_LINK_PATH
    ):
        raise UljinContractError("list form action drift")
    order_field, order_sort = _order_for(category_code)
    hidden = {
        _clean(node.get("name")): _clean(node.get("value"))
        for node in form.select('input[type="hidden"][name]')
    }
    if hidden != {
        "menuCd": ULJIN_LIST_MENU,
        "startPage": str(requested_page),
        "searchCondition": "RE_NAME",
        "orderField": order_field,
        "searchDateGubun": date_mode,
        "gubun": category_code,
    }:
        raise UljinContractError("hidden list filter drift")
    keyword = form.select_one('input[name="searchKeyword"]')
    if keyword is None or _clean(keyword.get("value")):
        raise UljinContractError("keyword filter drift")
    ordering = form.select_one('select[name="orderSort"]')
    if ordering is None:
        raise UljinContractError("missing education-date ordering")
    options = tuple(
        (_clean(option.get("value")), _clean(option.get_text(" ", strip=True)))
        for option in ordering.find_all("option", recursive=False)
    )
    if options != (("desc", "교육기간 빠른순"), ("asc", "교육기간 느린순")):
        raise UljinContractError("education-date ordering vocabulary drift")
    if _selected_option(ordering) != order_sort:
        raise UljinContractError("education-date ordering binding drift")
    status_nodes = form.select("ul.btn_condition > li")
    status_buttons = tuple(
        (
            _clean(node.get_text(" ", strip=True)),
            _clean(node.button.get("onclick") if node.button else ""),
            "on" in (node.get("class") or ()),
        )
        for node in status_nodes
    )
    expected_status = (
        ("전체", "searchDatefunc('3')", date_mode == "3"),
        ("모집중", "searchDatefunc('1')", date_mode == "1"),
    )
    if status_buttons != expected_status:
        raise UljinContractError("public status selector drift")

    summaries = soup.select("ul.search_result > li")
    if len(summaries) != 2:
        raise UljinContractError("result summary shape drift")
    return (
        _parse_count(summaries[0], "모집중"),
        _parse_count(summaries[1], "검색된 결과"),
    )


def _parse_pager_href(
    href: str,
    *,
    category_code: str,
    date_mode: str,
    allow_zero: bool = False,
) -> int:
    parsed, values = _unique_query(href)
    order_field, order_sort = _order_for(category_code)
    if (
        parsed.scheme not in {"", "https"}
        or (parsed.hostname or ULJIN_HOST).lower() != ULJIN_HOST
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != ULJIN_LINK_PATH
        or set(values)
        != {
            "menuCd",
            "searchCondition",
            "searchKeyword",
            "orderField",
            "orderSort",
            "searchDateGubun",
            "gubun",
            "startPage",
        }
        or values.get("menuCd") != ULJIN_LIST_MENU
        or values.get("searchCondition") != "RE_NAME"
        or values.get("searchKeyword") != ""
        or values.get("orderField") != order_field
        or values.get("orderSort") != order_sort
        or values.get("searchDateGubun") != date_mode
        or values.get("gubun") != category_code
        or not values.get("startPage", "").isdigit()
        or int(values["startPage"]) < (0 if allow_zero else 1)
        or parsed.fragment
    ):
        raise UljinContractError("pager URL drift")
    return int(values["startPage"])


def _parse_detail_href(
    href: str,
    *,
    requested_page: int,
    category_code: str,
    date_mode: str,
) -> tuple[str, str]:
    parsed, values = _unique_query(href)
    order_field, order_sort = _order_for(category_code)
    identity = values.get("reUniqId", "")
    if (
        parsed.scheme not in {"", "https"}
        or (parsed.hostname or ULJIN_HOST).lower() != ULJIN_HOST
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != ULJIN_LINK_PATH
        or set(values)
        != {
            "menuCd",
            "reUniqId",
            "searchCondition",
            "searchKeyword",
            "orderField",
            "orderSort",
            "searchDateGubun",
            "startPage",
            "gubun",
        }
        or values.get("menuCd") != ULJIN_APPLICATION_MENU
        or not _IDENTITY.fullmatch(identity)
        or values.get("searchCondition") != "RE_NAME"
        or values.get("searchKeyword") != ""
        or values.get("orderField") != order_field
        or values.get("orderSort") != order_sort
        or values.get("searchDateGubun") != date_mode
        or values.get("startPage") != str(requested_page)
        or values.get("gubun") != category_code
        or parsed.fragment
    ):
        raise UljinContractError("identity-bound application URL drift")
    return identity, _detail_url(identity)


def _parse_period(value: str, identity: str, label: str) -> tuple[date, date]:
    match = _DATE_RANGE.fullmatch(_clean(value))
    if match is None:
        raise UljinContractError(f"course {identity}: {label} period shape drift")
    start, end = (date.fromisoformat(token) for token in match.groups())
    if end < start:
        raise UljinContractError(f"course {identity}: reversed {label} period")
    return start, end


def _direct_text(node: Any) -> str:
    return _clean(" ".join(str(value) for value in node.find_all(string=True, recursive=False)))


def _list_fields(node: Any, identity: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    labels: list[str] = []
    for dd in node.select("dl > dd"):
        strong = dd.find("strong", recursive=False)
        if strong is None:
            raise UljinContractError(f"course {identity}: malformed list field")
        label = _clean(strong.get_text(" ", strip=True))
        if label in fields:
            raise UljinContractError(f"course {identity}: repeated list field")
        labels.append(label)
        fields[label] = _clean(
            " ".join(
                str(item)
                for item in dd.find_all(string=True)
                if item.parent is not strong
            )
        )
    if tuple(labels) != ULJIN_LIST_LABELS:
        raise UljinContractError(f"course {identity}: list field vocabulary drift")
    return fields


def _parse_row(
    node: Any,
    *,
    requested_page: int,
    position: int,
    category_code: str,
    date_mode: str,
) -> dict[str, Any]:
    links = node.select('dl > dt > a[href*="reUniqId"]')
    if len(links) != 1:
        raise UljinContractError("course row must have one identity link")
    identity, detail_url = _parse_detail_href(
        _clean(links[0].get("href")),
        requested_page=requested_page,
        category_code=category_code,
        date_mode=date_mode,
    )
    title = _clean(links[0].get_text(" ", strip=True))
    if not title:
        raise UljinContractError(f"course {identity}: empty title")
    if _PHONE.search(title) or _EMAIL.search(title) or _RESIDENT_ID.search(title):
        raise UljinContractError(f"course {identity}: title contains PII-like text")
    fields = _list_fields(node, identity)
    event_start, event_end = _parse_period(fields["교육기간"], identity, "education")
    apply_start, apply_end = _parse_period(fields["접수기간"], identity, "application")
    schedule = fields["교육시간"]
    venue = fields["교육장"]
    if not schedule or not venue:
        raise UljinContractError(f"course {identity}: missing schedule or venue")

    # Lifelong-learning rows can additionally expose a ``rec00`` link to a
    # public course-introduction board.  It is free-form and intentionally not
    # followed; only the non-rec00 reservation state is authoritative here.
    statuses = [
        status
        for status in node.select("div.r_btn > p.rec")
        if "rec00" not in (status.get("class") or ())
    ]
    if len(statuses) != 1:
        raise UljinContractError(f"course {identity}: status count drift")
    status_node = statuses[0]
    raw_status = _direct_text(status_node)
    if raw_status not in ULJIN_STATUS_MAP:
        raise UljinContractError(f"course {identity}: unknown status {raw_status}")
    if frozenset(status_node.get("class") or ()) not in ULJIN_STATUS_CLASSES[raw_status]:
        raise UljinContractError(f"course {identity}: status class drift")
    capacity_node = status_node.find("span")
    capacity_text = _clean(capacity_node.get_text(" ", strip=True) if capacity_node else "")
    capacity_match = _CAPACITY.fullmatch(capacity_text)
    if capacity_match is None:
        raise UljinContractError(f"course {identity}: capacity shape drift")
    capacity_current, capacity_total = (
        int(token.replace(",", "")) for token in capacity_match.groups()
    )

    controls = node.select("div.r_btn > a.possible")
    if len(controls) != 1:
        raise UljinContractError(f"course {identity}: control count drift")
    control = controls[0]
    active = raw_status in ULJIN_ACTIVE_SOURCE_STATUSES
    if active:
        if (
            frozenset(control.get("class") or ())
            != frozenset({"possible", "possible01", "blink"})
            or _clean(control.get_text(" ", strip=True)) != "교육신청"
            or not _clean(control.get("href"))
            or _clean(control.get("onclick"))
        ):
            raise UljinContractError(f"course {identity}: active control drift")
        control_identity, _ = _parse_detail_href(
            _clean(control.get("href")),
            requested_page=requested_page,
            category_code=category_code,
            date_mode=date_mode,
        )
        if control_identity != identity:
            raise UljinContractError(f"course {identity}: active control identity drift")
    elif (
        frozenset(control.get("class") or ())
        != frozenset({"possible", "possible02"})
        or _clean(control.get_text(" ", strip=True)) != "접수마감"
        or _clean(control.get("href"))
        or _clean(control.get("onclick"))
    ):
        raise UljinContractError(f"course {identity}: inactive control drift")

    return {
        "identity": identity,
        "detail_url": detail_url,
        "page": requested_page,
        "position": position,
        "title": title,
        "event_start": event_start,
        "event_end": event_end,
        "apply_start": apply_start,
        "apply_end": apply_end,
        "schedule": schedule,
        "venue": venue,
        "fee_material": fields["수강료/재료비"],
        "raw_status": raw_status,
        "status": ULJIN_STATUS_MAP[raw_status],
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "application_control": active,
    }


def _parse_page(
    soup: BeautifulSoup,
    *,
    requested_page: int,
    category_code: str,
    date_mode: str,
) -> dict[str, Any]:
    global_open_count, advertised_total = _validate_shell(
        soup,
        requested_page=requested_page,
        category_code=category_code,
        date_mode=date_mode,
    )
    pager = soup.select_one("div.bbs_page")
    if pager is None:
        raise UljinContractError("missing catalogue pager")
    page_links = [
        _parse_pager_href(
            _clean(anchor.get("href")),
            category_code=category_code,
            date_mode=date_mode,
            allow_zero=advertised_total == 0,
        )
        for anchor in pager.select("a[href]")
    ]
    if not page_links:
        raise UljinContractError("catalogue pager has no links")
    if advertised_total == 0 and set(page_links) - {0, 1}:
        raise UljinContractError("empty catalogue pager boundary drift")
    advertised_last = max(page_links)
    calculated_last = max(1, (advertised_total + ULJIN_PAGE_SIZE - 1) // ULJIN_PAGE_SIZE)
    if advertised_last != calculated_last:
        raise UljinContractError("advertised total/last-page disagreement")

    current_nodes = pager.select("span.on")
    if len(current_nodes) > 1:
        raise UljinContractError("multiple current-page markers")
    current_page: Optional[int] = None
    if current_nodes:
        current_text = _clean(current_nodes[0].get_text(" ", strip=True))
        if not current_text.isdigit():
            raise UljinContractError("current-page marker drift")
        current_page = int(current_text)

    items = soup.select("div.bbs_list01.type2 > ul > li")
    if advertised_total == 0:
        if (
            len(items) != 1
            or _clean(items[0].get_text(" ", strip=True)) != "검색된 자료가 없습니다."
            or items[0].select_one("a, dd, p, .r_btn") is not None
        ):
            raise UljinContractError("empty catalogue sentinel drift")
        rows: list[dict[str, Any]] = []
    else:
        if not items or len(items) > ULJIN_PAGE_SIZE:
            raise UljinContractError("catalogue page row-count drift")
        if any(_clean(item.get_text(" ", strip=True)) == "검색된 자료가 없습니다." for item in items):
            raise UljinContractError("non-empty catalogue contains empty sentinel")
        rows = [
            _parse_row(
                item,
                requested_page=requested_page,
                position=position,
                category_code=category_code,
                date_mode=date_mode,
            )
            for position, item in enumerate(items, 1)
        ]
    return {
        "requested_page": requested_page,
        "current_page": current_page,
        "category_code": category_code,
        "date_mode": date_mode,
        "global_open_count": global_open_count,
        "advertised_total": advertised_total,
        "advertised_last": advertised_last,
        "rows": rows,
    }


def _row_signature(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        row["identity"],
        row["title"],
        row["event_start"],
        row["event_end"],
        row["apply_start"],
        row["apply_end"],
        row["schedule"],
        row["venue"],
        row["fee_material"],
        row["raw_status"],
        row["status"],
        row["capacity_current"],
        row["capacity_total"],
        row["application_control"],
    )


def _page_data_signature(page: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        page["global_open_count"],
        page["advertised_total"],
        page["advertised_last"],
        tuple(_row_signature(row) for row in page["rows"]),
    )


def _collect_filter_pages(
    fetch_page: Callable[[int, str, str], dict[str, Any]],
    *,
    category_code: str,
    date_mode: str,
    max_pages: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    first = fetch_page(1, category_code, date_mode)
    last = int(first["advertised_last"])
    if last > max_pages:
        raise UljinContractError(
            f"source cap: advertised last page {last} exceeds max_pages {max_pages}"
        )
    pages = [first]
    for page_number in range(2, last + 1):
        pages.append(fetch_page(page_number, category_code, date_mode))
    total = int(first["advertised_total"])
    global_open = int(first["global_open_count"])
    for index, parsed in enumerate(pages, 1):
        if (
            parsed["requested_page"] != index
            or parsed["current_page"] != index
            or parsed["advertised_total"] != total
            or parsed["advertised_last"] != last
            or parsed["global_open_count"] != global_open
        ):
            raise UljinContractError("catalogue pagination metadata drift")
        expected_size = (
            ULJIN_PAGE_SIZE
            if total and index < last
            else total - ULJIN_PAGE_SIZE * (last - 1)
        )
        if total == 0:
            expected_size = 0
        if len(parsed["rows"]) != expected_size:
            raise UljinContractError("catalogue page-size boundary drift")
    rows = [row for parsed in pages for row in parsed["rows"]]
    if len(rows) != total:
        raise UljinContractError("advertised total does not match collected rows")
    identities = [str(row["identity"]) for row in rows]
    if len(identities) != len(set(identities)):
        raise UljinContractError("duplicate course identity within filter")
    return rows, pages


def _youth_branch(title: str, venue: str) -> tuple[str, str, str, str]:
    compact = _clean(venue).replace(" ", "")
    if "청소년문화의집" in compact or compact.endswith("문화의집"):
        key = "ULJIN_YOUTH_CULTURE_HOUSE"
        if "[수련관]" in title:
            raise UljinContractError("youth title/venue branch disagreement")
    elif "청소년수련관" in compact:
        key = "ULJIN_YOUTH_TRAINING_CENTER"
        if "[문화의집]" in title:
            raise UljinContractError("youth title/venue branch disagreement")
    else:
        raise UljinContractError(f"unknown youth facility venue: {venue}")
    branch = ULJIN_YOUTH_BRANCHES[key]
    return branch["name"], key, branch["url"], branch["address"]


def _branch_for(row: Mapping[str, Any], category: UljinCategory) -> tuple[str, str, str, str]:
    if category.code == "03":
        return _youth_branch(str(row["title"]), str(row["venue"]))
    return category.branch, category.branch_code, category.branch_url, category.address


def _fee_parts(value: str) -> tuple[str, str]:
    cleaned = _clean(value)
    if "/" not in cleaned:
        return cleaned, ""
    fee, material = cleaned.split("/", 1)
    return _clean(fee), _clean(material)


def _fee_amount(value: str) -> Optional[int]:
    cleaned = _clean(value)
    if not cleaned:
        return None
    if cleaned in {"없음", "무료", "0", "0원"}:
        return 0
    thousand = _KOREAN_THOUSAND.search(cleaned)
    if thousand:
        return int(float(thousand.group(1)) * 1000)
    won = _NUMBER_WON.search(cleaned)
    return int(won.group(1).replace(",", "")) if won else None


def _status_semantics(row: Mapping[str, Any], cutoff: date) -> None:
    raw_status = str(row["raw_status"])
    event_start = row["event_start"]
    event_end = row["event_end"]
    apply_start = row["apply_start"]
    apply_end = row["apply_end"]
    identity = row["identity"]
    if raw_status in ULJIN_ACTIVE_SOURCE_STATUSES and not apply_start <= cutoff <= apply_end:
        raise UljinContractError(f"course {identity}: active status/date disagreement")
    if raw_status == "접수예정" and cutoff >= apply_start:
        raise UljinContractError(f"course {identity}: scheduled status/date disagreement")
    if raw_status == "교육중" and not event_start <= cutoff <= event_end:
        raise UljinContractError(f"course {identity}: education status/date disagreement")
    if raw_status == "교육종료" and event_end >= cutoff:
        raise UljinContractError(f"course {identity}: ended status/date disagreement")


def _output_row(row: Mapping[str, Any], cutoff: date) -> dict[str, Any]:
    _status_semantics(row, cutoff)
    category = ULJIN_CATEGORY_BY_CODE[str(row["category_code"])]
    branch, branch_code, branch_url, address = _branch_for(row, category)
    fee, material_fee = _fee_parts(str(row["fee_material"]))
    event_start: date = row["event_start"]
    event_end: date = row["event_end"]
    apply_start: date = row["apply_start"]
    apply_end: date = row["apply_end"]
    identity = str(row["identity"])
    event_period = f"{event_start.isoformat()} ~ {event_end.isoformat()}"
    apply_period = f"{apply_start.isoformat()} ~ {apply_end.isoformat()}"
    active = bool(row["application_control"])
    detail_url = str(row["detail_url"])
    capacity_current = int(row["capacity_current"])
    capacity_total = int(row["capacity_total"])
    return {
        "provider": ULJIN_PROVIDER,
        "provider_course_id": f"{ULJIN_PROVIDER}:re:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": str(row["title"]),
        "description": str(row["title"]),
        "branch": branch,
        "branch_code": branch_code,
        "branch_url": branch_url,
        "preserve_branch": True,
        "category": "교육/강좌",
        "program_type": "교육",
        "raw_url": detail_url,
        "application_url": detail_url if active else "",
        "application_type": "ONLINE_RESERVATION" if active else "INFO_ONLY",
        "application_method": "온라인",
        "application_methods": ["온라인"],
        "reservation_available": active,
        "status": str(row["status"]),
        "raw_status": str(row["raw_status"]),
        "fee": fee,
        "fee_amount": _fee_amount(fee),
        "material_fee": material_fee,
        "material_fee_amount": _fee_amount(material_fee),
        "period": event_period,
        "start_date": event_start.isoformat(),
        "end_date": event_end.isoformat(),
        "apply_period": apply_period,
        "apply_start_date": apply_start.isoformat(),
        "apply_end_date": apply_end.isoformat(),
        "schedule_raw": str(row["schedule"]),
        "capacity": f"{capacity_total}명",
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "capacity_remaining": max(capacity_total - capacity_current, 0),
        "target": "",
        "venue": branch,
        "venue_name": branch,
        "room": str(row["venue"]),
        "facility_name": branch,
        "address": address,
        "venue_address": address,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": ULJIN_PARSER,
        "municipality_code": ULJIN_MUNICIPALITY_CODE,
        "municipality_full_name": ULJIN_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "source_category_code": category.code,
            "source_category_label": category.label,
            "source_page": int(row["page"]),
            "source_position": int(row["position"]),
            "source_status": str(row["raw_status"]),
            "source_apply_period": apply_period,
            "source_education_period": event_period,
            "source_schedule": str(row["schedule"]),
            "source_venue": str(row["venue"]),
            "source_fee_material": str(row["fee_material"]),
            "source_capacity_current": capacity_current,
            "source_capacity_total": capacity_total,
            "list_identity_verified": True,
            "facility_partition_verified": True,
            "application_control_present": active,
            "application_control_verified": True,
            "detail_endpoint_fetched": False,
            "login_endpoint_fetched": False,
            "application_endpoint_fetched": False,
            "application_form_submitted": False,
            "reservation_lookup_endpoint_fetched": False,
            "attachment_endpoint_fetched": False,
            "discarded_fields": list(ULJIN_FIELDS_NEVER_PERSISTED),
            "service_family": "education",
        },
    }


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_ROW_KEYS:
        errors.append("forbidden PII/free-text key")
    raw_fields = row.get("raw_fields")
    if not isinstance(raw_fields, Mapping) or not set(raw_fields) <= _SAFE_RAW_FIELDS:
        errors.append("raw_fields allowlist exceeded")
    payload = repr(
        {
            key: value
            for key, value in row.items()
            if key not in {"raw_url", "application_url", "branch_url", "raw_fields"}
        }
    )
    if _PHONE.search(payload) or _EMAIL.search(payload) or _RESIDENT_ID.search(payload):
        errors.append("PII-like value escaped the structured allowlist")
    return errors


def _initial_meta() -> dict[str, Any]:
    return {
        "provider": ULJIN_PROVIDER,
        "provider_decision": (
            "keep existing provider and replace the partial landing-page parser "
            "with the exact complete education catalogue"
        ),
        "incumbent_parser": "generic_card+generic_table",
        "canonical_url": ULJIN_CANONICAL_URL,
        "canonical_url_sha256": ULJIN_CANONICAL_URL_SHA256,
        "canonical_candidate_id": ULJIN_CANONICAL_CANDIDATE_ID,
        "review_root_candidate_id": ULJIN_REVIEW_ROOT_CANDIDATE_ID,
        "parser": ULJIN_PARSER,
        "ownership_scope": ULJIN_OWNERSHIP_SCOPE,
        "municipality_code": ULJIN_MUNICIPALITY_CODE,
        "municipality_full_name": ULJIN_MUNICIPALITY_NAME,
        "page_size": ULJIN_PAGE_SIZE,
        "recommended_max_pages": ULJIN_RECOMMENDED_MAX_PAGES,
        "recommended_detail_limit": ULJIN_RECOMMENDED_DETAIL_LIMIT,
        "pagination_boundary_mode": "advertised_last_plus_exact_last_page_clamp",
        "detail_access_policy": (
            "identity-bound authentication-gated application route; never fetched"
        ),
        "source_requests": 0,
        "list_requests": 0,
        "request_attempts": 0,
        "detail_requests": 0,
        "detail_pages": 0,
        "login_endpoint_requests": 0,
        "application_endpoint_requests": 0,
        "reservation_lookup_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "application_form_submissions": 0,
        "data_pages": 0,
        "category_filter_requests": 0,
        "open_filter_requests": 0,
        "full_recheck_requests": 0,
        "advertised_total": 0,
        "current_source_count": 0,
        "expired_source_count": 0,
        "row_count": 0,
        "application_control_count": 0,
        "category_partition_overlap_count": 0,
        "category_partition_union_count": 0,
        "empty_category_filter_count": 0,
        "pagination_complete": False,
        "category_partition_complete": False,
        "open_filter_complete": False,
        "overflow_clamp_verified": False,
        "stable_full_recheck": False,
        "details_complete": False,
        "privacy_boundary_complete": False,
        "semantic_quality_passed": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "configured_collection_error": "",
    }


def collect_uljin_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = ULJIN_RECOMMENDED_MAX_PAGES,
    detail_limit: int = ULJIN_RECOMMENDED_DETAIL_LIMIT,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete, stable, privacy-safe Uljin education snapshot."""

    meta = _initial_meta()
    if not is_uljin_education_target(target):
        meta["configured_collection_error"] = "target does not match exact retained Uljin owner"
        return [], ULJIN_PARSER, meta
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta["configured_collection_error"] = "managed session_factory injection is required"
            return [], ULJIN_PARSER, meta
        session_factory = _raw_session
    try:
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
            raise ValueError("timeout must be a positive integer")
        if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages < 1:
            raise ValueError("max_pages must be a positive integer")
        if (
            isinstance(detail_limit, bool)
            or not isinstance(detail_limit, int)
            or detail_limit < 0
        ):
            raise ValueError("detail_limit must be a non-negative integer")
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], ULJIN_PARSER, meta

    current_fetcher = fetcher or _request
    session = session_factory()
    category_request_start = 0
    open_request_start = 0
    recheck_request_start = 0

    def fetch_page(page: int, category_code: str, date_mode: str) -> dict[str, Any]:
        url = _list_url(page, category_code=category_code, date_mode=date_mode)
        soup, attempts = _fetch_soup(session, url, timeout, current_fetcher)
        meta["source_requests"] += 1
        meta["list_requests"] += 1
        meta["request_attempts"] += attempts
        return _parse_page(
            soup,
            requested_page=page,
            category_code=category_code,
            date_mode=date_mode,
        )

    try:
        listed, initial_pages = _collect_filter_pages(
            fetch_page,
            category_code="",
            date_mode="3",
            max_pages=max_pages,
        )
        initial_last = int(initial_pages[0]["advertised_last"])
        overflow = fetch_page(initial_last + 1, "", "3")
        if overflow["current_page"] is not None or _page_data_signature(overflow) != _page_data_signature(
            initial_pages[-1]
        ):
            raise UljinContractError("post-last page did not clamp exactly to last page")

        source_by_identity = {str(row["identity"]): row for row in listed}
        if len(source_by_identity) != len(listed):
            raise UljinContractError("duplicate identity in unfiltered ledger")
        global_open_count = int(initial_pages[0]["global_open_count"])

        category_request_start = int(meta["source_requests"])
        category_rows: dict[str, list[dict[str, Any]]] = {}
        category_pages: dict[str, int] = {}
        membership: dict[str, str] = {}
        overlap_count = 0
        for category in ULJIN_CATEGORIES:
            filtered, pages = _collect_filter_pages(
                fetch_page,
                category_code=category.code,
                date_mode="3",
                max_pages=max_pages,
            )
            category_rows[category.code] = filtered
            category_pages[category.code] = len(pages)
            if int(pages[0]["global_open_count"]) != global_open_count:
                raise UljinContractError("global open count changed across facility filters")
            for filtered_row in filtered:
                identity = str(filtered_row["identity"])
                if identity in membership:
                    overlap_count += 1
                    raise UljinContractError(
                        f"facility partitions overlap at course {identity}"
                    )
                source_row = source_by_identity.get(identity)
                if source_row is None:
                    raise UljinContractError(
                        f"facility partition escapes source ledger at course {identity}"
                    )
                if _row_signature(filtered_row) != _row_signature(source_row):
                    raise UljinContractError(
                        f"facility partition data drift at course {identity}"
                    )
                membership[identity] = category.code
        if set(membership) != set(source_by_identity):
            missing = sorted(set(source_by_identity) - set(membership))
            raise UljinContractError(
                "facility partition union is incomplete: " + ", ".join(missing[:5])
            )
        for row in listed:
            row["category_code"] = membership[str(row["identity"])]

        open_request_start = int(meta["source_requests"])
        open_rows, open_pages = _collect_filter_pages(
            fetch_page,
            category_code="",
            date_mode="1",
            max_pages=max_pages,
        )
        expected_open = {
            str(row["identity"])
            for row in listed
            if str(row["raw_status"]) in ULJIN_ACTIVE_SOURCE_STATUSES
            and bool(row["application_control"])
        }
        actual_open = {str(row["identity"]) for row in open_rows}
        if actual_open != expected_open or int(open_pages[0]["advertised_total"]) != len(
            expected_open
        ):
            raise UljinContractError("public open filter/control union drift")
        if global_open_count != len(expected_open):
            raise UljinContractError("global open summary/control count drift")
        for open_row in open_rows:
            source_row = source_by_identity[str(open_row["identity"])]
            if _row_signature(open_row) != _row_signature(source_row):
                raise UljinContractError(
                    f"public open filter data drift at course {open_row['identity']}"
                )

        recheck_request_start = int(meta["source_requests"])
        rechecked, recheck_pages = _collect_filter_pages(
            fetch_page,
            category_code="",
            date_mode="3",
            max_pages=max_pages,
        )
        recheck_overflow = fetch_page(int(recheck_pages[0]["advertised_last"]) + 1, "", "3")
        if (
            len(recheck_pages) != len(initial_pages)
            or any(
                _page_data_signature(left) != _page_data_signature(right)
                for left, right in zip(initial_pages, recheck_pages)
            )
            or _page_data_signature(recheck_overflow) != _page_data_signature(overflow)
            or recheck_overflow["current_page"] is not None
            or tuple(_row_signature(row) for row in rechecked)
            != tuple(_row_signature(row) for row in listed)
        ):
            raise UljinContractError("full ledger changed during stable recheck")

        current_listed: list[dict[str, Any]] = []
        expired_count = 0
        for row in listed:
            _status_semantics(row, cutoff)
            if row["event_end"] < cutoff:
                expired_count += 1
            else:
                current_listed.append(row)
        rows = [_output_row(row, cutoff) for row in current_listed]
        privacy_failures = [
            error
            for row in rows
            for error in _privacy_errors(row)
        ]
        if privacy_failures:
            raise UljinContractError("; ".join(sorted(set(privacy_failures))))

        before_ids = {str(row["provider_course_id"]) for row in rows}
        if dedupe_rows is not None:
            rows = [dict(row) for row in dedupe_rows(rows)]
        after_ids = [str(row.get("provider_course_id", "")) for row in rows]
        if len(after_ids) != len(set(after_ids)) or set(after_ids) != before_ids:
            raise UljinContractError("dedupe_rows changed complete identity cardinality")
        privacy_failures = [
            error
            for row in rows
            for error in _privacy_errors(row)
        ]
        if privacy_failures:
            raise UljinContractError("; ".join(sorted(set(privacy_failures))))

        category_request_count = open_request_start - category_request_start
        open_request_count = recheck_request_start - open_request_start
        full_recheck_count = int(meta["source_requests"]) - recheck_request_start
        current_categories = Counter(
            str(row["raw_fields"]["source_category_label"]) for row in rows
        )
        current_branches = Counter(str(row["branch"]) for row in rows)
        meta.update(
            {
                "cutoff": cutoff.isoformat(),
                "data_pages": len(initial_pages),
                "advertised_last_page": initial_last,
                "advertised_total": len(listed),
                "source_total_count": len(listed),
                "current_source_count": len(current_listed),
                "expired_source_count": expired_count,
                "row_count": len(rows),
                "identity_min": min(source_by_identity) if source_by_identity else "",
                "identity_max": max(source_by_identity) if source_by_identity else "",
                "category_filter_requests": category_request_count,
                "open_filter_requests": open_request_count,
                "full_recheck_requests": full_recheck_count,
                "category_partition_counts": {
                    code: len(category_rows[code]) for code in ULJIN_CATEGORY_BY_CODE
                },
                "category_partition_pages": category_pages,
                "category_partition_union_count": len(membership),
                "category_partition_overlap_count": overlap_count,
                "empty_category_filter_count": sum(
                    not category_rows[code] for code in ULJIN_CATEGORY_BY_CODE
                ),
                "open_filter_source_count": len(open_rows),
                "source_status_counts": dict(
                    Counter(str(row["raw_status"]) for row in listed)
                ),
                "current_raw_status_counts": dict(
                    Counter(str(row["raw_status"]) for row in current_listed)
                ),
                "status_counts": dict(Counter(str(row["status"]) for row in rows)),
                "source_category_counts": {
                    ULJIN_CATEGORY_BY_CODE[code].label: len(category_rows[code])
                    for code in ULJIN_CATEGORY_BY_CODE
                },
                "current_category_counts": dict(current_categories),
                "branch_counts": dict(current_branches),
                "application_control_count": sum(
                    bool(row["application_control"]) for row in current_listed
                ),
                "identity_bound_application_links": len(listed),
                "pagination_complete": True,
                "category_partition_complete": True,
                "open_filter_complete": True,
                "overflow_clamp_verified": True,
                "stable_full_recheck": True,
                "stable_full_recheck_after_filters": True,
                "details_complete": True,
                "privacy_boundary_complete": True,
                "semantic_quality_passed": True,
                "snapshot_complete": True,
                "no_current_data": not rows,
                "configured_collection_error": "",
            }
        )
        return rows, ULJIN_PARSER, meta
    except Exception as exc:
        if "source cap:" in _clean(exc):
            meta["source_cap_reached"] = True
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        meta["snapshot_complete"] = False
        meta["semantic_quality_passed"] = False
        return [], ULJIN_PARSER, meta
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


collect = collect_uljin_education


__all__ = [
    "ULJIN_APPLICATION_MENU",
    "ULJIN_CANONICAL_CANDIDATE_ID",
    "ULJIN_CANONICAL_URL",
    "ULJIN_CANONICAL_URL_SHA256",
    "ULJIN_CANDIDATE_AUDIT",
    "ULJIN_CATEGORIES",
    "ULJIN_EDUCATION_OFFICE_PROVIDER",
    "ULJIN_FIELDS_NEVER_PERSISTED",
    "ULJIN_HOST",
    "ULJIN_LEGACY_TARGET_URL",
    "ULJIN_LIBRARY_CANDIDATE_ID",
    "ULJIN_LIBRARY_PROVIDER",
    "ULJIN_LIST_MENU",
    "ULJIN_LIVE_AUDIT_BASELINE",
    "ULJIN_MUNICIPALITY_CODE",
    "ULJIN_MUNICIPALITY_NAME",
    "ULJIN_OWNER_BOUNDARIES",
    "ULJIN_OWNERSHIP_SCOPE",
    "ULJIN_PARSER",
    "ULJIN_PATH",
    "ULJIN_PROVIDER",
    "ULJIN_RECOMMENDED_DETAIL_LIMIT",
    "ULJIN_RECOMMENDED_MAX_PAGES",
    "ULJIN_REVIEW_ROOT_CANDIDATE_ID",
    "ULJIN_YOUTH_BRANCHES",
    "UljinContractError",
    "collect",
    "collect_uljin_education",
    "is_target",
    "is_uljin_education_target",
]
