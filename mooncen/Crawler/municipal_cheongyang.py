"""Fail-closed collector for Cheongyang County lifelong-learning courses.

The official Cheongyang lifelong-learning list is a *current recruiting*
ledger.  Its public default filter is ``reservYn=Y``; historical notices and
the separate child-experience/museum reservation services are not part of
this owner.  The generic generated crawler used to recurse through unrelated
tables linked from the page, so it could report many false courses.  This
collector reads only the seven-column course table, proves the immediate
empty page boundary, rechecks stable boundaries, and validates every public
detail without requesting an applicant form or applicant list.

The incumbent provider id is retained because it is already scheduled in the
repository.  The canonical course URL has its own candidate id and should be
bound to that incumbent provider during promotion.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


CHEONGYANG_PROVIDER = "MUNI_WWW_CHEONGYANG_GO_KR_25520BA7"
CHEONGYANG_CANONICAL_DERIVED_PROVIDER = "MUNI_WWW_CHEONGYANG_GO_KR_3CAA55E0"
CHEONGYANG_CANONICAL_CANDIDATE_ID = "MUNI_IR_58B97DB9DDF4"
CHEONGYANG_REVIEW_BOARD_PROVIDER = CHEONGYANG_PROVIDER
CHEONGYANG_REVIEW_BOARD_CANDIDATE_ID = "MUNI_IR_28C0549FFD3C"

CHEONGYANG_MUNICIPALITY_CODE = "4479000000"
CHEONGYANG_MUNICIPALITY_NAME = "충청남도 청양군"
CHEONGYANG_HOST = "www.cheongyang.go.kr"
CHEONGYANG_LIST_PATH = "/prog/educate/lll/sub02_01/list.do"
CHEONGYANG_DETAIL_PATH = "/prog/educate/lll/sub02_01/view.do"
CHEONGYANG_APPLICATION_PATH = "/prog/educate/reserve/lll/sub02_01/write.do"
CHEONGYANG_APPLICANT_LIST_PATH = "/prog/educate_reserve/lll/sub02_01/list.do"
CHEONGYANG_CANONICAL_URL = f"https://{CHEONGYANG_HOST}{CHEONGYANG_LIST_PATH}"
CHEONGYANG_SITE_NAME = "청양군 평생학습센터"
CHEONGYANG_OFFICIAL_BRANCH = "청양군평생학습관"
CHEONGYANG_OFFICIAL_ADDRESS = "충청남도 청양군 청양읍 문화예술로 150"
CHEONGYANG_PAGE_SIZE = 10
CHEONGYANG_MAX_PAGES = 50
CHEONGYANG_MAX_HTML_BYTES = 3_000_000
CHEONGYANG_PARSER = (
    "cheongyang_lifelong_current_recruiting_table+consecutive_get_pages+"
    "immediate_structural_empty_boundary+stable_boundaries+"
    "all_current_details+identity_bound_application_controls+pii_redaction"
)

CHEONGYANG_NOTICE_BOARD_URL = "https://www.cheongyang.go.kr/cop/bbs/BBSMSTR_000000000146/selectBoardList.do"
CHEONGYANG_CHILD_EXPERIENCE_URL = "https://www.cheongyang.go.kr/prog/exprnPrgrm/child/sub02_03/list.do"
CHEONGYANG_MUSEUM_GROUP_URL = "https://www.cheongyang.go.kr/prog/groupCate/museum/sub04_03/list.do"

CHEONGYANG_OWNER_BOUNDARY_AUDIT: dict[str, dict[str, str]] = {
    "lifelong_current_recruiting_ledger": {
        "url": CHEONGYANG_CANONICAL_URL,
        "decision": "include_as_the_lifelong_education_owner",
    },
    "lifelong_notice_board": {
        "url": CHEONGYANG_NOTICE_BOARD_URL,
        "decision": "exclude_information_only_alias; it links to this ledger",
    },
    "child_experience": {
        "url": CHEONGYANG_CHILD_EXPERIENCE_URL,
        "decision": "exclude_separate_child_experience_reservation_owner",
    },
    "museum_group_reservation": {
        "url": CHEONGYANG_MUSEUM_GROUP_URL,
        "decision": "exclude_separate_museum_group_visit_owner",
    },
}

CHEONGYANG_PII_FIELDS_NEVER_PERSISTED = (
    "문의전화",
    "담당자",
    "첨부파일",
    "신청자 리스트",
    "신청서 본문",
)


SessionFactory = Callable[[], Any]
HtmlFetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class CheongyangContractError(ValueError):
    """Raised when the audited public contract changes."""


@dataclass(frozen=True)
class _Page:
    number: int
    rows: tuple[dict[str, Any], ...]


_SPACE = re.compile(r"\s+")
_IDENTITY = re.compile(r"^[1-9]\d{0,11}$")
_DATE = re.compile(r"(?<!\d)(20\d{2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})(?!\d)")
_CAPACITY = re.compile(r"^(\d[\d,]*)\s*/\s*(\d[\d,]*)$")
_LIST_STATUS_MAP = {
    "모집중": "OPEN",
    "모집예정": "SCHEDULED",
    "모집마감": "CLOSED",
}
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_LIST_HEADERS = (
    "강좌명",
    "대상",
    "접수기간",
    "교육기간",
    "신청인원 / 모집인원",
    "시간",
    "상태",
)
_DETAIL_REQUIRED = frozenset(
    {
        "강좌명",
        "교육기간",
        "교육시간",
        "접수기간",
        "교육장소",
        "교육대상",
        "문의전화",
        "정원",
        "담당자",
        "교육기관",
        "교육내용",
        "기타사항",
    }
)
_DETAIL_OPTIONAL = frozenset({"첨부파일"})
_NO_DATA_TEXTS = frozenset({"검색된 내용이 없습니다.", "등록된 내용이 없습니다."})


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", _clean(value).casefold())


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _target_extra(target: Any) -> Mapping[str, Any]:
    value = _target_value(target, "extra")
    return value if isinstance(value, Mapping) else {}


def _audit_date(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _normalize_period(value: Any) -> str:
    text = _clean(value)
    text = _DATE.sub(
        lambda match: (f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"),
        text,
    )
    return _clean(re.sub(r"\s*~\s*", " ~ ", text))


def _period_dates(value: Any) -> tuple[date, date]:
    matches = list(_DATE.finditer(_clean(value)))
    if len(matches) != 2:
        raise CheongyangContractError("period must contain exactly two dates")
    parsed = tuple(date(int(match.group(1)), int(match.group(2)), int(match.group(3))) for match in matches)
    if parsed[1] < parsed[0]:
        raise CheongyangContractError("period ends before it starts")
    return parsed[0], parsed[1]


def _redact_public_text(value: Any) -> str:
    text = _clean(value)
    text = _EMAIL.sub("[이메일 비공개]", text)
    return _PHONE.sub("[연락처 비공개]", text)


def cheongyang_list_url(page: int) -> str:
    if not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    if page == 1:
        return CHEONGYANG_CANONICAL_URL
    return f"{CHEONGYANG_CANONICAL_URL}?{urlencode({'pageIndex': page})}"


def cheongyang_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY.fullmatch(value):
        raise ValueError("invalid Cheongyang education identity")
    return f"https://{CHEONGYANG_HOST}{CHEONGYANG_DETAIL_PATH}?{urlencode({'eduNo': value})}"


def _strict_url(value: Any, *, path: str, query: Mapping[str, list[str]]) -> bool:
    parsed = urlparse(_clean(value))
    try:
        port = parsed.port
        actual_query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError):
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == CHEONGYANG_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == path
        and actual_query == dict(query)
        and not parsed.fragment
    )


def is_cheongyang_education_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")) == CHEONGYANG_PROVIDER
        and _strict_url(_target_value(target, "url"), path=CHEONGYANG_LIST_PATH, query={})
    )


is_target = is_cheongyang_education_target


def cheongyang_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MooncenMunicipalCrawler/1.0)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        status=2,
        backoff_factor=0.3,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=False)


def _response_soup(value: Any, expected_url: str) -> BeautifulSoup:
    status = int(getattr(value, "status_code", 200) or 0)
    if status != 200:
        raise CheongyangContractError(f"unexpected HTTP status {status}")
    if getattr(value, "history", ()):
        raise CheongyangContractError("canonical request redirected")
    response_url = _clean(getattr(value, "url", expected_url))
    if response_url and response_url != expected_url:
        raise CheongyangContractError("response URL changed")
    headers = getattr(value, "headers", {}) or {}
    content_type = _clean(headers.get("Content-Type") if isinstance(headers, Mapping) else "")
    if content_type and "html" not in content_type.casefold():
        raise CheongyangContractError("response is not HTML")
    content = getattr(value, "content", None)
    if isinstance(content, bytes):
        if not content or len(content) > CHEONGYANG_MAX_HTML_BYTES:
            raise CheongyangContractError("HTML response size is invalid")
        try:
            html = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise CheongyangContractError("HTML is not valid UTF-8") from exc
    else:
        html = str(getattr(value, "text", value) or "")
        if not html or len(html.encode("utf-8")) > CHEONGYANG_MAX_HTML_BYTES:
            raise CheongyangContractError("HTML response size is invalid")
    return BeautifulSoup(html, "lxml")


def _fetch_soup(session: Any, url: str, timeout: int, fetcher: HtmlFetcher) -> BeautifulSoup:
    return _response_soup(fetcher(session, url, timeout), url)


def _official_table(soup: BeautifulSoup) -> Any:
    matches = []
    for table in soup.select("table"):
        headers = tuple(_clean(cell.get_text(" ", strip=True)) for cell in table.select("thead th"))
        if headers == _LIST_HEADERS:
            matches.append(table)
    if len(matches) != 1:
        raise CheongyangContractError(f"expected one official education table, got {len(matches)}")
    return matches[0]


def _search_contract(soup: BeautifulSoup) -> None:
    title = soup.select_one("h2.page__title")
    if _clean(title.get_text(" ", strip=True) if title else "") != "평생학습강좌신청":
        raise CheongyangContractError("official page title changed")
    form = soup.select_one('form[name="searchForm"]')
    if form is None or _clean(form.get("method")).casefold() != "post":
        raise CheongyangContractError("official search form changed")
    action = urlparse(_clean(form.get("action")))
    if action.path != CHEONGYANG_LIST_PATH or action.query or action.fragment:
        raise CheongyangContractError("official search form action changed")
    for name, expected in (("siteCode", "lll"), ("mno", "sub02_01")):
        node = form.select_one(f'input[name="{name}"]')
        if _clean(node.get("value") if node else "") != expected:
            raise CheongyangContractError(f"search form {name} changed")
    options = form.select('select[name="reservYn"] option')
    values = {_clean(option.get("value")): _clean(option.get_text(" ", strip=True)) for option in options}
    selected = [_clean(option.get("value")) for option in options if option.has_attr("selected")]
    if values != {"": "전체", "Y": "모집중", "N": "모집마감"} or selected != ["Y"]:
        raise CheongyangContractError("current recruiting filter contract changed")


def _parse_capacity(value: Any) -> tuple[int, int]:
    match = _CAPACITY.fullmatch(_clean(value))
    if not match:
        raise CheongyangContractError("invalid application/capacity value")
    current, total = (int(part.replace(",", "")) for part in match.groups())
    if total <= 0:
        raise CheongyangContractError("capacity must be positive")
    return current, total


def _canonical_href(value: Any, *, path: str) -> tuple[str, dict[str, list[str]]]:
    absolute = urljoin(CHEONGYANG_CANONICAL_URL, _clean(value))
    parsed = urlparse(absolute)
    try:
        port = parsed.port
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError) as exc:
        raise CheongyangContractError("malformed official link") from exc
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == CHEONGYANG_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == path
        and not parsed.fragment
    ):
        raise CheongyangContractError("official link escaped its audited path")
    return absolute, query


def _parse_list_row(row: Any, page: int) -> dict[str, Any]:
    cells = row.find_all("td", recursive=False)
    if len(cells) != 7:
        raise CheongyangContractError(f"page {page}: course row has {len(cells)} cells")
    texts = tuple(_clean(cell.get_text(" ", strip=True)) for cell in cells)
    detail_links = cells[0].select("a[href]")
    if len(detail_links) != 1:
        raise CheongyangContractError(f"page {page}: detail link changed")
    title = _clean(detail_links[0].get_text(" ", strip=True))
    detail_href, detail_query = _canonical_href(detail_links[0].get("href"), path=CHEONGYANG_DETAIL_PATH)
    if set(detail_query) != {"pageIndex", "eduNo"} or detail_query["pageIndex"] != [str(page)]:
        raise CheongyangContractError(f"page {page}: detail paging identity changed")
    identity = _clean(detail_query["eduNo"][0])
    if not _IDENTITY.fullmatch(identity) or len(_normalized(title)) < 2:
        raise CheongyangContractError(f"page {page}: invalid course identity/title")

    status_links = [
        link
        for link in cells[6].select("a[href]")
        if _clean(link.get_text(" ", strip=True)) in _LIST_STATUS_MAP
    ]
    status_labels = [
        label for label in _LIST_STATUS_MAP if label in texts[6]
    ]
    if len(status_labels) != 1:
        raise CheongyangContractError(
            f"course {identity}: source status changed"
        )
    source_status = status_labels[0]
    status = _LIST_STATUS_MAP[source_status]

    application_links: list[Any] = []
    for link in cells[6].select("a[href]"):
        path = urlparse(
            urljoin(CHEONGYANG_CANONICAL_URL, _clean(link.get("href")))
        ).path
        if path == CHEONGYANG_APPLICATION_PATH:
            application_links.append(link)
    application_url = ""
    if status == "OPEN":
        if len(status_links) != 1 or len(application_links) != 1:
            raise CheongyangContractError(
                f"course {identity}: expected one application control"
            )
        application_link = application_links[0]
        if status_links[0] is not application_link:
            raise CheongyangContractError(
                f"course {identity}: application status/control changed"
            )
        application_url, application_query = _canonical_href(
            application_link.get("href"),
            path=CHEONGYANG_APPLICATION_PATH,
        )
        if set(application_query) != {"pageIndex", "eduNo", "oneInwon"}:
            raise CheongyangContractError(
                f"course {identity}: application query changed"
            )
        if (
            application_query["pageIndex"] != [str(page)]
            or application_query["eduNo"] != [identity]
            or len(application_query["oneInwon"]) != 1
            or not re.fullmatch(r"\d*", application_query["oneInwon"][0])
        ):
            raise CheongyangContractError(
                f"course {identity}: application control is not identity-bound"
            )
    else:
        if application_links:
            raise CheongyangContractError(
                f"course {identity}: inactive status exposes an application control"
            )
        if len(status_links) > 1:
            raise CheongyangContractError(
                f"course {identity}: inactive status control is ambiguous"
            )
        if status_links:
            _status_url, status_query = _canonical_href(
                status_links[0].get("href"),
                path=CHEONGYANG_DETAIL_PATH,
            )
            if (
                set(status_query) != {"pageIndex", "eduNo"}
                or status_query["pageIndex"] != [str(page)]
                or status_query["eduNo"] != [identity]
            ):
                raise CheongyangContractError(
                    f"course {identity}: inactive status control is not identity-bound"
                )

    apply_period = _normalize_period(texts[2])
    period = _normalize_period(texts[3])
    apply_start, apply_end = _period_dates(apply_period)
    event_start, event_end = _period_dates(period)
    capacity_current, capacity_total = _parse_capacity(texts[4])
    if not texts[1]:
        raise CheongyangContractError(f"course {identity}: education target is empty")
    return {
        "identity": identity,
        "page": page,
        "title": title,
        "target": texts[1],
        "apply_period": apply_period,
        "period": period,
        "schedule": texts[5],
        "apply_start": apply_start,
        "apply_end": apply_end,
        "event_start": event_start,
        "event_end": event_end,
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "source_status": source_status,
        "status": status,
        "detail_fetch_url": detail_href,
        "application_url": application_url,
    }


def _parse_list_page(soup: BeautifulSoup, page: int) -> _Page:
    _search_contract(soup)
    table = _official_table(soup)
    raw_rows = table.select("tbody > tr")
    if len(raw_rows) == 1:
        cells = raw_rows[0].find_all("td", recursive=False)
        if (
            len(cells) == 1
            and _clean(cells[0].get("colspan")) == "7"
            and _clean(cells[0].get_text(" ", strip=True)) in _NO_DATA_TEXTS
        ):
            raw_rows = []
    if any(len(row.find_all("td", recursive=False)) != 7 for row in raw_rows):
        raise CheongyangContractError(f"page {page}: empty/data row contract changed")
    parsed = tuple(_parse_list_row(row, page) for row in raw_rows)
    if len(parsed) > CHEONGYANG_PAGE_SIZE:
        raise CheongyangContractError(f"page {page}: page size exceeded")
    return _Page(number=page, rows=parsed)


def _page_signature(page: _Page) -> tuple[Any, ...]:
    return tuple(
        (
            row["identity"],
            row["title"],
            row["target"],
            row["apply_period"],
            row["period"],
            row["schedule"],
            row["capacity_current"],
            row["capacity_total"],
            row["source_status"],
            row["application_url"],
        )
        for row in page.rows
    )


def _detail_pairs(soup: BeautifulSoup) -> dict[str, str]:
    candidates: list[dict[str, str]] = []
    for table in soup.select("table"):
        pairs: dict[str, str] = {}
        for row in table.select("tr"):
            cells = row.find_all(["th", "td"], recursive=False)
            index = 0
            while index < len(cells):
                if cells[index].name != "th" or index + 1 >= len(cells) or cells[index + 1].name != "td":
                    raise CheongyangContractError("detail table header/value structure changed")
                key = _clean(cells[index].get_text(" ", strip=True))
                value = _clean(cells[index + 1].get_text(" ", strip=True))
                if key in pairs and pairs[key] != value:
                    raise CheongyangContractError(f"conflicting detail label {key}")
                pairs[key] = value
                index += 2
        if _DETAIL_REQUIRED.issubset(pairs):
            candidates.append(pairs)
    if len(candidates) != 1:
        raise CheongyangContractError(f"expected one official detail table, got {len(candidates)}")
    pairs = candidates[0]
    keys = frozenset(pairs)
    if not _DETAIL_REQUIRED.issubset(keys) or not keys.issubset(
        _DETAIL_REQUIRED | _DETAIL_OPTIONAL
    ):
        raise CheongyangContractError("detail labels changed")
    return pairs


def _detail_application_control(
    soup: BeautifulSoup,
    identity: str,
    page: int,
    status: str,
) -> None:
    matches: list[tuple[Any, dict[str, list[str]]]] = []
    for link in soup.select("a[href]"):
        absolute = urljoin(CHEONGYANG_CANONICAL_URL, _clean(link.get("href")))
        if urlparse(absolute).path != CHEONGYANG_APPLICATION_PATH:
            continue
        _url, query = _canonical_href(link.get("href"), path=CHEONGYANG_APPLICATION_PATH)
        matches.append((link, query))
    if status != "OPEN":
        if matches:
            raise CheongyangContractError(
                f"course {identity}: inactive detail exposes an application control"
            )
        return
    if len(matches) != 1:
        raise CheongyangContractError(f"course {identity}: detail application control count changed")
    link, query = matches[0]
    if set(query) != {"pageIndex", "eduNo", "oneInwon"}:
        raise CheongyangContractError(f"course {identity}: detail application query changed")
    if (
        query["pageIndex"] != [str(page)]
        or query["eduNo"] != [identity]
        or len(query["oneInwon"]) != 1
        or not re.fullmatch(r"\d*", query["oneInwon"][0])
        or _clean(link.get_text(" ", strip=True)) != "모집중"
    ):
        raise CheongyangContractError(f"course {identity}: detail application control identity changed")


def _row_from_detail(target: Any, listed: Mapping[str, Any], soup: BeautifulSoup) -> dict[str, Any]:
    identity = _clean(listed.get("identity"))
    page = int(listed.get("page") or 0)
    status = _clean(listed.get("status"))
    open_now = status == "OPEN"
    pairs = _detail_pairs(soup)
    _detail_application_control(soup, identity, page, status)
    if _normalized(pairs["강좌명"]) != _normalized(listed.get("title")):
        raise CheongyangContractError(f"course {identity}: detail title identity drift")
    comparisons = {
        "교육기간": listed["period"],
        "교육시간": listed["schedule"],
        "접수기간": listed["apply_period"],
        "교육대상": listed["target"],
    }
    for label, expected in comparisons.items():
        actual = _normalize_period(pairs[label]) if "기간" in label else _clean(pairs[label])
        if actual != expected:
            raise CheongyangContractError(f"course {identity}: list/detail {label} mismatch")
    try:
        detail_capacity = int(_clean(pairs["정원"]).replace(",", ""))
    except ValueError as exc:
        raise CheongyangContractError(f"course {identity}: invalid detail capacity") from exc
    if detail_capacity != listed["capacity_total"]:
        raise CheongyangContractError(f"course {identity}: list/detail capacity mismatch")
    venue = _clean(pairs["교육장소"])
    institution = _clean(pairs["교육기관"])
    if not venue or not institution:
        raise CheongyangContractError(f"course {identity}: venue/institution is empty")
    branch = venue
    address = CHEONGYANG_OFFICIAL_ADDRESS if _normalized(venue) == _normalized(CHEONGYANG_OFFICIAL_BRANCH) else ""
    description_parts = [_redact_public_text(pairs["교육내용"])]
    other = _redact_public_text(pairs["기타사항"])
    if other and other not in {".", "-"}:
        description_parts.append(other)
    extra = _target_extra(target)
    row: dict[str, Any] = {
        "provider": CHEONGYANG_PROVIDER,
        "provider_course_id": f"{CHEONGYANG_PROVIDER}:edu:{identity}",
        "title": _clean(listed["title"]),
        "branch": branch,
        "branch_code": f"{CHEONGYANG_PROVIDER}:{_normalized(branch)}",
        "preserve_branch": True,
        "branch_url": CHEONGYANG_CANONICAL_URL,
        "raw_url": cheongyang_detail_url(identity),
        "application_url": _clean(listed["application_url"]) if open_now else "",
        "application_type": (
            "ONLINE_RESERVATION_LOGIN_REQUIRED" if open_now else ""
        ),
        "application_method_raw": "온라인 신청",
        "reservation_available": open_now,
        "status": status,
        "period": _clean(listed["period"]),
        "apply_period": _clean(listed["apply_period"]),
        "schedule_raw": _clean(listed["schedule"]),
        "start_date": listed["event_start"].isoformat(),
        "end_date": listed["event_end"].isoformat(),
        "apply_start_date": listed["apply_start"].isoformat(),
        "apply_end_date": listed["apply_end"].isoformat(),
        "target": _clean(listed["target"]),
        "fee": "요금 별도 안내",
        "price_text": "요금 별도 안내",
        "capacity": int(listed["capacity_total"]),
        "capacity_current": int(listed["capacity_current"]),
        "capacity_total": int(listed["capacity_total"]),
        "capacity_remaining": max(0, int(listed["capacity_total"]) - int(listed["capacity_current"])),
        "venue_name": venue,
        "room": venue,
        "address": address,
        "venue_address": address,
        "description": " | ".join(part for part in description_parts if part),
        "category": "교육",
        "collection_category": _clean(extra.get("collection_category") or "공공예약"),
        "domain_category": _clean(extra.get("domain_category") or "교육·강좌"),
        "operator_type": _clean(extra.get("operator_type") or "지자체/공공기관"),
        "source_group": _clean(extra.get("source_group") or "municipal_reservation"),
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": "static_html+detail_html",
        "program_type": "교육",
        "municipality_code": CHEONGYANG_MUNICIPALITY_CODE,
        "municipality_name": CHEONGYANG_MUNICIPALITY_NAME,
        "municipality_full_name": CHEONGYANG_MUNICIPALITY_NAME,
        "raw_fields": {
            "parser": CHEONGYANG_PARSER,
            "identity": identity,
            "source_page": page,
            "source_status": _clean(listed["source_status"]),
            "source_institution": institution,
            "source_venue": venue,
            "filter_scope": "reservYn=Y (모집중)",
            "branch_basis": "detail 교육장소",
            "detail_verified": True,
            "fee_contract": "official detail omits a fee field",
            "application_control_verified": open_now,
            "inactive_application_absence_verified": not open_now,
        },
    }
    prose = " ".join(_clean(row.get(key)) for key in ("title", "branch", "target", "description"))
    if _PHONE.search(prose) or _EMAIL.search(prose):
        raise CheongyangContractError(f"course {identity}: public row leaked contact data")
    return row


def collect_cheongyang_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = CHEONGYANG_MAX_PAGES,
    detail_limit: int = 100,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[HtmlFetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect the complete current recruiting Cheongyang education ledger."""

    audit_date = _audit_date(today)
    factory = session_factory or cheongyang_session_factory
    html_fetcher = fetcher or _default_fetcher
    meta: dict[str, Any] = {
        "municipality_code": CHEONGYANG_MUNICIPALITY_CODE,
        "municipality_name": CHEONGYANG_MUNICIPALITY_NAME,
        "owner_provider": CHEONGYANG_PROVIDER,
        "canonical_url": CHEONGYANG_CANONICAL_URL,
        "candidate_id": CHEONGYANG_CANONICAL_CANDIDATE_ID,
        "parser": CHEONGYANG_PARSER,
        "cutoff": audit_date.isoformat(),
        "pages": 0,
        "data_pages": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "application_endpoint_requests": 0,
        "applicant_list_requests": 0,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "configured_collection_error": "",
    }
    session: Any = None
    try:
        if not is_cheongyang_education_target(target):
            raise CheongyangContractError("target is not the canonical Cheongyang owner")
        if timeout < 1 or max_pages < 1 or detail_limit < 0:
            raise CheongyangContractError("invalid collector limits")
        session = factory()
        pages: dict[int, _Page] = {}
        sentinel: Optional[_Page] = None
        for page_number in range(1, max_pages + 2):
            parsed = _parse_list_page(
                _fetch_soup(
                    session,
                    cheongyang_list_url(page_number),
                    timeout,
                    html_fetcher,
                ),
                page_number,
            )
            meta["list_requests"] += 1
            if not parsed.rows:
                sentinel = parsed
                break
            if page_number > max_pages:
                meta["source_cap_reached"] = True
                raise CheongyangContractError("max_pages reached before empty boundary")
            pages[page_number] = parsed
        if sentinel is None:
            meta["source_cap_reached"] = True
            raise CheongyangContractError("max_pages reached before empty boundary")
        if sorted(pages) != list(range(1, len(pages) + 1)):
            raise CheongyangContractError("data pages are not consecutive")
        for page_number, page in pages.items():
            if page_number < len(pages) and len(page.rows) != CHEONGYANG_PAGE_SIZE:
                raise CheongyangContractError(f"page {page_number}: premature short page before boundary")
            if not 1 <= len(page.rows) <= CHEONGYANG_PAGE_SIZE:
                raise CheongyangContractError(f"page {page_number}: invalid row count")

        stability_rechecks = 0
        if pages:
            boundary_numbers = [1]
            if len(pages) > 1:
                boundary_numbers.append(len(pages))
            for page_number in boundary_numbers:
                check = _parse_list_page(
                    _fetch_soup(
                        session,
                        cheongyang_list_url(page_number),
                        timeout,
                        html_fetcher,
                    ),
                    page_number,
                )
                meta["list_requests"] += 1
                stability_rechecks += 1
                if _page_signature(check) != _page_signature(pages[page_number]):
                    raise CheongyangContractError(f"page {page_number}: boundary stability recheck changed")
        sentinel_check = _parse_list_page(
            _fetch_soup(
                session,
                cheongyang_list_url(sentinel.number),
                timeout,
                html_fetcher,
            ),
            sentinel.number,
        )
        meta["list_requests"] += 1
        stability_rechecks += 1
        if sentinel.rows or sentinel_check.rows:
            raise CheongyangContractError("structural empty boundary changed")

        listed = [row for number in sorted(pages) for row in pages[number].rows]
        identities = [_clean(row["identity"]) for row in listed]
        if len(identities) != len(set(identities)):
            raise CheongyangContractError("duplicate identities across pages")
        for row in listed:
            identity = _clean(row["identity"])
            if row["event_end"] < audit_date:
                raise CheongyangContractError(f"course {identity}: recruiting source contains an expired course")
            status = _clean(row["status"])
            if (
                status == "OPEN"
                and not row["apply_start"] <= audit_date <= row["apply_end"]
            ):
                raise CheongyangContractError(
                    f"course {identity}: 모집중 contradicts its application period"
                )
            if status == "SCHEDULED" and audit_date > row["apply_start"]:
                raise CheongyangContractError(
                    f"course {identity}: 모집예정 contradicts its application period"
                )
            if status == "CLOSED" and not (
                row["capacity_current"] >= row["capacity_total"]
                or audit_date >= row["apply_end"]
            ):
                raise CheongyangContractError(
                    f"course {identity}: 모집마감 lacks a capacity/date boundary"
                )
        if len(listed) > detail_limit:
            meta["source_cap_reached"] = True
            raise CheongyangContractError("detail_limit would create a partial snapshot")

        output: list[dict[str, Any]] = []
        for listed_row in listed:
            identity = _clean(listed_row["identity"])
            detail_url = _clean(listed_row["detail_fetch_url"])
            detail = _fetch_soup(session, detail_url, timeout, html_fetcher)
            meta["detail_pages"] += 1
            output.append(_row_from_detail(target, listed_row, detail))
        if dedupe_rows is not None:
            output = list(dedupe_rows(output))

        branch_counts = dict(Counter(_clean(row.get("branch")) for row in output))
        meta.update(
            {
                "pages": len(pages),
                "data_pages": len(pages),
                "page_counts": {number: len(page.rows) for number, page in sorted(pages.items())},
                "empty_boundary_page": sentinel.number,
                "stability_rechecks": stability_rechecks,
                "source_rows": len(listed),
                "source_total": len(listed),
                "source_status_counts": dict(Counter(_clean(row["source_status"]) for row in listed)),
                "current_source_count": len(listed),
                "expired_source_count": 0,
                "detail_attempts": len(listed),
                "detail_verified": len(listed),
                "application_control_count": sum(
                    row["status"] == "OPEN" for row in listed
                ),
                "branch_counts": branch_counts,
                "returned_count": len(output),
                "output_rows": len(output),
                "logical_requests": int(meta["list_requests"]) + int(meta["detail_pages"]),
                "physical_requests": int(meta["list_requests"]) + int(meta["detail_pages"]),
                "request_retry_count": 0,
                "pagination_complete": True,
                "details_complete": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
                "no_current_data": not listed,
                "no_current_reason": ("공식 reservYn=Y 모집중 원장이 구조적으로 비어 있음" if not listed else ""),
            }
        )
        return output, CHEONGYANG_PARSER, meta
    except Exception as exc:  # fail closed for every parser/network contract failure
        meta.update(
            {
                "configured_collection_error": f"{type(exc).__name__}: {_clean(exc)}",
                "pagination_complete": False,
                "details_complete": False,
                "snapshot_complete": False,
                "full_snapshot_validated": False,
                "returned_count": 0,
                "output_rows": 0,
                "logical_requests": int(meta.get("list_requests") or 0) + int(meta.get("detail_pages") or 0),
                "physical_requests": int(meta.get("list_requests") or 0) + int(meta.get("detail_pages") or 0),
                "request_retry_count": 0,
            }
        )
        return [], CHEONGYANG_PARSER, meta
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


