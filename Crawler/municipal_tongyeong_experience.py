"""Fail-closed Gyeongsangnam-do classroom-safety experience collector.

The Gyeongsangnam-do Office of Education reservation portal publishes the
province-wide ``csec`` classroom-safety ledger.  Its institution menu names
each facility ``[시군] 학교명 교실형 안전체험관`` and its independent search
fieldset explicitly labels the same 18 values as ``지역``.  Rows are mapped
only through that official title prefix and an exact municipality whitelist;
addresses are deliberately left empty rather than inferred.  The list already
contains the stable programme identity, operating/reception periods, audience,
method, and source status, so POST-only detail/application controls are not
followed.

The request allowlist contains just the canonical list GET and one fixed,
safe list POST with a page size of 50.  A first-list SSO bootstrap redirect may
set cookies, but it is never followed; the same list GET is retried once.
Login, detail, application, identity, applicant, attachment, download, and all
other routes fail closed.  Publication requires ordinals covering the exact
complete ledger, agreement between the first ten and complete list, and stable
rechecks of both representations.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag


TONGYEONG_EXPERIENCE_PROVIDER = "MUNI_SERVICE_GNE_GO_KR_8180F18B"
TONGYEONG_EXPERIENCE_CANDIDATE_ID = "MUNI_IR_25B30EABBA2B"
TONGYEONG_EXPERIENCE_HOST = "service.gne.go.kr"
TONGYEONG_EXPERIENCE_PATH = "/yeyak/exprn/exprnList.do"
TONGYEONG_EXPERIENCE_URL = (
    "https://service.gne.go.kr/yeyak/exprn/exprnList.do?insttId=csec&mi=14341"
)
TONGYEONG_EXPERIENCE_POST_URL = (
    "https://service.gne.go.kr/yeyak/exprn/exprnList.do?mi=14341"
)
TONGYEONG_EXPERIENCE_SAFE_POST_DATA: Mapping[str, str] = {
    "exprnEstbsSeq": "",
    "insttId": "csec",
    "srchExSeq": "",
    "srchPeriodDiv": "rcept",
    "srchRsvBgnde": "",
    "srchRsvEndde": "",
    "srchTxt": "",
    "pageIndex": "50",
}
TONGYEONG_EXPERIENCE_BOOTSTRAP_LOCATION = (
    "https://service.gne.go.kr/sso/agentInitProc.jsp"
)
TONGYEONG_EXPERIENCE_MUNICIPALITY_CODE = "4822000000"
TONGYEONG_EXPERIENCE_MUNICIPALITY_NAME = "경상남도 통영시"
TONGYEONG_EXPERIENCE_BRANCH = "경남 교실형 안전체험관"
TONGYEONG_EXPERIENCE_ADDRESS = ""
TONGYEONG_EXPERIENCE_REGION_MAP: Mapping[str, tuple[str, str, str]] = {
    "거제": ("4831000000", "거제시", "경상남도 거제시"),
    "거창": ("4888000000", "거창군", "경상남도 거창군"),
    "고성": ("4882000000", "고성군", "경상남도 고성군"),
    "김해": ("4825000000", "김해시", "경상남도 김해시"),
    "남해": ("4884000000", "남해군", "경상남도 남해군"),
    "밀양": ("4827000000", "밀양시", "경상남도 밀양시"),
    "사천": ("4824000000", "사천시", "경상남도 사천시"),
    "산청": ("4886000000", "산청군", "경상남도 산청군"),
    "양산": ("4833000000", "양산시", "경상남도 양산시"),
    "의령": ("4872000000", "의령군", "경상남도 의령군"),
    "진주": ("4817000000", "진주시", "경상남도 진주시"),
    "창녕": ("4874000000", "창녕군", "경상남도 창녕군"),
    "창원": ("4812000000", "창원시", "경상남도 창원시"),
    "통영": ("4822000000", "통영시", "경상남도 통영시"),
    "하동": ("4885000000", "하동군", "경상남도 하동군"),
    "함안": ("4873000000", "함안군", "경상남도 함안군"),
    "함양": ("4887000000", "함양군", "경상남도 함양군"),
    "합천": ("4889000000", "합천군", "경상남도 합천군"),
}
TONGYEONG_EXPERIENCE_REGION_FILTER_CODES: Mapping[str, str] = {
    "거제": "26",
    "거창": "36",
    "고성": "31",
    "김해": "24",
    "남해": "32",
    "밀양": "25",
    "사천": "23",
    "산청": "34",
    "양산": "27",
    "의령": "28",
    "진주": "21",
    "창녕": "30",
    "창원": "20",
    "통영": "22",
    "하동": "33",
    "함안": "29",
    "함양": "35",
    "합천": "37",
}
TONGYEONG_EXPERIENCE_CURRENT_COVERAGE_REGIONS = frozenset(
    {"김해", "양산", "창원", "통영", "함안", "함양"}
)
TONGYEONG_EXPERIENCE_CURRENT_IDENTITY_WHITELIST: Mapping[
    str, tuple[str, str]
] = {
    "10126": ("창원", "삼계중 교실형 안전체험관"),
    "10107": ("함양", "안의초 교실형 안전체험관"),
    "10151": ("창원", "팔룡초 교실형 안전체험관"),
    "10120": ("통영", "충무초 교실형 안전체험관"),
    "10173": ("김해", "영운초 교실형 안전체험관"),
    "10140": ("창원", "내동초 교실형 안전체험관"),
    "10150": ("함안", "함안중 교실형 안전체험관"),
    "10392": ("양산", "삼성초 교실형 안전체험관"),
}
TONGYEONG_EXPERIENCE_MAX_HTML_BYTES = 1_000_000
TONGYEONG_EXPERIENCE_OWNERSHIP_SCOPE = (
    "gne_csec_statewide_classroom_safety_experience_ledger"
)
TONGYEONG_EXPERIENCE_PARSER = (
    "gne_csec_statewide_classroom_safety_declared_total_exact_ordinals+"
    "first_ten_and_safe_page_size_50_complete_list+stable_rechecks+"
    "official_region_field_and_exact_title_prefix_municipality_whitelist+"
    "audited_current_region_coverage_guard+operation_currentness+"
    "locked_experience+list_get_and_fixed_list_post_only+"
    "sso_location_observed_not_followed+detail_application_controls_not_called+"
    "no_login_identity_applicant_attachment_download_or_pii_calls+atomic_snapshot"
)
TONGYEONG_EXPERIENCE_LIVE_AUDIT_BASELINE: Mapping[str, Any] = {
    "checked_at": "2026-08-05",
    "institution_filter": "csec",
    "source_total": 12,
    "source_current_count": 8,
    "returned_count": 8,
    "current_region_counts": {
        "김해": 1,
        "양산": 1,
        "창원": 3,
        "통영": 1,
        "함안": 1,
        "함양": 1,
    },
    "first_page_size": 10,
    "complete_page_size": 50,
    "detail_endpoint_requests": 0,
    "application_endpoint_requests": 0,
    "login_endpoint_requests": 0,
}

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, str, Mapping[str, str], int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE = re.compile(r"\s+")
_DETAIL_CONTROL = re.compile(
    r"goViewExprn\('(?P<identity>\d+)',\s*'view',\s*this\);?",
    re.IGNORECASE,
)
_TITLE_REGION = re.compile(r"^\[(?P<region>[가-힣]+)\]\s+(?P<body>.+)$")
_VENUE_BODY = re.compile(
    r"^(?P<venue>.+?\s교실형\s안전체험관)(?:\s+\(.*\))?$"
)
_DECLARED_TOTAL = re.compile(
    r"전체\s*:\s*(?P<total>\d+)\s*건\s*"
    r"\(\s*(?P<page>\d+)\s*/\s*(?P<last>\d+)\s*\)"
)
_OPERATION_PERIOD = re.compile(
    r"^(?P<start>20\d{2}\.\d{2}\.\d{2}\.)\s*~\s*"
    r"(?P<end>20\d{2}\.\d{2}\.\d{2}\.)$"
)
_RECEPTION_PERIOD = re.compile(
    r"^(?P<start>20\d{2}\.\d{2}\.\d{2}\.\s+\d{2}:\d{2})\s*~\s*"
    r"(?P<end>20\d{2}\.\d{2}\.\d{2}\.\s+\d{2}:\d{2})$"
)
_LIST_HEADERS = (
    "순번",
    "기관명",
    "체험명",
    "운영기간",
    "접수기간",
    "예약대상분류",
    "신청가능자",
    "신청방법",
    "예약상태",
)
_SOURCE_STATUSES = frozenset({"예약하기", "예약전", "예약마감"})
_FORBIDDEN_OUTPUT_KEYS = frozenset(
    {
        "phone",
        "email",
        "contact",
        "manager",
        "staff",
        "applicant",
        "member",
        "attachment",
        "attachments",
        "download",
        "raw_html",
        "description",
        "content",
    }
)
_PHONE = re.compile(
    r"(?<!\d)(?:0\d{1,2}[- .]?\d{3,4}[- .]?\d{4}|"
    r"01[016789][- .]?\d{3,4}[- .]?\d{4})(?!\d)"
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


class TongyeongExperienceContractError(ValueError):
    """Raised when the audited public-list contract changes."""


@dataclass(frozen=True)
class _ExperienceRow:
    sequence: int
    identity: str
    institution: str
    title: str
    region: str
    venue: str
    operation_start: date
    operation_end: date
    operation_period: str
    reception_start: datetime
    reception_end: datetime
    reception_period: str
    audience: str
    eligible_applicants: str
    application_method: str
    source_status: str


@dataclass(frozen=True)
class _ListPage:
    page_size: int
    declared_total: int
    declared_page: int
    declared_last_page: int
    rows: tuple[_ExperienceRow, ...]


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _parse_url(value: Any) -> tuple[Any, dict[str, str]]:
    parsed = urlparse(_clean(value))
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query = dict(pairs)
    if len(pairs) != len(query):
        raise TongyeongExperienceContractError("duplicate query key")
    if parsed.username or parsed.password or parsed.params or parsed.fragment:
        raise TongyeongExperienceContractError("unsafe URL authority or fragment")
    try:
        if parsed.port is not None:
            raise TongyeongExperienceContractError("explicit port is forbidden")
    except ValueError as exc:
        raise TongyeongExperienceContractError("invalid URL port") from exc
    return parsed, query


def _same_url(left: str, right: str) -> bool:
    left_parsed, left_query = _parse_url(left)
    right_parsed, right_query = _parse_url(right)
    return bool(
        left_parsed.scheme == right_parsed.scheme
        and (left_parsed.hostname or "").lower()
        == (right_parsed.hostname or "").lower()
        and left_parsed.path == right_parsed.path
        and left_query == right_query
    )


def _validate_request(
    method: str,
    url: str,
    data: Mapping[str, str],
) -> None:
    normalized_method = _clean(method).upper()
    if normalized_method == "GET":
        valid = not data and _same_url(url, TONGYEONG_EXPERIENCE_URL)
    elif normalized_method == "POST":
        valid = _same_url(url, TONGYEONG_EXPERIENCE_POST_URL) and dict(data) == dict(
            TONGYEONG_EXPERIENCE_SAFE_POST_DATA
        )
    else:
        valid = False
    if not valid:
        raise TongyeongExperienceContractError(
            "detail/application/login/identity/applicant/attachment/download/PII route refused"
        )


def is_tongyeong_experience_target(target: Any) -> bool:
    try:
        return bool(
            _clean(_target_value(target, "provider"))
            == TONGYEONG_EXPERIENCE_PROVIDER
            and _same_url(
                _clean(_target_value(target, "url")),
                TONGYEONG_EXPERIENCE_URL,
            )
        )
    except TongyeongExperienceContractError:
        return False


is_target = is_tongyeong_experience_target


def _default_session() -> requests.Session:
    current = requests.Session()
    current.trust_env = False
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return current


def _default_fetcher(
    current: Any,
    method: str,
    url: str,
    data: Mapping[str, str],
    timeout: int,
) -> Any:
    if method == "GET":
        return current.get(
            url,
            timeout=timeout,
            allow_redirects=False,
            verify=True,
        )
    return current.post(
        url,
        data=dict(data),
        timeout=timeout,
        allow_redirects=False,
        verify=True,
    )


class _Requester:
    def __init__(
        self,
        session_factory: SessionFactory,
        fetcher: Fetcher,
        timeout: int,
        meta: dict[str, Any],
    ) -> None:
        self.session = session_factory()
        self.fetcher = fetcher
        self.timeout = timeout
        self.meta = meta
        self.bootstrap_seen = False

    def soup(
        self,
        method: str,
        url: str,
        data: Mapping[str, str],
        *,
        permit_bootstrap: bool = False,
    ) -> BeautifulSoup:
        _validate_request(method, url, data)
        response = self._fetch(method, url, data)
        status = int(getattr(response, "status_code", 0) or 0)
        headers = getattr(response, "headers", {}) or {}
        location = _clean(
            next(
                (
                    value
                    for key, value in headers.items()
                    if str(key).lower() == "location"
                ),
                "",
            )
        )
        if status == 302 and permit_bootstrap and not self.bootstrap_seen:
            if not _same_url(location, TONGYEONG_EXPERIENCE_BOOTSTRAP_LOCATION):
                raise TongyeongExperienceContractError(
                    "unexpected bootstrap redirect location"
                )
            self.bootstrap_seen = True
            self.meta["sso_bootstrap_locations_observed_not_followed"] += 1
            response = self._fetch(method, url, data)
        return self._response_soup(response, method, url)

    def _fetch(
        self,
        method: str,
        url: str,
        data: Mapping[str, str],
    ) -> Any:
        self.meta["logical_requests"] += 1
        self.meta["list_requests"] += 1
        return self.fetcher(self.session, method, url, data, self.timeout)

    def _response_soup(self, response: Any, method: str, url: str) -> BeautifulSoup:
        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            raise TongyeongExperienceContractError(f"HTTP {status}")
        if tuple(getattr(response, "history", ()) or ()):
            raise TongyeongExperienceContractError("redirect history is forbidden")
        headers = getattr(response, "headers", {}) or {}
        if any(
            str(key).lower() == "location" and value
            for key, value in headers.items()
        ):
            raise TongyeongExperienceContractError("redirect location is forbidden")
        final_url = _clean(getattr(response, "url", ""))
        if final_url and not _same_url(final_url, url):
            raise TongyeongExperienceContractError("response URL changed")
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
            raise TongyeongExperienceContractError("non-HTML list response")
        content = getattr(response, "content", None)
        if content is None:
            text = str(getattr(response, "text", ""))
            content = text.encode("utf-8")
        else:
            content = bytes(content)
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise TongyeongExperienceContractError(
                    "list response is not strict UTF-8"
                ) from exc
        if not content or len(content) > TONGYEONG_EXPERIENCE_MAX_HTML_BYTES:
            raise TongyeongExperienceContractError("list response size changed")
        lowered = text.lower()
        if "web page blocked" in lowered or "web firewall" in lowered or "웹 방화벽" in text:
            raise TongyeongExperienceContractError("web firewall response")
        soup = BeautifulSoup(text, "html.parser")
        title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
        expected = "경상남도교육청 통합예약포털 견학/체험-교실형안전체험관"
        if title != expected:
            raise TongyeongExperienceContractError("official page title changed")
        return soup

    def close(self) -> None:
        close = getattr(self.session, "close", None)
        if callable(close):
            close()


def _parse_operation_period(value: str) -> tuple[date, date]:
    match = _OPERATION_PERIOD.fullmatch(value)
    if match is None:
        raise TongyeongExperienceContractError("operation period shape changed")
    start = datetime.strptime(match.group("start"), "%Y.%m.%d.").date()
    end = datetime.strptime(match.group("end"), "%Y.%m.%d.").date()
    if start > end:
        raise TongyeongExperienceContractError("operation period is reversed")
    return start, end


def _parse_reception_period(value: str) -> tuple[datetime, datetime]:
    match = _RECEPTION_PERIOD.fullmatch(value)
    if match is None:
        raise TongyeongExperienceContractError("reception period shape changed")
    start = datetime.strptime(match.group("start"), "%Y.%m.%d. %H:%M")
    end = datetime.strptime(match.group("end"), "%Y.%m.%d. %H:%M")
    if start > end:
        raise TongyeongExperienceContractError("reception period is reversed")
    return start, end


def _control_values(form: Tag) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for control in form.select("input[name]"):
        name = _clean(control.get("name"))
        values.setdefault(name, []).append(_clean(control.get("value")))
    return values


def _validate_official_region_field(soup: BeautifulSoup) -> None:
    forms = soup.select("form#exprnListForm")
    if len(forms) != 1:
        raise TongyeongExperienceContractError("experience search form changed")
    form = forms[0]
    institution = form.select("input[name='insttId']")
    active = form.select("a.on[data-seq='csec']")
    if (
        len(institution) != 1
        or _clean(institution[0].get("value")) != "csec"
        or len(active) != 1
        or _clean(active[0].get_text(" ", strip=True)) != "교실형안전체험관"
    ):
        raise TongyeongExperienceContractError("csec institution boundary changed")

    observed: dict[str, str] = {}
    for control in form.select("input[name='srchRsvAreaSe']"):
        code = _clean(control.get("value"))
        parent = control.parent
        if not isinstance(parent, Tag) or parent.name != "label":
            raise TongyeongExperienceContractError("official region label changed")
        label = _clean(parent.get_text(" ", strip=True))
        if not code or not label or code in observed:
            raise TongyeongExperienceContractError("official region field changed")
        observed[code] = label
    expected = {
        code: label
        for label, code in TONGYEONG_EXPERIENCE_REGION_FILTER_CODES.items()
    }
    if observed != expected:
        raise TongyeongExperienceContractError(
            "official 지역 field municipality whitelist changed"
        )


def _validate_paging_form(soup: BeautifulSoup, page_size: int) -> None:
    forms = soup.select("form[name='pagingForm']")
    if len(forms) != 1:
        raise TongyeongExperienceContractError("paging form changed")
    form = forms[0]
    action = _clean(form.get("action"))
    action_url = (
        f"https://{TONGYEONG_EXPERIENCE_HOST}{action}"
        if action.startswith("/")
        else action
    )
    parsed, query = _parse_url(action_url)
    if (
        _clean(form.get("method")).lower() != "post"
        or parsed.scheme != "https"
        or (parsed.hostname or "").lower() != TONGYEONG_EXPERIENCE_HOST
        or parsed.path != TONGYEONG_EXPERIENCE_PATH
        or query
    ):
        raise TongyeongExperienceContractError("paging form route changed")
    values = _control_values(form)
    expected = {
        "currPage": "1",
        "pageIndex": str(page_size),
        "limitRowCo": str(page_size),
        "maxSn": str(page_size),
        "minSn": "0",
        "limitOffset": "0",
        "insttId": "csec",
        "mi": "14341",
    }
    if any(values.get(key) != [value] for key, value in expected.items()):
        raise TongyeongExperienceContractError("paging completeness boundary changed")


def _parse_list_page(soup: BeautifulSoup, *, page_size: int) -> _ListPage:
    _validate_official_region_field(soup)
    _validate_paging_form(soup, page_size)
    total_matches = list(
        _DECLARED_TOTAL.finditer(_clean(soup.get_text(" ", strip=True)))
    )
    if len(total_matches) != 1:
        raise TongyeongExperienceContractError("declared total boundary changed")
    declared_total = int(total_matches[0].group("total"))
    declared_page = int(total_matches[0].group("page"))
    declared_last_page = int(total_matches[0].group("last"))
    expected_last_page = max(1, (declared_total + page_size - 1) // page_size)
    if declared_page != 1 or declared_last_page != expected_last_page:
        raise TongyeongExperienceContractError("declared pagination changed")
    tables = soup.select("table.reserv-list-table")
    if len(tables) != 1:
        raise TongyeongExperienceContractError("reservation list table changed")
    table = tables[0]
    headers = tuple(
        _clean(header.get_text(" ", strip=True))
        for header in table.select("thead th")
    )
    if headers != _LIST_HEADERS:
        raise TongyeongExperienceContractError(
            "list header changed or applicant/PII column appeared"
        )
    parsed_rows: list[_ExperienceRow] = []
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td", recursive=False)
        if len(cells) != len(_LIST_HEADERS):
            raise TongyeongExperienceContractError("list row column count changed")
        values = [_clean(cell.get_text(" ", strip=True)) for cell in cells]
        if not values[0].isdigit() or int(values[0]) < 1:
            raise TongyeongExperienceContractError("list ordinal changed")
        controls = cells[8].select("button[onclick]")
        if len(controls) != 1:
            raise TongyeongExperienceContractError("detail control changed")
        control = controls[0]
        control_match = _DETAIL_CONTROL.fullmatch(_clean(control.get("onclick")))
        if (
            control_match is None
            or _clean(control.get_text(" ", strip=True)) != values[8]
        ):
            raise TongyeongExperienceContractError("detail identity control changed")
        if values[8] not in _SOURCE_STATUSES:
            raise TongyeongExperienceContractError("unknown source status")
        title_match = _TITLE_REGION.fullmatch(values[2])
        if title_match is None:
            raise TongyeongExperienceContractError("official locality prefix changed")
        region = title_match.group("region")
        venue_match = _VENUE_BODY.fullmatch(title_match.group("body"))
        if region not in TONGYEONG_EXPERIENCE_REGION_MAP or venue_match is None:
            raise TongyeongExperienceContractError(
                "official region/venue whitelist changed"
            )
        if values[1] != "교실형안전체험관":
            raise TongyeongExperienceContractError("official institution changed")
        operation_start, operation_end = _parse_operation_period(values[3])
        reception_start, reception_end = _parse_reception_period(values[4])
        parsed_rows.append(
            _ExperienceRow(
                sequence=int(values[0]),
                identity=control_match.group("identity"),
                institution=values[1],
                title=values[2],
                region=region,
                venue=venue_match.group("venue"),
                operation_start=operation_start,
                operation_end=operation_end,
                operation_period=values[3],
                reception_start=reception_start,
                reception_end=reception_end,
                reception_period=values[4],
                audience=values[5],
                eligible_applicants=values[6],
                application_method=values[7],
                source_status=values[8],
            )
        )
    identities = [row.identity for row in parsed_rows]
    if len(identities) != len(set(identities)):
        raise TongyeongExperienceContractError("duplicate official identity")
    return _ListPage(
        page_size=page_size,
        declared_total=declared_total,
        declared_page=declared_page,
        declared_last_page=declared_last_page,
        rows=tuple(parsed_rows),
    )


def _row_signature(row: _ExperienceRow) -> tuple[Any, ...]:
    return (
        row.sequence,
        row.identity,
        row.institution,
        row.title,
        row.operation_period,
        row.reception_period,
        row.audience,
        row.eligible_applicants,
        row.application_method,
        row.source_status,
    )


def _page_signature(page: _ListPage) -> tuple[tuple[Any, ...], ...]:
    return (
        (
            page.declared_total,
            page.declared_page,
            page.declared_last_page,
        ),
        *(tuple(_row_signature(row) for row in page.rows)),
    )


def _output_row(row: _ExperienceRow) -> dict[str, Any]:
    normalized_status = "OPEN" if row.source_status == "예약하기" else "CLOSED"
    municipality_code, sigungu, municipality_name = (
        TONGYEONG_EXPERIENCE_REGION_MAP[row.region]
    )
    return {
        "provider": TONGYEONG_EXPERIENCE_PROVIDER,
        "municipality_code": municipality_code,
        "municipality_name": municipality_name,
        "region_sido": "경상남도",
        "region_sigungu": sigungu,
        "provider_course_id": (
            f"{TONGYEONG_EXPERIENCE_PROVIDER}:experience:{row.identity}"
        ),
        "source_course_id": row.identity,
        "title": row.title,
        "branch": row.venue,
        "branch_code": f"csec:{row.identity}",
        "branch_url": TONGYEONG_EXPERIENCE_URL,
        "preserve_branch": True,
        "category": "경남교육청 통합예약/견학·체험/교실형안전체험관",
        "collection_category": "공공예약",
        "domain_category": "체험·견학",
        "source_group": "municipal_reservation",
        "service_group": "체험",
        "service_group_policy": "locked",
        "classification_locked": True,
        "operator_type": "교육청/공공기관",
        "program_type": "체험",
        "source_status": row.source_status,
        "status_label": row.source_status,
        "status": normalized_status,
        # The public status remains useful, but no audited direct apply URL is
        # exposed.  Keep the downstream action invariant exact.
        "reservation_available": False,
        "period": row.operation_period,
        "program_period": row.operation_period,
        "start_date": row.operation_start,
        "end_date": row.operation_end,
        "application_period": row.reception_period,
        "application_start_date": row.reception_start.date(),
        "application_end_date": row.reception_end.date(),
        "schedule_raw": row.operation_period,
        "target": row.audience,
        "eligible_applicants": row.eligible_applicants,
        "application_method": row.application_method,
        "venue_name": row.venue,
        "venue_address": "",
        "address": "",
        "capacity": "",
        # The official application/detail action is POST-only and outside the
        # audited safe-list boundary.  Never manufacture or expose it.
        "application_url": "",
        "raw_url": TONGYEONG_EXPERIENCE_URL,
        "source_url": TONGYEONG_EXPERIENCE_URL,
        "raw_fields": {
            "parser": TONGYEONG_EXPERIENCE_PARSER,
            "official_institution_filter": "csec",
            "official_program_identity": row.identity,
            "official_source_ordinal": row.sequence,
            "official_locality_prefix": row.region,
            "official_municipality_code": municipality_code,
            "official_region_mapping_basis": (
                "csec facility title prefix cross-checked with official 지역 field"
            ),
            "official_venue_name": row.venue,
            "official_institution": row.institution,
            "official_operation_period": row.operation_period,
            "official_reception_period": row.reception_period,
            "official_source_status": row.source_status,
            "public_list_record": True,
            "detail_control_observed_not_called": True,
            "application_control_not_called": True,
        },
    }


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                child_path = f"{path}.{key}" if path else str(key)
                if str(key).lower() in _FORBIDDEN_OUTPUT_KEYS:
                    errors.append(f"forbidden key {child_path}")
                walk(child, child_path)
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")
        elif isinstance(value, str):
            if path.lower().endswith("url"):
                if value and not _same_url(value, TONGYEONG_EXPERIENCE_URL):
                    errors.append(f"non-allowlisted URL in {path}")
            elif _PHONE.search(value) or _EMAIL.search(value):
                errors.append(f"PII value in {path}")

    walk(row, "")
    return errors


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            output.append(row)
    return output


def _identity_hash(values: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(sorted(values)).encode("utf-8")).hexdigest()


def _meta() -> dict[str, Any]:
    return {
        "errors": [],
        "error_kind": "",
        "configured_collection_error": "",
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "pagination_complete": False,
        "partitions_complete": False,
        "details_complete": False,
        "logical_requests": 0,
        "list_requests": 0,
        "detail_requests": 0,
        "unsafe_endpoint_requests": 0,
        "application_endpoint_requests": 0,
        "login_endpoint_requests": 0,
        "identity_endpoint_requests": 0,
        "applicant_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "download_endpoint_requests": 0,
        "pii_endpoint_requests": 0,
        "sso_bootstrap_locations_observed_not_followed": 0,
    }


def collect_tongyeong_experience(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 2,
    detail_limit: int = 100,
    today: Optional[date | datetime | str] = None,
    session_factory: SessionFactory = _default_session,
    dedupe_rows: DedupeRows = _dedupe_default,
    fetcher: Fetcher = _default_fetcher,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return an atomic current/future snapshot of the audited GNE ledger."""

    meta = _meta()
    if not is_tongyeong_experience_target(target):
        meta["errors"] = ["target does not match the canonical experience list"]
        meta["error_kind"] = "contract"
        meta["configured_collection_error"] = meta["errors"][0]
        return [], TONGYEONG_EXPERIENCE_PARSER, meta
    if timeout < 1 or max_pages < 1 or detail_limit < 0:
        meta["errors"] = ["invalid collection limits"]
        meta["error_kind"] = "contract"
        meta["configured_collection_error"] = meta["errors"][0]
        return [], TONGYEONG_EXPERIENCE_PARSER, meta

    cutoff = _today(today)
    requester = _Requester(session_factory, fetcher, timeout, meta)
    try:
        first = _parse_list_page(
            requester.soup(
                "GET",
                TONGYEONG_EXPERIENCE_URL,
                {},
                permit_bootstrap=True,
            ),
            page_size=10,
        )
        complete = _parse_list_page(
            requester.soup(
                "POST",
                TONGYEONG_EXPERIENCE_POST_URL,
                TONGYEONG_EXPERIENCE_SAFE_POST_DATA,
            ),
            page_size=50,
        )
        if not complete.rows or len(complete.rows) > 50:
            raise TongyeongExperienceContractError("complete list size changed")
        if (
            complete.declared_total != len(complete.rows)
            or first.declared_total != complete.declared_total
        ):
            raise TongyeongExperienceContractError(
                "declared total does not match the complete ledger"
            )
        expected_ordinals = list(range(len(complete.rows), 0, -1))
        if [row.sequence for row in complete.rows] != expected_ordinals:
            raise TongyeongExperienceContractError(
                "ordinals do not prove an exact complete ledger"
            )
        if len(first.rows) != min(10, len(complete.rows)):
            raise TongyeongExperienceContractError("first page size changed")
        if tuple(_row_signature(row) for row in first.rows) != tuple(
            _row_signature(row) for row in complete.rows[: len(first.rows)]
        ):
            raise TongyeongExperienceContractError(
                "first page does not match complete ledger prefix"
            )

        stable_first = _parse_list_page(
            requester.soup("GET", TONGYEONG_EXPERIENCE_URL, {}),
            page_size=10,
        )
        stable_complete = _parse_list_page(
            requester.soup(
                "POST",
                TONGYEONG_EXPERIENCE_POST_URL,
                TONGYEONG_EXPERIENCE_SAFE_POST_DATA,
            ),
            page_size=50,
        )
        if _page_signature(stable_first) != _page_signature(first):
            raise TongyeongExperienceContractError("first-page boundary changed")
        if _page_signature(stable_complete) != _page_signature(complete):
            raise TongyeongExperienceContractError("complete-list boundary changed")

        current_source = [
            row for row in complete.rows if row.operation_end >= cutoff
        ]
        uncovered_current_regions = sorted(
            {
                row.region
                for row in current_source
                if row.region
                not in TONGYEONG_EXPERIENCE_CURRENT_COVERAGE_REGIONS
            }
        )
        if uncovered_current_regions:
            raise TongyeongExperienceContractError(
                "current region is outside audited coverage: "
                + ", ".join(uncovered_current_regions)
            )
        changed_current_identities = sorted(
            row.identity
            for row in current_source
            if TONGYEONG_EXPERIENCE_CURRENT_IDENTITY_WHITELIST.get(
                row.identity
            )
            != (row.region, row.venue)
        )
        if changed_current_identities:
            raise TongyeongExperienceContractError(
                "current identity/region/branch is outside the exact whitelist: "
                + ", ".join(changed_current_identities)
            )
        if len(current_source) > detail_limit:
            raise TongyeongExperienceContractError(
                "detail_limit truncates the current statewide ledger"
            )
        output = [_output_row(row) for row in current_source]
        privacy = [error for row in output for error in _privacy_errors(row)]
        if privacy:
            raise TongyeongExperienceContractError(
                f"PII/output allowlist violation: {privacy[0]}"
            )
        deduped = list(dedupe_rows(output))
        if len(deduped) != len(output):
            raise TongyeongExperienceContractError("dedupe changed complete output")

        source_region_counts = Counter(row.region for row in complete.rows)
        current_region_counts = Counter(row.region for row in current_source)
        source_status_counts = Counter(row.source_status for row in current_source)
        status_counts = Counter(row["status"] for row in deduped)
        municipality_counts = Counter(
            row["municipality_code"] for row in deduped
        )
        meta.update(
            {
                "provider": TONGYEONG_EXPERIENCE_PROVIDER,
                "ownership_scope": TONGYEONG_EXPERIENCE_OWNERSHIP_SCOPE,
                "cutoff": cutoff.isoformat(),
                "official_institution_filter": "csec",
                "official_institution_name": "교실형안전체험관",
                "source_total": len(complete.rows),
                "source_current_count": len(current_source),
                "source_expired_count": len(complete.rows) - len(current_source),
                "returned_count": len(deduped),
                "returned_municipality_count": len(municipality_counts),
                "municipality_counts": dict(sorted(municipality_counts.items())),
                "covered_current_regions": sorted(current_region_counts),
                "uncovered_current_regions": uncovered_current_regions,
                "audited_current_identity_count": len(current_source),
                "source_region_counts": dict(sorted(source_region_counts.items())),
                "current_source_region_counts": dict(
                    sorted(current_region_counts.items())
                ),
                "source_status_counts": dict(sorted(source_status_counts.items())),
                "status_counts": dict(sorted(status_counts.items())),
                "first_page_row_count": len(first.rows),
                "complete_page_row_count": len(complete.rows),
                "complete_page_size": 50,
                "declared_total": complete.declared_total,
                "declared_first_page_last": first.declared_last_page,
                "declared_complete_page_last": complete.declared_last_page,
                "exact_ordinal_first": complete.rows[0].sequence,
                "exact_ordinal_last": complete.rows[-1].sequence,
                "stable_first_page": True,
                "stable_complete_list": True,
                "official_detail_controls_observed_not_called": len(
                    complete.rows
                ),
                "statewide_reuse_contract": {
                    "institution_filter": "csec",
                    "locality_field": (
                        "official facility title bracket prefix cross-checked "
                        "with official 지역 field"
                    ),
                    "complete_list_method": "fixed_safe_list_post",
                    "declared_total_signal": (
                        "visible total plus descending ordinals to one"
                    ),
                    "page_size": 50,
                    "list_path": TONGYEONG_EXPERIENCE_PATH,
                    "detail_route_observed_not_called": True,
                },
                "source_identity_sha256": _identity_hash(
                    row.identity for row in complete.rows
                ),
                "output_identity_sha256": _identity_hash(
                    f"{TONGYEONG_EXPERIENCE_PROVIDER}:experience:{row.identity}"
                    for row in current_source
                ),
                "duplicate_count": 0,
                "no_current_data": not deduped,
                "safe_public_list_complete": True,
                "pagination_complete": True,
                "partitions_complete": True,
                "details_complete": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
            }
        )
        return deduped, TONGYEONG_EXPERIENCE_PARSER, meta
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        meta["errors"] = [message]
        meta["error_kind"] = (
            "contract"
            if isinstance(exc, TongyeongExperienceContractError)
            else "network_or_parse"
        )
        meta["configured_collection_error"] = message
        return [], TONGYEONG_EXPERIENCE_PARSER, meta
    finally:
        requester.close()


collect = collect_tongyeong_experience
collect_gne_csec_experience = collect_tongyeong_experience
is_gne_csec_experience_target = is_tongyeong_experience_target


__all__ = [
    name for name in globals() if name.startswith("TONGYEONG_EXPERIENCE_")
] + [
    "TongyeongExperienceContractError",
    "collect",
    "collect_gne_csec_experience",
    "collect_tongyeong_experience",
    "is_gne_csec_experience_target",
    "is_target",
    "is_tongyeong_experience_target",
]
