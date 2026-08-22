"""Fail-closed collector for Chilgok-gun's official education ledger.

The promotion review currently points at a third-party Tistory homepage
guide.  That page owns no course identities and must not execute.  Chilgok's
actual municipal owner is the unfiltered ``/reservation/edu/courseList.do``
catalogue on the county's official ``go.kr`` host.

The ledger is a native POST form with fixed 20-row pages.  It advertises a
total and a last page, then returns an empty live ``tbody`` on the immediate
next page (old example rows survive only inside an HTML comment).  This
collector verifies every advertised page and that sentinel, checks the
descending sequence and unique ``idx`` set, fetches every current/future
detail, and rechecks every list page containing a current row plus the first,
last, and sentinel pages.

Application is deliberately represented by the public detail URL.  The
identity-bound ``fn_apply(idx, 'receiptIng')`` control is verified but the
PII-bearing ``courseApply.do`` form is never requested.  Instructor names,
contacts, attachments, free-form descriptions, notices, and source HTML are
not retained.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import hashlib
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


CHILGOK_PROVIDER = "MUNI_WWW_CHILGOK_GO_KR_B19807DD"
CHILGOK_CANONICAL_CANDIDATE_ID = "MUNI_IR_85F08E80ABFF"
CHILGOK_REJECTED_CANDIDATE_ID = "MUNI_IR_59ADEB392567"
CHILGOK_REJECTED_PROVIDER = "MUNI_DONGRYO_TISTORY_COM_B8888C93"
CHILGOK_MUNICIPALITY_CODE = "4785000000"
CHILGOK_MUNICIPALITY_NAME = "경상북도 칠곡군"

CHILGOK_HOST = "www.chilgok.go.kr"
CHILGOK_LIST_PATH = "/reservation/edu/courseList.do"
CHILGOK_DETAIL_PATH = "/reservation/edu/courseView.do"
CHILGOK_APPLICATION_PATH = "/reservation/edu/courseApply.do"
CHILGOK_CANONICAL_URL = (
    f"https://{CHILGOK_HOST}{CHILGOK_LIST_PATH}?mId="
)
CHILGOK_DETAIL_POST_URL = (
    f"https://{CHILGOK_HOST}{CHILGOK_DETAIL_PATH}?mId="
)
CHILGOK_APPLICATION_POST_URL = (
    f"https://{CHILGOK_HOST}{CHILGOK_APPLICATION_PATH}?mId="
)
CHILGOK_OFFICIAL_PORTAL_URL = "https://www.chilgok.go.kr/reservation/main.do"
CHILGOK_PAGE_SIZE = 20
CHILGOK_RECOMMENDED_MAX_PAGES = 50
CHILGOK_RECOMMENDED_DETAIL_LIMIT = 500
CHILGOK_MAX_HTML_BYTES = 2_000_000
CHILGOK_PARSER = (
    "chilgok_official_unfiltered_education_post_pages+advertised_total+"
    "exact_comment_only_empty_sentinel+current_page_boundary_rechecks+"
    "all_current_future_details+hidden_idx_and_application_control_binding+"
    "official_agency_branches+no_application_form_fetch+pii_allowlist"
)

# This directory is emitted by the official unfiltered list form.  Treating a
# new agency as an ownership expansion requires another audit, so drift fails
# closed instead of silently broadening the provider.
CHILGOK_OFFICIAL_AGENCIES: Mapping[str, str] = {
    "172": "석적읍사무소",
    "170": "칠곡군보건소",
    "168": "동명면사무소",
    "167": "칠곡호국평화기념관",
    "165": "칠곡향사아트센터",
    "164": "칠곡공예테마공원",
    "160": "청소년문화의집",
    "150": "교육문화회관(평생교육)",
    "149": "교육문화회관(사회교육)",
    "148": "청소년수련관",
    "147": "교육문화회관(학점은행제)",
}

CHILGOK_CANDIDATE_AUDIT: Mapping[str, Mapping[str, Any]] = {
    CHILGOK_CANONICAL_CANDIDATE_ID: {
        "decision": "canonical_complete_owner",
        "provider": CHILGOK_PROVIDER,
        "url": CHILGOK_CANONICAL_URL,
        "owner": CHILGOK_PROVIDER,
        "reason": "official_go_kr_unfiltered_education_identity_ledger",
    },
    CHILGOK_REJECTED_CANDIDATE_ID: {
        "decision": "excluded_unofficial_third_party_homepage_guide",
        "provider": CHILGOK_REJECTED_PROVIDER,
        "url": "https://dongryo.tistory.com/5058",
        "owner": "",
        "reason": "unofficial_third_party_homepage_guide_not_course_catalogue",
    },
    "MUNI_IR_03B54318897D": {
        "decision": "duplicate_cross_host_presentation_alias",
        "provider": "MUNI_WWW_CHILGOKCTF_OR_KR_687648BA",
        "url": (
            "https://www.chilgokctf.or.kr/reservation/edu/courseList.do?"
            "mode=courseList&mId=0101010000"
        ),
        "owner": CHILGOK_PROVIDER,
        "reason": "same_288_idx_set_on_all_15_pages_and_same_page16_sentinel",
    },
    "MUNI_IR_20B3AB380CAF": {
        "decision": "separate_foundation_owner_subset",
        "provider": "MUNI_WWW_CHILGOKCTF_OR_KR_6366B6E9",
        "url": (
            "https://www.chilgokctf.or.kr/ctf/edu/courseList.do?"
            "searchAgency=2&mId=1005010000"
        ),
        "owner": "",
        "reason": "foundation_craft_theme_park_subset_not_county_education_idx_space",
    },
    "MUNI_IR_B41F1438BC1E": {
        "decision": "separate_foundation_owner_subset",
        "provider": "MUNI_WWW_CHILGOKCTF_OR_KR_26D8FE50",
        "url": (
            "https://www.chilgokctf.or.kr/ctf/edu/courseList.do?"
            "searchAgency=5&mId=1007010000"
        ),
        "owner": "",
        "reason": "foundation_culture_city_subset_not_county_education_idx_space",
    },
}

CHILGOK_NON_EXECUTING_ALIASES: tuple[Mapping[str, Any], ...] = (
    {
        "url": "https://www.chilgok.go.kr/reservation/",
        "reason": "navigation_home_without_course_rows",
        "owner": CHILGOK_PROVIDER,
    },
    {
        "url": (
            "https://www.chilgok.go.kr/reservation/edu/courseList.do?"
            "mode=courseList&mId=0101010000"
        ),
        "reason": "menu_context_alias_of_unfiltered_canonical_idx_set",
        "owner": CHILGOK_PROVIDER,
    },
    {
        "url": (
            "https://www.chilgokctf.or.kr/reservation/edu/courseList.do?"
            "mode=courseList&mId=0101010000"
        ),
        "reason": "cross_host_presentation_alias_of_all_288_county_idx_values",
        "owner": CHILGOK_PROVIDER,
    },
    {
        "url_pattern": (
            "https://www.chilgok.go.kr/reservation/edu/courseList.do?"
            "searchAgency=<agency>&mId=<menu>"
        ),
        "reason": "agency_filter_subset_owned_by_unfiltered_canonical_ledger",
        "owner": CHILGOK_PROVIDER,
    },
)

# These are deliberately outside this provider even when linked from the same
# portal or municipality.  They have independent identities and application
# controls and therefore need their own collectors/owners.
CHILGOK_SEPARATE_OWNER_BOUNDARIES: tuple[Mapping[str, Any], ...] = (
    {
        "provider": "MUNI_LIBRARY_CHILGOK_GO_KR_A1B147F1",
        "name": "칠곡군립도서관 온라인수강신청",
        "url": "https://library.chilgok.go.kr/cg/module/teach/index.do?menu_idx=362",
        "branches": ("칠곡군립", "북삼", "석적", "동명작은", "약목작은"),
        "reason": "separate_library_course_identity_and_login_application_ledger",
    },
    {
        "providers": (
            "MUNI_WWW_CHILGOKCTF_OR_KR_6366B6E9",
            "MUNI_WWW_CHILGOKCTF_OR_KR_26D8FE50",
        ),
        "name": "칠곡문화관광재단 교육프로그램",
        "urls": (
            "https://www.chilgokctf.or.kr/ctf/edu/courseList.do?"
            "searchAgency=2&mId=1005010000",
            "https://www.chilgokctf.or.kr/ctf/edu/courseList.do?"
            "searchAgency=5&mId=1007010000",
        ),
        "branches": (
            "칠곡향사아트센터",
            "칠곡공예테마공원",
            "문화도시사업본부",
            "칠곡생활문화센터",
        ),
        "reason": "separate_foundation_site_and_idx_space; candidate_urls_are_subsets",
    },
    {
        "provider": "MUNI_WWW_CHILGOKSPORTS_CO_KR_FB9670CD",
        "name": "칠곡군 체육시설 통합예약",
        "url": "https://www.chilgoksports.co.kr/",
        "reason": "separate_sports_facility_reservation_owner_not_education_ledger",
    },
    {
        "provider": "MUNI_WWW_CHILGOK_GO_KR_A4F014D5",
        "name": "칠곡군 농업기술센터 교육",
        "url": (
            "https://www.chilgok.go.kr/reservation/farmerUniv/program/list.do?"
            "mId=0103000000"
        ),
        "reason": "separate_farmer_university_program_identity_ledger",
    },
)

CHILGOK_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-23",
    "canonical_url": CHILGOK_CANONICAL_URL,
    "historical_rows": 288,
    "data_pages": 15,
    "empty_sentinel_page": 16,
    "current_or_future_rows": 29,
    "current_details_verified": 29,
    "application_controls_verified": 2,
    "source_status_counts": {
        "교육마감": 257,
        "교육중": 15,
        "접수마감": 10,
        "접수중": 2,
        "폐강": 4,
    },
    "historical_branch_counts": {
        "교육문화회관(사회교육)": 96,
        "교육문화회관(평생교육)": 9,
        "교육문화회관(학점은행제)": 53,
        "동명면사무소": 38,
        "석적읍사무소": 18,
        "청소년문화의집": 29,
        "청소년수련관": 27,
        "칠곡군보건소": 18,
    },
    "foundation_reservation_mirror": (
        "same all-page 288 idx set and page-16 sentinel"
    ),
}

CHILGOK_RECOMMENDED_OVERRIDE: Mapping[str, Any] = {
    "code": CHILGOK_MUNICIPALITY_CODE,
    "full_name": CHILGOK_MUNICIPALITY_NAME,
    "candidates": (
        {
            "status": "candidate",
            "score": 100,
            "title": "칠곡군 통합예약 전체 교육 강좌",
            "url": CHILGOK_CANONICAL_URL,
            "evidence_urls": (
                CHILGOK_OFFICIAL_PORTAL_URL,
                CHILGOK_CANONICAL_URL,
            ),
        },
        {
            "status": "excluded",
            "exclusion_reason": (
                "unofficial_third_party_homepage_guide_not_course_catalogue"
            ),
            "provider": CHILGOK_REJECTED_PROVIDER,
            "url": "https://dongryo.tistory.com/5058",
        },
    ),
}


SessionFactory = Callable[[], Any]
Poster = Callable[[Any, str, Mapping[str, str], int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class ChilgokContractError(ValueError):
    """Raised when an official page no longer satisfies the audited contract."""


_SPACE = re.compile(r"\s+")
_IDENTITY = re.compile(r"^[1-9]\d*$")
_DATE_PAIR = re.compile(
    r"^(20\d{2})-(\d{2})-(\d{2})\s*~\s*"
    r"(20\d{2})-(\d{2})-(\d{2})$"
)
_CAPACITY = re.compile(
    r"^(\d+)\s*/\s*(\d+)\s*\(\s*(\d+)\s*/\s*(\d+)\s*\)$"
)
_DETAIL_CAPACITY = re.compile(r"^(\d+)\s*명\s*/\s*(\d+)\s*명$")
_VIEW_CONTROL = re.compile(r"^fn_view\('([1-9]\d*)'\);?$")
_APPLY_CONTROL = re.compile(
    r"^fn_apply\('([1-9]\d*)',\s*'([A-Za-z][A-Za-z0-9]*)'\);?$"
)
_PAGE_CONTROL = re.compile(r"goPage\((\d+)\)")
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_LIST_CAPTION = (
    "강좌게시판 목록 - 번호,기관명,강좌명,수강료,교육기간,교육시간,"
    "접수/정원(대기/정원)/접수현황 순서대로 안내하는 표입니다."
)
_LIST_HEADERS = (
    "번호",
    "기관명",
    "강좌명",
    "수강료",
    "교육기간",
    "교육시간",
    "접수/정원 (대기/정원)",
    "접수현황",
)
_LIST_CELL_CLASSES = (
    "list_num",
    "list_pub",
    "list_tit",
    "list_money",
    "list_dudate",
    "list_dutime",
    "list_user",
    "list_state",
)
_SOURCE_STATUS: Mapping[str, str] = {
    "접수중": "OPEN",
    "접수대기": "SCHEDULED",
    "접수예정": "SCHEDULED",
    "접수마감": "CLOSED",
    "교육중": "CLOSED",
    "교육마감": "CLOSED",
    "폐강": "CANCELLED",
}
_OPEN_CONTROL = {"접수중": "receiptIng"}
_DETAIL_REQUIRED = frozenset(
    {
        "접수기간",
        "주관기관",
        "교육기간",
        "교육시간/요일",
        "수강료",
        "교육대상",
        "모집인원(신청/정원)",
        "강의장소",
    }
)
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "source_page",
        "source_sequence",
        "source_status",
        "source_apply_period",
        "source_education_period",
        "source_schedule",
        "source_venue",
        "source_fee",
        "official_agency_id",
        "waitlist_current",
        "waitlist_total",
        "detail_verified",
        "detail_identity_control",
        "application_control_present",
        "application_control_contract",
        "application_control_verified",
        "application_endpoint_fetched",
        "pii_form_fetched",
        "service_family",
    }
)
_FORBIDDEN_ROW_KEYS = frozenset(
    {
        "phone",
        "email",
        "contact",
        "contacts",
        "instructor",
        "instructor_name",
        "attachments",
        "attachment_urls",
        "detail_description",
        "course_content",
        "source_html",
        "raw_html",
        "notice",
        "applicant_name",
        "applicant_phone",
        "password",
    }
)


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _query(url: str) -> list[tuple[str, str]]:
    return parse_qsl(urlparse(url).query, keep_blank_values=True, strict_parsing=True)


def is_chilgok_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != CHILGOK_PROVIDER:
        return False
    value = _clean(_target_value(target, "url"))
    try:
        parsed = urlparse(value)
        port = parsed.port
        query = _query(value)
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == CHILGOK_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == CHILGOK_LIST_PATH
        and query == [("mId", "")]
        and not parsed.fragment
        and value == CHILGOK_CANONICAL_URL
    )


is_target = is_chilgok_education_target


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MooncenMunicipalCrawler/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        }
    )
    return session


def _allowed_post_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        port = parsed.port
        query = _query(url)
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == CHILGOK_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {CHILGOK_LIST_PATH, CHILGOK_DETAIL_PATH}
        and query == [("mId", "")]
        and not parsed.fragment
    )


def _validated_response(response: Any, requested_url: str) -> BeautifulSoup:
    response.raise_for_status()
    requested = urlparse(requested_url)
    final_url = _clean(getattr(response, "url", requested_url))
    final = urlparse(final_url)
    if (
        final.scheme != "https"
        or (final.hostname or "").lower() != CHILGOK_HOST
        or final.port is not None
        or final.username is not None
        or final.password is not None
        or final.path != requested.path
        or _query(final_url) != [("mId", "")]
        or final.fragment
    ):
        raise ChilgokContractError("response left the exact official HTTPS endpoint")
    content_type = _clean(response.headers.get("Content-Type")).lower()
    if "html" not in content_type:
        raise ChilgokContractError("response is not HTML")
    content = response.content
    if len(content) > CHILGOK_MAX_HTML_BYTES:
        raise ChilgokContractError("HTML response exceeded the bounded size limit")
    return BeautifulSoup(content, "html.parser")


def _default_poster(
    session: Any, url: str, data: Mapping[str, str], timeout: int
) -> BeautifulSoup:
    if not _allowed_post_url(url):
        raise ChilgokContractError("refused non-list/detail POST endpoint")
    return _validated_response(
        session.post(
            url,
            data=dict(data),
            timeout=timeout,
            allow_redirects=True,
        ),
        url,
    )


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, bytes):
        if len(value) > CHILGOK_MAX_HTML_BYTES:
            raise ChilgokContractError("fixture HTML exceeded the bounded size limit")
        return BeautifulSoup(value, "html.parser")
    if isinstance(value, str):
        if len(value.encode("utf-8")) > CHILGOK_MAX_HTML_BYTES:
            raise ChilgokContractError("fixture HTML exceeded the bounded size limit")
        return BeautifulSoup(value, "html.parser")
    content = getattr(value, "content", None)
    if isinstance(content, (bytes, bytearray)):
        return _coerce_soup(bytes(content))
    raise TypeError("poster must return HTML, bytes, a response, or BeautifulSoup")


def _post_soup(
    session: Any,
    poster: Poster,
    url: str,
    data: Mapping[str, str],
    timeout: int,
) -> BeautifulSoup:
    if not _allowed_post_url(url):
        raise ChilgokContractError("refused unsafe POST endpoint")
    required = {"page", "idx", "applyGubun", "searchTxt"}
    if set(data) != required or _clean(data.get("applyGubun")):
        raise ChilgokContractError("POST payload left the audited form contract")
    if not _clean(data.get("page")).isdigit():
        raise ChilgokContractError("POST page is invalid")
    if url == CHILGOK_CANONICAL_URL:
        if _clean(data.get("idx")) != "0":
            raise ChilgokContractError("list POST carried a course identity")
    elif not _IDENTITY.fullmatch(_clean(data.get("idx"))):
        raise ChilgokContractError("detail POST identity is invalid")
    return _coerce_soup(poster(session, url, data, timeout))


def _close_quietly(value: Any) -> None:
    try:
        close = getattr(value, "close", None)
        if callable(close):
            close()
    except Exception:
        pass


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        return (
            value.astimezone(ZoneInfo("Asia/Seoul")).date()
            if value.tzinfo
            else value.date()
        )
    if isinstance(value, date):
        return value
    return date.fromisoformat(_clean(value))


def _date_period(value: Any, field: str) -> tuple[date, date, str]:
    text = _clean(value)
    match = _DATE_PAIR.fullmatch(text)
    if not match:
        raise ChilgokContractError(f"unsupported {field} period: {text!r}")
    parts = [int(item) for item in match.groups()]
    try:
        start = date(parts[0], parts[1], parts[2])
        end = date(parts[3], parts[4], parts[5])
    except ValueError as exc:
        raise ChilgokContractError(f"invalid {field} calendar date") from exc
    if end < start:
        raise ChilgokContractError(f"reversed {field} period")
    return start, end, f"{start.isoformat()} ~ {end.isoformat()}"


def _fee(value: Any) -> tuple[str, int]:
    text = _clean(value)
    if text == "무료":
        return "무료", 0
    match = re.fullmatch(r"([0-9][0-9,]*)\s*원?", text)
    if not match:
        raise ChilgokContractError(f"unsupported tuition value: {text!r}")
    amount = int(match.group(1).replace(",", ""))
    return f"{amount:,}원", amount


def _hidden(form: Any, name: str, context: str) -> str:
    controls = form.select(f"input[type='hidden'][name='{name}']")
    if len(controls) != 1:
        raise ChilgokContractError(f"{context}: hidden {name} control drift")
    return _clean(controls[0].get("value"))


def _agency_directory(form: Any, context: str) -> dict[str, str]:
    controls = form.select("input[type='checkbox'][name='searchAgency']")
    result: dict[str, str] = {}
    for control in controls:
        identity = _clean(control.get("value"))
        control_id = _clean(control.get("id"))
        labels = form.select(f"label[for='{control_id}']") if control_id else []
        if (
            not identity.isdigit()
            or identity in result
            or len(labels) != 1
            or control.has_attr("checked")
        ):
            raise ChilgokContractError(f"{context}: agency selector drift")
        result[identity] = _clean(labels[0].get_text(" ", strip=True))
    if result != dict(CHILGOK_OFFICIAL_AGENCIES):
        raise ChilgokContractError(f"{context}: official agency directory changed")
    return result


def _list_form(soup: BeautifulSoup, page: int) -> dict[str, str]:
    forms = soup.select("form#listForm")
    if len(forms) != 1:
        raise ChilgokContractError(f"page {page}: exact list form missing")
    form = forms[0]
    action = urljoin(CHILGOK_CANONICAL_URL, _clean(form.get("action")))
    if _clean(form.get("method")).lower() != "post" or action != CHILGOK_CANONICAL_URL:
        raise ChilgokContractError(f"page {page}: list form endpoint/method drift")
    if (
        _hidden(form, "page", f"page {page}") != str(page)
        or _hidden(form, "idx", f"page {page}") != "0"
        or _hidden(form, "applyGubun", f"page {page}")
    ):
        raise ChilgokContractError(f"page {page}: list hidden state drift")
    if len(form.select("input[name='searchAgencyAll']")) != 1:
        raise ChilgokContractError(f"page {page}: all-agency control drift")
    if len(form.select("input[name='searchGroupAll']")) != 1:
        raise ChilgokContractError(f"page {page}: all-group control drift")
    if len(form.select("input[type='text'][name='searchTxt']")) != 1:
        raise ChilgokContractError(f"page {page}: search control drift")
    receipt = form.select("input[type='checkbox'][name='searchReceiptIng']")
    if (
        len(receipt) != 1
        or _clean(receipt[0].get("value")) != "Y"
        or receipt[0].has_attr("checked")
    ):
        raise ChilgokContractError(f"page {page}: receipt filter drift")
    return _agency_directory(form, f"page {page}")


def _total_and_last(soup: BeautifulSoup, page: int) -> tuple[int, int]:
    totals = soup.select(".bod_result em")
    if len(totals) != 1 or not _clean(totals[0].get_text()).isdigit():
        raise ChilgokContractError(f"page {page}: advertised total missing")
    total = int(_clean(totals[0].get_text()))
    if total < 1:
        raise ChilgokContractError(f"page {page}: audited historical ledger became empty")
    expected = max(1, math.ceil(total / CHILGOK_PAGE_SIZE))
    controls = soup.select(".bod_page a.btn_end[onclick]")
    # The immediate out-of-range sentinel intentionally renders no paging
    # anchors.  Its still-present advertised total determines the only valid
    # predecessor.  Deeper arbitrary out-of-range pages remain invalid.
    if not controls and page == expected + 1:
        return total, expected
    if len(controls) != 1:
        raise ChilgokContractError(f"page {page}: last-page control missing")
    match = _PAGE_CONTROL.search(_clean(controls[0].get("onclick")))
    if not match:
        raise ChilgokContractError(f"page {page}: last-page control drift")
    last = int(match.group(1))
    if last != expected:
        raise ChilgokContractError(
            f"page {page}: last page {last} disagrees with total {total}"
        )
    return total, last


def _public_detail_url(identity: str) -> str:
    return (
        f"https://{CHILGOK_HOST}{CHILGOK_DETAIL_PATH}?"
        + urlencode((("mId", ""), ("idx", identity)))
    )


def _parse_list_page(soup: BeautifulSoup, page: int) -> dict[str, Any]:
    agencies = _list_form(soup, page)
    total, last = _total_and_last(soup, page)
    tables = soup.select("table.bod_list.edu")
    if len(tables) != 1:
        raise ChilgokContractError(f"page {page}: exact course table missing")
    table = tables[0]
    caption = _clean(table.caption.get_text(" ", strip=True) if table.caption else "")
    if caption != _LIST_CAPTION:
        raise ChilgokContractError(f"page {page}: course-table caption drift")
    headers = tuple(_clean(item.get_text(" ", strip=True)) for item in table.select("thead th"))
    if headers != _LIST_HEADERS:
        raise ChilgokContractError(f"page {page}: course-table headers drift")
    bodies = table.find_all("tbody", recursive=False)
    if len(bodies) != 1:
        raise ChilgokContractError(f"page {page}: course tbody drift")
    row_nodes = bodies[0].find_all("tr", recursive=False)
    if not row_nodes:
        return {
            "page": page,
            "total": total,
            "last": last,
            "agencies": agencies,
            "rows": [],
            "empty": True,
        }
    if len(row_nodes) > CHILGOK_PAGE_SIZE:
        raise ChilgokContractError(f"page {page}: fixed page size exceeded")

    rows: list[dict[str, Any]] = []
    identities: set[str] = set()
    agency_by_name = {name: identity for identity, name in agencies.items()}
    for row_node in row_nodes:
        cells = row_node.find_all("td", recursive=False)
        if len(cells) != len(_LIST_HEADERS):
            raise ChilgokContractError(f"page {page}: course column count drift")
        for cell, expected_class in zip(cells, _LIST_CELL_CLASSES):
            if expected_class not in set(cell.get("class") or []):
                raise ChilgokContractError(f"page {page}: course cell-class drift")

        sequence_text = _clean(cells[0].get_text(" ", strip=True))
        branch = _clean(cells[1].get_text(" ", strip=True))
        title_links = cells[2].select("a[onclick]")
        if len(title_links) != 1:
            raise ChilgokContractError(f"page {page}: course title control drift")
        title_link = title_links[0]
        view_match = _VIEW_CONTROL.fullmatch(_clean(title_link.get("onclick")))
        title = _clean(title_link.get_text(" ", strip=True))
        if (
            not sequence_text.isdigit()
            or branch not in agency_by_name
            or not title
            or view_match is None
            or _clean(title_link.get("href")) != "#n"
        ):
            raise ChilgokContractError(f"page {page}: required course identity drift")
        identity = view_match.group(1)
        if identity in identities:
            raise ChilgokContractError(f"page {page}: duplicate course idx")
        identities.add(identity)

        source_period = _clean(cells[4].get_text(" ", strip=True))
        start, end, normalized_period = _date_period(source_period, "education")
        schedule = _clean(cells[5].get_text(" ", strip=True))
        if not schedule:
            raise ChilgokContractError(f"page {page}: empty education schedule")
        capacity_text = _clean(cells[6].get_text(" ", strip=True))
        capacity_match = _CAPACITY.fullmatch(capacity_text)
        if capacity_match is None:
            raise ChilgokContractError(f"page {page}: capacity shape drift")
        current, capacity, wait_current, wait_capacity = map(
            int, capacity_match.groups()
        )
        fee, fee_amount = _fee(cells[3].get_text(" ", strip=True))
        source_status = _clean(cells[7].get_text(" ", strip=True))
        if source_status not in _SOURCE_STATUS:
            raise ChilgokContractError(
                f"page {page}: unsupported source status {source_status!r}"
            )

        apply_controls = cells[7].select("a[onclick]")
        apply_token = ""
        if source_status in _OPEN_CONTROL:
            if len(apply_controls) != 1:
                raise ChilgokContractError(
                    f"page {page}: open course lacks one application control"
                )
            apply_match = _APPLY_CONTROL.fullmatch(
                _clean(apply_controls[0].get("onclick"))
            )
            if (
                apply_match is None
                or apply_match.group(1) != identity
                or apply_match.group(2) != _OPEN_CONTROL[source_status]
                or _clean(apply_controls[0].get_text(" ", strip=True)) != source_status
                or _clean(apply_controls[0].get("href")) != "#n"
            ):
                raise ChilgokContractError(
                    f"page {page}: application identity/state binding drift"
                )
            apply_token = apply_match.group(2)
        elif apply_controls:
            raise ChilgokContractError(
                f"page {page}: non-open course exposes application control"
            )

        rows.append(
            {
                "identity": identity,
                "page": page,
                "sequence": int(sequence_text),
                "agency_id": agency_by_name[branch],
                "branch": branch,
                "title": title,
                "fee": fee,
                "fee_amount": fee_amount,
                "start": start,
                "end": end,
                "period": normalized_period,
                "schedule": schedule,
                "capacity_current": current,
                "capacity_total": capacity,
                "waitlist_current": wait_current,
                "waitlist_total": wait_capacity,
                "source_status": source_status,
                "status": _SOURCE_STATUS[source_status],
                "apply_token": apply_token,
                "detail_url": _public_detail_url(identity),
            }
        )
    return {
        "page": page,
        "total": total,
        "last": last,
        "agencies": agencies,
        "rows": rows,
        "empty": False,
    }


def _page_signature(page: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        bool(page["empty"]),
        int(page["total"]),
        int(page["last"]),
        tuple(page["agencies"].items()),
        tuple(
            (
                row["identity"],
                row["sequence"],
                row["branch"],
                row["title"],
                row["fee_amount"],
                row["period"],
                row["schedule"],
                row["capacity_current"],
                row["capacity_total"],
                row["waitlist_current"],
                row["waitlist_total"],
                row["source_status"],
                row["apply_token"],
            )
            for row in page["rows"]
        ),
    )


def _detail_fields(table: Any, identity: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for row in table.select("tbody > tr"):
        items = row.find_all(["th", "td"], recursive=False)
        position = 0
        while position < len(items):
            if (
                items[position].name == "th"
                and position + 1 < len(items)
                and items[position + 1].name == "td"
            ):
                label = _clean(items[position].get_text(" ", strip=True))
                value = _clean(items[position + 1].get_text(" ", strip=True))
                if not label or label in fields:
                    raise ChilgokContractError(
                        f"course {identity}: duplicate/empty detail label"
                    )
                fields[label] = value
                position += 2
            else:
                raise ChilgokContractError(
                    f"course {identity}: detail label/value shape drift"
                )
    if not _DETAIL_REQUIRED <= set(fields):
        missing = sorted(_DETAIL_REQUIRED - set(fields))
        raise ChilgokContractError(
            f"course {identity}: required detail fields missing: {missing}"
        )
    return fields


def _schedule_signature(value: Any) -> str:
    return _clean(_clean(value).replace("/", " "))


def _branch_code(branch: str) -> str:
    digest = hashlib.sha1(branch.encode("utf-8")).hexdigest()[:12].upper()
    return f"CHILGOK_{digest}"


def _parse_detail(
    listed: Mapping[str, Any], soup: BeautifulSoup, cutoff: date
) -> dict[str, Any]:
    identity = str(listed["identity"])
    forms = soup.select("form#listForm")
    if len(forms) != 1:
        raise ChilgokContractError(f"course {identity}: detail identity form missing")
    form = forms[0]
    action = urljoin(CHILGOK_DETAIL_POST_URL, _clean(form.get("action")))
    if _clean(form.get("method")).lower() != "post" or action != CHILGOK_CANONICAL_URL:
        raise ChilgokContractError(f"course {identity}: detail return form drift")
    if (
        _hidden(form, "page", f"course {identity}") != str(listed["page"])
        or _hidden(form, "idx", f"course {identity}") != identity
        or _hidden(form, "applyGubun", f"course {identity}")
    ):
        raise ChilgokContractError(f"course {identity}: hidden idx/page binding drift")

    tables = soup.select("table.tbl.frm")
    if len(tables) != 1:
        raise ChilgokContractError(f"course {identity}: exact detail table missing")
    fields = _detail_fields(tables[0], identity)
    if fields["주관기관"] != listed["branch"]:
        raise ChilgokContractError(f"course {identity}: official branch drift")
    detail_start, detail_end, detail_period = _date_period(
        fields["교육기간"], "detail education"
    )
    if (
        (detail_start, detail_end) != (listed["start"], listed["end"])
        or detail_period != listed["period"]
        or detail_end < cutoff
    ):
        raise ChilgokContractError(f"course {identity}: detail education period drift")
    if _schedule_signature(fields["교육시간/요일"]) != _schedule_signature(
        listed["schedule"]
    ):
        raise ChilgokContractError(f"course {identity}: detail schedule drift")
    detail_fee, detail_fee_amount = _fee(fields["수강료"])
    if (
        detail_fee != listed["fee"]
        or detail_fee_amount != listed["fee_amount"]
    ):
        raise ChilgokContractError(f"course {identity}: detail tuition drift")
    capacity_match = _DETAIL_CAPACITY.fullmatch(fields["모집인원(신청/정원)"])
    if capacity_match is None or tuple(map(int, capacity_match.groups())) != (
        listed["capacity_current"],
        listed["capacity_total"],
    ):
        raise ChilgokContractError(f"course {identity}: detail capacity drift")
    apply_start, apply_end, apply_period = _date_period(
        fields["접수기간"], "application"
    )

    controls = []
    for anchor in soup.select("a[onclick]"):
        match = _APPLY_CONTROL.fullmatch(_clean(anchor.get("onclick")))
        if match:
            controls.append((anchor, match))
    expected_open = listed["status"] == "OPEN"
    if expected_open:
        if len(controls) != 1:
            raise ChilgokContractError(
                f"course {identity}: open detail lacks one application control"
            )
        anchor, match = controls[0]
        if (
            match.group(1) != identity
            or match.group(2) != listed["apply_token"]
            or match.group(2) != "receiptIng"
            or _clean(anchor.get_text(" ", strip=True)) != "수강신청"
            or _clean(anchor.get("href")) != "#n"
            or not apply_start <= cutoff <= apply_end
        ):
            raise ChilgokContractError(
                f"course {identity}: detail application identity/state drift"
            )
    elif controls:
        raise ChilgokContractError(
            f"course {identity}: non-open detail exposes application control"
        )

    target = _clean(fields["교육대상"])
    venue = _clean(fields["강의장소"])
    if not target or not venue:
        raise ChilgokContractError(f"course {identity}: target/venue missing")
    if _PHONE.search(venue) or _EMAIL.search(venue):
        raise ChilgokContractError(f"course {identity}: venue contains contact data")

    detail_url = str(listed["detail_url"])
    control_contract = (
        f"POST {CHILGOK_APPLICATION_PATH} idx={identity} "
        f"applyGubun={listed['apply_token']}"
        if expected_open
        else ""
    )
    return {
        "provider": CHILGOK_PROVIDER,
        "provider_course_id": f"{CHILGOK_PROVIDER}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": str(listed["title"]),
        "description": str(listed["title"]),
        "branch": str(listed["branch"]),
        "branch_code": _branch_code(str(listed["branch"])),
        "preserve_branch": True,
        "category": "공공교육",
        "program_type": "교육",
        "raw_url": detail_url,
        # The public detail contains the verified button.  Do not turn the
        # native POST application form into an invented GET URL.
        "application_url": detail_url,
        "application_type": (
            "ONLINE_RESERVATION" if expected_open else "INFO_ONLY"
        ),
        "application_method": "온라인",
        "application_methods": ["온라인"],
        "reservation_available": expected_open,
        "status": str(listed["status"]),
        "fee": detail_fee,
        "fee_amount": detail_fee_amount,
        "period": detail_period,
        "start_date": detail_start.isoformat(),
        "end_date": detail_end.isoformat(),
        "apply_period": apply_period,
        "schedule_raw": _clean(fields["교육시간/요일"]),
        "capacity": f"{listed['capacity_total']}명",
        "capacity_current": int(listed["capacity_current"]),
        "capacity_total": int(listed["capacity_total"]),
        "target": target,
        "venue": venue,
        "venue_name": venue,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": CHILGOK_PARSER,
        "municipality_code": CHILGOK_MUNICIPALITY_CODE,
        "municipality_full_name": CHILGOK_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "source_page": int(listed["page"]),
            "source_sequence": int(listed["sequence"]),
            "source_status": str(listed["source_status"]),
            "source_apply_period": apply_period,
            "source_education_period": detail_period,
            "source_schedule": _clean(fields["교육시간/요일"]),
            "source_venue": venue,
            "source_fee": detail_fee,
            "official_agency_id": str(listed["agency_id"]),
            "waitlist_current": int(listed["waitlist_current"]),
            "waitlist_total": int(listed["waitlist_total"]),
            "detail_verified": True,
            "detail_identity_control": f"hidden idx={identity}",
            "application_control_present": expected_open,
            "application_control_contract": control_contract,
            "application_control_verified": True,
            "application_endpoint_fetched": False,
            "pii_form_fetched": False,
            "service_family": "education",
        },
    }


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_ROW_KEYS:
        errors.append("forbidden detail/PII key")
    raw_fields = row.get("raw_fields")
    if not isinstance(raw_fields, Mapping) or not set(raw_fields) <= _SAFE_RAW_FIELDS:
        errors.append("raw field allowlist exceeded")
    payload = repr(
        {
            key: value
            for key, value in row.items()
            if key not in {"raw_url", "application_url"}
        }
    )
    if _PHONE.search(payload) or _EMAIL.search(payload):
        errors.append("contact data persisted")
    if row.get("description") != row.get("title"):
        errors.append("free-form detail persisted")
    return errors


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = str(row["provider_course_id"])
        if identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _base_meta() -> dict[str, Any]:
    return {
        "municipality_code": CHILGOK_MUNICIPALITY_CODE,
        "municipality_full_name": CHILGOK_MUNICIPALITY_NAME,
        "owner_provider": CHILGOK_PROVIDER,
        "canonical_candidate_id": CHILGOK_CANONICAL_CANDIDATE_ID,
        "rejected_candidate_id": CHILGOK_REJECTED_CANDIDATE_ID,
        "rejected_provider": CHILGOK_REJECTED_PROVIDER,
        "canonical_url": CHILGOK_CANONICAL_URL,
        "official_portal_url": CHILGOK_OFFICIAL_PORTAL_URL,
        "parser": CHILGOK_PARSER,
        "ownership_scope": "chilgok_county_unfiltered_reservation_education_idx_set",
        "excluded_candidate_reason": (
            "unofficial_third_party_homepage_guide_not_course_catalogue"
        ),
        "official_agencies": dict(CHILGOK_OFFICIAL_AGENCIES),
        "candidate_audit": {
            key: dict(value) for key, value in CHILGOK_CANDIDATE_AUDIT.items()
        },
        "non_executing_aliases": [dict(item) for item in CHILGOK_NON_EXECUTING_ALIASES],
        "separate_owner_boundaries": [
            dict(item) for item in CHILGOK_SEPARATE_OWNER_BOUNDARIES
        ],
        "recommended_override": dict(CHILGOK_RECOMMENDED_OVERRIDE),
        "recommended_max_pages": CHILGOK_RECOMMENDED_MAX_PAGES,
        "recommended_detail_limit": CHILGOK_RECOMMENDED_DETAIL_LIMIT,
        "source_requests": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "data_pages": 0,
        "advertised_pages": 0,
        "sentinel_page": 0,
        "source_rows": 0,
        "current_source_count": 0,
        "returned_count": 0,
        "boundary_rechecks": 0,
        "current_list_pages_rechecked": [],
        "page1_rechecked": False,
        "last_page_rechecked": False,
        "sentinel_rechecked": False,
        "application_endpoints_called": 0,
        "pii_form_endpoints_called": 0,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "configured_collection_error": "",
    }


def collect_chilgok_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = CHILGOK_RECOMMENDED_MAX_PAGES,
    detail_limit: int = CHILGOK_RECOMMENDED_DETAIL_LIMIT,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    poster: Optional[Poster] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete, atomic current/future Chilgok education snapshot."""

    meta = _base_meta()
    if not is_chilgok_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match exact canonical Chilgok education owner"
        )
        return [], CHILGOK_PARSER, meta
    try:
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int)
            or timeout < 1
            or isinstance(max_pages, bool)
            or not isinstance(max_pages, int)
            or max_pages < 2
            or isinstance(detail_limit, bool)
            or not isinstance(detail_limit, int)
            or detail_limit < 0
        ):
            raise ValueError("timeout/max_pages/detail_limit caps are invalid")
        cutoff = _today(today)
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], CHILGOK_PARSER, meta

    factory = session_factory or _default_session_factory
    current_poster = poster or _default_poster
    session = factory()

    def get_list(page: int) -> dict[str, Any]:
        soup = _post_soup(
            session,
            current_poster,
            CHILGOK_CANONICAL_URL,
            {
                "page": str(page),
                "idx": "0",
                "applyGubun": "",
                "searchTxt": "",
            },
            timeout,
        )
        meta["source_requests"] += 1
        meta["list_requests"] += 1
        return _parse_list_page(soup, page)

    try:
        first = get_list(1)
        total = int(first["total"])
        last = int(first["last"])
        sentinel_number = last + 1
        meta.update(
            {
                "cutoff": cutoff.isoformat(),
                "source_total": total,
                "advertised_pages": last,
                "sentinel_page": sentinel_number,
            }
        )
        if sentinel_number > max_pages:
            meta["source_cap_reached"] = True
            raise ChilgokContractError(
                f"max_pages {max_pages} below required sentinel page {sentinel_number}"
            )
        if first["empty"]:
            raise ChilgokContractError("first advertised page is unexpectedly empty")

        pages: dict[int, dict[str, Any]] = {1: first}
        for page_number in range(2, last + 1):
            parsed = get_list(page_number)
            if parsed["empty"]:
                raise ChilgokContractError(
                    f"page {page_number}: empty before advertised last page"
                )
            pages[page_number] = parsed
        sentinel = get_list(sentinel_number)
        if not sentinel["empty"]:
            raise ChilgokContractError(
                f"page {sentinel_number}: immediate empty sentinel missing"
            )

        for page_number, parsed in [*pages.items(), (sentinel_number, sentinel)]:
            if (
                parsed["total"] != total
                or parsed["last"] != last
                or parsed["agencies"] != first["agencies"]
            ):
                raise ChilgokContractError(
                    f"page {page_number}: total/pagination/agency contract drift"
                )
        for page_number in range(1, last):
            if len(pages[page_number]["rows"]) != CHILGOK_PAGE_SIZE:
                raise ChilgokContractError(
                    f"page {page_number}: non-final data page is not full"
                )
        expected_final = total - CHILGOK_PAGE_SIZE * (last - 1)
        if len(pages[last]["rows"]) != expected_final:
            raise ChilgokContractError("final-page row count disagrees with total")

        listed = [
            row
            for page_number in range(1, last + 1)
            for row in pages[page_number]["rows"]
        ]
        if len(listed) != total:
            raise ChilgokContractError("all-page row count disagrees with advertised total")
        sequences = [int(row["sequence"]) for row in listed]
        if sequences != list(range(total, 0, -1)):
            raise ChilgokContractError("display sequence is not complete and descending")
        identities = [str(row["identity"]) for row in listed]
        if len(identities) != len(set(identities)):
            raise ChilgokContractError("course idx repeated across advertised pages")

        current = [row for row in listed if row["end"] >= cutoff]
        meta.update(
            {
                "data_pages": last,
                "source_rows": len(listed),
                "current_source_count": len(current),
                "expired_source_count": len(listed) - len(current),
                "source_status_counts": dict(
                    Counter(str(row["source_status"]) for row in listed)
                ),
                "source_branch_counts": dict(
                    Counter(str(row["branch"]) for row in listed)
                ),
                "pagination_complete": True,
            }
        )
        if len(current) > detail_limit:
            meta["source_cap_reached"] = True
            raise ChilgokContractError(
                f"detail_limit {detail_limit} below required {len(current)}"
            )

        rows: list[dict[str, Any]] = []
        for listed_row in current:
            soup = _post_soup(
                session,
                current_poster,
                CHILGOK_DETAIL_POST_URL,
                {
                    "page": str(listed_row["page"]),
                    "idx": str(listed_row["identity"]),
                    "applyGubun": "",
                    "searchTxt": "",
                },
                timeout,
            )
            meta["source_requests"] += 1
            meta["detail_pages"] += 1
            rows.append(_parse_detail(listed_row, soup, cutoff))

        # Recheck each list page whose current rows were detailed, plus the
        # first/last boundaries, then recheck the exact empty sentinel.
        stability_pages = sorted(
            {1, last} | {int(row["page"]) for row in current}
        )
        for page_number in stability_pages:
            rechecked = get_list(page_number)
            meta["boundary_rechecks"] += 1
            if _page_signature(rechecked) != _page_signature(pages[page_number]):
                raise ChilgokContractError(
                    f"page {page_number}: stability recheck failed"
                )
        sentinel_recheck = get_list(sentinel_number)
        meta["boundary_rechecks"] += 1
        if _page_signature(sentinel_recheck) != _page_signature(sentinel):
            raise ChilgokContractError("empty-sentinel stability recheck failed")
        meta.update(
            {
                "current_list_pages_rechecked": stability_pages,
                "page1_rechecked": 1 in stability_pages,
                "last_page_rechecked": last in stability_pages,
                "sentinel_rechecked": True,
            }
        )

        rows.sort(key=lambda row: (row["start_date"], row["provider_course_id"]))
        rows = list((dedupe_rows or _dedupe)(rows))
        privacy_errors = [error for row in rows for error in _privacy_errors(row)]
        if privacy_errors:
            raise ChilgokContractError("; ".join(privacy_errors[:5]))
        if len(rows) != len(current):
            raise ChilgokContractError("dedupe changed the complete current idx set")
        meta.update(
            {
                "returned_count": len(rows),
                "status_counts": dict(Counter(str(row["status"]) for row in rows)),
                "branch_counts": dict(Counter(str(row["branch"]) for row in rows)),
                "application_control_count": sum(
                    bool(row["raw_fields"]["application_control_present"])
                    for row in rows
                ),
                "details_complete": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
                "no_current_data": not rows,
            }
        )
        return rows, CHILGOK_PARSER, meta
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], CHILGOK_PARSER, meta
    finally:
        _close_quietly(session)


collect = collect_chilgok_education


__all__ = [
    "CHILGOK_CANONICAL_CANDIDATE_ID",
    "CHILGOK_CANONICAL_URL",
    "CHILGOK_CANDIDATE_AUDIT",
    "CHILGOK_DISCOVERY_AUDIT",
    "CHILGOK_NON_EXECUTING_ALIASES",
    "CHILGOK_OFFICIAL_AGENCIES",
    "CHILGOK_PARSER",
    "CHILGOK_PROVIDER",
    "CHILGOK_RECOMMENDED_DETAIL_LIMIT",
    "CHILGOK_RECOMMENDED_MAX_PAGES",
    "CHILGOK_RECOMMENDED_OVERRIDE",
    "CHILGOK_REJECTED_CANDIDATE_ID",
    "CHILGOK_REJECTED_PROVIDER",
    "CHILGOK_SEPARATE_OWNER_BOUNDARIES",
    "ChilgokContractError",
    "collect",
    "collect_chilgok_education",
    "is_chilgok_education_target",
    "is_target",
]
