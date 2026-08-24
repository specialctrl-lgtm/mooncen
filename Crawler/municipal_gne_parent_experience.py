"""Fail-closed collector for the GNE parent experience ledger.

The Gyeongsangnam-do Office of Education reservation portal publishes one
province-wide public experience list.  This collector owns only the exact
current/future identities audited for six official institutions and six
municipalities.  Every other institution and identity is excluded, including
the separately owned ``csec`` classroom-safety ledger and two Geoje
school-visiting programmes.

Only the canonical list GET and the same-path public list POST are allowed.
The first GET may return the portal's SSO bootstrap 302; the Location is
observed but never followed, and the exact GET is retried.  Detail,
application, SSO, login/auth, member/applicant, attachment, download, and all
other routes fail closed.  The list already contains identity, operation and
reception periods, audience, eligibility, method, and status, so no detail
request is necessary.
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

from bs4 import BeautifulSoup, Tag
import requests

from Crawler.municipal_tongyeong_experience import (
    TONGYEONG_EXPERIENCE_CURRENT_IDENTITY_WHITELIST,
    TONGYEONG_EXPERIENCE_PROVIDER,
)


GNE_PARENT_PROVIDER = "MUNI_SERVICE_GNE_GO_KR_8A9E7604"
GNE_PARENT_CANDIDATE_ID = "MUNI_IR_0B7CF53680D0"
GNE_PARENT_HOST = "service.gne.go.kr"
GNE_PARENT_PATH = "/yeyak/exprn/exprnList.do"
GNE_PARENT_URL = "https://service.gne.go.kr/yeyak/exprn/exprnList.do?mi=6927"
GNE_PARENT_POST_URL = "https://service.gne.go.kr/yeyak/exprn/exprnList.do"
GNE_PARENT_BOOTSTRAP_LOCATION = "https://service.gne.go.kr/sso/agentInitProc.jsp"
GNE_PARENT_PAGE_SIZE = 50
GNE_PARENT_MAX_HTML_BYTES = 2_000_000
GNE_PARENT_OWNERSHIP_SCOPE = (
    "gne_parent_complete_public_experience_ledger_exact_six_municipality_whitelist"
)
GNE_PARENT_PARSER = (
    "gne_parent_experience_complete_219_ledger+first_get_sso_bootstrap_not_followed+"
    "five_safe_post_pages+exact_empty_page6_sentinel+stable_get_first_last_sentinel+"
    "continuous_descending_ordinals+all_public_list_fields+"
    "exact_six_institution_current_identity_whitelist+geoje_school_visit_excluded+"
    "csec_owner_disjoint+locked_experience+empty_address+"
    "canonical_list_get_and_same_path_fixed_post_only+"
    "no_detail_apply_sso_login_auth_member_applicant_attachment_download_or_pii_calls+"
    "atomic_snapshot"
)

GNE_PARENT_INSTITUTIONS: Mapping[str, tuple[str, str, str]] = {
    "거제수학체험센터": ("4831000000", "거제시", "경상남도 거제시"),
    "거창수학체험센터": ("4888000000", "거창군", "경상남도 거창군"),
    "경상남도고성교육지원청": ("4882000000", "고성군", "경상남도 고성군"),
    "과학교육원 우포생태교육원": ("4874000000", "창녕군", "경상남도 창녕군"),
    "밀양수학체험센터": ("4827000000", "밀양시", "경상남도 밀양시"),
    "합천미래교육지구": ("4889000000", "합천군", "경상남도 합천군"),
}

GNE_PARENT_IDENTITY_WHITELIST: Mapping[str, str] = {
    "10318": "거제수학체험센터",
    "10316": "거제수학체험센터",
    "10379": "거제수학체험센터",
    "10377": "거제수학체험센터",
    "10332": "거제수학체험센터",
    "10397": "거창수학체험센터",
    "10426": "경상남도고성교육지원청",
    "10428": "경상남도고성교육지원청",
    "10430": "경상남도고성교육지원청",
    "10432": "경상남도고성교육지원청",
    "10402": "경상남도고성교육지원청",
    "10403": "경상남도고성교육지원청",
    "10404": "경상남도고성교육지원청",
    "10409": "경상남도고성교육지원청",
    "1004": "과학교육원 우포생태교육원",
    "10340": "밀양수학체험센터",
    "10338": "밀양수학체험센터",
    "10419": "밀양수학체험센터",
    "10417": "밀양수학체험센터",
    "2526": "합천미래교육지구",
    "1417": "합천미래교육지구",
}

GNE_PARENT_EXCLUDED_SCHOOL_VISIT_IDENTITIES: Mapping[str, str] = {
    "1952": "거제수학체험센터",
    "4153": "거제수학체험센터",
}

_SAFE_POST_FIXED: Mapping[str, str] = {
    "srchExSeq": "",
    "limitOffset": "0",
    "insttId": "",
    "exprnEstbsSeq": "",
    "srchRsvEndde": "",
    "srchRsvBgnde": "",
    "limitRowCo": "50",
    "cmmnCode": "gradeSe",
    "maxSn": "50",
    "srchTxt": "",
    "pageIndex": "50",
    "srchPeriodDiv": "rcept",
    "mi": "6927",
    "minSn": "0",
}

GNE_PARENT_LIVE_AUDIT_BASELINE: Mapping[str, Any] = {
    "checked_at": "2026-08-05",
    "source_total": 219,
    "source_current_count": 86,
    "page_row_counts": [50, 50, 50, 50, 19],
    "sentinel_page": 6,
    "returned_count": 21,
    "municipality_counts": {
        "4827000000": 4,
        "4831000000": 5,
        "4874000000": 1,
        "4882000000": 8,
        "4888000000": 1,
        "4889000000": 2,
    },
    "status_counts": {"CLOSED": 8, "OPEN": 9, "SCHEDULED": 4},
    "detail_endpoint_requests": 0,
    "application_endpoint_requests": 0,
}

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, str, Mapping[str, str], int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE = re.compile(r"\s+")
_DETAIL_CONTROL = re.compile(
    r"goViewExprn\('(?P<identity>\d+)',\s*'view',\s*this\);?",
    re.IGNORECASE,
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
_STATUS_MAP = {
    "예약하기": "OPEN",
    "예약전": "SCHEDULED",
    "예약마감": "CLOSED",
}
_NO_DATA_TEXT = "조회된 데이터가 없습니다."
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


class GneParentExperienceContractError(ValueError):
    """Raised when the audited public parent-list contract changes."""


@dataclass(frozen=True)
class _ExperienceRow:
    sequence: int
    identity: str
    institution: str
    title: str
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
    requested_page: int
    page_size: int
    declared_total: int
    declared_page: int
    declared_last_page: int
    rows: tuple[_ExperienceRow, ...]
    exact_empty_sentinel: bool


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
        raise GneParentExperienceContractError("duplicate query key")
    if parsed.username or parsed.password or parsed.params or parsed.fragment:
        raise GneParentExperienceContractError("unsafe URL authority or fragment")
    try:
        if parsed.port is not None:
            raise GneParentExperienceContractError("explicit port is forbidden")
    except ValueError as exc:
        raise GneParentExperienceContractError("invalid URL port") from exc
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


def gne_parent_post_data(page: int) -> dict[str, str]:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise GneParentExperienceContractError("invalid list page")
    return {"currPage": str(page), **_SAFE_POST_FIXED}


def _validate_request(method: str, url: str, data: Mapping[str, str]) -> None:
    normalized_method = _clean(method).upper()
    if normalized_method == "GET":
        valid = not data and _same_url(url, GNE_PARENT_URL)
    elif normalized_method == "POST":
        try:
            page = int(_clean(data.get("currPage")))
        except (TypeError, ValueError):
            page = 0
        valid = _same_url(url, GNE_PARENT_POST_URL) and dict(data) == gne_parent_post_data(page)
    else:
        valid = False
    if not valid:
        raise GneParentExperienceContractError(
            "detail/apply/SSO/login/auth/member/applicant/attachment/download/PII route refused"
        )


def is_gne_parent_experience_target(target: Any) -> bool:
    try:
        return bool(
            _clean(_target_value(target, "provider")) == GNE_PARENT_PROVIDER
            and _same_url(_clean(_target_value(target, "url")), GNE_PARENT_URL)
        )
    except GneParentExperienceContractError:
        return False


is_target = is_gne_parent_experience_target


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
        return current.get(url, timeout=timeout, allow_redirects=False, verify=True)
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
            if not _same_url(location, GNE_PARENT_BOOTSTRAP_LOCATION):
                raise GneParentExperienceContractError(
                    "unexpected bootstrap redirect location"
                )
            self.bootstrap_seen = True
            self.meta["sso_bootstrap_locations_observed_not_followed"] += 1
            response = self._fetch(method, url, data)
        return self._response_soup(response, url)

    def _fetch(self, method: str, url: str, data: Mapping[str, str]) -> Any:
        self.meta["logical_requests"] += 1
        self.meta["list_requests"] += 1
        return self.fetcher(self.session, method, url, data, self.timeout)

    def _response_soup(self, response: Any, url: str) -> BeautifulSoup:
        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            raise GneParentExperienceContractError(f"HTTP {status}")
        if tuple(getattr(response, "history", ()) or ()):
            raise GneParentExperienceContractError("redirect history is forbidden")
        headers = getattr(response, "headers", {}) or {}
        if any(
            str(key).lower() == "location" and value
            for key, value in headers.items()
        ):
            raise GneParentExperienceContractError("redirect location is forbidden")
        final_url = _clean(getattr(response, "url", ""))
        if final_url and not _same_url(final_url, url):
            raise GneParentExperienceContractError("response URL changed")
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
            raise GneParentExperienceContractError("non-HTML list response")
        content = getattr(response, "content", None)
        if content is None:
            content = str(getattr(response, "text", "")).encode("utf-8")
        else:
            content = bytes(content)
        if not content or len(content) > GNE_PARENT_MAX_HTML_BYTES:
            raise GneParentExperienceContractError("list response size changed")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GneParentExperienceContractError("list response is not strict UTF-8") from exc
        lowered = text.lower()
        if "web page blocked" in lowered or "web firewall" in lowered or "웹 방화벽" in text:
            raise GneParentExperienceContractError("web firewall response")
        soup = BeautifulSoup(text, "html.parser")
        title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
        if title != "경상남도교육청 통합예약포털 -견학/체험":
            raise GneParentExperienceContractError("official page title changed")
        return soup

    def close(self) -> None:
        close = getattr(self.session, "close", None)
        if callable(close):
            close()


def _parse_operation_period(value: str) -> tuple[date, date]:
    match = _OPERATION_PERIOD.fullmatch(value)
    if match is None:
        raise GneParentExperienceContractError("operation period shape changed")
    start = datetime.strptime(match.group("start"), "%Y.%m.%d.").date()
    end = datetime.strptime(match.group("end"), "%Y.%m.%d.").date()
    if start > end:
        raise GneParentExperienceContractError("operation period is reversed")
    return start, end


def _parse_reception_period(value: str) -> tuple[datetime, datetime]:
    match = _RECEPTION_PERIOD.fullmatch(value)
    if match is None:
        raise GneParentExperienceContractError("reception period shape changed")
    start = datetime.strptime(match.group("start"), "%Y.%m.%d. %H:%M")
    end = datetime.strptime(match.group("end"), "%Y.%m.%d. %H:%M")
    if start > end:
        raise GneParentExperienceContractError("reception period is reversed")
    return start, end


def _control_values(form: Tag) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for control in form.select("input[name]"):
        name = _clean(control.get("name"))
        values.setdefault(name, []).append(_clean(control.get("value")))
    return values


def _validate_parent_form(soup: BeautifulSoup) -> None:
    forms = soup.select("form#exprnListForm")
    if len(forms) != 1:
        raise GneParentExperienceContractError("experience search form changed")
    form = forms[0]
    institution = form.select("input[name='insttId']")
    if len(institution) != 1 or _clean(institution[0].get("value")):
        raise GneParentExperienceContractError("parent institution boundary changed")
    form_text = _clean(form.get_text(" ", strip=True))
    if any(name not in form_text for name in GNE_PARENT_INSTITUTIONS):
        raise GneParentExperienceContractError("owned institution menu changed")


def _validate_paging_form(
    soup: BeautifulSoup,
    *,
    requested_page: int,
    page_size: int,
) -> None:
    forms = soup.select("form[name='pagingForm']")
    if len(forms) != 1:
        raise GneParentExperienceContractError("paging form changed")
    form = forms[0]
    action = _clean(form.get("action"))
    action_url = f"https://{GNE_PARENT_HOST}{action}" if action.startswith("/") else action
    parsed, query = _parse_url(action_url)
    if (
        _clean(form.get("method")).lower() != "post"
        or parsed.scheme != "https"
        or (parsed.hostname or "").lower() != GNE_PARENT_HOST
        or parsed.path != GNE_PARENT_PATH
        or query
    ):
        raise GneParentExperienceContractError("paging form route changed")
    values = _control_values(form)
    if page_size == GNE_PARENT_PAGE_SIZE:
        expected = gne_parent_post_data(requested_page)
        expected["limitOffset"] = str((requested_page - 1) * page_size)
        expected["maxSn"] = str(requested_page * page_size)
        expected["minSn"] = str((requested_page - 1) * page_size)
    else:
        expected = {
            "currPage": str(requested_page),
            "cmmnCode": "gradeSe",
            "maxSn": str(page_size),
            "pageIndex": str(page_size),
            "limitOffset": "0",
            "mi": "6927",
            "minSn": "0",
            "limitRowCo": str(page_size),
        }
    if set(values) != set(expected) or any(
        values.get(key) != [value] for key, value in expected.items()
    ):
        raise GneParentExperienceContractError("paging completeness boundary changed")


def _parse_list_page(
    soup: BeautifulSoup,
    *,
    requested_page: int,
    page_size: int,
) -> _ListPage:
    _validate_parent_form(soup)
    _validate_paging_form(soup, requested_page=requested_page, page_size=page_size)
    total_matches = list(_DECLARED_TOTAL.finditer(_clean(soup.get_text(" ", strip=True))))
    if len(total_matches) != 1:
        raise GneParentExperienceContractError("declared total boundary changed")
    declared_total = int(total_matches[0].group("total"))
    declared_page = int(total_matches[0].group("page"))
    declared_last_page = int(total_matches[0].group("last"))
    expected_last_page = max(1, (declared_total + page_size - 1) // page_size)
    if declared_page != requested_page or declared_last_page != expected_last_page:
        raise GneParentExperienceContractError("declared pagination changed")
    tables = soup.select("table.reserv-list-table")
    if len(tables) != 1:
        raise GneParentExperienceContractError("reservation list table changed")
    table = tables[0]
    headers = tuple(_clean(header.get_text(" ", strip=True)) for header in table.select("thead th"))
    if headers != _LIST_HEADERS:
        raise GneParentExperienceContractError(
            "list header changed or applicant/PII column appeared"
        )
    body_rows = table.select("tbody tr")
    if len(body_rows) == 1:
        cells = body_rows[0].find_all("td", recursive=False)
        if (
            len(cells) == 1
            and _clean(cells[0].get_text(" ", strip=True)) == _NO_DATA_TEXT
            and _clean(cells[0].get("colspan")) == str(len(_LIST_HEADERS))
        ):
            if requested_page <= declared_last_page:
                raise GneParentExperienceContractError("data page became empty")
            return _ListPage(
                requested_page=requested_page,
                page_size=page_size,
                declared_total=declared_total,
                declared_page=declared_page,
                declared_last_page=declared_last_page,
                rows=(),
                exact_empty_sentinel=True,
            )
    parsed_rows: list[_ExperienceRow] = []
    for tr in body_rows:
        cells = tr.find_all("td", recursive=False)
        if len(cells) != len(_LIST_HEADERS):
            raise GneParentExperienceContractError("list row column count changed")
        values = [_clean(cell.get_text(" ", strip=True)) for cell in cells]
        if not values[0].isdigit() or int(values[0]) < 1:
            raise GneParentExperienceContractError("list ordinal changed")
        controls = cells[8].select("button[onclick]")
        if len(controls) != 1:
            raise GneParentExperienceContractError("detail control changed")
        control_match = _DETAIL_CONTROL.fullmatch(_clean(controls[0].get("onclick")))
        if control_match is None or _clean(controls[0].get_text(" ", strip=True)) != values[8]:
            raise GneParentExperienceContractError("detail identity control changed")
        if values[8] not in _SOURCE_STATUSES:
            raise GneParentExperienceContractError("unknown source status")
        if not values[1] or not values[2]:
            raise GneParentExperienceContractError("institution or title is empty")
        operation_start, operation_end = _parse_operation_period(values[3])
        reception_start, reception_end = _parse_reception_period(values[4])
        parsed_rows.append(
            _ExperienceRow(
                sequence=int(values[0]),
                identity=control_match.group("identity"),
                institution=values[1],
                title=values[2],
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
        raise GneParentExperienceContractError("duplicate identity within page")
    if requested_page > declared_last_page or not parsed_rows:
        raise GneParentExperienceContractError("post-last page is not exact empty sentinel")
    return _ListPage(
        requested_page=requested_page,
        page_size=page_size,
        declared_total=declared_total,
        declared_page=declared_page,
        declared_last_page=declared_last_page,
        rows=tuple(parsed_rows),
        exact_empty_sentinel=False,
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


def _page_signature(page: _ListPage) -> tuple[Any, ...]:
    return (
        page.requested_page,
        page.page_size,
        page.declared_total,
        page.declared_page,
        page.declared_last_page,
        page.exact_empty_sentinel,
        tuple(_row_signature(row) for row in page.rows),
    )


def _branch_code(institution: str) -> str:
    digest = hashlib.sha1(institution.encode("utf-8")).hexdigest()[:8].upper()
    return f"GNE_PARENT_{digest}"


def _output_row(row: _ExperienceRow) -> dict[str, Any]:
    expected_institution = GNE_PARENT_IDENTITY_WHITELIST.get(row.identity)
    if expected_institution != row.institution:
        raise GneParentExperienceContractError("output identity/institution drift")
    municipality_code, sigungu, municipality_name = GNE_PARENT_INSTITUTIONS[row.institution]
    return {
        "provider": GNE_PARENT_PROVIDER,
        "municipality_code": municipality_code,
        "municipality_name": municipality_name,
        "municipality_full_name": municipality_name,
        "region_sido": "경상남도",
        "region_sigungu": sigungu,
        "region_full_name": municipality_name,
        "provider_course_id": f"{GNE_PARENT_PROVIDER}:experience:{row.identity}",
        "source_course_id": row.identity,
        "prefer_incoming_provider_course_id": True,
        "title": row.title,
        "branch": row.institution,
        "branch_code": _branch_code(row.institution),
        "branch_url": GNE_PARENT_URL,
        "preserve_branch": True,
        "provider_organizer": row.institution,
        "category": "경남교육청 통합예약/견학·체험",
        "collection_category": "공공예약",
        "domain_category": "체험·견학",
        "source_group": "municipal_reservation",
        "service_group": "체험",
        "service_group_policy": "locked",
        "service_family": "experience",
        "classification_locked": True,
        "operator_type": "교육청/공공기관",
        "program_type": "체험",
        "source_status": row.source_status,
        "status_label": row.source_status,
        "status": _STATUS_MAP[row.source_status],
        "reservation_available": False,
        "application_type": "INFO_ONLY",
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
        "venue": row.institution,
        "venue_name": row.institution,
        "venue_address": "",
        "address": "",
        "capacity": "",
        "application_url": "",
        "raw_url": GNE_PARENT_URL,
        "source_url": GNE_PARENT_URL,
        "collection_type": GNE_PARENT_PARSER,
        "raw_fields": {
            "parser": GNE_PARENT_PARSER,
            "official_program_identity": row.identity,
            "official_source_ordinal": row.sequence,
            "official_institution": row.institution,
            "official_municipality_code": municipality_code,
            "official_operation_period": row.operation_period,
            "official_reception_period": row.reception_period,
            "official_source_status": row.source_status,
            "public_list_record": True,
            "detail_control_observed_not_called": True,
            "application_control_not_called": True,
            "classification_locked": True,
            "service_family": "experience",
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
                if value and not _same_url(value, GNE_PARENT_URL):
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
        "classification_complete": False,
        "logical_requests": 0,
        "list_requests": 0,
        "detail_requests": 0,
        "unsafe_endpoint_requests": 0,
        "application_endpoint_requests": 0,
        "sso_endpoint_requests": 0,
        "login_endpoint_requests": 0,
        "auth_endpoint_requests": 0,
        "member_endpoint_requests": 0,
        "applicant_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "download_endpoint_requests": 0,
        "pii_endpoint_requests": 0,
        "sso_bootstrap_locations_observed_not_followed": 0,
    }


def collect_gne_parent_experience(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 10,
    detail_limit: int = 100,
    today: Optional[date | datetime | str] = None,
    session_factory: SessionFactory = _default_session,
    dedupe_rows: DedupeRows = _dedupe_default,
    fetcher: Fetcher = _default_fetcher,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return an atomic current/future snapshot of the exact parent owner."""

    meta = _meta()
    if not is_gne_parent_experience_target(target):
        meta["errors"] = ["target does not match the canonical parent experience list"]
        meta["error_kind"] = "contract"
        meta["configured_collection_error"] = meta["errors"][0]
        return [], GNE_PARENT_PARSER, meta
    if (
        isinstance(timeout, bool)
        or isinstance(max_pages, bool)
        or isinstance(detail_limit, bool)
        or timeout < 1
        or max_pages < 1
        or detail_limit < 0
    ):
        meta["errors"] = ["invalid collection limits"]
        meta["error_kind"] = "contract"
        meta["configured_collection_error"] = meta["errors"][0]
        return [], GNE_PARENT_PARSER, meta

    cutoff = _today(today)
    requester = _Requester(session_factory, fetcher, timeout, meta)
    try:
        first_get = _parse_list_page(
            requester.soup("GET", GNE_PARENT_URL, {}, permit_bootstrap=True),
            requested_page=1,
            page_size=10,
        )
        declared_data_pages = (first_get.declared_total + GNE_PARENT_PAGE_SIZE - 1) // GNE_PARENT_PAGE_SIZE
        if declared_data_pages + 1 > max_pages:
            raise GneParentExperienceContractError(
                "max_pages truncates declared ledger and exact sentinel"
            )

        pages: list[_ListPage] = []
        for page_number in range(1, declared_data_pages + 1):
            pages.append(
                _parse_list_page(
                    requester.soup(
                        "POST",
                        GNE_PARENT_POST_URL,
                        gne_parent_post_data(page_number),
                    ),
                    requested_page=page_number,
                    page_size=GNE_PARENT_PAGE_SIZE,
                )
            )
        sentinel_page_number = declared_data_pages + 1
        sentinel = _parse_list_page(
            requester.soup(
                "POST",
                GNE_PARENT_POST_URL,
                gne_parent_post_data(sentinel_page_number),
            ),
            requested_page=sentinel_page_number,
            page_size=GNE_PARENT_PAGE_SIZE,
        )
        if not sentinel.exact_empty_sentinel or sentinel.rows:
            raise GneParentExperienceContractError("page6 is not exact empty sentinel")

        if any(
            page.declared_total != first_get.declared_total
            or page.declared_last_page != declared_data_pages
            for page in [*pages, sentinel]
        ):
            raise GneParentExperienceContractError("declared page boundary changed")
        page_row_counts = [len(page.rows) for page in pages]
        expected_last_size = first_get.declared_total % GNE_PARENT_PAGE_SIZE or GNE_PARENT_PAGE_SIZE
        if page_row_counts != [GNE_PARENT_PAGE_SIZE] * (declared_data_pages - 1) + [expected_last_size]:
            raise GneParentExperienceContractError("declared page row counts changed")

        source = [row for page in pages for row in page.rows]
        identities = [row.identity for row in source]
        if len(source) != first_get.declared_total or len(identities) != len(set(identities)):
            raise GneParentExperienceContractError("declared total or identity set changed")
        expected_ordinals = list(range(first_get.declared_total, 0, -1))
        if [row.sequence for row in source] != expected_ordinals:
            raise GneParentExperienceContractError(
                "ordinals do not prove the complete parent ledger"
            )
        if len(first_get.rows) != 10 or tuple(_row_signature(row) for row in first_get.rows) != tuple(
            _row_signature(row) for row in source[:10]
        ):
            raise GneParentExperienceContractError("GET first page does not match POST prefix")

        stable_get = _parse_list_page(
            requester.soup("GET", GNE_PARENT_URL, {}),
            requested_page=1,
            page_size=10,
        )
        stable_first = _parse_list_page(
            requester.soup("POST", GNE_PARENT_POST_URL, gne_parent_post_data(1)),
            requested_page=1,
            page_size=GNE_PARENT_PAGE_SIZE,
        )
        stable_last = _parse_list_page(
            requester.soup(
                "POST",
                GNE_PARENT_POST_URL,
                gne_parent_post_data(declared_data_pages),
            ),
            requested_page=declared_data_pages,
            page_size=GNE_PARENT_PAGE_SIZE,
        )
        stable_sentinel = _parse_list_page(
            requester.soup(
                "POST",
                GNE_PARENT_POST_URL,
                gne_parent_post_data(sentinel_page_number),
            ),
            requested_page=sentinel_page_number,
            page_size=GNE_PARENT_PAGE_SIZE,
        )
        if (
            _page_signature(stable_get) != _page_signature(first_get)
            or _page_signature(stable_first) != _page_signature(pages[0])
            or _page_signature(stable_last) != _page_signature(pages[-1])
            or _page_signature(stable_sentinel) != _page_signature(sentinel)
        ):
            raise GneParentExperienceContractError("stable source boundary changed")

        current_source = [row for row in source if row.operation_end >= cutoff]
        known_institution_identities = {
            **GNE_PARENT_IDENTITY_WHITELIST,
            **GNE_PARENT_EXCLUDED_SCHOOL_VISIT_IDENTITIES,
        }
        unknown_owned = sorted(
            row.identity
            for row in current_source
            if row.institution in GNE_PARENT_INSTITUTIONS
            and known_institution_identities.get(row.identity) != row.institution
        )
        known_identity_drift = sorted(
            row.identity
            for row in current_source
            if row.identity in known_institution_identities
            and known_institution_identities[row.identity] != row.institution
        )
        if unknown_owned or known_identity_drift:
            changed = sorted(set(unknown_owned + known_identity_drift))
            raise GneParentExperienceContractError(
                "current owned institution identity is outside exact whitelist: "
                + ", ".join(changed)
            )

        selected = [
            row
            for row in current_source
            if GNE_PARENT_IDENTITY_WHITELIST.get(row.identity) == row.institution
        ]
        if len(selected) > detail_limit:
            raise GneParentExperienceContractError(
                "detail_limit truncates exact current parent owner"
            )
        csec_overlap = sorted(
            set(row.identity for row in selected)
            & set(TONGYEONG_EXPERIENCE_CURRENT_IDENTITY_WHITELIST)
        )
        if csec_overlap:
            raise GneParentExperienceContractError(
                "selected identities overlap existing csec owner: " + ", ".join(csec_overlap)
            )

        output = [_output_row(row) for row in selected]
        privacy = [error for row in output for error in _privacy_errors(row)]
        if privacy:
            raise GneParentExperienceContractError(
                f"PII/output allowlist violation: {privacy[0]}"
            )
        deduped = list(dedupe_rows(output))
        if len(deduped) != len(output):
            raise GneParentExperienceContractError("dedupe changed exact output")
        if any(bool(row["application_url"]) != row["reservation_available"] for row in deduped):
            raise GneParentExperienceContractError("application URL/availability contract changed")

        explicit_school_visit = [
            row
            for row in current_source
            if GNE_PARENT_EXCLUDED_SCHOOL_VISIT_IDENTITIES.get(row.identity) == row.institution
        ]
        non_owned_current = [
            row for row in current_source if row.institution not in GNE_PARENT_INSTITUTIONS
        ]
        classified_count = len(selected) + len(explicit_school_visit) + len(non_owned_current)
        if classified_count != len(current_source):
            raise GneParentExperienceContractError("current source classification is incomplete")

        municipality_counts = Counter(row["municipality_code"] for row in deduped)
        institution_counts = Counter(row["branch"] for row in deduped)
        source_status_counts = Counter(row.source_status for row in selected)
        status_counts = Counter(row["status"] for row in deduped)
        existing_csec_current_count = sum(
            row.institution == "교실형안전체험관" for row in current_source
        )
        meta.update(
            {
                "provider": GNE_PARENT_PROVIDER,
                "ownership_scope": GNE_PARENT_OWNERSHIP_SCOPE,
                "ownership_disjoint_from": TONGYEONG_EXPERIENCE_PROVIDER,
                "cutoff": cutoff.isoformat(),
                "source_total": len(source),
                "source_current_count": len(current_source),
                "source_expired_count": len(source) - len(current_source),
                "returned_count": len(deduped),
                "experience_rows": len(deduped),
                "returned_municipality_count": len(municipality_counts),
                "municipality_counts": dict(sorted(municipality_counts.items())),
                "institution_counts": dict(sorted(institution_counts.items())),
                "source_status_counts": dict(sorted(source_status_counts.items())),
                "status_counts": dict(sorted(status_counts.items())),
                "excluded_current_count": len(current_source) - len(selected),
                "excluded_non_owned_institution_count": len(non_owned_current),
                "excluded_geoje_school_visit_count": len(explicit_school_visit),
                "excluded_existing_csec_current_count": existing_csec_current_count,
                "existing_csec_source_identity_overlap_count": len(csec_overlap),
                "first_get_row_count": len(first_get.rows),
                "page_row_counts": page_row_counts,
                "data_pages": declared_data_pages,
                "sentinel_page": sentinel_page_number,
                "sentinel_pages": 1,
                "stable_rechecks": 4,
                "declared_total": first_get.declared_total,
                "declared_first_get_last": first_get.declared_last_page,
                "declared_post_last": pages[0].declared_last_page,
                "exact_ordinal_first": source[0].sequence,
                "exact_ordinal_last": source[-1].sequence,
                "official_detail_controls_observed_not_called": len(source),
                "application_urls": 0,
                "source_identity_sha256": _identity_hash(row.identity for row in source),
                "output_identity_sha256": _identity_hash(
                    f"{GNE_PARENT_PROVIDER}:experience:{row.identity}" for row in selected
                ),
                "duplicate_count": 0,
                "no_current_data": not deduped,
                "safe_public_list_complete": True,
                "pagination_complete": True,
                "partitions_complete": True,
                "details_complete": True,
                "classification_complete": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
            }
        )
        return deduped, GNE_PARENT_PARSER, meta
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        meta["errors"] = [message]
        meta["error_kind"] = (
            "contract"
            if isinstance(exc, GneParentExperienceContractError)
            else "network_or_parse"
        )
        meta["configured_collection_error"] = message
        return [], GNE_PARENT_PARSER, meta
    finally:
        requester.close()


collect = collect_gne_parent_experience


__all__ = [name for name in globals() if name.startswith("GNE_PARENT_")] + [
    "GneParentExperienceContractError",
    "collect",
    "collect_gne_parent_experience",
    "gne_parent_post_data",
    "is_gne_parent_experience_target",
    "is_target",
]
