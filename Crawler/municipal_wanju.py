"""Fail-closed collector for Wanju County Library's education programme board.

Wanju does not publish a county-wide structured reservation catalogue.  The
county library does publish one authoritative programme ledger with a stable
``dataUid`` per post, an official event period, an official library label, and
identity-bound application links on current education details.  This collector
walks every numbered row plus the separately declared pinned notices, verifies
an empty post-last page and stable first/last boundaries, and then retains only
current/future education programmes.

Concerts, ceremonies, performances, trips, facility notices, attachments,
authors, contacts, and free-form bodies are not persisted.  Detail body text is
used transiently only to detect an explicit closed marker and application-link
context.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import ipaddress
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


WANJU_PROVIDER = "MUNI_LIB_WANJU_GO_KR_083FC338"
WANJU_CANDIDATE_ID = "MUNI_IR_236996DF1FD7"
WANJU_MUNICIPALITY_CODE = "5271000000"
WANJU_MUNICIPALITY_NAME = "전북특별자치도 완주군"
WANJU_HOST = "lib.wanju.go.kr"
WANJU_LIST_PATH = "/planweb/board/list.9is"
WANJU_DETAIL_PATH = "/planweb/board/view.9is"
WANJU_BOARD_UID = "ff808081737e5a410173a2472101563d"
WANJU_CONTENT_UID = "ff808081727e842d017291a8906600c8"
WANJU_CANONICAL_URL = (
    f"https://{WANJU_HOST}{WANJU_LIST_PATH}?"
    + urlencode(
        (("boardUid", WANJU_BOARD_UID), ("contentUid", WANJU_CONTENT_UID))
    )
)
WANJU_PAGE_SIZE = 10
WANJU_MAX_WORKERS = 4
WANJU_MAX_HTML_BYTES = 8_000_000
WANJU_PARSER = (
    "wanju_county_library_complete_programme_board+numbered_rows_and_pinned_"
    "notices+empty_sentinel+stable_first_last+education_semantic_partition+"
    "current_details+identity_bound_external_application_controls+"
    "official_library_branches+pii_allowlist"
)

WANJU_BRANCHES: Mapping[str, tuple[str, str]] = {
    "공통": ("완주군립도서관(공통)", "WANJU_LIBRARY_COMMON"),
    "중앙": ("완주군립중앙도서관", "WANJU_LIBRARY_CENTRAL"),
    "삼례": ("삼례도서관", "WANJU_LIBRARY_SAMRYE"),
    "고산": ("고산도서관", "WANJU_LIBRARY_GOSAN"),
    "둔산": ("둔산영어도서관", "WANJU_LIBRARY_DUNSAN"),
    "콩쥐팥쥐": ("콩쥐팥쥐도서관", "WANJU_LIBRARY_KONGJWI_PATJWI"),
    "작은": ("완주군 작은도서관(공통)", "WANJU_SMALL_LIBRARIES"),
    "고운삼봉": ("고운삼봉도서관", "WANJU_LIBRARY_GOUN_SAMBONG"),
    "미표기": (
        "완주군립도서관(공식 게시물 지점 미표기)",
        "WANJU_LIBRARY_UNSPECIFIED",
    ),
}

# The complete archive has exactly these 20 old rows whose title predates the
# current ``<strong>[지점]</strong>`` convention.  Keeping the identities
# explicit prevents a newly malformed row from being silently assigned to an
# arbitrary branch.  All are expired; current/future rows must still carry an
# official branch label.
WANJU_LEGACY_UNLABELED = frozenset(
    {
        "ff8080818cd76465018d78d8d00b405e",
        "ff80808189294652018a491bcdab66cf",
        "ff808081892946520189aaab55fe09e3",
        "ff8080818929465201896cd8c9c34cfd",
        "ff808081737e5a410173a25917295667",
        "ff808081737e5a410173a25917245666",
        "ff808081737e5a410173a259171e5665",
        "ff808081737e5a410173a25917195664",
        "ff808081737e5a410173a25917145663",
        "ff808081737e5a410173a25917055660",
        "ff808081737e5a410173a25916fc565e",
        "ff808081737e5a410173a25916f1565c",
        "ff808081737e5a410173a25916e8565a",
        "ff808081737e5a410173a25916dd5658",
        "ff808081737e5a410173a25916cc5656",
        "ff808081737e5a410173a25916b25654",
        "ff808081737e5a410173a25916a45651",
        "ff808081737e5a410173a2591697564f",
        "ff808081737e5a410173a259168b564d",
        "ff808081737e5a410173a2591657564b",
    }
)

# These are the only two rows in the complete official archive without an
# event period.  Both details declare 2016 registration dates and are cultural
# events rather than education programmes.
WANJU_LEGACY_PERIODLESS: Mapping[str, str] = {
    "ff808081737e5a410173a259170f5662": "2016-11-29",
    "ff808081737e5a410173a259170a5661": "2016-11-15",
}

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class WanjuContractError(ValueError):
    """Raised when the audited official board contract changes."""


@dataclass(frozen=True)
class _Listed:
    identity: str
    number: Optional[int]
    pinned: bool
    title: str
    branch_label: str
    branch: str
    branch_code: str
    start: date
    end: date
    registered: date
    page: int
    legacy_periodless: bool


@dataclass(frozen=True)
class _Page:
    requested: int
    observed: int
    total: int
    last: int
    numbered: tuple[_Listed, ...]
    notices: tuple[_Listed, ...]


_SPACE = re.compile(r"\s+")
_IDENTITY = re.compile(r"^[0-9a-f]{32}$")
_DATE = re.compile(r"20\d{2}-\d{2}-\d{2}")
_MARKER = re.compile(
    r"전체\s*([\d,]+)\s*건\s*페이지\s*(\d+)\s*/\s*(\d+)"
)
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_EDUCATION_TOKENS = (
    "독서교실",
    "교육",
    "교실",
    "강좌",
    "특강",
    "수강",
    "인문학",
    "공방",
    "아카데미",
    "클래스",
    "동아리",
    "학습",
    "배움",
    "워크숍",
)
_NON_EDUCATION_TOKENS = (
    "공연",
    "콘서트",
    "여행",
    "체험",
    "개관식",
    "휴관",
    "주차",
    "시범운영",
    "전시",
    "영화",
    "마술",
    "탐방",
    "축제",
    "대회",
    "행사 운영",
)
_CLOSED_TITLE_TOKENS = ("신청마감", "모집완료", "[마감]", "(마감)")
_CLOSED_BODY_TOKENS = (
    "모집마감",
    "모집 마감",
    "신청마감",
    "신청 마감",
    "접수마감",
    "접수 마감",
)
_OPEN_TITLE_TOKENS = (
    "모집중",
    "마감임박",
    "추가모집",
    "참여자 모집",
    "수강생 모집",
    "[모집]",
)
_APPLICATION_CONTEXT = ("신청", "접수", "참여")
_AUDITED_APPLICATION_HOSTS = frozenset({"naver.me"})
_SENSITIVE_QUERY_KEYS = {
    "token",
    "session",
    "sessionid",
    "jsessionid",
    "password",
    "passwd",
    "ci",
    "di",
}
_SAFE_RAW = frozenset(
    {
        "identity",
        "source_number",
        "source_page",
        "pinned_notice",
        "official_branch_label",
        "official_event_period",
        "registered_date",
        "source_status",
        "semantic_classification",
        "application_control_count",
        "application_control_kind",
        "detail_verified",
        "legacy_periodless",
        "service_family",
    }
)
_FORBIDDEN = frozenset(
    {
        "phone",
        "email",
        "contact",
        "author",
        "instructor",
        "attachments",
        "attachment_urls",
        "detail_description",
        "source_html",
        "raw_html",
        "image_url",
    }
)


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def is_wanju_education_target(target: Any) -> bool:
    if _clean(_value(target, "provider")) != WANJU_PROVIDER:
        return False
    parsed = urlparse(_clean(_value(target, "url")))
    try:
        port = parsed.port
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError):
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == WANJU_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == WANJU_LIST_PATH
        and sorted(query)
        == [("boardUid", WANJU_BOARD_UID), ("contentUid", WANJU_CONTENT_UID)]
        and not parsed.fragment
    )


is_target = is_wanju_education_target


def _session() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MooncenMunicipalCrawler/1.0)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return current


def _request(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=False)


def _coerce_soup(value: Any, requested_url: str) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    status = int(getattr(value, "status_code", 200))
    if status != 200:
        raise WanjuContractError(f"unexpected HTTP status {status}")
    headers = getattr(value, "headers", {}) or {}
    if headers.get("Location"):
        raise WanjuContractError("redirect response is not accepted")
    final_url = str(getattr(value, "url", requested_url) or requested_url)
    parsed = urlparse(final_url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != WANJU_HOST:
        raise WanjuContractError("response left the official host")
    content = getattr(value, "content", None)
    if content is None:
        content = str(getattr(value, "text", value)).encode("utf-8")
    if not content:
        raise WanjuContractError("empty official response")
    if len(content) > WANJU_MAX_HTML_BYTES:
        raise WanjuContractError("HTML size cap exceeded")
    return BeautifulSoup(content, "lxml")


def _soup(
    url: str,
    timeout: int,
    factory: SessionFactory,
    fetcher: Fetcher,
) -> BeautifulSoup:
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != WANJU_HOST:
        raise WanjuContractError("non-canonical request refused")
    last_error: Optional[Exception] = None
    for _attempt in range(3):
        current = factory()
        try:
            return _coerce_soup(fetcher(current, url, timeout), url)
        except (requests.RequestException, TimeoutError) as exc:
            last_error = exc
        finally:
            close = getattr(current, "close", None)
            if callable(close):
                close()
    assert last_error is not None
    raise last_error


def wanju_list_url(page: int = 1) -> str:
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("page must be a positive integer")
    query: list[tuple[str, Any]] = [
        ("boardUid", WANJU_BOARD_UID),
        ("contentUid", WANJU_CONTENT_UID),
    ]
    if page > 1:
        query.append(("page", page))
    return f"https://{WANJU_HOST}{WANJU_LIST_PATH}?{urlencode(query)}"


def wanju_detail_url(identity: str) -> str:
    if not _IDENTITY.fullmatch(str(identity)):
        raise ValueError("invalid programme identity")
    return (
        f"https://{WANJU_HOST}{WANJU_DETAIL_PATH}?"
        + urlencode(
            (
                ("boardUid", WANJU_BOARD_UID),
                ("contentUid", WANJU_CONTENT_UID),
                ("dataUid", identity),
            )
        )
    )


def _period(value: str) -> tuple[date, date]:
    values = _DATE.findall(value)
    if len(values) != 2:
        raise WanjuContractError("event period shape changed")
    start, end = date.fromisoformat(values[0]), date.fromisoformat(values[1])
    if end < start:
        raise WanjuContractError("event period is reversed")
    return start, end


def _title(anchor: Any, identity: str = "") -> tuple[str, str]:
    clone = BeautifulSoup(str(anchor), "lxml")
    branch_node = clone.select_one("strong")
    branch_text = _clean(branch_node.get_text(" ", strip=True) if branch_node else "")
    match = re.fullmatch(r"\[([^\]]+)\]", branch_text)
    if match is None and identity in WANJU_LEGACY_UNLABELED:
        branch_label = "미표기"
    elif match is not None and match.group(1) in WANJU_BRANCHES:
        branch_label = match.group(1)
    else:
        raise WanjuContractError("official library branch label changed")
    if branch_node is not None:
        branch_node.decompose()
    for node in clone.select(".icon-new"):
        node.decompose()
    title = _clean(clone.get_text(" ", strip=True))
    if not title:
        raise WanjuContractError("programme title missing")
    return title, branch_label


def _listed_row(node: Any, page: int, pinned: bool) -> _Listed:
    anchor = node.select_one(".title a[href*='view.9is'][href*='dataUid=']")
    if anchor is None:
        raise WanjuContractError(f"page {page}: row identity missing")
    href = urljoin(wanju_list_url(page), _clean(anchor.get("href")))
    parsed = urlparse(href)
    query = parse_qs(parsed.query)
    identity = (query.get("dataUid") or [""])[0]
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != WANJU_HOST
        or parsed.path != WANJU_DETAIL_PATH
        or not _IDENTITY.fullmatch(identity)
        or query.get("boardUid") != [WANJU_BOARD_UID]
        or query.get("contentUid") != [WANJU_CONTENT_UID]
    ):
        raise WanjuContractError(f"page {page}: invalid detail identity")
    number_text = _clean(
        node.select_one(".num").get_text(" ", strip=True)
        if node.select_one(".num")
        else ""
    )
    if pinned:
        if number_text != "공지":
            raise WanjuContractError(f"page {page}: pinned row marker changed")
        number: Optional[int] = None
    else:
        if not number_text.isdigit():
            raise WanjuContractError(f"page {page}: numbered row marker changed")
        number = int(number_text)
    title, branch_label = _title(anchor, identity)
    branch, branch_code = WANJU_BRANCHES[branch_label]
    event_node = node.select_one(".col_w20")
    registered_node = node.select_one(".date")
    event_text = _clean(event_node.get_text(" ", strip=True) if event_node else "")
    registered_text = _clean(
        registered_node.get_text(" ", strip=True) if registered_node else ""
    )
    if not _DATE.fullmatch(registered_text):
        raise WanjuContractError(f"course {identity}: registration date missing")
    registered = date.fromisoformat(registered_text)
    if event_text:
        start, end = _period(event_text)
        legacy_periodless = False
    else:
        expected = WANJU_LEGACY_PERIODLESS.get(identity)
        if expected != registered.isoformat():
            raise WanjuContractError(
                f"course {identity}: unrecognized periodless official row"
            )
        start = end = registered
        legacy_periodless = True
    return _Listed(
        identity=identity,
        number=number,
        pinned=pinned,
        title=title,
        branch_label=branch_label,
        branch=branch,
        branch_code=branch_code,
        start=start,
        end=end,
        registered=registered,
        page=page,
        legacy_periodless=legacy_periodless,
    )


def _parse_page(soup: BeautifulSoup, requested: int) -> _Page:
    head = soup.select_one(".headList")
    marker = _clean(head.get_text(" ", strip=True) if head else "")
    match = _MARKER.search(marker)
    if match is None:
        raise WanjuContractError(f"page {requested}: declared count marker missing")
    total = int(match.group(1).replace(",", ""))
    observed, last = int(match.group(2)), int(match.group(3))
    if last != max(1, math.ceil(total / WANJU_PAGE_SIZE)):
        raise WanjuContractError("declared last page drift")
    if observed != requested:
        raise WanjuContractError(
            f"page {requested}: observed page {observed} instead of requested page"
        )
    numbered: list[_Listed] = []
    notices: list[_Listed] = []
    for node in soup.select(".list_group .group_con > ul"):
        anchor = node.select_one(".title a[href*='view.9is'][href*='dataUid=']")
        if anchor is None:
            continue
        pinned = "colNotice" in (node.get("class") or [])
        if pinned and requested != 1:
            raise WanjuContractError("pinned notices appeared outside page one")
        row = _listed_row(node, requested, pinned)
        (notices if pinned else numbered).append(row)
    if requested <= last:
        expected = min(WANJU_PAGE_SIZE, total - ((requested - 1) * WANJU_PAGE_SIZE))
        if len(numbered) != expected:
            raise WanjuContractError(
                f"page {requested}: expected {expected} numbered rows, found {len(numbered)}"
            )
    elif numbered or notices:
        raise WanjuContractError("post-last page is not structurally empty")
    identities = [row.identity for row in numbered + notices]
    if len(identities) != len(set(identities)):
        raise WanjuContractError(f"page {requested}: duplicate identities")
    return _Page(requested, observed, total, last, tuple(numbered), tuple(notices))


def _signature(page: _Page) -> tuple[Any, ...]:
    return (
        page.total,
        page.last,
        tuple(
            (row.identity, row.number, row.title, row.start, row.end)
            for row in page.notices + page.numbered
        ),
    )


def _semantic(title: str) -> str:
    if any(token in title for token in _NON_EDUCATION_TOKENS):
        return "non_education_cultural_or_facility_event"
    if any(token in title for token in _EDUCATION_TOKENS):
        return "education"
    return "non_education_unclassified_event"


def _detail_title(root: Any) -> tuple[str, str]:
    node = root.select_one(".view-title h4")
    if node is None:
        raise WanjuContractError("detail title missing")
    return _title(node)


def _detail_fields(root: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in root.select(".view-info > li"):
        label = item.select_one("strong")
        value = item.select_one("span")
        if label is None or value is None:
            continue
        key = _clean(label.get_text(" ", strip=True))
        text = _clean(value.get_text(" ", strip=True))
        if key in result and result[key] != text:
            raise WanjuContractError(f"conflicting detail field {key}")
        result[key] = text
    return result


def _safe_external_application_url(value: str) -> bool:
    parsed = urlparse(value)
    try:
        port = parsed.port
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=False)
    except (TypeError, ValueError):
        return False
    hostname = (parsed.hostname or "").rstrip(".").lower()
    if not (
        parsed.scheme == "https"
        and hostname
        and hostname != WANJU_HOST
        and port in {None, 443}
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
        and not any(key.lower() in _SENSITIVE_QUERY_KEYS for key, _ in query)
    ):
        return False
    if hostname == "localhost":
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return True
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def _application_controls(root: Any, detail_url: str) -> tuple[str, ...]:
    container = root.select_one(".view-con")
    if container is None:
        raise WanjuContractError("detail content boundary missing")
    result: list[str] = []
    for anchor in container.select("a[href]"):
        href = urljoin(detail_url, _clean(anchor.get("href")))
        parsed = urlparse(href)
        if (parsed.hostname or "").lower() == WANJU_HOST:
            # Attachments and images are explicitly outside the education row.
            continue
        context = _clean(
            " ".join(
                (
                    anchor.get_text(" ", strip=True),
                    anchor.parent.get_text(" ", strip=True)
                    if anchor.parent is not None
                    else "",
                )
            )
        )
        hostname = (parsed.hostname or "").rstrip(".").lower()
        # Current Wanju notices often publish a bare Naver Form short URL with
        # no adjacent "신청" label.  Only that audited host may bypass the
        # contextual-token requirement; every other external URL remains
        # fail-closed.
        if not (
            any(token in context for token in _APPLICATION_CONTEXT)
            or hostname in _AUDITED_APPLICATION_HOSTS
        ):
            continue
        if not _safe_external_application_url(href):
            raise WanjuContractError("unsafe external application control")
        if href not in result:
            result.append(href)
    return tuple(result)


def _status(title: str, body_text: str, cutoff: date, start: date) -> str:
    if any(token in body_text for token in _CLOSED_BODY_TOKENS):
        return "CLOSED"
    if "마감임박" not in title and any(
        token in title for token in _CLOSED_TITLE_TOKENS
    ):
        return "CLOSED"
    if any(token in title for token in _OPEN_TITLE_TOKENS):
        return "OPEN"
    return "SCHEDULED" if start > cutoff else "CLOSED"


def _detail(listed: _Listed, soup: BeautifulSoup, cutoff: date) -> dict[str, Any]:
    root = soup.select_one(".view-group")
    if root is None:
        raise WanjuContractError(f"course {listed.identity}: detail root missing")
    title, branch_label = _detail_title(root)
    if title != listed.title or branch_label != listed.branch_label:
        raise WanjuContractError(f"course {listed.identity}: detail identity drift")
    fields = _detail_fields(root)
    if not {"등록일", "행사기간"} <= set(fields):
        raise WanjuContractError(f"course {listed.identity}: detail fields missing")
    if fields["등록일"] != listed.registered.isoformat():
        raise WanjuContractError(f"course {listed.identity}: registration date drift")
    start, end = _period(fields["행사기간"])
    if (start, end) != (listed.start, listed.end) or end < cutoff:
        raise WanjuContractError(f"course {listed.identity}: event period drift")
    content = root.select_one(".view-con")
    body_text = _clean(content.get_text(" ", strip=True) if content else "")
    controls = _application_controls(root, wanju_detail_url(listed.identity))
    status = _status(listed.title, body_text, cutoff, start)
    if status == "OPEN" and len(controls) == 1:
        application_url = controls[0]
        application_type = "EXTERNAL_OFFICIAL_LINK"
    elif status == "OPEN" and len(controls) > 1:
        application_url = wanju_detail_url(listed.identity)
        application_type = "MULTIPLE_EXTERNAL_CONTROLS_ON_OFFICIAL_DETAIL"
    elif status == "OPEN":
        application_url = ""
        application_type = "OFFICIAL_NOTICE_ONLY"
    else:
        application_url = ""
        application_type = "INFORMATION_ONLY"
    period = f"{start.isoformat()} ~ {end.isoformat()}"
    row = {
        "provider": WANJU_PROVIDER,
        "provider_course_id": f"{WANJU_PROVIDER}:{listed.identity}",
        "prefer_incoming_provider_course_id": True,
        "title": listed.title,
        "description": listed.title,
        "branch": listed.branch,
        "branch_code": listed.branch_code,
        "preserve_branch": True,
        "category": "독서문화교육",
        "program_type": "교육",
        "raw_url": wanju_detail_url(listed.identity),
        "application_url": application_url,
        "application_type": application_type,
        "application_method": "공식 상세의 외부 신청 링크" if controls else "공식 공지 확인",
        "reservation_available": bool(status == "OPEN" and controls),
        "status": status,
        "fee": "",
        "period": period,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": "",
        "schedule_raw": period,
        "capacity": "",
        "capacity_current": None,
        "capacity_total": None,
        "target": "",
        "venue": listed.branch,
        "venue_name": listed.branch,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": WANJU_PARSER,
        "municipality_code": WANJU_MUNICIPALITY_CODE,
        "municipality_full_name": WANJU_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": listed.identity,
            "source_number": listed.number,
            "source_page": listed.page,
            "pinned_notice": listed.pinned,
            "official_branch_label": listed.branch_label,
            "official_event_period": period,
            "registered_date": listed.registered.isoformat(),
            "source_status": status,
            "semantic_classification": "education",
            "application_control_count": len(controls),
            "application_control_kind": application_type,
            "detail_verified": True,
            "legacy_periodless": False,
            "service_family": "education",
        },
    }
    return row


def _contact_in(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_contact_in(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_contact_in(item) for item in value)
    text = _clean(value)
    return bool(_PHONE.search(text) or _EMAIL.search(text))


def _privacy(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN:
        errors.append("forbidden detail/PII key")
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping) or not set(raw) <= _SAFE_RAW:
        errors.append("raw field allowlist exceeded")
    public = {
        key: row.get(key)
        for key in (
            "title",
            "description",
            "branch",
            "fee",
            "target",
            "venue",
            "venue_name",
            "application_method",
            "raw_fields",
        )
    }
    if _contact_in(public):
        errors.append("contact data persisted")
    return errors


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = str(row["provider_course_id"])
        if identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now().astimezone().date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(_clean(value))


def collect_wanju_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 100,
    detail_limit: int = 100,
    today: Optional[date | datetime | str] = None,
    max_workers: int = WANJU_MAX_WORKERS,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    meta: dict[str, Any] = {
        "municipality_code": WANJU_MUNICIPALITY_CODE,
        "owner_provider": WANJU_PROVIDER,
        "canonical_url": WANJU_CANONICAL_URL,
        "parser": WANJU_PARSER,
        "list_requests": 0,
        "detail_pages": 0,
        "source_rows": 0,
        "current_source_count": 0,
        "returned_count": 0,
        "pagination_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "configured_collection_error": "",
    }
    if not is_wanju_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match canonical Wanju library owner"
        )
        return [], WANJU_PARSER, meta
    try:
        cutoff = _today(today)
        if any(
            isinstance(value, bool) or int(value) < 1
            for value in (timeout, max_pages, max_workers)
        ) or isinstance(detail_limit, bool) or int(detail_limit) < 0:
            raise ValueError("invalid collection limits")
    except Exception as exc:
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": f"{type(exc).__name__}: {_clean(exc)}",
            }
        )
        return [], WANJU_PARSER, meta
    factory, current_fetcher = session_factory or _session, fetcher or _request
    workers = min(int(max_workers), WANJU_MAX_WORKERS)
    try:
        first = _parse_page(
            _soup(wanju_list_url(1), int(timeout), factory, current_fetcher), 1
        )
        meta["list_requests"] = 1
        required = first.last + 3
        if required > int(max_pages):
            raise WanjuContractError(
                f"max_pages {max_pages} below required {required}"
            )
        jobs = [("data", page) for page in range(2, first.last + 1)] + [
            ("sentinel", first.last + 1),
            ("first", 1),
            ("last", first.last),
        ]
        pages: dict[int, _Page] = {1: first}
        checks: dict[str, _Page] = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    lambda page=page: _parse_page(
                        _soup(
                            wanju_list_url(page),
                            int(timeout),
                            factory,
                            current_fetcher,
                        ),
                        page,
                    )
                ): (kind, page)
                for kind, page in jobs
            }
            for future in as_completed(futures):
                kind, page = futures[future]
                parsed = future.result()
                meta["list_requests"] += 1
                if kind == "data":
                    pages[page] = parsed
                else:
                    checks[kind] = parsed
        if set(pages) != set(range(1, first.last + 1)):
            raise WanjuContractError("data page missing")
        if any(
            page.total != first.total or page.last != first.last
            for page in pages.values()
        ):
            raise WanjuContractError("catalogue boundary drift")
        if checks.get("sentinel") is None or (
            checks["sentinel"].numbered or checks["sentinel"].notices
        ):
            raise WanjuContractError("post-last structural empty sentinel failed")
        if checks.get("first") is None or _signature(checks["first"]) != _signature(first):
            raise WanjuContractError("first page stability recheck failed")
        if checks.get("last") is None or _signature(checks["last"]) != _signature(
            pages[first.last]
        ):
            raise WanjuContractError("last page stability recheck failed")
        numbered = [
            row
            for page in range(1, first.last + 1)
            for row in pages[page].numbered
        ]
        notices = list(first.notices)
        numbers = [row.number for row in numbered]
        if (
            len(numbered) != first.total
            or set(numbers) != set(range(1, first.total + 1))
            or len(numbers) != len(set(numbers))
        ):
            raise WanjuContractError("declared numbered ledger is incomplete")
        listed = notices + numbered
        identities = [row.identity for row in listed]
        if len(identities) != len(set(identities)):
            raise WanjuContractError("pinned and numbered identities overlap")
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        meta["source_cap_reached"] = "max_pages" in meta["configured_collection_error"]
        return [], WANJU_PARSER, meta

    current_all = [row for row in listed if row.end >= cutoff]
    current_education = [
        row for row in current_all if _semantic(row.title) == "education"
    ]
    current_excluded = [
        row for row in current_all if _semantic(row.title) != "education"
    ]
    meta.update(
        {
            "cutoff": cutoff.isoformat(),
            "declared_numbered_total": first.total,
            "pinned_notice_count": len(notices),
            "source_rows": len(listed),
            "source_total": len(listed),
            "data_pages": first.last,
            "empty_sentinel_page": first.last + 1,
            "boundary_rechecks": 2,
            "legacy_periodless_count": sum(
                row.legacy_periodless for row in listed
            ),
            "current_source_count": len(current_all),
            "current_education_count": len(current_education),
            "excluded_current_non_education_count": len(current_excluded),
            "excluded_current_non_education_titles": [
                row.title for row in current_excluded
            ],
            "expired_count": len(listed) - len(current_all),
            "semantic_counts": dict(Counter(_semantic(row.title) for row in listed)),
            "pagination_complete": True,
        }
    )
    if len(current_education) > int(detail_limit):
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": (
                    f"detail_limit {detail_limit} below required "
                    f"{len(current_education)}"
                ),
            }
        )
        return [], WANJU_PARSER, meta

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                lambda item=item: _detail(
                    item,
                    _soup(
                        wanju_detail_url(item.identity),
                        int(timeout),
                        factory,
                        current_fetcher,
                    ),
                    cutoff,
                )
            ): item.identity
            for item in current_education
        }
        for future in as_completed(futures):
            try:
                rows.append(future.result())
                meta["detail_pages"] += 1
            except Exception as exc:
                errors.append(
                    f"{futures[future]}: {type(exc).__name__}: {_clean(exc)}"
                )
    if errors:
        meta["configured_collection_error"] = "; ".join(errors[:5])
        return [], WANJU_PARSER, meta
    rows.sort(key=lambda row: (row["start_date"], row["provider_course_id"]))
    rows = list((dedupe_rows or _dedupe)(rows))
    privacy = [error for row in rows for error in _privacy(row)]
    if privacy or len(rows) != len(current_education):
        meta["configured_collection_error"] = (
            "; ".join(privacy[:5]) or "dedupe changed complete education identity set"
        )
        return [], WANJU_PARSER, meta
    meta.update(
        {
            "returned_count": len(rows),
            "status_counts": dict(Counter(row["status"] for row in rows)),
            "branch_counts": dict(Counter(row["branch"] for row in rows)),
            "application_control_count": sum(
                int(row["raw_fields"]["application_control_count"]) for row in rows
            ),
            "actionable_row_count": sum(
                bool(row["reservation_available"]) for row in rows
            ),
            "identity_duplicate_count": 0,
            "snapshot_complete": True,
            "full_snapshot_validated": True,
            "no_current_data": not rows,
        }
    )
    return rows, WANJU_PARSER, meta


collect = collect_wanju_education


__all__ = [
    "WANJU_BRANCHES",
    "WANJU_CANDIDATE_ID",
    "WANJU_CANONICAL_URL",
    "WANJU_LEGACY_PERIODLESS",
    "WANJU_LEGACY_UNLABELED",
    "WANJU_MUNICIPALITY_CODE",
    "WANJU_MUNICIPALITY_NAME",
    "WANJU_PARSER",
    "WANJU_PROVIDER",
    "WanjuContractError",
    "collect_wanju_education",
    "is_wanju_education_target",
    "wanju_detail_url",
    "wanju_list_url",
]
