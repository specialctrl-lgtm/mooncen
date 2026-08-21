"""Fail-closed collector for Namyangju's official experience catalogue.

The municipal reservation portal owns a separate ``searchTourKey`` identity
ledger from the resident-centre education catalogue.  Only the public list and
public programme-detail routes are requested.  Date-selection, application,
login, attachment, applicant and personal-information routes are never read.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


NAMYANGJU_EXPERIENCE_PROVIDER = "MUNI_WWW_NYJ_GO_KR_F8D7794B"
NAMYANGJU_EXPERIENCE_HOST = "www.nyj.go.kr"
NAMYANGJU_EXPERIENCE_LIST_PATH = (
    "/reserve/selectUserExprnTourBasicInfoList.do"
)
NAMYANGJU_EXPERIENCE_DETAIL_PATH = (
    "/reserve/selectUserExprnTourBasicInfoView.do"
)
NAMYANGJU_EXPERIENCE_URL = (
    "https://www.nyj.go.kr/reserve/"
    "selectUserExprnTourBasicInfoList.do?key=3383"
)
NAMYANGJU_EXPERIENCE_MENU_KEY = "3383"
NAMYANGJU_EXPERIENCE_MUNICIPALITY_CODE = "4136000000"
NAMYANGJU_EXPERIENCE_MUNICIPALITY_NAME = "경기도 남양주시"
NAMYANGJU_EXPERIENCE_PAGE_SIZE = 10
NAMYANGJU_EXPERIENCE_MAX_HTML_BYTES = 5_000_000
NAMYANGJU_EXPERIENCE_PARSER = (
    "namyangju_experience_complete_103_ledger+declared_pages+exact_last_clamp+"
    "stable_boundaries+all_open_public_details+official_nine_branch_registry+"
    "locked_experience+no_date_selection_application_login_attachment_or_pii_calls"
)


@dataclass(frozen=True)
class NamyangjuExperienceBranch:
    code: str
    name: str
    menu_key: str


NAMYANGJU_EXPERIENCE_BRANCHES: tuple[NamyangjuExperienceBranch, ...] = (
    NamyangjuExperienceBranch("3", "남양주시", "3384"),
    NamyangjuExperienceBranch("10", "정약용유적지", "4680"),
    NamyangjuExperienceBranch("2", "물맑음수목원", "3385"),
    NamyangjuExperienceBranch("7", "유아숲체험원", "3389"),
    NamyangjuExperienceBranch("5", "농업기술센터", "3387"),
    NamyangjuExperienceBranch("4", "남양주시립박물관", "3386"),
    NamyangjuExperienceBranch("9", "REMEMBER 1910", "4672"),
    NamyangjuExperienceBranch("12", "남양주 궁집", "4923"),
    NamyangjuExperienceBranch("11", "수상레저체험", "4849"),
)
NAMYANGJU_EXPERIENCE_BRANCH_BY_CODE = {
    item.code: item.name for item in NAMYANGJU_EXPERIENCE_BRANCHES
}

SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]
Fetcher = Callable[[Any, str, int], Any]

_SPACE = re.compile(r"\s+")
_POSITIVE = re.compile(r"[1-9]\d*")
_DATE = re.compile(r"(?<!\d)(20\d{2}|9999)-(\d{2})-(\d{2})(?!\d)")
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_STATUS_MAP: Mapping[str, str] = {
    "접수중": "OPEN",
    "접수대기": "SCHEDULED",
    "접수예정": "SCHEDULED",
    "접수마감": "CLOSED",
}
_CURRENT_STATUSES = frozenset({"OPEN", "SCHEDULED"})
_LIST_HEADERS = (
    "No.",
    "기관",
    "카테고리",
    "체험견학명",
    "접수기간",
    "접수방법",
    "접수상태",
)
_DETAIL_TOP_FIELDS = frozenset(
    {"기관", "카테고리", "접수방법", "접수기간", "운영기간"}
)
_DETAIL_TABLE_FIELDS = frozenset(
    {
        "소요시간",
        "장소",
        "모집대상",
        "강사명",
        "신청인원",
        "이용요금",
        "재료비/체험비",
        "문의전화",
    }
)
_FORBIDDEN_OUTPUT_KEYS = frozenset(
    {
        "phone",
        "email",
        "contact",
        "manager",
        "staff",
        "teacher",
        "instructor",
        "description",
        "content",
        "attachment",
        "attachments",
        "image_url",
        "applicant",
        "raw_html",
    }
)


class NamyangjuExperienceContractError(RuntimeError):
    """Raised when the audited public source no longer matches its contract."""


@dataclass(frozen=True)
class _ListRow:
    number: int
    identity: str
    branch_code: str
    branch: str
    category: str
    title: str
    apply_period: str
    application_method: str
    source_status: str
    page: int


@dataclass(frozen=True)
class _ListPage:
    page: int
    total: int
    last: int
    rows: tuple[_ListRow, ...]


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


def is_namyangju_experience_target(target: Any) -> bool:
    return (
        _clean(_target_value(target, "provider"))
        == NAMYANGJU_EXPERIENCE_PROVIDER
        and _clean(_target_value(target, "url")) == NAMYANGJU_EXPERIENCE_URL
    )


is_target = is_namyangju_experience_target


def namyangju_experience_list_url(page: int = 1) -> str:
    if not isinstance(page, int) or page < 1:
        raise NamyangjuExperienceContractError("invalid list page")
    query: list[tuple[str, Any]] = [("key", NAMYANGJU_EXPERIENCE_MENU_KEY)]
    if page > 1:
        query.append(("pageIndex", page))
    return (
        f"https://{NAMYANGJU_EXPERIENCE_HOST}"
        f"{NAMYANGJU_EXPERIENCE_LIST_PATH}?{urlencode(query)}"
    )


def namyangju_experience_detail_url(identity: Any, branch_code: Any) -> str:
    identity = _clean(identity)
    branch_code = _clean(branch_code)
    if not _POSITIVE.fullmatch(identity) or branch_code not in (
        NAMYANGJU_EXPERIENCE_BRANCH_BY_CODE
    ):
        raise NamyangjuExperienceContractError("invalid experience identity")
    return (
        f"https://{NAMYANGJU_EXPERIENCE_HOST}"
        f"{NAMYANGJU_EXPERIENCE_DETAIL_PATH}?"
        + urlencode(
            (
                ("key", NAMYANGJU_EXPERIENCE_MENU_KEY),
                ("searchTourKey", identity),
                ("searchExprnKey", branch_code),
            )
        )
    )


def _query(url: str) -> tuple[Any, dict[str, str]]:
    parsed = urlparse(_clean(url))
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    values = dict(pairs)
    if len(pairs) != len(values):
        raise NamyangjuExperienceContractError("duplicate query key")
    if parsed.username or parsed.password or parsed.fragment or parsed.params:
        raise NamyangjuExperienceContractError("unsafe URL authority or fragment")
    try:
        if parsed.port is not None:
            raise NamyangjuExperienceContractError("explicit port is forbidden")
    except ValueError as exc:
        raise NamyangjuExperienceContractError("invalid URL port") from exc
    return parsed, values


def _request_kind(method: str, url: str) -> str:
    parsed, query = _query(url)
    if (
        method != "GET"
        or parsed.scheme != "https"
        or (parsed.hostname or "").lower() != NAMYANGJU_EXPERIENCE_HOST
    ):
        raise NamyangjuExperienceContractError("request boundary changed")
    if parsed.path == NAMYANGJU_EXPERIENCE_LIST_PATH:
        if query.get("key") != NAMYANGJU_EXPERIENCE_MENU_KEY:
            raise NamyangjuExperienceContractError("list menu key changed")
        allowed = {"key"} if "pageIndex" not in query else {"key", "pageIndex"}
        if set(query) != allowed or (
            "pageIndex" in query and not _POSITIVE.fullmatch(query["pageIndex"])
        ):
            raise NamyangjuExperienceContractError("list query is not allowlisted")
        return "list"
    if parsed.path == NAMYANGJU_EXPERIENCE_DETAIL_PATH:
        if set(query) != {"key", "searchTourKey", "searchExprnKey"}:
            raise NamyangjuExperienceContractError("detail query is not allowlisted")
        if (
            query.get("key") != NAMYANGJU_EXPERIENCE_MENU_KEY
            or not _POSITIVE.fullmatch(query.get("searchTourKey", ""))
            or query.get("searchExprnKey")
            not in NAMYANGJU_EXPERIENCE_BRANCH_BY_CODE
        ):
            raise NamyangjuExperienceContractError("detail identity changed")
        return "detail"
    raise NamyangjuExperienceContractError("route is not allowlisted")


def _default_session() -> requests.Session:
    current = requests.Session()
    current.trust_env = False
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/140 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return current


def _default_fetcher(current: Any, url: str, timeout: int) -> Any:
    return current.get(url, timeout=timeout, allow_redirects=False)


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
        kind = _request_kind("GET", url)
        self.meta["logical_requests"] += 1
        self.meta[f"{kind}_requests"] += 1
        response = self.fetcher(self.session, url, self.timeout)
        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            raise NamyangjuExperienceContractError(f"HTTP {status}")
        if tuple(getattr(response, "history", ()) or ()):
            raise NamyangjuExperienceContractError("redirect history is forbidden")
        headers = getattr(response, "headers", {}) or {}
        if any(
            str(key).lower() == "location" and value
            for key, value in headers.items()
        ):
            raise NamyangjuExperienceContractError("redirect location is forbidden")
        final_url = _clean(getattr(response, "url", ""))
        if final_url and _query(final_url) != _query(url):
            raise NamyangjuExperienceContractError("response URL changed")
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
            raise NamyangjuExperienceContractError("unexpected content type")
        body = getattr(response, "content", None)
        if body is None:
            body = str(getattr(response, "text", response)).encode("utf-8")
        body = bytes(body)
        if not body or len(body) > NAMYANGJU_EXPERIENCE_MAX_HTML_BYTES:
            raise NamyangjuExperienceContractError("empty or oversized response")
        soup = BeautifulSoup(body, "html.parser")
        title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
        text = _clean(soup.get_text(" ", strip=True))[:5000].lower()
        if any(
            token in f"{title.lower()} {text}"
            for token in (
                "access denied",
                "request rejected",
                "captcha",
            )
        ):
            raise NamyangjuExperienceContractError("source access restriction detected")
        return soup

    def close(self) -> None:
        close = getattr(self.session, "close", None)
        if callable(close):
            close()


def _validate_branch_registry(soup: BeautifulSoup) -> None:
    observed: dict[str, tuple[str, str]] = {}
    for anchor in soup.select(
        f"a[href*='{NAMYANGJU_EXPERIENCE_LIST_PATH.rsplit('/', 1)[-1]}']"
    ):
        href = _clean(anchor.get("href"))
        if not href:
            continue
        parsed, query = _query(
            urljoin(
                f"https://{NAMYANGJU_EXPERIENCE_HOST}/reserve/",
                href,
            )
        )
        if parsed.path != NAMYANGJU_EXPERIENCE_LIST_PATH:
            continue
        branch_code = query.get("searchExprnKey", "")
        if not branch_code:
            continue
        value = (query.get("key", ""), _clean(anchor.get_text(" ", strip=True)))
        previous = observed.get(branch_code)
        if previous is not None and previous != value:
            raise NamyangjuExperienceContractError("branch registry is ambiguous")
        observed[branch_code] = value
    expected = {
        item.code: (item.menu_key, item.name)
        for item in NAMYANGJU_EXPERIENCE_BRANCHES
    }
    if observed != expected:
        raise NamyangjuExperienceContractError("official branch registry changed")


def _parse_list_page(soup: BeautifulSoup, page: int) -> _ListPage:
    expected_title = "전체보기 - 체험·견학 프로그램 - 체험·견학 - 통합예약포털"
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != expected_title:
        raise NamyangjuExperienceContractError(f"page {page}: title changed")
    form = soup.select_one("form#exprnTourBasicInfoSearchVO")
    if (
        form is None
        or _clean(form.get("method")).upper() != "GET"
        or _clean(form.get("action")) != "./selectUserExprnTourBasicInfoList.do"
    ):
        raise NamyangjuExperienceContractError("public search form changed")
    key = form.select_one("input[name='key']")
    if key is None or _clean(key.get("value")) != NAMYANGJU_EXPERIENCE_MENU_KEY:
        raise NamyangjuExperienceContractError("public search key changed")

    count = soup.select_one(".bbs_page .count")
    pager = soup.select_one(".bbs_page .page")
    count_match = re.fullmatch(r"총\s*([\d,]+)\s*건", _clean(count.get_text(" ", strip=True)) if count else "")
    page_match = re.fullmatch(
        r"\[\s*(\d+)\s*/\s*(\d+)\s*페이지\s*\]",
        _clean(pager.get_text(" ", strip=True)) if pager else "",
    )
    if count_match is None or page_match is None:
        raise NamyangjuExperienceContractError(f"page {page}: pager changed")
    total = int(count_match.group(1).replace(",", ""))
    declared_page = int(page_match.group(1))
    last = int(page_match.group(2))
    expected_last = max(1, math.ceil(total / NAMYANGJU_EXPERIENCE_PAGE_SIZE))
    if declared_page != min(page, expected_last) or last != expected_last:
        raise NamyangjuExperienceContractError(f"page {page}: pager boundary changed")

    tables = []
    for table in soup.select("table"):
        headers = tuple(
            _clean(node.get_text(" ", strip=True)) for node in table.select("thead th")
        )
        if headers == _LIST_HEADERS:
            tables.append(table)
    if len(tables) != 1:
        raise NamyangjuExperienceContractError(f"page {page}: list table changed")
    rows: list[_ListRow] = []
    for tr in tables[0].select("tbody tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if len(cells) != len(_LIST_HEADERS):
            raise NamyangjuExperienceContractError(f"page {page}: row shape changed")
        values = [_clean(cell.get_text(" ", strip=True)) for cell in cells]
        if not _POSITIVE.fullmatch(values[0]):
            raise NamyangjuExperienceContractError(f"page {page}: row number changed")
        anchor = cells[3].select_one(
            "a[href*='selectUserExprnTourBasicInfoView.do']"
        )
        if anchor is None:
            raise NamyangjuExperienceContractError(f"page {page}: detail link missing")
        href = _clean(anchor.get("href"))
        absolute = urljoin(
            f"https://{NAMYANGJU_EXPERIENCE_HOST}/reserve/",
            href,
        )
        if _request_kind("GET", absolute) != "detail":
            raise NamyangjuExperienceContractError(f"page {page}: unsafe detail link")
        _, query = _query(absolute)
        identity = query["searchTourKey"]
        branch_code = query["searchExprnKey"]
        branch = NAMYANGJU_EXPERIENCE_BRANCH_BY_CODE[branch_code]
        if values[1] != branch or values[3] != _clean(anchor.get_text(" ", strip=True)):
            raise NamyangjuExperienceContractError(
                f"course {identity}: branch/title binding changed"
            )
        if values[6] not in _STATUS_MAP:
            raise NamyangjuExperienceContractError(
                f"course {identity}: status vocabulary changed"
            )
        if values[5] not in {"온라인", "방문접수", "전화접수"}:
            raise NamyangjuExperienceContractError(
                f"course {identity}: application method changed"
            )
        rows.append(
            _ListRow(
                number=int(values[0]),
                identity=identity,
                branch_code=branch_code,
                branch=branch,
                category=values[2],
                title=values[3],
                apply_period=values[4],
                application_method=values[5],
                source_status=values[6],
                page=page,
            )
        )
    expected_rows = (
        NAMYANGJU_EXPERIENCE_PAGE_SIZE
        if page < expected_last
        else total % NAMYANGJU_EXPERIENCE_PAGE_SIZE
        or NAMYANGJU_EXPERIENCE_PAGE_SIZE
    )
    if len(rows) != expected_rows:
        raise NamyangjuExperienceContractError(f"page {page}: row count changed")
    if len({row.identity for row in rows}) != len(rows):
        raise NamyangjuExperienceContractError(f"page {page}: duplicate identity")
    return _ListPage(page, total, last, tuple(rows))


def _page_signature(value: _ListPage) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            row.number,
            row.identity,
            row.branch_code,
            row.branch,
            row.category,
            row.title,
            row.apply_period,
            row.application_method,
            row.source_status,
        )
        for row in value.rows
    )


def _date_pair(value: str, field: str) -> tuple[str, str]:
    text = _clean(value)
    if text == "상시":
        return "", ""
    found: list[date] = []
    for match in _DATE.finditer(text):
        try:
            found.append(date(*(int(item) for item in match.groups())))
        except ValueError as exc:
            raise NamyangjuExperienceContractError(f"invalid {field} date") from exc
    if len(found) != 2 or found[0] > found[1]:
        raise NamyangjuExperienceContractError(f"invalid {field} period")
    return found[0].isoformat(), found[1].isoformat()


def _pairs(container: Any, selector: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for box in container.select(selector):
        label = box.select_one(".tit")
        value = box.select_one(".con")
        if label is None or value is None:
            raise NamyangjuExperienceContractError("detail field pairing changed")
        key = _clean(label.get_text(" ", strip=True))
        if not key or key in result:
            raise NamyangjuExperienceContractError("duplicate detail field")
        result[key] = _clean(value.get_text(" ", strip=True))
    return result


def _table_pairs(table: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for tr in table.select("tr"):
        th = tr.find("th", recursive=False)
        td = tr.find("td", recursive=False)
        if th is None or td is None:
            raise NamyangjuExperienceContractError("detail table pairing changed")
        label = _clean(th.get_text(" ", strip=True))
        if not label or label in result:
            raise NamyangjuExperienceContractError("duplicate detail table field")
        result[label] = _clean(td.get_text(" ", strip=True))
    return result


def _parse_detail(soup: BeautifulSoup, listed: _ListRow) -> dict[str, Any]:
    expected_title = "전체보기 - 체험·견학 프로그램 - 체험·견학 - 통합예약포털"
    page_title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if page_title != expected_title:
        raise NamyangjuExperienceContractError(
            f"course {listed.identity}: detail title changed"
        )
    root = soup.select_one(".p_experience_wrap")
    heading = root.find("h3", recursive=False) if root else None
    status = heading.select_one(".p_status") if heading else None
    source_status = _clean(status.get_text(" ", strip=True) if status else "")
    if heading is None or source_status not in _STATUS_MAP:
        raise NamyangjuExperienceContractError(
            f"course {listed.identity}: detail heading/status changed"
        )
    title = _clean(heading.get_text(" ", strip=True)).removeprefix(source_status).strip()
    if title != listed.title or source_status != listed.source_status:
        raise NamyangjuExperienceContractError(
            f"course {listed.identity}: title/status drift"
        )
    top = _pairs(root, ".p_exp_tit_wrap .titbox_wrap")
    if set(top) != _DETAIL_TOP_FIELDS:
        raise NamyangjuExperienceContractError(
            f"course {listed.identity}: top field registry changed"
        )
    if (
        top["기관"] != listed.branch
        or top["카테고리"] != listed.category
        or top["접수방법"] != listed.application_method
        or top["접수기간"] != listed.apply_period
    ):
        raise NamyangjuExperienceContractError(
            f"course {listed.identity}: list/detail binding changed"
        )
    tables = root.select(".p_exp_cont_wrap table.table.type2")
    if len(tables) != 1:
        raise NamyangjuExperienceContractError(
            f"course {listed.identity}: detail table changed"
        )
    pairs = _table_pairs(tables[0])
    if not {"소요시간", "신청인원", "이용요금", "재료비/체험비"}.issubset(pairs) or not set(pairs).issubset(
        _DETAIL_TABLE_FIELDS
    ):
        raise NamyangjuExperienceContractError(
            f"course {listed.identity}: detail table registry changed"
        )
    apply_start, apply_end = _date_pair(top["접수기간"], "application")
    start, end = _date_pair(top["운영기간"], "operation")
    capacity_numbers = [int(item) for item in re.findall(r"\d+", pairs["신청인원"])]
    if not capacity_numbers:
        raise NamyangjuExperienceContractError(
            f"course {listed.identity}: capacity changed"
        )
    capacity_total = capacity_numbers[-1]
    fee = f"이용요금: {pairs['이용요금']} / 재료비/체험비: {pairs['재료비/체험비']}"
    raw_url = namyangju_experience_detail_url(
        listed.identity, listed.branch_code
    )
    return {
        "provider": NAMYANGJU_EXPERIENCE_PROVIDER,
        "municipality_code": NAMYANGJU_EXPERIENCE_MUNICIPALITY_CODE,
        "municipality_name": NAMYANGJU_EXPERIENCE_MUNICIPALITY_NAME,
        "provider_course_id": (
            f"{NAMYANGJU_EXPERIENCE_PROVIDER}:experience:"
            f"{listed.branch_code}:{listed.identity}"
        ),
        "source_course_id": f"experience:{listed.branch_code}:{listed.identity}",
        "title": listed.title,
        "branch": listed.branch,
        "preserve_branch": True,
        "category": f"남양주시 체험·견학/{listed.category}",
        "collection_category": "공공예약",
        "domain_category": "체험·견학",
        "source_group": "municipal_reservation",
        "service_group": "체험",
        "service_group_policy": "locked",
        "classification_locked": True,
        "operator_type": "지자체/공공기관",
        "program_type": "체험",
        "source_status": source_status,
        "status": _STATUS_MAP[source_status],
        "reservation_available": _STATUS_MAP[source_status] == "OPEN",
        "period": top["운영기간"],
        "start_date": start,
        "end_date": end,
        "apply_period": top["접수기간"],
        "apply_start_date": apply_start,
        "apply_end_date": apply_end,
        "schedule_raw": pairs["소요시간"],
        "target": pairs.get("모집대상", ""),
        "venue_name": pairs.get("장소", listed.branch),
        "fee": fee,
        "application_method_raw": listed.application_method,
        "application_url": "",
        "capacity_total": capacity_total,
        "raw_url": raw_url,
        "raw_fields": {
            "parser": NAMYANGJU_EXPERIENCE_PARSER,
            "search_tour_key": listed.identity,
            "search_exprn_key": listed.branch_code,
            "official_branch": listed.branch,
            "official_category": listed.category,
            "official_source_status": source_status,
            "list_page": listed.page,
            "date_selection_control_observed_not_called": bool(
                root.select_one("#redirectRegisBtn")
            ),
        },
    }


def _identity_hash(values: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(sorted(values)).encode("utf-8")).hexdigest()


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                key_text = str(key).lower()
                child_path = f"{path}.{key}" if path else str(key)
                if key_text in _FORBIDDEN_OUTPUT_KEYS:
                    errors.append(f"forbidden key {child_path}")
                walk(child, child_path)
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")
        elif isinstance(value, str) and (_PHONE.search(value) or _EMAIL.search(value)):
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


def _meta() -> dict[str, Any]:
    return {
        "errors": [],
        "error_kind": "",
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "pagination_complete": False,
        "details_complete": False,
        "logical_requests": 0,
        "list_requests": 0,
        "detail_requests": 0,
        "application_endpoint_requests": 0,
        "login_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "pii_endpoint_requests": 0,
    }


def collect_namyangju_experience_courses(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 20,
    detail_limit: int = 100,
    today: Optional[date | datetime | str] = None,
    session_factory: SessionFactory = _default_session,
    dedupe_rows: DedupeRows = _dedupe_default,
    fetcher: Fetcher = _default_fetcher,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one complete snapshot of the official current experience rows."""

    meta = _meta()
    if not is_namyangju_experience_target(target):
        meta["errors"] = ["target does not match the canonical experience route"]
        meta["error_kind"] = "contract"
        return [], NAMYANGJU_EXPERIENCE_PARSER, meta
    if timeout < 1 or max_pages < 1 or detail_limit < 0:
        meta["errors"] = ["invalid collection limits"]
        meta["error_kind"] = "contract"
        return [], NAMYANGJU_EXPERIENCE_PARSER, meta

    cutoff = _today(today)
    requester = _Requester(session_factory, fetcher, timeout, meta)
    try:
        first_soup = requester.soup(namyangju_experience_list_url(1))
        _validate_branch_registry(first_soup)
        first = _parse_list_page(first_soup, 1)
        if first.total < 1 or first.last > max_pages:
            raise NamyangjuExperienceContractError(
                "declared catalogue exceeds collection limits"
            )
        pages = [first]
        for page in range(2, first.last + 1):
            value = _parse_list_page(
                requester.soup(namyangju_experience_list_url(page)), page
            )
            if value.total != first.total or value.last != first.last:
                raise NamyangjuExperienceContractError("declared total drift")
            pages.append(value)
        source_rows = [row for page in pages for row in page.rows]
        if len(source_rows) != first.total:
            raise NamyangjuExperienceContractError("complete row union changed")
        if len({row.identity for row in source_rows}) != first.total:
            raise NamyangjuExperienceContractError("complete identity union changed")
        expected_numbers = list(range(first.total, 0, -1))
        if [row.number for row in source_rows] != expected_numbers:
            raise NamyangjuExperienceContractError("row numbering is incomplete")

        overflow_page = first.last + 1
        overflow = _parse_list_page(
            requester.soup(namyangju_experience_list_url(overflow_page)),
            overflow_page,
        )
        if _page_signature(overflow) != _page_signature(pages[-1]):
            raise NamyangjuExperienceContractError("post-last clamp changed")
        stable_first = _parse_list_page(
            requester.soup(namyangju_experience_list_url(1)), 1
        )
        stable_last = _parse_list_page(
            requester.soup(namyangju_experience_list_url(first.last)), first.last
        )
        stable_overflow = _parse_list_page(
            requester.soup(namyangju_experience_list_url(overflow_page)),
            overflow_page,
        )
        if (
            _page_signature(stable_first) != _page_signature(first)
            or _page_signature(stable_last) != _page_signature(pages[-1])
            or _page_signature(stable_overflow) != _page_signature(overflow)
        ):
            raise NamyangjuExperienceContractError("list boundary stability changed")

        current_rows = [
            row
            for row in source_rows
            if _STATUS_MAP[row.source_status] in _CURRENT_STATUSES
        ]
        if len(current_rows) > detail_limit:
            raise NamyangjuExperienceContractError(
                "detail limit truncates the current catalogue"
            )
        output: list[dict[str, Any]] = []
        for listed in current_rows:
            output.append(
                _parse_detail(
                    requester.soup(
                        namyangju_experience_detail_url(
                            listed.identity, listed.branch_code
                        )
                    ),
                    listed,
                )
            )
        privacy = [error for row in output for error in _privacy_errors(row)]
        if privacy:
            raise NamyangjuExperienceContractError(
                f"PII/output allowlist violation: {privacy[0]}"
            )
        deduped = list(dedupe_rows(output))
        if len(deduped) != len(output):
            raise NamyangjuExperienceContractError("dedupe changed complete output")

        status_counts = Counter(row["status"] for row in deduped)
        branch_counts = Counter(row.branch for row in source_rows)
        current_branch_counts = Counter(row["branch"] for row in deduped)
        meta.update(
            {
                "source_total": first.total,
                "source_pages": first.last,
                "last_page_rows": len(pages[-1].rows),
                "clamp_page": overflow_page,
                "source_status_counts": dict(
                    sorted(Counter(row.source_status for row in source_rows).items())
                ),
                "current_source_count": len(current_rows),
                "expired_or_closed_count": len(source_rows) - len(current_rows),
                "returned_count": len(deduped),
                "detail_pages": len(current_rows),
                "status_counts": dict(sorted(status_counts.items())),
                "branch_counts": {
                    item.name: branch_counts.get(item.name, 0)
                    for item in NAMYANGJU_EXPERIENCE_BRANCHES
                },
                "current_branch_counts": {
                    item.name: current_branch_counts.get(item.name, 0)
                    for item in NAMYANGJU_EXPERIENCE_BRANCHES
                },
                "source_identity_sha256": _identity_hash(
                    row.identity for row in source_rows
                ),
                "current_identity_sha256": _identity_hash(
                    row.identity for row in current_rows
                ),
                "cutoff": cutoff.isoformat(),
                "duplicate_count": 0,
                "semantic_duplicate_count": 0,
                "pagination_complete": True,
                "details_complete": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
            }
        )
        return deduped, NAMYANGJU_EXPERIENCE_PARSER, meta
    except Exception as exc:
        meta["errors"] = [f"{type(exc).__name__}: {exc}"]
        meta["error_kind"] = (
            "contract"
            if isinstance(exc, NamyangjuExperienceContractError)
            else "network_or_parse"
        )
        return [], NAMYANGJU_EXPERIENCE_PARSER, meta
    finally:
        requester.close()


collect = collect_namyangju_experience_courses


__all__ = [name for name in globals() if name.startswith("NAMYANGJU_EXPERIENCE_")] + [
    "NamyangjuExperienceBranch",
    "NamyangjuExperienceContractError",
    "collect",
    "collect_namyangju_experience_courses",
    "is_namyangju_experience_target",
    "namyangju_experience_detail_url",
    "namyangju_experience_list_url",
]
