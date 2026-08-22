"""Fail-closed collector for Incheon's official integrated reservation lists.

Only the two reviewed public catalogues are owned here: ``강좌/교육`` and
``견학/체험``.  Collection is deliberately limited to public list and public
detail GET requests.  Login, application, applicant, cart, payment,
cancellation, identity-verification and personal-reservation endpoints are
outside the request allowlist and are never fetched.

The site publishes ten rows per page and returns an explicit no-data card on
the page immediately after the declared final page.  A snapshot is emitted
only after all declared pages, that sentinel, stable first/final boundaries,
and every current public detail have been validated.  Experience rows are
also reconciled one-to-one against the official eleven-district filter so the
2026 Incheon municipality code is supported by source evidence rather than a
guess from a branch name.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag


INCHEON_RESERVATION_PROVIDER = "INCHEON_RESERVATION"
INCHEON_RESERVATION_HOST = "www.incheon.go.kr"
INCHEON_RESERVATION_ROOT = "https://www.incheon.go.kr"
INCHEON_CITY_CODE = "2800000000"
INCHEON_CITY_NAME = "인천광역시"
INCHEON_PAGE_SIZE = 10
INCHEON_DEFAULT_MAX_PAGES = 200
INCHEON_DEFAULT_DETAIL_LIMIT = 200
INCHEON_REQUEST_BUDGET = 400

INCHEON_EDUCATION_URL = (
    "https://www.incheon.go.kr/res/RE010101/lctreEdcList?resveInsttCode="
)
INCHEON_EXPERIENCE_URL = (
    "https://www.incheon.go.kr/res/RE030101/lnbnsExprnList?resveInsttCode="
)

INCHEON_EDUCATION_PARSER = (
    "incheon_official_education+declared_pages+empty_sentinel+"
    "stable_boundaries+all_current_public_details+2026_address_mapping+"
    "notice_exclusion+get_only_allowlist"
)
INCHEON_EXPERIENCE_PARSER = (
    "incheon_official_experience+declared_pages+empty_sentinel+"
    "eleven_district_reconcile+stable_boundaries+all_current_public_details+"
    "notice_exclusion+get_only_allowlist"
)

INCHEON_EDUCATION_OWNERSHIP_SCOPE = (
    "incheon_online_integrated_reservation_official_education_current_future"
)
INCHEON_EXPERIENCE_OWNERSHIP_SCOPE = (
    "incheon_online_integrated_reservation_official_experience_current_future"
)


@dataclass(frozen=True)
class IncheonDistrict:
    source_code: str
    label: str
    municipality_code: str
    municipality_full_name: str


INCHEON_EXPERIENCE_DISTRICTS: tuple[IncheonDistrict, ...] = (
    IncheonDistrict("1", "강화군", "2871000000", "인천광역시 강화군"),
    IncheonDistrict("9", "옹진군", "2872000000", "인천광역시 옹진군"),
    IncheonDistrict("11", "제물포구", "2812500000", "인천광역시 제물포구"),
    IncheonDistrict("12", "영종구", "2815500000", "인천광역시 영종구"),
    IncheonDistrict("3", "미추홀구", "2817700000", "인천광역시 미추홀구"),
    IncheonDistrict("8", "연수구", "2818500000", "인천광역시 연수구"),
    IncheonDistrict("4", "남동구", "2820000000", "인천광역시 남동구"),
    IncheonDistrict("6", "부평구", "2823700000", "인천광역시 부평구"),
    IncheonDistrict("2", "계양구", "2824500000", "인천광역시 계양구"),
    IncheonDistrict("13", "서해구", "2827500000", "인천광역시 서해구"),
    IncheonDistrict("14", "검단구", "2829000000", "인천광역시 검단구"),
)
INCHEON_DISTRICT_BY_SOURCE = {
    district.source_code: district for district in INCHEON_EXPERIENCE_DISTRICTS
}
INCHEON_DISTRICT_BY_CODE = {
    district.municipality_code: district
    for district in INCHEON_EXPERIENCE_DISTRICTS
}
INCHEON_ROW_MUNICIPALITY_CODES = tuple(
    district.municipality_code for district in INCHEON_EXPERIENCE_DISTRICTS
)
INCHEON_COVERED_MUNICIPALITIES: tuple[dict[str, str], ...] = (
    {
        "code": INCHEON_CITY_CODE,
        "sido": INCHEON_CITY_NAME,
        "sigungu": "",
        "full_name": INCHEON_CITY_NAME,
    },
    *(
        {
            "code": district.municipality_code,
            "sido": INCHEON_CITY_NAME,
            "sigungu": district.label,
            "full_name": district.municipality_full_name,
        }
        for district in INCHEON_EXPERIENCE_DISTRICTS
    ),
)


@dataclass(frozen=True)
class IncheonCatalogue:
    kind: str
    name: str
    canonical_url: str
    list_path: str
    detail_path: str
    source_type: str
    event_label: str
    event_detail_field: str
    parser: str
    ownership_scope: str
    domain_category: str
    service_group: str
    program_type: str


INCHEON_EDUCATION = IncheonCatalogue(
    kind="education",
    name="강좌/교육",
    canonical_url=INCHEON_EDUCATION_URL,
    list_path="/res/RE010101/lctreEdcList",
    detail_path="/res/RE010101/lctreEdcView",
    source_type="L",
    event_label="수강",
    event_detail_field="교육기간",
    parser=INCHEON_EDUCATION_PARSER,
    ownership_scope=INCHEON_EDUCATION_OWNERSHIP_SCOPE,
    domain_category="교육·강좌",
    service_group="공공강좌",
    program_type="교육",
)
INCHEON_EXPERIENCE = IncheonCatalogue(
    kind="experience",
    name="견학/체험",
    canonical_url=INCHEON_EXPERIENCE_URL,
    list_path="/res/RE030101/lnbnsExprnList",
    detail_path="/res/RE030101/lnbnsExprnView",
    source_type="E",
    event_label="운영",
    event_detail_field="운영기간",
    parser=INCHEON_EXPERIENCE_PARSER,
    ownership_scope=INCHEON_EXPERIENCE_OWNERSHIP_SCOPE,
    domain_category="체험·견학",
    service_group="체험",
    program_type="체험",
)
INCHEON_CATALOGUES = (INCHEON_EDUCATION, INCHEON_EXPERIENCE)


@dataclass(frozen=True)
class _ListedRow:
    catalogue: IncheonCatalogue
    identity: str
    group_id: str
    program_id: str
    page: int
    position: int
    title: str
    institution: str
    source_status: str
    status: str
    fee: str
    venue: str
    target: str
    apply_start: Optional[date]
    apply_end: Optional[date]
    start_date: Optional[date]
    end_date: Optional[date]
    raw_url: str
    non_program_reason: str = ""

    def current_on(self, cutoff: date) -> bool:
        return bool(
            not self.non_program_reason
            and self.end_date is not None
            and self.end_date >= cutoff
        )


@dataclass(frozen=True)
class _ListSnapshot:
    rows: tuple[_ListedRow, ...]
    total_pages: int
    sentinel_page: int
    boundaries: Mapping[int, str]
    list_requests: int


SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"
)
_DIGITS_RE = re.compile(r"[1-9]\d*")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2}[- .]?\d{3,4}[- .]?\d{4}|"
    r"01[016789][- .]?\d{3,4}[- .]?\d{4})(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_NOTICE_RE = re.compile(
    r"^(?:\[[^\]]*(?:공지|알림|점검|테스트)[^\]]*\]|"
    r"\(?공지(?:사항)?\)?|알림(?:사항)?|시스템\s*점검|테스트(?:\s*프로그램)?)",
    re.IGNORECASE,
)
_STATUS_MAP: Mapping[str, str] = {
    "접수예정": "SCHEDULED",
    "접수대기": "SCHEDULED",
    "접수중": "OPEN",
    "대기자접수": "WAITLIST",
    "대기자접수중": "WAITLIST",
    "접수마감": "CLOSED",
    "모집마감": "CLOSED",
    "운영중": "CLOSED",
    "종료": "CLOSED",
    "완료": "CLOSED",
}
_APPLICATION_STATUSES = frozenset(
    {"접수예정", "접수대기", "접수중", "대기자접수", "대기자접수중"}
)
_NO_DATA_TEXT = "등록된 게시물이 없습니다."
_LIST_HIDDEN_FIELDS = frozenset({"useAt", "resveProgrmSeCode", "resveGroupSn"})
_EDUCATION_SORT_OPTIONS = (
    ("", "전체"),
    ("IT", "IT/컴퓨터"),
    ("HELTH", "건강/스포츠"),
    ("JOB", "취업/창업/전문"),
    ("LINGUISTICS", "어학"),
    ("HOBBY", "문화/취미/실용"),
    ("CHILD", "어린이"),
    ("ETC", "기타"),
    ("SWIMMING", "수영/헬스"),
    ("WEEKEND", "주말"),
    ("NIGHT", "야간"),
)
_EXPERIENCE_SORT_OPTIONS = (
    ("", "전체"),
    *((district.source_code, district.label) for district in INCHEON_EXPERIENCE_DISTRICTS),
)


class IncheonReservationContractError(ValueError):
    """Raised when the reviewed public-source contract has drifted."""


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", _clean(value).casefold())


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        try:
            return date.fromisoformat(_clean(value))
        except ValueError as exc:
            raise IncheonReservationContractError("today must be an ISO date") from exc
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _single_query(query: Mapping[str, list[str]], key: str) -> str:
    values = query.get(key) or []
    if len(values) != 1:
        return ""
    return values[0]


def _exact_catalogue_url(value: Any, catalogue: IncheonCatalogue) -> bool:
    parsed = urlparse(_clean(value))
    try:
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == INCHEON_RESERVATION_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == catalogue.list_path
        and not parsed.params
        and not parsed.fragment
        and parse_qs(parsed.query, keep_blank_values=True)
        == {"resveInsttCode": [""]}
    )


def incheon_catalogue_for_target(target: Any) -> Optional[IncheonCatalogue]:
    if _clean(_target_value(target, "provider")).upper() != INCHEON_RESERVATION_PROVIDER:
        return None
    return next(
        (
            catalogue
            for catalogue in INCHEON_CATALOGUES
            if _exact_catalogue_url(_target_value(target, "url"), catalogue)
        ),
        None,
    )


def is_incheon_reservation_target(target: Any) -> bool:
    return incheon_catalogue_for_target(target) is not None


is_target = is_incheon_reservation_target


def incheon_list_url(
    catalogue: IncheonCatalogue,
    page: Any,
    *,
    district_source_code: str = "",
) -> str:
    raw_page = _clean(page)
    if catalogue not in INCHEON_CATALOGUES or not raw_page.isdigit():
        return ""
    page_number = int(raw_page)
    if page_number < 1:
        return ""
    if district_source_code and (
        catalogue is not INCHEON_EXPERIENCE
        or district_source_code not in INCHEON_DISTRICT_BY_SOURCE
    ):
        return ""
    if page_number == 1 and not district_source_code:
        return catalogue.canonical_url
    params: list[tuple[str, Any]] = [("resveInsttCode", "")]
    if district_source_code:
        params.append(("sortType", district_source_code))
    params.append(("curPage", page_number))
    return f"{INCHEON_RESERVATION_ROOT}{catalogue.list_path}?{urlencode(params)}"


def _assert_safe_public_url(url: str) -> None:
    parsed = urlparse(_clean(url))
    try:
        port = parsed.port
    except ValueError as exc:
        raise IncheonReservationContractError("invalid URL port") from exc
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == INCHEON_RESERVATION_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and not parsed.params
        and not parsed.fragment
    ):
        raise IncheonReservationContractError("URL escaped the reviewed Incheon host")
    query = parse_qs(parsed.query, keep_blank_values=True)
    catalogue = next(
        (
            item
            for item in INCHEON_CATALOGUES
            if parsed.path in {item.list_path, item.detail_path}
        ),
        None,
    )
    if catalogue is None:
        raise IncheonReservationContractError(
            "login/application/PII/non-public endpoint refused"
        )
    if parsed.path == catalogue.list_path:
        allowed = {"resveInsttCode", "curPage"}
        if catalogue is INCHEON_EXPERIENCE:
            allowed.add("sortType")
        if (
            set(query) - allowed
            or "resveInsttCode" not in query
            or _single_query(query, "resveInsttCode") != ""
        ):
            raise IncheonReservationContractError("invalid public list query")
        page = _single_query(query, "curPage") if "curPage" in query else "1"
        if not _DIGITS_RE.fullmatch(page):
            raise IncheonReservationContractError("invalid public list page")
        if "sortType" in query:
            source_code = _single_query(query, "sortType")
            if source_code not in INCHEON_DISTRICT_BY_SOURCE:
                raise IncheonReservationContractError("invalid experience district filter")
        return
    allowed = {
        "resveGroupSn",
        "resveProgrmSeCode",
        "progrmSn",
        "curPage",
        "resveInsttCode",
    }
    if set(query) - allowed:
        raise IncheonReservationContractError("invalid public detail query")
    if any(
        not _DIGITS_RE.fullmatch(_single_query(query, key))
        for key in ("resveGroupSn", "progrmSn", "curPage")
    ):
        raise IncheonReservationContractError("invalid public detail identity")
    if _single_query(query, "resveProgrmSeCode") != catalogue.source_type:
        raise IncheonReservationContractError("detail escaped its catalogue type")
    if "resveInsttCode" in query and _single_query(query, "resveInsttCode") != "":
        raise IncheonReservationContractError("invalid detail institution filter")


def incheon_detail_url(catalogue: IncheonCatalogue, href: Any) -> str:
    absolute = urljoin(INCHEON_RESERVATION_ROOT, _clean(href))
    try:
        _assert_safe_public_url(absolute)
    except IncheonReservationContractError:
        return ""
    return absolute if urlparse(absolute).path == catalogue.detail_path else ""


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    return session


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _response_soup(response: Any, expected_url: str) -> BeautifulSoup:
    try:
        status = int(getattr(response, "status_code", 200))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise IncheonReservationContractError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise IncheonReservationContractError("redirects are not accepted")
    final_url = _clean(getattr(response, "url", ""))
    if final_url:
        final, expected = urlparse(final_url), urlparse(expected_url)
        if (
            final.scheme != "https"
            or (final.hostname or "").lower() != INCHEON_RESERVATION_HOST
            or final.path != expected.path
        ):
            raise IncheonReservationContractError("response escaped the reviewed endpoint")
    text = str(getattr(response, "text", "") or "")
    if not text and getattr(response, "content", None):
        text = bytes(response.content).decode("utf-8", errors="replace")
    if not text:
        raise IncheonReservationContractError("empty public response")
    return BeautifulSoup(text, "lxml")


class _Requester:
    def __init__(self, session_factory: SessionFactory, timeout: int, max_list_calls: int) -> None:
        self.session = session_factory()
        self.timeout = timeout
        self.max_list_calls = max_list_calls
        self.requests = 0
        self.list_requests = 0
        self.detail_requests = 0
        headers = getattr(self.session, "headers", None)
        if headers is not None and hasattr(headers, "update"):
            headers.update(
                {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
                }
            )

    def close(self) -> None:
        _close_quietly(self.session)

    def soup(self, url: str, *, kind: str, referer: str = "") -> BeautifulSoup:
        _assert_safe_public_url(url)
        if self.requests >= INCHEON_REQUEST_BUDGET:
            raise IncheonReservationContractError("audited request budget exceeded")
        if kind == "list" and self.list_requests >= self.max_list_calls:
            raise IncheonReservationContractError("max_pages list budget exhausted")
        headers = {"Referer": referer} if referer else None
        response = self.session.get(
            url,
            timeout=self.timeout,
            allow_redirects=False,
            headers=headers,
        )
        self.requests += 1
        if kind == "list":
            self.list_requests += 1
        elif kind == "detail":
            self.detail_requests += 1
        else:
            raise IncheonReservationContractError("unknown request kind")
        return _response_soup(response, url)


def _form_schema(soup: BeautifulSoup, catalogue: IncheonCatalogue) -> None:
    section = soup.select_one("#onlineSection")
    forms = soup.select("form#searchFrm")
    if section is None or len(forms) != 1:
        raise IncheonReservationContractError("public list section/form changed")
    form = forms[0]
    method = _clean(form.get("method")).lower()
    action = urlparse(urljoin(INCHEON_RESERVATION_ROOT, _clean(form.get("action"))))
    if method != "get" or action.path != catalogue.list_path:
        raise IncheonReservationContractError("public list form endpoint changed")
    hidden = {
        _clean(node.get("name")): _clean(node.get("value"))
        for node in form.select("input[type=hidden][name]")
    }
    if not _LIST_HIDDEN_FIELDS.issubset(hidden) or any(
        hidden[name] for name in _LIST_HIDDEN_FIELDS
    ):
        raise IncheonReservationContractError("public list hidden contract changed")
    options: list[tuple[str, str]] = []
    for node in form.select("input[type=radio][name=sortType]"):
        identity = _clean(node.get("id"))
        label = form.select_one(f'label[for="{identity}"]') if identity else None
        options.append(
            (_clean(node.get("value")), _clean(label.get_text(" ", strip=True) if label else ""))
        )
    expected = (
        _EDUCATION_SORT_OPTIONS
        if catalogue is INCHEON_EDUCATION
        else _EXPERIENCE_SORT_OPTIONS
    )
    if tuple(options) != expected:
        raise IncheonReservationContractError("public list sort/filter contract changed")


def _declared_pages(soup: BeautifulSoup, *, allow_missing_for_empty: bool = False) -> int:
    nodes = soup.select("#onlineSection .pagination .num-page-total em")
    if not nodes and allow_missing_for_empty:
        return 1
    if len(nodes) != 1:
        raise IncheonReservationContractError("declared total-pages marker changed")
    value = _clean(nodes[0].get_text(" ", strip=True))
    if not _DIGITS_RE.fullmatch(value):
        raise IncheonReservationContractError("invalid declared total-pages value")
    return int(value)


def _date_range(value: Any, label: str) -> tuple[date, date]:
    values = [
        date(int(year), int(month), int(day))
        for year, month, day in _DATE_RE.findall(_clean(value))
    ]
    if len(values) < 2:
        raise IncheonReservationContractError(f"{label} has no complete date range")
    start, end = values[-2], values[-1]
    if end < start:
        raise IncheonReservationContractError(f"{label} is reversed")
    return start, end


def _dl_fields(node: Tag) -> dict[str, str]:
    result: dict[str, str] = {}
    for dl in node.select("dl"):
        dt = dl.find("dt", recursive=False)
        dd = dl.find("dd", recursive=False)
        if dt is None or dd is None:
            continue
        key = _clean(dt.get_text(" ", strip=True))
        if key:
            if key in result:
                raise IncheonReservationContractError(f"duplicate field {key!r}")
            result[key] = _clean(dd.get_text(" ", strip=True))
    return result


def _labeled_groups(dd: Tag) -> dict[str, str]:
    result: dict[str, str] = {}
    for group in dd.select(".item-data-group"):
        label_node = group.select_one("span.item")
        if label_node is None:
            continue
        label = _clean(label_node.get_text(" ", strip=True))
        clone = BeautifulSoup(str(group), "lxml").select_one(".item-data-group")
        if clone is None:
            continue
        cloned_label = clone.select_one("span.item")
        if cloned_label is not None:
            cloned_label.decompose()
        result[label] = _clean(clone.get_text(" ", strip=True))
    return result


def _title_without_badges(node: Tag) -> str:
    clone = BeautifulSoup(str(node), "lxml").find(node.name)
    if clone is None:
        return ""
    for badge in clone.select("i"):
        badge.decompose()
    return _clean(clone.get_text(" ", strip=True))


def _normalize_detail_link(
    catalogue: IncheonCatalogue, href: Any
) -> tuple[str, str, str, str]:
    url = incheon_detail_url(catalogue, href)
    if not url:
        raise IncheonReservationContractError("row detail link escaped the public detail route")
    query = parse_qs(urlparse(url).query, keep_blank_values=True)
    group_id = _single_query(query, "resveGroupSn")
    program_id = _single_query(query, "progrmSn")
    identity = f"{catalogue.kind}:{group_id}:{program_id}"
    return url, identity, group_id, program_id


def _parse_card(
    card: Tag,
    catalogue: IncheonCatalogue,
    page: int,
    position: int,
) -> _ListedRow:
    anchors = card.find_all("a", href=True, recursive=False)
    if len(anchors) != 1:
        raise IncheonReservationContractError("list card detail-anchor contract changed")
    raw_url, identity, group_id, program_id = _normalize_detail_link(
        catalogue, anchors[0].get("href")
    )
    heading = card.select_one("strong.reservation-name")
    institution_node = card.select_one("span.institution")
    if heading is None or institution_node is None:
        raise IncheonReservationContractError("list card title/institution changed")
    title = _title_without_badges(heading)
    institution = _clean(institution_node.get_text(" ", strip=True))
    if not title or not institution:
        raise IncheonReservationContractError("list card title/institution is empty")
    non_program_reason = "notice" if _NOTICE_RE.search(title) else ""
    badges = [_clean(node.get_text(" ", strip=True)) for node in heading.select("i.accept")]
    statuses = [badge for badge in badges if badge in _STATUS_MAP]
    fees = [badge for badge in badges if badge in {"무료", "유료"}]
    if non_program_reason:
        return _ListedRow(
            catalogue=catalogue,
            identity=identity,
            group_id=group_id,
            program_id=program_id,
            page=page,
            position=position,
            title=title,
            institution=institution,
            source_status=statuses[0] if len(statuses) == 1 else "",
            status=_STATUS_MAP.get(statuses[0], "INFO") if len(statuses) == 1 else "INFO",
            fee=fees[0] if len(fees) == 1 else "",
            venue="",
            target="",
            apply_start=None,
            apply_end=None,
            start_date=None,
            end_date=None,
            raw_url=raw_url,
            non_program_reason=non_program_reason,
        )
    if len(statuses) != 1 or len(fees) != 1:
        raise IncheonReservationContractError("list status/fee badge contract changed")
    data = card.select_one(".item-data-wrap")
    if data is None:
        raise IncheonReservationContractError("list card data block changed")
    fields = _dl_fields(data)
    required = {"기관", "장소", "일자", "문의"}
    if catalogue is INCHEON_EDUCATION:
        required.add("대상")
    missing = sorted(required.difference(fields))
    if missing:
        raise IncheonReservationContractError(f"list card missing fields {missing!r}")
    if _normalized(fields["기관"]) != _normalized(institution):
        raise IncheonReservationContractError("list institution mismatch")
    date_dl = next(
        (
            dl
            for dl in data.select("dl")
            if _clean(dl.dt.get_text(" ", strip=True) if dl.dt else "") == "일자"
        ),
        None,
    )
    if date_dl is None or date_dl.dd is None:
        raise IncheonReservationContractError("list date groups changed")
    groups = _labeled_groups(date_dl.dd)
    if set(("신청", catalogue.event_label)).difference(groups):
        raise IncheonReservationContractError("list date labels changed")
    apply_start, apply_end = _date_range(groups["신청"], "list application period")
    start_date, end_date = _date_range(
        groups[catalogue.event_label], "list operation period"
    )
    place_dl = next(
        (
            dl
            for dl in data.select("dl")
            if _clean(dl.dt.get_text(" ", strip=True) if dl.dt else "") == "장소"
        ),
        None,
    )
    if place_dl is None or place_dl.dd is None:
        raise IncheonReservationContractError("list place structure changed")
    place_groups = place_dl.dd.select(".item-data-group")
    venue = (
        _clean(place_groups[0].get_text(" ", strip=True))
        if place_groups
        else _clean(place_dl.dd.get_text(" ", strip=True))
    )
    if not venue:
        raise IncheonReservationContractError("list venue is empty")
    return _ListedRow(
        catalogue=catalogue,
        identity=identity,
        group_id=group_id,
        program_id=program_id,
        page=page,
        position=position,
        title=title,
        institution=institution,
        source_status=statuses[0],
        status=_STATUS_MAP[statuses[0]],
        fee=fees[0],
        venue=venue,
        target=fields.get("대상", ""),
        apply_start=apply_start,
        apply_end=apply_end,
        start_date=start_date,
        end_date=end_date,
        raw_url=raw_url,
    )


def _parse_list_page(
    soup: BeautifulSoup,
    catalogue: IncheonCatalogue,
    page: int,
    *,
    allow_empty: bool,
) -> tuple[list[_ListedRow], int]:
    _form_schema(soup, catalogue)
    nodes = soup.select(
        "#onlineSection .search-list-wrap ul.img-wrap-1117 > li"
    )
    if not nodes:
        raise IncheonReservationContractError("public list card container changed")
    cards = [node for node in nodes if node.find("a", href=True, recursive=False)]
    no_data = [node for node in nodes if node.select_one(".board-nodata")]
    declared_pages = _declared_pages(
        soup, allow_missing_for_empty=bool(no_data)
    )
    if cards and no_data:
        raise IncheonReservationContractError("program and no-data cards are mixed")
    if no_data:
        if len(nodes) != 1 or _clean(no_data[0].get_text(" ", strip=True)) != _NO_DATA_TEXT:
            raise IncheonReservationContractError("no-data sentinel contract changed")
        if not allow_empty:
            raise IncheonReservationContractError("declared data page became empty")
        return [], declared_pages
    if not cards or len(cards) != len(nodes):
        raise IncheonReservationContractError("unknown list row shape")
    if allow_empty:
        raise IncheonReservationContractError("post-final sentinel is no longer empty")
    active = soup.select("#onlineSection .pagination a.active")
    if len(active) != 1 or _clean(active[0].get_text(" ", strip=True)) != str(page):
        raise IncheonReservationContractError("active pagination marker changed")
    return [
        _parse_card(card, catalogue, page, position)
        for position, card in enumerate(cards, start=1)
    ], declared_pages


def _signature(rows: Iterable[_ListedRow]) -> str:
    payload = "\n".join(
        f"{row.identity}|{row.title}|{row.source_status}" for row in rows
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _collect_list_snapshot(
    requester: _Requester,
    catalogue: IncheonCatalogue,
    *,
    district_source_code: str = "",
) -> _ListSnapshot:
    before = requester.list_requests
    first_url = incheon_list_url(
        catalogue, 1, district_source_code=district_source_code
    )
    first_soup = requester.soup(first_url, kind="list")
    # A district filter is authoritative only when the server keeps it selected.
    if district_source_code:
        checked = first_soup.select(
            "form#searchFrm input[name=sortType][checked]"
        )
        checked_values = {_clean(node.get("value")) for node in checked}
        # The live template leaves the default ``전체`` radio checked while
        # also checking the requested district.  Retain that reviewed quirk,
        # but reject any third selected value or a missing requested value.
        if (
            district_source_code not in checked_values
            or not checked_values.issubset({"", district_source_code})
        ):
            raise IncheonReservationContractError("district filter was not retained")
    first_nodes = first_soup.select(
        "#onlineSection .search-list-wrap ul.img-wrap-1117 > li"
    )
    first_empty = bool(first_nodes and first_nodes[0].select_one(".board-nodata"))
    first_rows, total_pages = _parse_list_page(
        first_soup, catalogue, 1, allow_empty=first_empty
    )
    if total_pages < 1:
        raise IncheonReservationContractError("declared total pages must be positive")
    all_rows = list(first_rows)
    boundaries: dict[int, str] = {1: _signature(first_rows)}
    if first_empty and total_pages != 1:
        raise IncheonReservationContractError("empty list declares multiple data pages")
    for page in range(2, total_pages + 1):
        url = incheon_list_url(
            catalogue, page, district_source_code=district_source_code
        )
        soup = requester.soup(url, kind="list", referer=first_url)
        rows, declared = _parse_list_page(soup, catalogue, page, allow_empty=False)
        if declared != total_pages:
            raise IncheonReservationContractError("declared total pages drifted")
        all_rows.extend(rows)
        boundaries[page] = _signature(rows)
    if all_rows:
        for page in range(1, total_pages):
            count = sum(row.page == page for row in all_rows)
            if count != INCHEON_PAGE_SIZE:
                raise IncheonReservationContractError("non-final data page is not full")
        final_count = sum(row.page == total_pages for row in all_rows)
        if not 1 <= final_count <= INCHEON_PAGE_SIZE:
            raise IncheonReservationContractError("invalid final-page row count")
        expected_pages = math.ceil(len(all_rows) / INCHEON_PAGE_SIZE)
        if expected_pages != total_pages:
            raise IncheonReservationContractError("row count does not reconcile with pages")
    identities = [row.identity for row in all_rows]
    if len(identities) != len(set(identities)):
        raise IncheonReservationContractError("duplicate source identity in list")
    sentinel_page = total_pages + 1
    sentinel_url = incheon_list_url(
        catalogue, sentinel_page, district_source_code=district_source_code
    )
    sentinel = requester.soup(sentinel_url, kind="list", referer=first_url)
    _, declared = _parse_list_page(
        sentinel, catalogue, sentinel_page, allow_empty=True
    )
    if declared != total_pages:
        raise IncheonReservationContractError("sentinel declared pages drifted")
    return _ListSnapshot(
        rows=tuple(all_rows),
        total_pages=total_pages,
        sentinel_page=sentinel_page,
        boundaries=boundaries,
        list_requests=requester.list_requests - before,
    )


def _experience_district_membership(
    requester: _Requester,
    global_snapshot: _ListSnapshot,
) -> tuple[dict[str, IncheonDistrict], dict[str, int], int]:
    global_rows = {row.identity: row for row in global_snapshot.rows}
    membership: dict[str, list[IncheonDistrict]] = defaultdict(list)
    totals: dict[str, int] = {}
    requests_before = requester.list_requests
    for district in INCHEON_EXPERIENCE_DISTRICTS:
        snapshot = _collect_list_snapshot(
            requester,
            INCHEON_EXPERIENCE,
            district_source_code=district.source_code,
        )
        totals[district.source_code] = len(snapshot.rows)
        for row in snapshot.rows:
            global_row = global_rows.get(row.identity)
            if global_row is None:
                raise IncheonReservationContractError(
                    "district filter contains an identity absent from the global list"
                )
            if _normalized(global_row.title) != _normalized(row.title):
                raise IncheonReservationContractError(
                    "district/global title mismatch"
                )
            membership[row.identity].append(district)
    if sum(totals.values()) > len(global_rows):
        raise IncheonReservationContractError(
            "district totals exceed the global list"
        )
    result: dict[str, IncheonDistrict] = {}
    for identity in global_rows:
        owners = membership.get(identity, [])
        if len(owners) > 1:
            raise IncheonReservationContractError(
                "global experience row belongs to multiple district filters"
            )
        if owners:
            result[identity] = owners[0]
    return result, totals, requester.list_requests - requests_before


def _recheck_boundaries(
    requester: _Requester,
    catalogue: IncheonCatalogue,
    snapshot: _ListSnapshot,
) -> int:
    before = requester.list_requests
    for page in sorted({1, snapshot.total_pages}):
        soup = requester.soup(incheon_list_url(catalogue, page), kind="list")
        rows, declared = _parse_list_page(
            soup, catalogue, page, allow_empty=not snapshot.rows
        )
        if declared != snapshot.total_pages or _signature(rows) != snapshot.boundaries[page]:
            raise IncheonReservationContractError("global list boundary changed during crawl")
    return requester.list_requests - before


def _detail_fields(soup: BeautifulSoup) -> dict[str, str]:
    detail = soup.select_one(".cont-view-board .detail-txt-area")
    if detail is None:
        raise IncheonReservationContractError("detail field container changed")
    return _dl_fields(detail)


def _address_from_detail(soup: BeautifulSoup) -> tuple[str, str]:
    headings = [
        node
        for node in soup.select("form#frm h5.h6")
        if _clean(node.get_text(" ", strip=True)) == "주소(위치정보)"
    ]
    if len(headings) != 1:
        raise IncheonReservationContractError("detail address heading changed")
    values = headings[0].find_next_sibling("ul")
    address = _clean(values.get_text(" ", strip=True) if values else "")
    route_heading = next(
        (
            node
            for node in soup.select("form#frm h5.h6")
            if _clean(node.get_text(" ", strip=True)) == "찾아오시는길"
        ),
        None,
    )
    route_values = route_heading.find_next_sibling("ul") if route_heading else None
    route = _clean(route_values.get_text(" ", strip=True) if route_values else "")
    if not address:
        raise IncheonReservationContractError("detail address is empty")
    return address, route


def _municipality_from_address(value: Any) -> IncheonDistrict:
    text = _clean(value)
    direct = sorted(
        INCHEON_EXPERIENCE_DISTRICTS,
        key=lambda item: len(item.label),
        reverse=True,
    )
    for district in direct:
        if district.label in text:
            return district
    # Legacy address strings can remain in older programme records after the
    # 2026-07-01 administrative reorganisation.  The aliases below are only
    # used with an explicit Incheon address and keep the new municipality code.
    if "인천" not in text:
        raise IncheonReservationContractError("detail address is outside Incheon")
    if re.search(r"(?:동구|중구)", text):
        if any(
            marker in text
            for marker in (
                "영종", "용유", "운서", "운남", "운북", "중산", "을왕", "덕교", "남북", "무의",
            )
        ):
            return INCHEON_DISTRICT_BY_CODE["2815500000"]
        return INCHEON_DISTRICT_BY_CODE["2812500000"]
    if "서구" in text:
        if any(
            marker in text
            for marker in (
                "검단", "원당", "마전", "당하", "불로", "오류", "왕길", "금곡", "대곡",
            )
        ):
            return INCHEON_DISTRICT_BY_CODE["2829000000"]
        return INCHEON_DISTRICT_BY_CODE["2827500000"]
    raise IncheonReservationContractError("detail address has no 2026 district evidence")


def _capacity(value: Any) -> tuple[Optional[int], Optional[int]]:
    text = _clean(value)
    total = re.search(r"(?:정원|최대)\s*([\d,]+)\s*명", text)
    current = re.search(r"예약\s*([\d,]+)\s*명", text)
    return (
        int(current.group(1).replace(",", "")) if current else None,
        int(total.group(1).replace(",", "")) if total else None,
    )


def _redact(value: Any) -> str:
    return _clean(_EMAIL_RE.sub(" ", _PHONE_RE.sub(" ", _clean(value))))


def _branch_code(branch: str, municipality_code: str) -> str:
    digest = hashlib.sha1(
        f"{municipality_code}|{_clean(branch)}".encode("utf-8")
    ).hexdigest()[:12].upper()
    return f"INCHEON_{municipality_code}_{digest}"[:50]


def _row_from_detail(
    target: Any,
    listed: _ListedRow,
    soup: BeautifulSoup,
    *,
    district: Optional[IncheonDistrict],
) -> dict[str, Any]:
    board = soup.select_one("form#frm .cont-view-board")
    title_node = soup.select_one("form#frm .cont-view-board-title h4")
    institution_node = soup.select_one("form#frm .detail-img-area .institution")
    if board is None or title_node is None or institution_node is None:
        raise IncheonReservationContractError("public detail structure changed")
    detail_title = _title_without_badges(title_node)
    detail_statuses = [
        _clean(node.get_text(" ", strip=True))
        for node in title_node.select("i.accept")
        if _clean(node.get_text(" ", strip=True)) in _STATUS_MAP
    ]
    if _normalized(detail_title) != _normalized(listed.title):
        raise IncheonReservationContractError("detail/list title mismatch")
    if len(detail_statuses) != 1 or detail_statuses[0] != listed.source_status:
        raise IncheonReservationContractError("detail/list status mismatch")
    institution = _clean(institution_node.get_text(" ", strip=True))
    if _normalized(institution) != _normalized(listed.institution):
        raise IncheonReservationContractError("detail/list institution mismatch")
    fields = _detail_fields(soup)
    required = {listed.catalogue.event_detail_field, "신청기간", "문의전화"}
    if listed.catalogue is INCHEON_EDUCATION:
        required.update({"수강료", "수강신청방법", "수강대상", "교육장소/수강정원"})
    else:
        required.update({"신청방법", "신청가능인원", "대상"})
    missing = sorted(required.difference(fields))
    if missing:
        raise IncheonReservationContractError(f"detail missing required fields {missing!r}")
    apply_start, apply_end = _date_range(fields["신청기간"], "detail application period")
    start_date, end_date = _date_range(
        fields[listed.catalogue.event_detail_field], "detail operation period"
    )
    if (
        (apply_start, apply_end) != (listed.apply_start, listed.apply_end)
        or (start_date, end_date) != (listed.start_date, listed.end_date)
    ):
        raise IncheonReservationContractError("detail/list date range mismatch")
    address, route = _address_from_detail(soup)
    address_district = _municipality_from_address(
        f"{address} {route} {listed.venue} {institution}"
    )
    # ``sortType`` is an official catalogue-area classification, while the
    # public detail address is the physical programme location.  Multi-area
    # programmes can intentionally be filed under one area while running at a
    # different public facility.  Prefer the exact detail address for Ops map
    # attribution and retain both pieces of evidence for audit.
    filter_matches_address = district is None or district == address_district
    municipality = address_district
    controls = [
        node
        for node in board.select("#btn_appl")
        if _clean(node.get_text(" ", strip=True)) == "예약하기"
    ]
    if len(controls) > 1:
        raise IncheonReservationContractError("multiple application controls found")
    has_application = bool(controls) and listed.source_status in _APPLICATION_STATUSES
    if listed.catalogue is INCHEON_EDUCATION:
        detail_fee = fields["수강료"]
        if (listed.fee == "무료") != ("무료" in detail_fee):
            raise IncheonReservationContractError("detail/list fee mismatch")
        target_label = fields["수강대상"]
        venue_name = re.sub(r"\s*/\s*[\d,]+\s*명\s*$", "", fields["교육장소/수강정원"])
        method = fields["수강신청방법"]
        capacity_value = fields.get("신청인원", "") or fields["교육장소/수강정원"]
        schedule_raw = fields.get("요일/시간", "")
    else:
        detail_fee = listed.fee
        target_label = fields["대상"]
        venue_name = listed.venue
        method = fields["신청방법"]
        capacity_value = fields["신청가능인원"]
        schedule_raw = fields.get("신청가능요일", "")
    if _normalized(venue_name) and _normalized(listed.venue):
        left, right = _normalized(venue_name), _normalized(listed.venue)
        if listed.catalogue is INCHEON_EDUCATION and left != right:
            raise IncheonReservationContractError("detail/list venue mismatch")
    current_capacity, total_capacity = _capacity(capacity_value)
    provider = _clean(_target_value(target, "provider")).upper()
    description = _redact(
        f"{listed.title} | {institution} | {venue_name} | "
        f"{start_date.isoformat()} ~ {end_date.isoformat()}"
    )
    return {
        "provider": provider,
        "provider_course_id": (
            f"{provider}:{listed.catalogue.kind}:{listed.group_id}:{listed.program_id}"
        )[:100],
        "prefer_incoming_provider_course_id": True,
        "title": listed.title,
        "branch": institution,
        "branch_code": _branch_code(institution, municipality.municipality_code),
        "preserve_branch": True,
        "branch_url": listed.catalogue.canonical_url,
        "category": listed.catalogue.domain_category,
        "program_type": listed.catalogue.program_type,
        "raw_url": listed.raw_url,
        "application_url": listed.raw_url if has_application else "",
        "application_type": "ONLINE_RESERVATION" if has_application else "INFO_ONLY",
        "application_method_raw": method,
        "reservation_available": has_application,
        "status": listed.status,
        "fee": detail_fee,
        "period": f"{start_date.isoformat()} ~ {end_date.isoformat()}",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "apply_period": f"{apply_start.isoformat()} ~ {apply_end.isoformat()}",
        "apply_start": apply_start.isoformat(),
        "apply_end": apply_end.isoformat(),
        "schedule_raw": schedule_raw or f"{start_date.isoformat()} ~ {end_date.isoformat()}",
        "target": target_label,
        "capacity": (
            f"{current_capacity}/{total_capacity}"
            if current_capacity is not None and total_capacity is not None
            else f"정원 {total_capacity}명"
            if total_capacity is not None
            else ""
        ),
        "capacity_current": current_capacity,
        "capacity_total": total_capacity,
        "venue_name": venue_name,
        "venue_address": address,
        "address": address,
        "description": description,
        "collection_category": "공공예약",
        "domain_category": listed.catalogue.domain_category,
        "operator_type": "지자체/공공기관",
        "source_group": "public_reservation",
        "service_group": listed.catalogue.service_group,
        "service_group_policy": "locked",
        "collection_type": listed.catalogue.parser,
        "municipality_code": municipality.municipality_code,
        "municipality_full_name": municipality.municipality_full_name,
        "raw_fields": {
            "parser": listed.catalogue.parser,
            "ownership_scope": listed.catalogue.ownership_scope,
            "source_identity": listed.identity,
            "source_group_id": listed.group_id,
            "source_program_id": listed.program_id,
            "source_status": listed.source_status,
            "source_page": listed.page,
            "source_position": listed.position,
            "district_filter_code": district.source_code if district else "",
            "district_filter_label": district.label if district else "",
            "district_filter_matches_address": filter_matches_address,
            "district_filter_conflict": bool(district and not filter_matches_address),
            "detail_address": address,
            "application_control_present": bool(controls),
            "application_endpoint_fetched": False,
            "login_endpoint_fetched": False,
            "pii_endpoint_fetched": False,
            "full_detail_contract": True,
        },
    }


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _failure(
    catalogue: Optional[IncheonCatalogue], message: str, **extra: Any
) -> dict[str, Any]:
    return {
        "pages": 0,
        "request_count": 0,
        "list_requests": 0,
        "district_list_requests": 0,
        "list_recheck_requests": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "source_total": 0,
        "unique_id_count": 0,
        "current_count": 0,
        "returned_count": 0,
        "explicit_non_program_count": 0,
        "notice_count": 0,
        "pagination_complete": False,
        "district_reconciled": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "canonical_provider": INCHEON_RESERVATION_PROVIDER,
        "canonical_url": catalogue.canonical_url if catalogue else "",
        "catalogue_kind": catalogue.kind if catalogue else "",
        "ownership_scope": catalogue.ownership_scope if catalogue else "",
        "covered_municipalities": [
            dict(row) for row in INCHEON_COVERED_MUNICIPALITIES
        ],
        "row_municipality_codes": list(INCHEON_ROW_MUNICIPALITY_CODES),
        "notice_board_requests": 0,
        "application_endpoint_requests": 0,
        "application_endpoints_called": 0,
        "authentication_endpoint_requests": 0,
        "pii_endpoint_requests": 0,
        "http_methods": ["GET"],
        "configured_collection_error": message,
        **extra,
    }


def collect_incheon_reservations(
    target: Any,
    timeout: int = 30,
    max_pages: int = INCHEON_DEFAULT_MAX_PAGES,
    detail_limit: int = INCHEON_DEFAULT_DETAIL_LIMIT,
    *,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    today: Optional[date | datetime | str] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one exact Incheon education or experience sibling target."""

    catalogue = incheon_catalogue_for_target(target)
    parser = catalogue.parser if catalogue else INCHEON_EDUCATION_PARSER
    if catalogue is None:
        return [], parser, _failure(
            None, "target does not match an exact reviewed Incheon catalogue"
        )
    try:
        page_cap = int(max_pages)
        detail_cap = int(detail_limit)
        timeout_value = int(timeout)
        cutoff = _today(today)
    except (TypeError, ValueError, IncheonReservationContractError) as exc:
        return [], parser, _failure(
            catalogue,
            f"invalid collection arguments: {type(exc).__name__}: {_clean(exc)}",
        )
    if page_cap < 1 or detail_cap < 0 or timeout_value < 1:
        return [], parser, _failure(
            catalogue, "collection caps are invalid", source_cap_reached=True
        )
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], parser, _failure(
                catalogue,
                "session_factory is required for managed production collection",
            )
        session_factory = _default_session_factory

    requester: Optional[_Requester] = None
    snapshot: Optional[_ListSnapshot] = None
    district_membership: dict[str, IncheonDistrict] = {}
    district_totals: dict[str, int] = {}
    district_requests = 0
    list_rechecks = 0
    detail_attempts = 0
    errors: list[str] = []
    rows: list[dict[str, Any]] = []
    source_cap_reached = False
    try:
        requester = _Requester(session_factory, timeout_value, page_cap)
        snapshot = _collect_list_snapshot(requester, catalogue)
        if catalogue is INCHEON_EXPERIENCE:
            (
                district_membership,
                district_totals,
                district_requests,
            ) = _experience_district_membership(requester, snapshot)
        current_records = [row for row in snapshot.rows if row.current_on(cutoff)]
        non_program = [row for row in snapshot.rows if row.non_program_reason]
        if len(current_records) > detail_cap:
            source_cap_reached = True
            raise IncheonReservationContractError(
                f"detail_limit={detail_cap} is below current rows={len(current_records)}"
            )
        detail_attempts = len(current_records)
        for listed in current_records:
            soup = requester.soup(
                listed.raw_url,
                kind="detail",
                referer=catalogue.canonical_url,
            )
            rows.append(
                _row_from_detail(
                    target,
                    listed,
                    soup,
                    district=district_membership.get(listed.identity),
                )
            )
        list_rechecks = _recheck_boundaries(requester, catalogue, snapshot)
        current_dedupe = dedupe_rows or _dedupe_default
        deduped = list(current_dedupe(rows))
        if len(deduped) != len(rows):
            raise IncheonReservationContractError(
                f"dedupe changed complete row count {len(rows)} to {len(deduped)}"
            )
        rows = deduped
        municipality_counts = Counter(row["municipality_code"] for row in rows)
        branch_counts = Counter(row["branch"] for row in rows)
        current_identities = {record.identity for record in current_records}
        filter_attributed_current = sum(
            identity in current_identities for identity in district_membership
        )
        filter_conflicts = sum(
            bool((row.get("raw_fields") or {}).get("district_filter_conflict"))
            for row in rows
        )
        meta = {
            "pages": snapshot.total_pages,
            "sentinel_page": snapshot.sentinel_page,
            "request_count": requester.requests,
            "list_requests": requester.list_requests,
            "district_list_requests": district_requests,
            "list_recheck_requests": list_rechecks,
            "detail_attempts": detail_attempts,
            "detail_pages": requester.detail_requests,
            "source_total": len(snapshot.rows),
            "unique_id_count": len({row.identity for row in snapshot.rows}),
            "current_count": len(current_records),
            "returned_count": len(rows),
            "expired_count": sum(
                row.end_date is not None and row.end_date < cutoff
                for row in snapshot.rows
                if not row.non_program_reason
            ),
            "explicit_non_program_count": len(non_program),
            "notice_count": sum(
                row.non_program_reason == "notice" for row in non_program
            ),
            "pagination_complete": True,
            "district_reconciled": True,
            "district_source_totals": district_totals,
            "district_filter_attributed_count": filter_attributed_current,
            "district_address_fallback_count": (
                len(current_records) - filter_attributed_current
                if catalogue is INCHEON_EXPERIENCE
                else 0
            ),
            "district_filter_conflict_count": filter_conflicts,
            "stable_first_page": True,
            "stable_final_page": True,
            "details_complete": requester.detail_requests == len(current_records),
            "snapshot_complete": True,
            "full_snapshot_validated": True,
            "source_cap_reached": False,
            "no_current_data": not current_records,
            "no_current_reason": (
                "official complete snapshot contains no current programmes"
                if not current_records
                else ""
            ),
            "canonical_provider": INCHEON_RESERVATION_PROVIDER,
            "canonical_url": catalogue.canonical_url,
            "catalogue_kind": catalogue.kind,
            "ownership_scope": catalogue.ownership_scope,
            "covered_municipalities": [
                dict(item) for item in INCHEON_COVERED_MUNICIPALITIES
            ],
            "row_municipality_codes": list(INCHEON_ROW_MUNICIPALITY_CODES),
            "municipality_counts": dict(sorted(municipality_counts.items())),
            "branch_counts": dict(sorted(branch_counts.items())),
            "notice_board_requests": 0,
            "application_endpoint_requests": 0,
            "authentication_endpoint_requests": 0,
            "pii_endpoint_requests": 0,
            "application_endpoints_called": 0,
            "http_methods": ["GET"],
            "configured_collection_error": "",
        }
        return rows, parser, meta
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {_clean(exc)}")
        return [], parser, _failure(
            catalogue,
            "; ".join(errors),
            pages=snapshot.total_pages if snapshot else 0,
            request_count=requester.requests if requester else 0,
            list_requests=requester.list_requests if requester else 0,
            district_list_requests=district_requests,
            list_recheck_requests=list_rechecks,
            detail_attempts=detail_attempts,
            detail_pages=requester.detail_requests if requester else 0,
            source_total=len(snapshot.rows) if snapshot else 0,
            unique_id_count=(
                len({row.identity for row in snapshot.rows}) if snapshot else 0
            ),
            district_source_totals=district_totals,
            source_cap_reached=source_cap_reached,
        )
    finally:
        if requester is not None:
            requester.close()


collect = collect_incheon_reservations