collect = collect_cheongyang_education


__all__ = [
    "CHEONGYANG_CANONICAL_CANDIDATE_ID",
    "CHEONGYANG_CANONICAL_DERIVED_PROVIDER",
    "CHEONGYANG_CANONICAL_URL",
    "CHEONGYANG_CHILD_EXPERIENCE_URL",
    "CHEONGYANG_HOST",
    "CHEONGYANG_LIST_PATH",
    "CHEONGYANG_MUNICIPALITY_CODE",
    "CHEONGYANG_MUNICIPALITY_NAME",
    "CHEONGYANG_MUSEUM_GROUP_URL",
    "CHEONGYANG_NOTICE_BOARD_URL",
    "CHEONGYANG_OFFICIAL_ADDRESS",
    "CHEONGYANG_OFFICIAL_BRANCH",
    "CHEONGYANG_OWNER_BOUNDARY_AUDIT",
    "CHEONGYANG_PARSER",
    "CHEONGYANG_PII_FIELDS_NEVER_PERSISTED",
    "CHEONGYANG_PROVIDER",
    "CHEONGYANG_REVIEW_BOARD_CANDIDATE_ID",
    "CHEONGYANG_REVIEW_BOARD_PROVIDER",
    "CHEONGYANG_SITE_NAME",
    "CheongyangContractError",
    "cheongyang_detail_url",
    "cheongyang_list_url",
    "cheongyang_session_factory",
    "collect_cheongyang_education",
    "is_cheongyang_education_target",
]
