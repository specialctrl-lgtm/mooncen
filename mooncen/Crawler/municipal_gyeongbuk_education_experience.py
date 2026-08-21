"""Fail-closed collector for the Gyeongbuk education experience portal.

The Gyeongsangbuk-do Office of Education integrated reservation site exposes
one public ``견학/체험`` ledger.  This collector reads only that exact list and
its public information details.  It never follows the application controls,
authentication routes, applicant/member routes, calendar AJAX routes, file
attachments, or downloads.

Rows are attributed to a municipality only when the exact operating
institution has an audited official venue address.  Programmes delivered at a
school selected by the applicant are retained in source accounting but are
not emitted because their real venue municipality is not known from the
public programme identity.  The portal's 22 ``예약지역`` values are eligibility
filters, not venue evidence, and are deliberately ignored for attribution.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import html
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import requests


GYEONGBUK_EDU_EXPERIENCE_PROVIDER = "MUNI_WWW_GBE_KR_E9340F09"
GYEONGBUK_EDU_EXPERIENCE_CANDIDATE_ID = "MUNI_IR_40D8191FCB59"
GYEONGBUK_EDU_EXPERIENCE_HOST = "www.gbe.kr"
GYEONGBUK_EDU_EXPERIENCE_LIST_PATH = (
    "/edushare/exprn/selectExprnList.do"
)
GYEONGBUK_EDU_EXPERIENCE_DETAIL_PATH = (
    "/edushare/exprn/selectExprnInfo.do"
)
GYEONGBUK_EDU_EXPERIENCE_URL = (
    "https://www.gbe.kr/edushare/exprn/selectExprnList.do?mi=17609"
)
GYEONGBUK_EDU_EXPERIENCE_POST_URL = (
    "https://www.gbe.kr/edushare/exprn/selectExprnList.do"
)
GYEONGBUK_EDU_EXPERIENCE_PAGE_SIZE = 10
GYEONGBUK_EDU_EXPERIENCE_MI = "17609"
GYEONGBUK_EDU_EXPERIENCE_OWNERSHIP_SCOPE = (
    "gyeongbuk_education_office_integrated_reservation_exact_experience_"
    "ledger_with_verified_fixed_venues"
)
GYEONGBUK_EDU_EXPERIENCE_PARSER = (
    "gbe_edushare_complete_experience_ledger+declared_total_and_pages+"
    "fixed_paging_form_posts+exact_empty_post_last_sentinel+"
    "stable_first_last_sentinel_rechecks+all_current_public_details+"
    "exact_institution_official_venue_registry+variable_school_venue_exclusion+"
    "nonproduction_test_owner_exclusion+eligibility_regions_not_venue+"
    "locked_experience+public_list_and_detail_only+"
    "no_application_login_auth_identity_applicant_member_pii_calendar_"
    "attachment_or_download_calls"
)
GYEONGBUK_EDU_EXPERIENCE_MAX_BYTES = 1_000_000

GYEONGBUK_EDU_EXPERIENCE_LIVE_BASELINE: Mapping[str, Any] = {
    "checked_at": "2026-08-05",
    "source_total": 145,
    "data_pages": 15,
    "current_count": 82,
    "expired_count": 63,
    "returned_count": 75,
    "excluded_nonproduction_count": 1,
    "excluded_variable_venue_count": 6,
    "detail_pages": 82,
    "municipality_counts": {
        "4711000000": 6,
        "4713000000": 21,
        "4717000000": 1,
        "4725000000": 20,
        "4729000000": 16,
        "4773000000": 5,
        "4785000000": 6,
    },
    "status_counts": {"OPEN": 49, "CLOSED": 22, "SCHEDULED": 4},
}


@dataclass(frozen=True)
class _Venue:
    municipality_code: str
    municipality_name: str
    sigungu: str
    venue_name: str
    address: str
    evidence_url: str


# Every address is published by the named Gyeongsangbuk-do Office of Education
# institution.  Similar-looking operators are intentionally not inferred.
GYEONGBUK_EDU_EXPERIENCE_VENUES: Mapping[str, _Venue] = {
    "상주수학체험센터": _Venue(
        "4725000000",
        "경상북도 상주시",
        "상주시",
        "경상북도교육청 상주수학체험센터",
        "경상북도 상주시 왕산로 41",
        "https://www.gbe.kr/sjmath/main.do",
    ),
    "경주안전체험관": _Venue(
        "4713000000",
        "경상북도 경주시",
        "경주시",
        "경상북도교육청 경주안전체험관",
        "경상북도 경주시 안강읍 안현로 1853-12",
        "https://www.gbe.kr/gjsafe/main.do",
    ),
    "경상북도교육청남부미래교육관": _Venue(
        "4729000000",
        "경상북도 경산시",
        "경산시",
        "경상북도교육청 남부미래교육관",
        "경상북도 경산시 삼성현로 110",
        "https://www.gbe.kr/mirae/main.do",
    ),
    "안동수학체험센터": _Venue(
        "4717000000",
        "경상북도 안동시",
        "안동시",
        "경상북도교육청 안동수학체험센터",
        "경상북도 안동시 풍천면 지풍로 1434-11",
        "https://www.gbe.kr/admath/main.do",
    ),
    "칠곡수학체험센터": _Venue(
        "4785000000",
        "경상북도 칠곡군",
        "칠곡군",
        "경상북도교육청 칠곡수학체험센터",
        "경상북도 칠곡군 북삼읍 시덕로 311",
        "https://www.gbe.kr/cgmath/main.do",
    ),
    "경상북도교육청발명인공지능교육원": _Venue(
        "4713000000",
        "경상북도 경주시",
        "경주시",
        "경상북도교육청발명인공지능교육원",
        "경상북도 경주시 첨성로 97",
        "https://www.gbe.kr/ieec/lm/location/locationMapView.do?mi=19933",
    ),
    "경상북도교육청 수학문화관": _Venue(
        "4711000000",
        "경상북도 포항시",
        "포항시",
        "경상북도교육청 수학문화관",
        "경상북도 포항시 북구 우미길 93-1",
        "https://www.gbe.kr/gbemc/lm/location/locationMapView.do?mi=22782",
    ),
    "과학원": _Venue(
        "4711000000",
        "경상북도 포항시",
        "포항시",
        "경상북도교육청과학원",
        "경상북도 포항시 북구 우미길 93",
        "https://www.gbe.kr/gsei/main.do",
    ),
    "의성안전체험관": _Venue(
        "4773000000",
        "경상북도 의성군",
        "의성군",
        "경상북도교육청 의성안전체험관",
        "경상북도 의성군 다인면 자미로 492",
        "https://www.gbe.kr/safetycenter/main.do",
    ),
    "경산교육지원청": _Venue(
        "4729000000",
        "경상북도 경산시",
        "경산시",
        "경산과학발명교육센터",
        "경상북도 경산시 삼성현로 110 남부미래교육관 2층",
        "https://www.gbe.kr/gs/cm/cntnts/cntntsView.do?cntntsId=3485&mi=6098",
    ),
    "남부메이커교육센터": _Venue(
        "4729000000",
        "경상북도 경산시",
        "경산시",
        "경상북도교육청 남부메이커교육센터",
        "경상북도 경산시 삼성현로 110",
        "https://www.gbe.kr/mirae/main.do",
    ),
}

GYEONGBUK_EDU_EXPERIENCE_COVERED_MUNICIPALITIES = (
    {
        "code": "4711000000",
        "sido": "경상북도",
        "sigungu": "포항시",
        "full_name": "경상북도 포항시",
    },
    {
        "code": "4713000000",
        "sido": "경상북도",
        "sigungu": "경주시",
        "full_name": "경상북도 경주시",
    },
    {
        "code": "4717000000",
        "sido": "경상북도",
        "sigungu": "안동시",
        "full_name": "경상북도 안동시",
    },
    {
        "code": "4725000000",
        "sido": "경상북도",
        "sigungu": "상주시",
        "full_name": "경상북도 상주시",
    },
    {
        "code": "4729000000",
        "sido": "경상북도",
        "sigungu": "경산시",
        "full_name": "경상북도 경산시",
    },
    {
        "code": "4773000000",
        "sido": "경상북도",
        "sigungu": "의성군",
        "full_name": "경상북도 의성군",
    },
    {
        "code": "4785000000",
        "sido": "경상북도",
        "sigungu": "칠곡군",
        "full_name": "경상북도 칠곡군",
    },
)

SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_COUNTER_RE = re.compile(
    r"전체\s*([\d,]+)\s*건\s*\(\s*(\d+)\s*/\s*(\d+)\s*\)"
)
_DATE_RE = re.compile(r"20\d{2}/\d{2}/\d{2}")
_DATETIME_RE = re.compile(r"20\d{2}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}")
_POSITIVE_INTEGER_RE = re.compile(r"[1-9]\d*")
_SOURCE_STATUS_MAP: Mapping[str, str] = {
    "접수중": "OPEN",
    "마감": "CLOSED",
    "예정": "SCHEDULED",
}
_VARIABLE_VENUE_MARKERS = (
    "찾아가는",
    "장소:학교",
    "장소: 학교",
)
_NONPRODUCTION_INSTITUTIONS = frozenset({"테스트홈페이지"})
_FORBIDDEN_ROUTE_TOKENS = (
    "exprnreqstpage",
    "exprnselectdeschelist",
    "/login",
    "/auth",
    "/identity",
    "/applicant",
    "/member",
    "attachment",
    "filedownload",
    "download",
)
_FORBIDDEN_OUTPUT_KEYS = frozenset(
    {
        "phone",
        "email",
        "contact",
        "manager",
        "applicant",
        "member",
        "attachment",
        "attachments",
        "download",
        "raw_html",
        "content",
    }
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2}[- .]?\d{3,4}[- .]?\d{4}|"
    r"01[016789][- .]?\d{3,4}[- .]?\d{4})(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RESIDENT_ID_RE = re.compile(r"(?<!\d)\d{6}\s*[- ]\s*[1-4]\d{6}(?!\d)")


class GyeongbukEducationExperienceContractError(ValueError):
    """Raised when the audited official public contract changes."""


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(
        " ", html.unescape(str(value or "")).replace("\xa0", " ")
    ).strip()


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _positive(value: Any, name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise GyeongbukEducationExperienceContractError(
            f"{name} must be a positive integer"
        ) from exc
    if result < 1:
        raise GyeongbukEducationExperienceContractError(
            f"{name} must be a positive integer"
        )
    return result


def _exact_list_get_url(value: str) -> bool:
    parsed = urlparse(_clean(value))
    return bool(
        parsed.scheme == "https"
        and parsed.hostname == GYEONGBUK_EDU_EXPERIENCE_HOST
        and parsed.port is None
        and parsed.path == GYEONGBUK_EDU_EXPERIENCE_LIST_PATH
        and parse_qsl(parsed.query, keep_blank_values=True)
        == [("mi", GYEONGBUK_EDU_EXPERIENCE_MI)]
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


def _exact_list_post_url(value: str) -> bool:
    parsed = urlparse(_clean(value))
    return bool(
        parsed.scheme == "https"
        and parsed.hostname == GYEONGBUK_EDU_EXPERIENCE_HOST
        and parsed.port is None
        and parsed.path == GYEONGBUK_EDU_EXPERIENCE_LIST_PATH
        and not parsed.query
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


def gyeongbuk_edu_experience_detail_url(
    exprn_seq: Any, exprn_period_seq: Any, curr_page: Any
) -> str:
    values = tuple(_clean(value) for value in (exprn_seq, exprn_period_seq, curr_page))
    if any(not _POSITIVE_INTEGER_RE.fullmatch(value) for value in values):
        return ""
    return (
        f"https://{GYEONGBUK_EDU_EXPERIENCE_HOST}"
        f"{GYEONGBUK_EDU_EXPERIENCE_DETAIL_PATH}?"
        + urlencode(
            (
                ("mi", GYEONGBUK_EDU_EXPERIENCE_MI),
                ("exprnSeq", values[0]),
                ("exprnPeriodSeq", values[1]),
                ("currPage", values[2]),
            )
        )
    )


def _exact_detail_url(value: str) -> bool:
    parsed = urlparse(_clean(value))
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    return bool(
        parsed.scheme == "https"
        and parsed.hostname == GYEONGBUK_EDU_EXPERIENCE_HOST
        and parsed.port is None
        and parsed.path == GYEONGBUK_EDU_EXPERIENCE_DETAIL_PATH
        and [key for key, _ in pairs]
        == ["mi", "exprnSeq", "exprnPeriodSeq", "currPage"]
        and len(pairs) == 4
        and pairs[0][1] == GYEONGBUK_EDU_EXPERIENCE_MI
        and all(_POSITIVE_INTEGER_RE.fullmatch(item[1]) for item in pairs[1:])
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


def is_gyeongbuk_edu_experience_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider"))
        == GYEONGBUK_EDU_EXPERIENCE_PROVIDER
        and _exact_list_get_url(_clean(_target_value(target, "url")))
    )


is_target = is_gyeongbuk_edu_experience_target


def _paging_form(page: int) -> tuple[tuple[str, str], ...]:
    return (
        ("currPage", str(page)),
        ("cmmnCode", "rsvAreaSe"),
        ("maxSn", "10"),
        ("pageIndex", "10"),
        ("sysId", "edushare"),
        ("mi", GYEONGBUK_EDU_EXPERIENCE_MI),
        ("minSn", "0"),
    )


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 municipal-course-crawler/1.0",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return session


def _response_soup(response: Any, expected_url: str) -> BeautifulSoup:
    try:
        status = int(getattr(response, "status_code", 0) or 0)
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise GyeongbukEducationExperienceContractError(
            f"unexpected HTTP status {status}"
        )
    if tuple(getattr(response, "history", ()) or ()):
        raise GyeongbukEducationExperienceContractError(
            "redirect history is forbidden"
        )
    headers = getattr(response, "headers", {}) or {}
    if any(
        str(key).lower() == "location" and value
        for key, value in headers.items()
    ):
        raise GyeongbukEducationExperienceContractError(
            "redirect location is forbidden"
        )
    final_url = _clean(getattr(response, "url", ""))
    if final_url and final_url != expected_url:
        raise GyeongbukEducationExperienceContractError(
            "official response escaped the exact public route"
        )
    content_type = _clean(
        next(
            (
                value
                for key, value in headers.items()
                if str(key).lower() == "content-type"
            ),
            "text/html",
        )
    ).lower()
    if "html" not in content_type:
        raise GyeongbukEducationExperienceContractError(
            "official response is not HTML"
        )
    body = getattr(response, "content", None)
    if body is None:
        body = str(getattr(response, "text", response)).encode("utf-8")
    body = bytes(body)
    if not body or len(body) > GYEONGBUK_EDU_EXPERIENCE_MAX_BYTES:
        raise GyeongbukEducationExperienceContractError(
            "empty or oversized official response"
        )
    soup = BeautifulSoup(body, "html.parser")
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != "경상북도통합예약시스템-견학/체험":
        raise GyeongbukEducationExperienceContractError(
            "official experience page title changed"
        )
    headings = [_clean(node.get_text(" ", strip=True)) for node in soup.select("h2.titleH2")]
    if headings != ["견학/체험"]:
        raise GyeongbukEducationExperienceContractError(
            "official experience menu heading changed"
        )
    return soup


class _Runner:
    def __init__(self, session_factory: SessionFactory, timeout: int) -> None:
        self.session_factory = session_factory
        self.timeout = timeout
        self.session: Any = None
        self.get_requests = 0
        self.post_requests = 0
        self.list_requests = 0
        self.detail_requests = 0

    def __enter__(self) -> "_Runner":
        self.session = self.session_factory()
        return self

    def __exit__(self, *_: Any) -> None:
        close = getattr(self.session, "close", None)
        if callable(close):
            close()

    def list_get(self) -> BeautifulSoup:
        url = GYEONGBUK_EDU_EXPERIENCE_URL
        if not _exact_list_get_url(url):
            raise GyeongbukEducationExperienceContractError(
                "unsafe public list GET refused"
            )
        self.get_requests += 1
        self.list_requests += 1
        response = self.session.get(
            url,
            timeout=self.timeout,
            allow_redirects=False,
            headers={"Referer": GYEONGBUK_EDU_EXPERIENCE_URL},
        )
        return _response_soup(response, url)

    def list_post(self, page: int) -> BeautifulSoup:
        page = _positive(page, "page")
        url = GYEONGBUK_EDU_EXPERIENCE_POST_URL
        form = _paging_form(page)
        if not _exact_list_post_url(url) or form != _paging_form(page):
            raise GyeongbukEducationExperienceContractError(
                "unsafe public list POST refused"
            )
        self.post_requests += 1
        self.list_requests += 1
        response = self.session.post(
            url,
            data=dict(form),
            timeout=self.timeout,
            allow_redirects=False,
            headers={"Referer": GYEONGBUK_EDU_EXPERIENCE_URL},
        )
        return _response_soup(response, url)

    def detail_get(self, url: str) -> BeautifulSoup:
        lowered = _clean(url).lower()
        if not _exact_detail_url(url) or any(
            token in lowered for token in _FORBIDDEN_ROUTE_TOKENS
        ):
            raise GyeongbukEducationExperienceContractError(
                "application/login/auth/applicant/member/PII/calendar/"
                "attachment/download route refused"
            )
        self.get_requests += 1
        self.detail_requests += 1
        response = self.session.get(
            url,
            timeout=self.timeout,
            allow_redirects=False,
            headers={"Referer": GYEONGBUK_EDU_EXPERIENCE_URL},
        )
        return _response_soup(response, url)


def _direct_text(cell: Any, label: str) -> str:
    values = [
        _clean(value)
        for value in cell.find_all(string=True)
        if _clean(value) and _clean(value) != label
    ]
    return _clean(" ".join(values))


def _counter(soup: BeautifulSoup) -> tuple[int, int, int]:
    nodes = soup.select("h3.titT1")
    if len(nodes) != 1:
        raise GyeongbukEducationExperienceContractError(
            "expected one official source counter"
        )
    match = _COUNTER_RE.fullmatch(_clean(nodes[0].get_text(" ", strip=True)))
    if match is None:
        raise GyeongbukEducationExperienceContractError(
            "official source counter changed"
        )
    return (
        int(match.group(1).replace(",", "")),
        int(match.group(2)),
        int(match.group(3)),
    )


def _identity_from_control(control: Any) -> tuple[str, str, str]:
    if _clean(control.get("href")) != "javascript:":
        raise GyeongbukEducationExperienceContractError(
            "experience control is not inert"
        )
    values = tuple(
        _clean(control.get(key))
        for key in ("data-id", "data-period-id", "data-rssysid")
    )
    if (
        not _POSITIVE_INTEGER_RE.fullmatch(values[0])
        or not _POSITIVE_INTEGER_RE.fullmatch(values[1])
        or not re.fullmatch(r"[a-z][a-z0-9_-]{1,31}", values[2])
    ):
        raise GyeongbukEducationExperienceContractError(
            "malformed public experience identity"
        )
    return values


def _parse_list_page(
    soup: BeautifulSoup, *, page: int
) -> tuple[list[dict[str, Any]], bool]:
    headers = [_clean(node.get_text(" ", strip=True)) for node in soup.select("table th")]
    expected_headers = [
        "순번",
        "기관명",
        "체험명",
        "운영기간",
        "접수기간",
        "체험대상",
        "신청대상",
        "예약상태",
    ]
    if headers != expected_headers:
        raise GyeongbukEducationExperienceContractError(
            f"page {page}: experience table header changed"
        )
    body_rows = soup.select("table tbody tr")
    if len(body_rows) == 1:
        cells = body_rows[0].find_all("td", recursive=False)
        if (
            len(cells) == 1
            and cells[0].get("class") == ["noData"]
            and _clean(cells[0].get("colspan")) == "8"
            and _clean(cells[0].get_text(" ", strip=True))
            == "등록된 체험이 없습니다."
        ):
            return [], True

    rows: list[dict[str, Any]] = []
    for node in body_rows:
        cells = node.find_all("td", recursive=False)
        if len(cells) != 8:
            raise GyeongbukEducationExperienceContractError(
                f"page {page}: experience row schema changed"
            )
        ordinal = _clean(cells[0].get_text(" ", strip=True))
        if not _POSITIVE_INTEGER_RE.fullmatch(ordinal):
            raise GyeongbukEducationExperienceContractError(
                f"page {page}: invalid source ordinal"
            )
        institution = _direct_text(cells[1], "기관명")
        title_controls = cells[2].select("a.viewExprnInfo")
        status_controls = cells[7].select("a.viewExprnInfo")
        if len(title_controls) != 1 or len(status_controls) != 1:
            raise GyeongbukEducationExperienceContractError(
                f"page {page}: ambiguous public detail control"
            )
        identity = _identity_from_control(title_controls[0])
        if _identity_from_control(status_controls[0]) != identity:
            raise GyeongbukEducationExperienceContractError(
                f"page {page}: status/detail identity mismatch"
            )
        primary_nodes = title_controls[0].select(".pc_mint")
        secondary_nodes = title_controls[0].select(".list_st2 li")
        if len(primary_nodes) != 1 or len(secondary_nodes) != 1:
            raise GyeongbukEducationExperienceContractError(
                f"page {page}: experience title structure changed"
            )
        primary = _clean(primary_nodes[0].get_text(" ", strip=True))
        secondary = _clean(secondary_nodes[0].get_text(" ", strip=True))
        if not primary:
            raise GyeongbukEducationExperienceContractError(
                f"page {page}: empty experience title"
            )
        title = primary if not secondary else f"{primary} - {secondary}"

        operation_dates = _DATE_RE.findall(
            _clean(cells[3].get_text(" ", strip=True))
        )
        reception_datetimes = _DATETIME_RE.findall(
            _clean(cells[4].get_text(" ", strip=True))
        )
        if len(operation_dates) != 2 or len(reception_datetimes) != 2:
            raise GyeongbukEducationExperienceContractError(
                f"page {page}: programme period structure changed"
            )
        try:
            start = date.fromisoformat(operation_dates[0].replace("/", "-"))
            end = date.fromisoformat(operation_dates[1].replace("/", "-"))
            apply_start = datetime.strptime(
                reception_datetimes[0], "%Y/%m/%d %H:%M:%S"
            )
            apply_end = datetime.strptime(
                reception_datetimes[1], "%Y/%m/%d %H:%M:%S"
            )
        except ValueError as exc:
            raise GyeongbukEducationExperienceContractError(
                f"page {page}: invalid programme period"
            ) from exc
        if end < start or apply_end < apply_start:
            raise GyeongbukEducationExperienceContractError(
                f"page {page}: reversed programme period"
            )
        source_status = _direct_text(cells[7], "예약상태")
        if source_status not in _SOURCE_STATUS_MAP:
            raise GyeongbukEducationExperienceContractError(
                f"page {page}: unknown source status {source_status!r}"
            )
        audience = _direct_text(cells[5], "체험대상")
        applicant_scope = _direct_text(cells[6], "신청대상")
        if not institution or not audience or not applicant_scope:
            raise GyeongbukEducationExperienceContractError(
                f"page {page}: required public list field is empty"
            )
        exprn_seq, exprn_period_seq, rs_sys_id = identity
        detail_url = gyeongbuk_edu_experience_detail_url(
            exprn_seq, exprn_period_seq, page
        )
        rows.append(
            {
                "ordinal": int(ordinal),
                "institution": institution,
                "title": title,
                "primary_title": primary,
                "secondary_title": secondary,
                "exprn_seq": exprn_seq,
                "exprn_period_seq": exprn_period_seq,
                "rs_sys_id": rs_sys_id,
                "page": page,
                "start": start,
                "end": end,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "audience": audience,
                "applicant_scope": applicant_scope,
                "source_status": source_status,
                "status": _SOURCE_STATUS_MAP[source_status],
                "detail_url": detail_url,
            }
        )
    return rows, False


def _list_fingerprint(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            row["ordinal"],
            row["exprn_seq"],
            row["exprn_period_seq"],
            row["rs_sys_id"],
            row["institution"],
            row["title"],
            row["start"].isoformat(),
            row["end"].isoformat(),
            row["apply_start"].isoformat(),
            row["apply_end"].isoformat(),
            row["source_status"],
        )
        for row in rows
    )


def _detail_pairs(root: Any) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for node in root.select("li"):
        labels = node.find_all("span", recursive=False)
        if len(labels) != 1:
            raise GyeongbukEducationExperienceContractError(
                "public detail field structure changed"
            )
        label = _clean(labels[0].get_text(" ", strip=True))
        labels[0].extract()
        value = _clean(node.get_text(" ", strip=True))
        if not label or label in pairs:
            raise GyeongbukEducationExperienceContractError(
                "duplicate or empty public detail label"
            )
        pairs[label] = value
    return pairs


def _validate_detail(row: dict[str, Any], soup: BeautifulSoup) -> None:
    forms = soup.select("form#exprnInfoForm")
    roots = soup.select(".content_box.rveInfo")
    if len(forms) != 1 or len(roots) != 1:
        raise GyeongbukEducationExperienceContractError(
            "public detail owner structure changed"
        )
    form = forms[0]
    if _clean(form.get("method")).lower() != "get" or _clean(form.get("action")):
        raise GyeongbukEducationExperienceContractError(
            "public detail form contract changed"
        )
    inputs: dict[str, str] = {}
    for node in form.select("input[name]"):
        name = _clean(node.get("name"))
        value = _clean(node.get("value"))
        if name in inputs:
            raise GyeongbukEducationExperienceContractError(
                "duplicate public detail identity input"
            )
        inputs[name] = value
    expected_identity = {
        "mi": GYEONGBUK_EDU_EXPERIENCE_MI,
        "rsSysId": row["rs_sys_id"],
        "exprnSeq": row["exprn_seq"],
        "exprnPeriodSeq": row["exprn_period_seq"],
        "exprnScheSeq": "",
        "currPage": str(row["page"]),
    }
    if any(inputs.get(key) != value for key, value in expected_identity.items()):
        raise GyeongbukEducationExperienceContractError(
            "public detail/list identity mismatch"
        )
    headings = roots[0].select("h4.titT2")
    if len(headings) != 1 or _clean(headings[0].get_text(" ", strip=True)) != row["title"]:
        raise GyeongbukEducationExperienceContractError(
            "public detail/list title mismatch"
        )
    pairs = _detail_pairs(roots[0])
    required = {
        "운영기관",
        "운영기간",
        "접수기간",
        "신청대상",
        "대상",
        "예약지역",
    }
    if not required.issubset(pairs):
        raise GyeongbukEducationExperienceContractError(
            "public detail required fields changed"
        )
    if pairs["운영기관"] != row["institution"]:
        raise GyeongbukEducationExperienceContractError(
            "public detail/list institution mismatch"
        )
    operation_dates = _DATE_RE.findall(pairs["운영기간"])
    reception_datetimes = _DATETIME_RE.findall(pairs["접수기간"])
    if operation_dates != [
        row["start"].strftime("%Y/%m/%d"),
        row["end"].strftime("%Y/%m/%d"),
    ]:
        raise GyeongbukEducationExperienceContractError(
            "public detail/list operation period mismatch"
        )
    if reception_datetimes != [
        row["apply_start"].strftime("%Y/%m/%d %H:%M:%S"),
        row["apply_end"].strftime("%Y/%m/%d %H:%M:%S"),
    ]:
        raise GyeongbukEducationExperienceContractError(
            "public detail/list reception period mismatch"
        )
    if pairs["신청대상"] != row["applicant_scope"]:
        raise GyeongbukEducationExperienceContractError(
            "public detail/list applicant scope mismatch"
        )
    # 예약지역 is an applicant eligibility field.  Its contents must never be
    # used as venue or municipal coverage evidence.
    if not pairs["예약지역"]:
        raise GyeongbukEducationExperienceContractError(
            "public detail eligibility region contract changed"
        )
    detail_sections = soup.select(
        ".cnDivExprnDetailCn, .cnDivExprnCn, .cnDivExprnAtpn"
    )
    if len(detail_sections) != 3:
        raise GyeongbukEducationExperienceContractError(
            "public information detail tabs changed"
        )
    row["target_audience"] = pairs["대상"]
    row["detail_verified"] = True
    row["application_controls_observed_not_called"] = len(
        soup.select(".exprnReqst")
    )


def _exclusion_reason(row: Mapping[str, Any]) -> str:
    institution = _clean(row.get("institution"))
    title = _clean(row.get("title"))
    if institution in _NONPRODUCTION_INSTITUTIONS:
        return "nonproduction_test_owner"
    if any(marker in title for marker in _VARIABLE_VENUE_MARKERS):
        return "variable_applicant_school_venue"
    if institution not in GYEONGBUK_EDU_EXPERIENCE_VENUES:
        return "unmapped_official_institution"
    return ""


def _output_row(row: Mapping[str, Any]) -> dict[str, Any]:
    venue = GYEONGBUK_EDU_EXPERIENCE_VENUES[_clean(row.get("institution"))]
    detail_url = _clean(row.get("detail_url"))
    status = _clean(row.get("status"))
    application_url = detail_url if status == "OPEN" else ""
    identity = f"{row['exprn_seq']}:{row['exprn_period_seq']}"
    output = {
        "provider": GYEONGBUK_EDU_EXPERIENCE_PROVIDER,
        "provider_course_id": (
            f"{GYEONGBUK_EDU_EXPERIENCE_PROVIDER}:experience:"
            f"{row['exprn_seq']}:{row['exprn_period_seq']}"
        ),
        "prefer_incoming_provider_course_id": True,
        "source_course_id": identity,
        "title": _clean(row.get("title")),
        "branch": _clean(row.get("institution")),
        "branch_code": _clean(row.get("rs_sys_id")),
        "preserve_branch": True,
        "provider_organizer": _clean(row.get("institution")),
        "raw_url": detail_url,
        "source_url": GYEONGBUK_EDU_EXPERIENCE_URL,
        "application_url": application_url,
        "application_type": "ONLINE_RESERVATION" if application_url else "INFO_ONLY",
        "reservation_available": bool(application_url),
        "status": status,
        "course_status": status,
        "source_status": _clean(row.get("source_status")),
        "start_date": row["start"].isoformat(),
        "end_date": row["end"].isoformat(),
        "period": f"{row['start'].isoformat()} ~ {row['end'].isoformat()}",
        "apply_start": row["apply_start"].isoformat(sep=" "),
        "apply_end": row["apply_end"].isoformat(sep=" "),
        "apply_period": (
            f"{row['apply_start'].isoformat(sep=' ')} ~ "
            f"{row['apply_end'].isoformat(sep=' ')}"
        ),
        "target": _clean(row.get("target_audience")),
        "target_audience": _clean(row.get("target_audience")),
        "eligibility_raw": _clean(row.get("applicant_scope")),
        "location": venue.venue_name,
        "venue_name": venue.venue_name,
        "address": venue.address,
        "venue_address": venue.address,
        "collection_category": "공공예약",
        "domain_category": "체험·견학",
        "category": "체험·견학",
        "operator_type": "교육청/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "체험",
        "service_group_policy": "locked",
        "program_type": "체험",
        "program_type_source": "official_gbe_experience_menu",
        "classification_locked": True,
        "collection_type": GYEONGBUK_EDU_EXPERIENCE_PARSER,
        "municipality_code": venue.municipality_code,
        "municipality_name": venue.municipality_name,
        "municipality_full_name": venue.municipality_name,
        "region": venue.municipality_name,
        "sido": "경상북도",
        "sigungu": venue.sigungu,
        "raw_fields": {
            "parser": GYEONGBUK_EDU_EXPERIENCE_PARSER,
            "exprn_seq": _clean(row.get("exprn_seq")),
            "exprn_period_seq": _clean(row.get("exprn_period_seq")),
            "rs_sys_id": _clean(row.get("rs_sys_id")),
            "source_ordinal": int(row["ordinal"]),
            "list_page": int(row["page"]),
            "source_status": _clean(row.get("source_status")),
            "detail_verified": bool(row.get("detail_verified")),
            "venue_mapping_basis": "exact_official_institution_address_registry",
            "venue_evidence_url": venue.evidence_url,
            "eligibility_regions_ignored_for_venue": True,
            "application_controls_observed_not_called": int(
                row.get("application_controls_observed_not_called", 0)
            ),
            "application_endpoint_called": False,
            "pii_fields_omitted": True,
        },
    }
    return output


def _privacy_errors(value: Any, path: str = "row") -> list[str]:
    errors: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in _FORBIDDEN_OUTPUT_KEYS:
                errors.append(f"{path}.{key}: forbidden output key")
            errors.extend(_privacy_errors(item, f"{path}.{key}"))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            errors.extend(_privacy_errors(item, f"{path}[{index}]"))
    elif isinstance(value, str):
        if _PHONE_RE.search(value) or _EMAIL_RE.search(value) or _RESIDENT_ID_RE.search(value):
            errors.append(f"{path}: PII-shaped output")
    return errors


def _identity_hash(values: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            output.append(row)
    return output


def _failure(message: str, *, cutoff: str = "") -> dict[str, Any]:
    return {
        "provider": GYEONGBUK_EDU_EXPERIENCE_PROVIDER,
        "ownership_scope": GYEONGBUK_EDU_EXPERIENCE_OWNERSHIP_SCOPE,
        "cutoff": cutoff,
        "source_total": 0,
        "source_rows": 0,
        "source_current_count": 0,
        "source_expired_count": 0,
        "current_count": 0,
        "returned_count": 0,
        "data_pages": 0,
        "sentinel_page": 0,
        "list_requests": 0,
        "detail_requests": 0,
        "detail_pages": 0,
        "application_endpoint_requests": 0,
        "login_auth_identity_applicant_member_pii_endpoint_requests": 0,
        "calendar_endpoint_requests": 0,
        "attachment_download_endpoint_requests": 0,
        "unsafe_endpoint_calls": 0,
        "pagination_complete": False,
        "details_complete": False,
        "venue_mapping_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "no_current_data": False,
        "configured_collection_error": message,
    }


def collect_gyeongbuk_edu_experience(
    target: Any,
    timeout: int = 20,
    max_pages: int = 20,
    detail_limit: int = 100,
    *,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return an atomic current/future snapshot with verified venue regions."""

    if not is_gyeongbuk_edu_experience_target(target):
        return [], GYEONGBUK_EDU_EXPERIENCE_PARSER, _failure(
            "target does not match the exact Gyeongbuk education experience owner"
        )
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], GYEONGBUK_EDU_EXPERIENCE_PARSER, _failure(
                "managed session_factory injection is required"
            )
        session_factory = _default_session_factory
    try:
        allowed_list_requests = _positive(max_pages, "max_pages")
        allowed_details = _positive(detail_limit, "detail_limit")
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        return [], GYEONGBUK_EDU_EXPERIENCE_PARSER, _failure(
            f"invalid collection limits or date: {exc}"
        )

    meta = _failure("", cutoff=cutoff.isoformat())
    try:
        with _Runner(session_factory, timeout) as runner:
            first_soup = runner.list_get()
            total, current_page, declared_last = _counter(first_soup)
            if current_page != 1 or declared_last != max(
                1, math.ceil(total / GYEONGBUK_EDU_EXPERIENCE_PAGE_SIZE)
            ):
                raise GyeongbukEducationExperienceContractError(
                    "first-page total/page contract changed"
                )
            data_pages = declared_last
            required_list_requests = data_pages + 4
            if required_list_requests > allowed_list_requests:
                raise GyeongbukEducationExperienceContractError(
                    f"max_pages permits {allowed_list_requests} of "
                    f"{required_list_requests} required list requests"
                )

            page_rows: dict[int, list[dict[str, Any]]] = {}
            first_rows, first_empty = _parse_list_page(first_soup, page=1)
            if first_empty:
                raise GyeongbukEducationExperienceContractError(
                    "declared source unexpectedly has an empty first page"
                )
            page_rows[1] = first_rows
            sentinel_soup: Optional[BeautifulSoup] = None
            for page in range(2, data_pages + 2):
                soup = runner.list_post(page)
                page_total, displayed_page, page_last = _counter(soup)
                if (page_total, displayed_page, page_last) != (
                    total,
                    page,
                    data_pages,
                ):
                    raise GyeongbukEducationExperienceContractError(
                        f"page {page}: source counter changed"
                    )
                parsed, empty = _parse_list_page(soup, page=page)
                if page <= data_pages:
                    if empty:
                        raise GyeongbukEducationExperienceContractError(
                            f"page {page}: data page became empty"
                        )
                    page_rows[page] = parsed
                else:
                    if not empty or parsed:
                        raise GyeongbukEducationExperienceContractError(
                            "immediate post-last page is not exactly empty"
                        )
                    sentinel_soup = soup

            first_recheck = runner.list_get()
            first_recheck_rows, first_recheck_empty = _parse_list_page(
                first_recheck, page=1
            )
            if (
                _counter(first_recheck) != (total, 1, data_pages)
                or first_recheck_empty
                or _list_fingerprint(first_recheck_rows)
                != _list_fingerprint(page_rows[1])
            ):
                raise GyeongbukEducationExperienceContractError(
                    "first page changed during collection"
                )

            last_recheck = runner.list_post(data_pages)
            last_recheck_rows, last_recheck_empty = _parse_list_page(
                last_recheck, page=data_pages
            )
            if (
                _counter(last_recheck) != (total, data_pages, data_pages)
                or last_recheck_empty
                or _list_fingerprint(last_recheck_rows)
                != _list_fingerprint(page_rows[data_pages])
            ):
                raise GyeongbukEducationExperienceContractError(
                    "last page changed during collection"
                )

            sentinel_recheck = runner.list_post(data_pages + 1)
            sentinel_recheck_rows, sentinel_recheck_empty = _parse_list_page(
                sentinel_recheck, page=data_pages + 1
            )
            if (
                sentinel_soup is None
                or _counter(sentinel_recheck)
                != (total, data_pages + 1, data_pages)
                or not sentinel_recheck_empty
                or sentinel_recheck_rows
            ):
                raise GyeongbukEducationExperienceContractError(
                    "post-last sentinel changed during collection"
                )

            page_counts = {page: len(rows) for page, rows in page_rows.items()}
            for page in range(1, data_pages):
                if page_counts.get(page) != GYEONGBUK_EDU_EXPERIENCE_PAGE_SIZE:
                    raise GyeongbukEducationExperienceContractError(
                        f"page {page}: non-terminal page is not full"
                    )
            terminal_expected = total - (
                GYEONGBUK_EDU_EXPERIENCE_PAGE_SIZE * (data_pages - 1)
            )
            if page_counts.get(data_pages) != terminal_expected:
                raise GyeongbukEducationExperienceContractError(
                    "terminal page row count does not match declared total"
                )
            source_rows = [
                row
                for page in range(1, data_pages + 1)
                for row in page_rows[page]
            ]
            if len(source_rows) != total:
                raise GyeongbukEducationExperienceContractError(
                    "parsed source cardinality differs from declared total"
                )
            ordinals = [int(row["ordinal"]) for row in source_rows]
            if ordinals != list(range(total, 0, -1)):
                raise GyeongbukEducationExperienceContractError(
                    "source ordinals are not one complete descending range"
                )
            identities = [
                f"{row['exprn_seq']}:{row['exprn_period_seq']}"
                for row in source_rows
            ]
            if len(set(identities)) != len(identities):
                raise GyeongbukEducationExperienceContractError(
                    "duplicate public experience identity"
                )
            detail_urls = [_clean(row.get("detail_url")) for row in source_rows]
            if len(set(detail_urls)) != len(detail_urls):
                raise GyeongbukEducationExperienceContractError(
                    "duplicate public experience detail URL"
                )

            current_rows = [row for row in source_rows if row["end"] >= cutoff]
            if len(current_rows) > allowed_details:
                raise GyeongbukEducationExperienceContractError(
                    f"detail_limit permits {allowed_details} of "
                    f"{len(current_rows)} required current details"
                )
            for row in current_rows:
                detail_soup = runner.detail_get(_clean(row.get("detail_url")))
                _validate_detail(row, detail_soup)

            excluded: list[dict[str, str]] = []
            mapped_rows: list[dict[str, Any]] = []
            for row in current_rows:
                reason = _exclusion_reason(row)
                if reason:
                    excluded.append(
                        {
                            "identity": (
                                f"{row['exprn_seq']}:{row['exprn_period_seq']}"
                            ),
                            "institution": _clean(row.get("institution")),
                            "title": _clean(row.get("title")),
                            "reason": reason,
                        }
                    )
                    continue
                mapped_rows.append(_output_row(row))

            privacy = [
                error for output in mapped_rows for error in _privacy_errors(output)
            ]
            if privacy:
                raise GyeongbukEducationExperienceContractError(
                    f"PII/output allowlist violation: {privacy[0]}"
                )
            deduper = dedupe_rows or _dedupe_default
            result = list(deduper(mapped_rows))
            if len(result) != len(mapped_rows):
                raise GyeongbukEducationExperienceContractError(
                    "dedupe changed complete mapped output"
                )
            if any(
                bool(row.get("application_url"))
                != bool(row.get("reservation_available"))
                for row in result
            ):
                raise GyeongbukEducationExperienceContractError(
                    "application URL/reservation boolean contract changed"
                )

            source_status_counts = Counter(
                _clean(row.get("source_status")) for row in source_rows
            )
            current_source_status_counts = Counter(
                _clean(row.get("source_status")) for row in current_rows
            )
            status_counts = Counter(_clean(row.get("status")) for row in result)
            source_institution_counts = Counter(
                _clean(row.get("institution")) for row in source_rows
            )
            current_institution_counts = Counter(
                _clean(row.get("institution")) for row in current_rows
            )
            mapped_municipality_counts = Counter(
                _clean(row.get("municipality_code")) for row in result
            )
            exclusion_counts = Counter(item["reason"] for item in excluded)
            meta.update(
                {
                    "source_total": total,
                    "source_rows": len(source_rows),
                    "source_current_count": len(current_rows),
                    "source_expired_count": len(source_rows) - len(current_rows),
                    "current_count": len(current_rows),
                    "returned_count": len(result),
                    "excluded_current_count": len(excluded),
                    "excluded_nonproduction_count": exclusion_counts.get(
                        "nonproduction_test_owner", 0
                    ),
                    "excluded_variable_venue_count": exclusion_counts.get(
                        "variable_applicant_school_venue", 0
                    ),
                    "unmapped_unknown_institution_count": exclusion_counts.get(
                        "unmapped_official_institution", 0
                    ),
                    "excluded_current_rows": excluded,
                    "data_pages": data_pages,
                    "sentinel_page": data_pages + 1,
                    "page_counts": page_counts,
                    "required_list_requests": required_list_requests,
                    "list_requests": runner.list_requests,
                    "get_requests": runner.get_requests,
                    "post_requests": runner.post_requests,
                    "detail_requests": runner.detail_requests,
                    "detail_pages": len(current_rows),
                    "physical_requests": runner.get_requests + runner.post_requests,
                    "stable_first_page": True,
                    "stable_last_page": True,
                    "stable_sentinel_page": True,
                    "source_identity_sha256": _identity_hash(identities),
                    "output_identity_sha256": _identity_hash(
                        _clean(row.get("provider_course_id")) for row in result
                    ),
                    "source_status_counts": dict(sorted(source_status_counts.items())),
                    "current_source_status_counts": dict(
                        sorted(current_source_status_counts.items())
                    ),
                    "status_counts": dict(sorted(status_counts.items())),
                    "source_institution_counts": dict(
                        sorted(source_institution_counts.items())
                    ),
                    "current_institution_counts": dict(
                        sorted(current_institution_counts.items())
                    ),
                    "mapped_municipality_counts": dict(
                        sorted(mapped_municipality_counts.items())
                    ),
                    "mapped_municipalities": [
                        dict(item)
                        for item in GYEONGBUK_EDU_EXPERIENCE_COVERED_MUNICIPALITIES
                        if item["code"] in mapped_municipality_counts
                    ],
                    "reservation_region_field_used_for_venue": False,
                    "application_urls": sum(
                        bool(row.get("application_url")) for row in result
                    ),
                    "application_endpoint_requests": 0,
                    "login_auth_identity_applicant_member_pii_endpoint_requests": 0,
                    "calendar_endpoint_requests": 0,
                    "attachment_download_endpoint_requests": 0,
                    "unsafe_endpoint_calls": 0,
                    "pii_payload_persisted": False,
                    "duplicate_count": 0,
                    "pagination_complete": True,
                    "details_complete": True,
                    "venue_mapping_complete": True,
                    "snapshot_complete": True,
                    "full_snapshot_validated": True,
                    "no_current_data": not result,
                    "configured_collection_error": "",
                }
            )
            return result, GYEONGBUK_EDU_EXPERIENCE_PARSER, meta
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        meta["configured_collection_error"] = message
        meta["error_kind"] = (
            "contract"
            if isinstance(exc, GyeongbukEducationExperienceContractError)
            else "network_or_parse"
        )
        return [], GYEONGBUK_EDU_EXPERIENCE_PARSER, meta


collect = collect_gyeongbuk_edu_experience


__all__ = [
    name for name in globals() if name.startswith("GYEONGBUK_EDU_EXPERIENCE_")
] + [
    "GyeongbukEducationExperienceContractError",
    "collect",
    "collect_gyeongbuk_edu_experience",
    "gyeongbuk_edu_experience_detail_url",
    "is_gyeongbuk_edu_experience_target",
    "is_target",
]
