"""Fail-closed collector for Seosan's official experience/visit ledgers.

The Seosan integrated-reservation navigation declares three public
``체험 / 견학`` partitions.  Two studio-room rentals are also exposed by the
children's-library partition, so rows are classified in context and those
facility rentals are not promoted as experiences.  Only identity-bearing
list rows and current/future public detail pages are requested.  Login,
application, applicant, attachment, calendar-action and PII endpoints are
outside the GET allowlist.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import requests


SEOSAN_EXPERIENCE_PROVIDER = "SEOSAN_WELFARE_TOTAL_RESERVATION"
SEOSAN_EXPERIENCE_CANDIDATE_ID = "MUNI_IR_01A8F328CC04"
SEOSAN_EXPERIENCE_HOST = "total.seosan.go.kr"
SEOSAN_EXPERIENCE_DIRECTORY_PATH = "/total/index.do"
SEOSAN_EXPERIENCE_LIST_PATH = "/total/selectFcltyResveSrvcListU.do"
SEOSAN_EXPERIENCE_DETAIL_PATH = "/total/fcltyResveSrvcViewU.do"
SEOSAN_EXPERIENCE_DIRECTORY_URL = (
    f"https://{SEOSAN_EXPERIENCE_HOST}{SEOSAN_EXPERIENCE_DIRECTORY_PATH}"
)
SEOSAN_EXPERIENCE_URL = (
    f"https://{SEOSAN_EXPERIENCE_HOST}{SEOSAN_EXPERIENCE_LIST_PATH}"
    "?key=28&searchResveSrvcSe=exprn"
)
SEOSAN_EXPERIENCE_MUNICIPALITY_CODE = "4421000000"
SEOSAN_EXPERIENCE_MUNICIPALITY_NAME = "충청남도 서산시"
SEOSAN_EXPERIENCE_PAGE_SIZE = 50
SEOSAN_EXPERIENCE_MAX_HTML_BYTES = 3_000_000
SEOSAN_EXPERIENCE_PARSER = (
    "seosan_official_experience_three_menu_partitions+declared_totals+"
    "exact_empty_post_last_sentinels+stable_directory_first_last_sentinel+"
    "current_future_public_details+contextual_notice_and_studio_exclusion+"
    "identity_allowlist+locked_experience+no_application_login_or_pii_calls"
)


@dataclass(frozen=True)
class SeosanExperiencePartition:
    key: str
    label: str
    query: tuple[tuple[str, str], ...]
    institution: str
    branch_code: str


SEOSAN_EXPERIENCE_PARTITIONS: tuple[SeosanExperiencePartition, ...] = (
    SeosanExperiencePartition(
        "city_library",
        "시립도서관",
        (("key", "14"), ("searchFcltyNo", "121")),
        "시립도서관",
        "SEOSAN_CITY_LIBRARY",
    ),
    SeosanExperiencePartition(
        "children_library",
        "어린이도서관",
        (("key", "646"), ("searchInstt", "05")),
        "어린이도서관",
        "SEOSAN_CHILDREN_LIBRARY",
    ),
    SeosanExperiencePartition(
        "city_safety_center",
        "도시안전통합센터",
        (("key", "287"), ("searchFcltyNo", "101")),
        "스마트정보과",
        "SEOSAN_CITY_SAFETY_CENTER",
    ),
)
_PARTITION_BY_KEY = {partition.key: partition for partition in SEOSAN_EXPERIENCE_PARTITIONS}

_STATUS_MAP = {
    "접수중": "OPEN",
    "예약가능": "OPEN",
    "접수예정": "SCHEDULED",
    "접수마감": "CLOSED",
    "예약마감": "CLOSED",
    "사용종료": "CLOSED",
    "운영종료": "CLOSED",
}
_EXPERIENCE_MARKERS = (
    "체험",
    "견학",
    "독서교실",
    "찾아가는 도서관",
    "도움 터 도서관",
)
_FACILITY_EXCLUSION_MARKERS = ("스튜디온", "스튜디오", "편집실", "회의실", "시설대관")
_EDITORIAL_EXCLUSION_MARKERS = ("공지사항", "공지", "알림", "휴관", "운영 안내", "게시판")
_LIST_HEADERS = ("번호", "예약서비스명", "접수/사용기간", "문의전화", "예약방법", "접수상태")
_DETAIL_FIELDS = {
    "시설명",
    "기관정보",
    "예약접수기간",
    "이용기간",
    "예약방법",
    "문의전화",
}
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)")
_TOTAL_RE = re.compile(
    r"총\s*게시물\s*([\d,]+)\s*개\s*,\s*페이지\s*([\d,]+)\s*/\s*([\d,]+)"
)
_IDENTITY_RE = re.compile(r"[1-9]\d{0,11}")
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,2}[- ]?\d{3,4}[- ]?\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
_WARNING_SUFFIX_RE = re.compile(r"\s*\([^)]*(?:신청|취소|예약)[^)]*\)\s*$")

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class SeosanExperienceContractError(RuntimeError):
    """Raised when the audited public source contract changes."""


@dataclass(frozen=True)
class _ListRow:
    partition: SeosanExperiencePartition
    identity: str
    title: str
    apply_period: str
    apply_start: date
    apply_end: date
    event_period: str
    event_start: date
    event_end: date
    reservation_method: str
    source_status: str
    page: int
    detail_url: str


@dataclass(frozen=True)
class _ListPage:
    partition: SeosanExperiencePartition
    page: int
    total: int
    last: int
    rows: tuple[_ListRow, ...]


def _clean(value: Any) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def is_seosan_experience_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")) == SEOSAN_EXPERIENCE_PROVIDER
        and _clean(_target_value(target, "url")) == SEOSAN_EXPERIENCE_URL
    )


is_target = is_seosan_experience_target


def seosan_experience_list_url(partition: SeosanExperiencePartition, page: int) -> str:
    if partition not in SEOSAN_EXPERIENCE_PARTITIONS:
        raise ValueError("unknown Seosan experience partition")
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("page must be a positive integer")
    query = (*partition.query, ("pageUnit", str(SEOSAN_EXPERIENCE_PAGE_SIZE)), ("pageIndex", str(page)))
    return (
        f"https://{SEOSAN_EXPERIENCE_HOST}{SEOSAN_EXPERIENCE_LIST_PATH}?"
        f"{urlencode(query)}"
    )


def seosan_experience_detail_url(
    partition: SeosanExperiencePartition, identity: Any
) -> str:
    value = _clean(identity)
    if partition not in SEOSAN_EXPERIENCE_PARTITIONS or not _IDENTITY_RE.fullmatch(value):
        raise ValueError("invalid Seosan experience identity")
    query = (
        ("key", dict(partition.query)["key"]),
        ("fcltyResveSrvcNo", value),
        ("searchResveSrvcSe", "fclty"),
        *(item for item in partition.query if item[0] != "key"),
    )
    return (
        f"https://{SEOSAN_EXPERIENCE_HOST}{SEOSAN_EXPERIENCE_DETAIL_PATH}?"
        f"{urlencode(query)}"
    )


def _canonical_key(url: str) -> tuple[str, str, tuple[tuple[str, str], ...]]:
    parsed = urlparse(_clean(url))
    return (
        parsed.scheme.lower(),
        parsed.netloc.lower() + parsed.path,
        tuple(sorted(parse_qsl(parsed.query, keep_blank_values=True))),
    )


def _request_kind(url: str) -> str:
    parsed = urlparse(_clean(url))
    if not (
        parsed.scheme == "https"
        and parsed.hostname == SEOSAN_EXPERIENCE_HOST
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
    ):
        raise SeosanExperienceContractError("unsafe official request URL")
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if parsed.path == SEOSAN_EXPERIENCE_DIRECTORY_PATH and not query:
        return "directory"
    if parsed.path == SEOSAN_EXPERIENCE_LIST_PATH:
        for partition in SEOSAN_EXPERIENCE_PARTITIONS:
            prefix = list(partition.query) + [("pageUnit", str(SEOSAN_EXPERIENCE_PAGE_SIZE))]
            if query[:-1] == prefix and len(query) == len(prefix) + 1:
                name, page = query[-1]
                if name == "pageIndex" and _IDENTITY_RE.fullmatch(page):
                    return "list"
    if parsed.path == SEOSAN_EXPERIENCE_DETAIL_PATH:
        for partition in SEOSAN_EXPERIENCE_PARTITIONS:
            expected = [
                ("key", dict(partition.query)["key"]),
                ("fcltyResveSrvcNo", ""),
                ("searchResveSrvcSe", "fclty"),
                *(item for item in partition.query if item[0] != "key"),
            ]
            if len(query) == len(expected):
                identity = dict(query).get("fcltyResveSrvcNo", "")
                candidate = [(key, identity if key == "fcltyResveSrvcNo" else value) for key, value in expected]
                if query == candidate and _IDENTITY_RE.fullmatch(identity):
                    return "detail"
    raise SeosanExperienceContractError("request is outside the public GET allowlist")


def _default_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 municipal-course-crawler/1.0",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=False)


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

    def soup(self, url: str) -> BeautifulSoup:
        kind = _request_kind(url)
        self.meta["logical_requests"] += 1
        self.meta[f"{kind}_requests"] += 1
        response = self.fetcher(self.session, url, self.timeout)
        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            raise SeosanExperienceContractError(f"unexpected HTTP status {status}")
        if tuple(getattr(response, "history", ()) or ()):
            raise SeosanExperienceContractError("redirect history is forbidden")
        headers = getattr(response, "headers", {}) or {}
        if any(str(key).lower() == "location" and value for key, value in headers.items()):
            raise SeosanExperienceContractError("redirect location is forbidden")
        final_url = _clean(getattr(response, "url", ""))
        if final_url and _canonical_key(final_url) != _canonical_key(url):
            raise SeosanExperienceContractError("official response URL changed")
        content_type = _clean(
            next(
                (value for key, value in headers.items() if str(key).lower() == "content-type"),
                "text/html",
            )
        ).lower()
        if "html" not in content_type:
            raise SeosanExperienceContractError("official response is not HTML")
        body = getattr(response, "content", None)
        if body is None:
            body = str(getattr(response, "text", response)).encode("utf-8")
        body = bytes(body)
        if not body or len(body) > SEOSAN_EXPERIENCE_MAX_HTML_BYTES:
            raise SeosanExperienceContractError("empty or oversized official response")
        soup = BeautifulSoup(body, "html.parser")
        title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
        if not title or (kind != "directory" and not title.endswith("- 통합예약시스템")):
            raise SeosanExperienceContractError("official page title changed")
        if kind == "directory" and title != "통합예약시스템":
            raise SeosanExperienceContractError("official directory title changed")
        return soup

    def close(self) -> None:
        close = getattr(self.session, "close", None)
        if callable(close):
            close()


def _base_partition_url(partition: SeosanExperiencePartition) -> str:
    return (
        f"https://{SEOSAN_EXPERIENCE_HOST}{SEOSAN_EXPERIENCE_LIST_PATH}?"
        f"{urlencode(partition.query)}"
    )


def _validate_directory(soup: BeautifulSoup) -> tuple[tuple[str, str], ...]:
    candidates = []
    for item in soup.select("#lnb li.depth1"):
        anchor = item.select_one(":scope > a.depth1_ti[href]")
        if anchor and _clean(anchor.get_text(" ", strip=True)) == "체험 / 견학":
            candidates.append(item)
    if len(candidates) != 1:
        raise SeosanExperienceContractError("official experience menu root changed")
    observed: dict[str, str] = {}
    for anchor in candidates[0].select("ul.depth2 > li > a[href]"):
        label = _clean(anchor.get_text(" ", strip=True))
        if label in observed:
            raise SeosanExperienceContractError("duplicate experience menu partition")
        observed[label] = urljoin(SEOSAN_EXPERIENCE_DIRECTORY_URL, _clean(anchor.get("href")))
    expected = {partition.label: _base_partition_url(partition) for partition in SEOSAN_EXPERIENCE_PARTITIONS}
    if observed != expected:
        raise SeosanExperienceContractError("official experience partition registry changed")
    return tuple(sorted(observed.items()))


def _date_bounds(value: Any, field: str) -> tuple[date, date]:
    matches = _DATE_RE.findall(_clean(value))
    if len(matches) != 2:
        raise SeosanExperienceContractError(f"{field} must contain two ISO dates")
    start, end = (date(int(year), int(month), int(day)) for year, month, day in matches)
    if end < start:
        raise SeosanExperienceContractError(f"{field} is reversed")
    return start, end


def _validate_list_form(soup: BeautifulSoup, partition: SeosanExperiencePartition) -> None:
    form = soup.select_one("form#fcltyResveSrvcForm")
    if form is None or _clean(form.get("method")).lower() != "get":
        raise SeosanExperienceContractError(f"{partition.key}: list search form changed")
    action = urlparse(urljoin(SEOSAN_EXPERIENCE_DIRECTORY_URL, _clean(form.get("action"))))
    if action.hostname != SEOSAN_EXPERIENCE_HOST or action.path != SEOSAN_EXPERIENCE_LIST_PATH:
        raise SeosanExperienceContractError(f"{partition.key}: list action changed")
    values: dict[str, str] = {}
    for control in form.select("input[name], select[name]"):
        name = _clean(control.get("name"))
        if control.name == "select":
            selected = control.select_one("option[selected]")
            value = _clean(selected.get("value") if selected else "")
        else:
            value = _clean(control.get("value"))
        values[name] = value
    expected = dict(partition.query)
    if values.get("key") != expected["key"] or values.get("searchResveSrvcSe") != "fclty":
        raise SeosanExperienceContractError(f"{partition.key}: reservation ledger scope changed")
    for name in ("searchInstt", "searchFcltyNo"):
        if name in expected and values.get(name) != expected[name]:
            raise SeosanExperienceContractError(f"{partition.key}: partition filter changed")


def _validate_identity_link(
    href: Any, partition: SeosanExperiencePartition, page: int
) -> tuple[str, str]:
    url = urljoin(SEOSAN_EXPERIENCE_DIRECTORY_URL, _clean(href))
    parsed = urlparse(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    values = dict(query)
    required = {
        "key",
        "fcltyResveSrvcNo",
        "searchResveSrvcSe",
        "searchInstt",
        "searchFcltyNo",
        "searchRceptBgnde",
        "searchRceptEndde",
        "pageUnit",
        "searchCnd",
        "searchKrwd",
        "pageIndex",
    }
    identity = values.get("fcltyResveSrvcNo", "")
    expected = dict(partition.query)
    if not (
        parsed.scheme == "https"
        and parsed.hostname == SEOSAN_EXPERIENCE_HOST
        and parsed.path == SEOSAN_EXPERIENCE_DETAIL_PATH
        and not parsed.fragment
        and len(query) == len(required)
        and set(values) == required
        and _IDENTITY_RE.fullmatch(identity)
        and values["key"] == expected["key"]
        and values["searchResveSrvcSe"] == "fclty"
        and values["searchInstt"] == expected.get("searchInstt", "")
        and values["searchFcltyNo"] == expected.get("searchFcltyNo", "")
        and values["searchRceptBgnde"] == values["searchRceptEndde"] == ""
        and values["pageUnit"] == str(SEOSAN_EXPERIENCE_PAGE_SIZE)
        and values["searchCnd"] == "all"
        and values["searchKrwd"] == ""
        and values["pageIndex"] == str(page)
    ):
        raise SeosanExperienceContractError(f"{partition.key}: public identity link changed")
    return identity, seosan_experience_detail_url(partition, identity)


def _parse_period_cell(value: Any) -> tuple[str, date, date, str, date, date]:
    text = _clean(value)
    match = re.fullmatch(r"접수\s*:\s*(.+?)\s+사용\s*:\s*(.+)", text)
    if match is None:
        raise SeosanExperienceContractError("reservation/use period cell changed")
    apply_period, event_period = _clean(match.group(1)), _clean(match.group(2))
    apply_start, apply_end = _date_bounds(apply_period, "application period")
    event_start, event_end = _date_bounds(event_period, "use period")
    return apply_period, apply_start, apply_end, event_period, event_start, event_end


def _parse_list_page(
    soup: BeautifulSoup, partition: SeosanExperiencePartition, page: int
) -> _ListPage:
    _validate_list_form(soup, partition)
    body = _clean(soup.get_text(" ", strip=True))
    match = _TOTAL_RE.search(body)
    if match is None:
        raise SeosanExperienceContractError(f"{partition.key}: pagination declaration missing")
    total, observed_page, last = (int(value.replace(",", "")) for value in match.groups())
    if observed_page != page or last < 1:
        raise SeosanExperienceContractError(f"{partition.key}: pagination declaration changed")
    table = soup.select_one("table.bbs_default.list")
    if table is None:
        raise SeosanExperienceContractError(f"{partition.key}: identity table missing")
    headers = tuple(_clean(cell.get_text(" ", strip=True)) for cell in table.select("thead th"))
    if headers != _LIST_HEADERS:
        raise SeosanExperienceContractError(f"{partition.key}: list columns changed")
    rows: list[_ListRow] = []
    for table_row in table.select("tbody > tr"):
        cells = table_row.select(":scope > td")
        if len(cells) == 1 and "등록된 게시물이 없습니다" in _clean(cells[0].get_text(" ", strip=True)):
            continue
        if len(cells) != len(_LIST_HEADERS):
            raise SeosanExperienceContractError(f"{partition.key}: list row shape changed")
        anchor = cells[1].select_one("a[href]")
        if anchor is None:
            raise SeosanExperienceContractError(f"{partition.key}: row lacks public identity")
        identity, detail_url = _validate_identity_link(anchor.get("href"), partition, page)
        title = _clean(anchor.get_text(" ", strip=True))
        if not title:
            raise SeosanExperienceContractError(f"{partition.key}:{identity}: blank title")
        apply_period, apply_start, apply_end, event_period, event_start, event_end = _parse_period_cell(
            cells[2].get_text(" ", strip=True)
        )
        phone = _clean(cells[3].get_text(" ", strip=True))
        if not _PHONE_RE.fullmatch(phone):
            raise SeosanExperienceContractError(f"{partition.key}:{identity}: contact shape changed")
        method = _clean(cells[4].get_text(" ", strip=True))
        if method not in {"선착순", "추첨"}:
            raise SeosanExperienceContractError(f"{partition.key}:{identity}: method changed")
        source_status = _clean(cells[5].get_text(" ", strip=True))
        if source_status not in _STATUS_MAP:
            raise SeosanExperienceContractError(
                f"{partition.key}:{identity}: unknown status {source_status!r}"
            )
        rows.append(
            _ListRow(
                partition,
                identity,
                title,
                apply_period,
                apply_start,
                apply_end,
                event_period,
                event_start,
                event_end,
                method,
                source_status,
                page,
                detail_url,
            )
        )
    if page <= last and total and not rows:
        raise SeosanExperienceContractError(f"{partition.key}: declared data page is empty")
    if page > last and rows:
        raise SeosanExperienceContractError(f"{partition.key}: post-last sentinel is not empty")
    return _ListPage(partition, page, total, last, tuple(rows))


def _classification(row: _ListRow) -> tuple[str, str]:
    title = row.title
    if any(marker in title for marker in _FACILITY_EXCLUSION_MARKERS):
        return "exclude", "facility_or_studio_rental"
    if any(marker in title for marker in _EDITORIAL_EXCLUSION_MARKERS):
        return "exclude", "notice_or_editorial"
    if any(marker in title for marker in _EXPERIENCE_MARKERS):
        return "experience", "official_experience_title"
    raise SeosanExperienceContractError(
        f"{row.partition.key}:{row.identity}: unclassified experience-menu row {title!r}"
    )


def _page_signature(page: _ListPage) -> tuple[Any, ...]:
    return (
        page.total,
        page.last,
        tuple(
            (
                row.identity,
                row.title,
                row.event_period,
                row.apply_period,
                row.source_status,
            )
            for row in page.rows
        ),
    )


def _detail_fields(soup: BeautifulSoup) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in soup.select("article#contents .day_plan .bg_in > ul.total_bu:first-of-type > li"):
        label = item.select_one(":scope > span")
        if label is None:
            continue
        name = _clean(label.get_text(" ", strip=True))
        clone = BeautifulSoup(str(item), "html.parser").select_one("li")
        clone_label = clone.select_one(":scope > span") if clone else None
        if clone_label is not None:
            clone_label.extract()
        value = _clean(clone.get_text(" ", strip=True) if clone else "")
        if name in result:
            raise SeosanExperienceContractError("duplicate public detail field")
        result[name] = value
    if set(result) != _DETAIL_FIELDS:
        raise SeosanExperienceContractError("public detail field contract changed")
    return result


def _semantic_title(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", _WARNING_SUFFIX_RE.sub("", value)).casefold()


def _row_from_detail(listed: _ListRow, soup: BeautifulSoup) -> dict[str, Any]:
    heading = soup.select_one("article#contents .detail_tit_top > strong")
    badges = tuple(
        _clean(value.get_text(" ", strip=True))
        for value in soup.select("article#contents .detail_tit_top > span")
    )
    if heading is None or listed.source_status not in badges or listed.reservation_method not in badges:
        raise SeosanExperienceContractError(
            f"{listed.partition.key}:{listed.identity}: detail status/method changed"
        )
    detail_title = _clean(heading.get_text(" ", strip=True))
    list_key, detail_key = _semantic_title(listed.title), _semantic_title(detail_title)
    if not list_key or not detail_key or list_key not in detail_key:
        raise SeosanExperienceContractError(
            f"{listed.partition.key}:{listed.identity}: list/detail title mismatch"
        )
    fields = _detail_fields(soup)
    if fields["기관정보"] != listed.partition.institution:
        raise SeosanExperienceContractError(
            f"{listed.partition.key}:{listed.identity}: institution changed"
        )
    if fields["예약방법"] != f"온라인 예약 / {listed.reservation_method}":
        raise SeosanExperienceContractError(
            f"{listed.partition.key}:{listed.identity}: detail method changed"
        )
    detail_apply_start, detail_apply_end = _date_bounds(fields["예약접수기간"], "detail application")
    detail_event_start, detail_event_end = _date_bounds(fields["이용기간"], "detail use")
    if (detail_apply_start, detail_apply_end) != (listed.apply_start, listed.apply_end) or (
        detail_event_start,
        detail_event_end,
    ) != (listed.event_start, listed.event_end):
        raise SeosanExperienceContractError(
            f"{listed.partition.key}:{listed.identity}: list/detail dates differ"
        )
    if not _PHONE_RE.fullmatch(fields["문의전화"]):
        raise SeosanExperienceContractError(
            f"{listed.partition.key}:{listed.identity}: detail contact shape changed"
        )
    identity_controls = 0
    for anchor in soup.select("article#contents a[href]"):
        href = _clean(anchor.get("href"))
        parsed = urlparse(urljoin(listed.detail_url, href))
        values = dict(parse_qsl(parsed.query, keep_blank_values=True))
        if "fcltyResveSrvcNo" in values:
            identity_controls += 1
            if values["fcltyResveSrvcNo"] != listed.identity:
                raise SeosanExperienceContractError(
                    f"{listed.partition.key}:{listed.identity}: detail identity control changed"
                )
    if identity_controls < 2:
        raise SeosanExperienceContractError(
            f"{listed.partition.key}:{listed.identity}: public identity controls missing"
        )
    raw_url = seosan_experience_detail_url(listed.partition, listed.identity)
    return {
        "provider": SEOSAN_EXPERIENCE_PROVIDER,
        "municipality_code": SEOSAN_EXPERIENCE_MUNICIPALITY_CODE,
        "municipality_name": SEOSAN_EXPERIENCE_MUNICIPALITY_NAME,
        "provider_course_id": (
            f"{SEOSAN_EXPERIENCE_PROVIDER}:experience:"
            f"{listed.partition.key}:{listed.identity}"
        ),
        "source_course_id": f"experience:{listed.partition.key}:{listed.identity}",
        "title": listed.title,
        "branch": fields["기관정보"],
        "branch_code": listed.partition.branch_code,
        "preserve_branch": True,
        "category": f"서산시/체험·견학/{listed.partition.label}",
        "collection_category": "공공예약",
        "domain_category": "체험·견학",
        "source_group": "municipal_reservation",
        "service_group": "체험",
        "service_group_policy": "locked",
        "classification_locked": True,
        "operator_type": "지자체/공공기관",
        "program_type": "체험·견학",
        "source_status": listed.source_status,
        "status": _STATUS_MAP[listed.source_status],
        "reservation_available": _STATUS_MAP[listed.source_status] == "OPEN",
        "period": listed.event_period,
        "start_date": listed.event_start.isoformat(),
        "end_date": listed.event_end.isoformat(),
        "apply_period": listed.apply_period,
        "apply_start_date": listed.apply_start.isoformat(),
        "apply_end_date": listed.apply_end.isoformat(),
        "target": "",
        "venue_name": fields["시설명"],
        "fee": "",
        "application_url": "",
        "raw_url": raw_url,
        "raw_fields": {
            "parser": SEOSAN_EXPERIENCE_PARSER,
            "official_partition": listed.partition.key,
            "official_identity": listed.identity,
            "official_source_status": listed.source_status,
            "list_page": listed.page,
            "public_detail_verified": True,
            "contact_discarded": True,
            "application_controls_observed_not_called": True,
        },
    }


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    forbidden = {"phone", "email", "contact", "manager", "applicant", "attachment", "login"}

    def walk(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                key_text = str(key).lower()
                child_path = f"{path}.{key}" if path else str(key)
                if key_text in forbidden:
                    errors.append(f"forbidden key {child_path}")
                walk(child, child_path)
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")
        elif isinstance(value, str) and (_PHONE_RE.search(value) or _EMAIL_RE.search(value)):
            errors.append(f"PII value in {path}")

    walk(row, "")
    return errors


def _default_dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _identity_hash(values: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(sorted(values)).encode("utf-8")).hexdigest()


def _audit_date(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(_clean(value))


def _meta() -> dict[str, Any]:
    return {
        "errors": [],
        "error_kind": "",
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "partition_registry_complete": False,
        "pagination_complete": False,
        "details_complete": False,
        "directory_requests": 0,
        "list_requests": 0,
        "detail_requests": 0,
        "logical_requests": 0,
        "application_endpoint_requests": 0,
        "login_endpoint_requests": 0,
        "applicant_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "calendar_action_requests": 0,
        "pii_endpoint_requests": 0,
        "pii_values_persisted": 0,
    }


def collect_seosan_experience(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 30,
    detail_limit: int = 100,
    today: Optional[date | datetime | str] = None,
    session_factory: SessionFactory = _default_session,
    fetcher: Fetcher = _default_fetcher,
    dedupe_rows: DedupeRows = _default_dedupe,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one atomic current/future snapshot from all official partitions."""

    meta = _meta()
    if not is_seosan_experience_target(target):
        meta["errors"] = ["target does not match the canonical Seosan experience route"]
        meta["error_kind"] = "contract"
        return [], SEOSAN_EXPERIENCE_PARSER, meta
    try:
        cutoff = _audit_date(today)
        if timeout < 1 or max_pages < 1 or detail_limit < 0:
            raise ValueError("invalid collection limits")
    except (TypeError, ValueError) as exc:
        meta["errors"] = [str(exc)]
        meta["error_kind"] = "contract"
        return [], SEOSAN_EXPERIENCE_PARSER, meta

    requester = _Requester(session_factory, fetcher, int(timeout), meta)
    try:
        directory_signature = _validate_directory(requester.soup(SEOSAN_EXPERIENCE_DIRECTORY_URL))
        all_source_rows: list[_ListRow] = []
        source_summaries: dict[str, dict[str, Any]] = {}
        total_data_pages = 0
        boundary_rechecks: dict[str, dict[str, bool]] = {}

        for partition in SEOSAN_EXPERIENCE_PARTITIONS:
            first = _parse_list_page(
                requester.soup(seosan_experience_list_url(partition, 1)), partition, 1
            )
            if first.last > max_pages:
                raise SeosanExperienceContractError(
                    f"{partition.key}: declared page count exceeds max_pages"
                )
            pages = [first]
            for page_number in range(2, first.last + 1):
                page = _parse_list_page(
                    requester.soup(seosan_experience_list_url(partition, page_number)),
                    partition,
                    page_number,
                )
                if page.total != first.total or page.last != first.last:
                    raise SeosanExperienceContractError(
                        f"{partition.key}: pagination declaration drift"
                    )
                pages.append(page)
            rows = [row for page in pages for row in page.rows]
            if len(rows) != first.total:
                raise SeosanExperienceContractError(
                    f"{partition.key}: declared total differs from identity rows"
                )
            if len({row.identity for row in rows}) != len(rows):
                raise SeosanExperienceContractError(
                    f"{partition.key}: duplicate source identities"
                )
            sentinel_page = first.last + 1
            sentinel = _parse_list_page(
                requester.soup(seosan_experience_list_url(partition, sentinel_page)),
                partition,
                sentinel_page,
            )
            if sentinel.total != first.total or sentinel.last != first.last or sentinel.rows:
                raise SeosanExperienceContractError(
                    f"{partition.key}: immediate empty sentinel changed"
                )
            stable_first = _parse_list_page(
                requester.soup(seosan_experience_list_url(partition, 1)), partition, 1
            )
            stable_last = stable_first
            if first.last != 1:
                stable_last = _parse_list_page(
                    requester.soup(seosan_experience_list_url(partition, first.last)),
                    partition,
                    first.last,
                )
            stable_sentinel = _parse_list_page(
                requester.soup(seosan_experience_list_url(partition, sentinel_page)),
                partition,
                sentinel_page,
            )
            checks = {
                "first": _page_signature(stable_first) == _page_signature(first),
                "last": _page_signature(stable_last) == _page_signature(pages[-1]),
                "sentinel": _page_signature(stable_sentinel) == _page_signature(sentinel),
            }
            if not all(checks.values()):
                raise SeosanExperienceContractError(
                    f"{partition.key}: boundary stability changed"
                )
            boundary_rechecks[partition.key] = checks
            all_source_rows.extend(rows)
            total_data_pages += first.last
            source_summaries[partition.key] = {
                "label": partition.label,
                "source_total": first.total,
                "pages": first.last,
                "sentinel_page": sentinel_page,
                "page_counts": {page.page: len(page.rows) for page in pages},
            }

        source_ids = [row.identity for row in all_source_rows]
        if len(source_ids) != len(set(source_ids)):
            raise SeosanExperienceContractError("identities overlap across official partitions")

        experience_rows: list[_ListRow] = []
        excluded_reasons: Counter[str] = Counter()
        excluded_ids: list[str] = []
        for listed in all_source_rows:
            decision, reason = _classification(listed)
            if decision == "experience":
                experience_rows.append(listed)
            else:
                excluded_reasons[reason] += 1
                excluded_ids.append(f"{listed.partition.key}:{listed.identity}")

        current_rows = [row for row in experience_rows if row.event_end >= cutoff]
        expired_rows = [row for row in experience_rows if row.event_end < cutoff]
        if len(current_rows) > detail_limit:
            raise SeosanExperienceContractError(
                "detail_limit truncates the current/future experience ledger"
            )

        output = [
            _row_from_detail(listed, requester.soup(listed.detail_url))
            for listed in current_rows
        ]
        if _validate_directory(requester.soup(SEOSAN_EXPERIENCE_DIRECTORY_URL)) != directory_signature:
            raise SeosanExperienceContractError("official experience registry changed during crawl")

        deduped = list(dedupe_rows(output))
        if len(deduped) != len(output):
            raise SeosanExperienceContractError("dedupe changed the complete current snapshot")
        privacy = [error for row in deduped for error in _privacy_errors(row)]
        if privacy:
            raise SeosanExperienceContractError("; ".join(privacy))
        deduped.sort(
            key=lambda row: (
                _clean(row.get("start_date")),
                _clean(row.get("title")),
                _clean(row.get("provider_course_id")),
            )
        )
        meta.update(
            {
                "checked_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds"),
                "cutoff": cutoff.isoformat(),
                "municipality_code": SEOSAN_EXPERIENCE_MUNICIPALITY_CODE,
                "municipality_name": SEOSAN_EXPERIENCE_MUNICIPALITY_NAME,
                "candidate_id": SEOSAN_EXPERIENCE_CANDIDATE_ID,
                "owner_provider": SEOSAN_EXPERIENCE_PROVIDER,
                "canonical_url": SEOSAN_EXPERIENCE_URL,
                "partitions": source_summaries,
                "partition_count": len(SEOSAN_EXPERIENCE_PARTITIONS),
                "pages": total_data_pages,
                "data_pages": total_data_pages,
                "boundary_rechecks": boundary_rechecks,
                "source_total": len(all_source_rows),
                "experience_source_count": len(experience_rows),
                "contextual_excluded_count": len(excluded_ids),
                "contextual_exclusion_reasons": dict(excluded_reasons),
                "contextual_excluded_ids": sorted(excluded_ids),
                "current_source_count": len(current_rows),
                "expired_count": len(expired_rows),
                "detail_verified": len(output),
                "returned_count": len(deduped),
                "source_status_counts": dict(Counter(row.source_status for row in experience_rows)),
                "status_counts": dict(Counter(row["status"] for row in deduped)),
                "branch_counts": dict(Counter(row["branch"] for row in deduped)),
                "source_identity_sha256": _identity_hash(source_ids),
                "experience_identity_sha256": _identity_hash(row.identity for row in experience_rows),
                "returned_identity_sha256": _identity_hash(
                    str(row["provider_course_id"]) for row in deduped
                ),
                "partition_registry_complete": True,
                "directory_stable": True,
                "pagination_complete": True,
                "details_complete": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
                "no_current_data": not deduped,
                "no_current_reason": (
                    "complete official Seosan experience ledger has no current/future rows"
                    if not deduped
                    else ""
                ),
            }
        )
        return deduped, SEOSAN_EXPERIENCE_PARSER, meta
    except Exception as exc:
        meta.update(
            {
                "errors": [f"{type(exc).__name__}: {_clean(exc)}"],
                "error_kind": "contract",
                "returned_count": 0,
                "snapshot_complete": False,
                "full_snapshot_validated": False,
                "pagination_complete": False,
                "details_complete": False,
            }
        )
        return [], SEOSAN_EXPERIENCE_PARSER, meta
    finally:
        requester.close()


collect = collect_seosan_experience


__all__ = [
    "SEOSAN_EXPERIENCE_CANDIDATE_ID",
    "SEOSAN_EXPERIENCE_DETAIL_PATH",
    "SEOSAN_EXPERIENCE_DIRECTORY_PATH",
    "SEOSAN_EXPERIENCE_DIRECTORY_URL",
    "SEOSAN_EXPERIENCE_HOST",
    "SEOSAN_EXPERIENCE_LIST_PATH",
    "SEOSAN_EXPERIENCE_MUNICIPALITY_CODE",
    "SEOSAN_EXPERIENCE_MUNICIPALITY_NAME",
    "SEOSAN_EXPERIENCE_PAGE_SIZE",
    "SEOSAN_EXPERIENCE_PARSER",
    "SEOSAN_EXPERIENCE_PARTITIONS",
    "SEOSAN_EXPERIENCE_PROVIDER",
    "SEOSAN_EXPERIENCE_URL",
    "SeosanExperienceContractError",
    "SeosanExperiencePartition",
    "collect",
    "collect_seosan_experience",
    "is_seosan_experience_target",
    "is_target",
    "seosan_experience_detail_url",
    "seosan_experience_list_url",
]
