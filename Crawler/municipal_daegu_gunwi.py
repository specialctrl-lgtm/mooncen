"""Fail-closed collector for Gunwi-gun's official education ledger.

Gunwi-gun does not publish a district-wide integrated reservation catalogue on
its main homepage.  The county homepage links to the Gunwi Agricultural
Technology Center, whose own homepage in turn links to this public online
education-management ledger.  It is a county-department owner and is separate
from Daegu's city-wide ``/lect/list`` aggregate, the city experience/camping
catalogues, the education-office library, and tourism/facility reservation
sites.

The ledger exposes three finite server-side status partitions: applications in
progress, applications being prepared, and ended applications.  It has no
pagination controls.  A complete snapshot therefore requires all three
partitions, a page-parameter non-pagination probe for each partition, a stable
recheck of each partition, and every public programme detail.  Application,
identity-verification, confirmation, and applicant pages are never requested.
Only an explicit allowlist of public programme fields is persisted.

As audited on 2026-07-22, the first two partitions are explicit empty
sentinels and all 12 rows in the ended partition have expired.  This is thus a
deliberate, audited ``no_current_data`` collector rather than an accidental
empty scrape.  Any source-shape, identity, detail, pagination, or privacy
contract change discards the entire snapshot.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import html
import re
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag


GUNWI_EDUCATION_PROVIDER = "MUNI_EDU_GWA_GO_KR_08B25674"
GUNWI_EDUCATION_CANDIDATE_ID = "MUNI_IR_3D1A86E912D5"
GUNWI_MUNICIPALITY_CODE = "2772000000"
GUNWI_MUNICIPALITY_NAME = "대구광역시 군위군"
GUNWI_BRANCH = "군위군농업기술센터"

GUNWI_EDUCATION_HOST = "edu.gwa.go.kr"
GUNWI_EDUCATION_LIST_PATH = "/application/edu_application_list.htm"
GUNWI_EDUCATION_DETAIL_PATH = "/application/edu_dtl_view.php"
GUNWI_EDUCATION_APPLICATION_PATH = "/application/edu_application.php"
GUNWI_EDUCATION_CANONICAL_URL = (
    f"https://{GUNWI_EDUCATION_HOST}{GUNWI_EDUCATION_LIST_PATH}?pageNum=4"
)
GUNWI_EDUCATION_URL = GUNWI_EDUCATION_CANONICAL_URL

GUNWI_COUNTY_HOME_URL = "https://www.gunwi.go.kr/ko/index.do"
GUNWI_AGRITECH_HOME_URL = "https://www.gwa.go.kr/"
DAEGU_CITY_EDUCATION_URL = "https://yeyak.daegu.go.kr/lect/list"
DAEGU_CITY_EXPERIENCE_URL = "https://yeyak.daegu.go.kr/expr/list"
GUNWI_LIBRARY_URL = "https://library.daegu.go.kr/gw/"
GUNWI_ARTS_URL = "https://www.gunwiart.go.kr:6451/"
GUNWI_THEME_PARK_URL = "https://gunwi3964.co.kr/gunwi/main.do"

GUNWI_EDUCATION_PARSER = (
    "gunwi_agritech_three_status_partitions+explicit_empty_sentinels+"
    "nonpagination_probes+stable_rechecks+all_public_details+"
    "current_only+pii_allowlist+atomic_snapshot"
)
GUNWI_EDUCATION_OWNERSHIP_SCOPE = (
    "gunwi_county_agricultural_technology_center_public_education_ledger"
)

# Region-qualified aliases make the central municipal router unambiguous while
# retaining the shorter names used inside this focused module.
DAEGU_GUNWI_PROVIDER = GUNWI_EDUCATION_PROVIDER
DAEGU_GUNWI_CANDIDATE_ID = GUNWI_EDUCATION_CANDIDATE_ID
DAEGU_GUNWI_CANONICAL_URL = GUNWI_EDUCATION_CANONICAL_URL
DAEGU_GUNWI_PARSER = GUNWI_EDUCATION_PARSER
DAEGU_GUNWI_OWNERSHIP_SCOPE = GUNWI_EDUCATION_OWNERSHIP_SCOPE

GUNWI_EDUCATION_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-22",
    "canonical_url": GUNWI_EDUCATION_CANONICAL_URL,
    "official_ownership_chain": (
        GUNWI_COUNTY_HOME_URL,
        GUNWI_AGRITECH_HOME_URL,
        GUNWI_EDUCATION_CANONICAL_URL,
    ),
    "partition_counts": {"ing": 0, "ready": 0, "end": 12},
    "source_rows": 12,
    "public_detail_rows": 12,
    "current_rows": 0,
    "required_list_requests": 9,
    "required_detail_requests": 12,
    "complete_network_requests": 21,
}

GUNWI_EDUCATION_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    GUNWI_EDUCATION_PROVIDER: {
        "decision": "retain_county_department_education_owner",
        "candidate_id": GUNWI_EDUCATION_CANDIDATE_ID,
        "canonical_url": GUNWI_EDUCATION_CANONICAL_URL,
    },
    "DAEGU_CITY_EDUCATION": {
        "decision": "exclude_separate_city_aggregate",
        "url": DAEGU_CITY_EDUCATION_URL,
        "reason": (
            "the audited city education details currently contain no Gunwi-owned "
            "institution and are already owned by DAEGU_RESERVATION"
        ),
    },
    "DAEGU_CITY_EXPERIENCE": {
        "decision": "exclude_non_education_city_owner",
        "url": DAEGU_CITY_EXPERIENCE_URL,
    },
    "GUNWI_LIBRARY": {
        "decision": "exclude_separate_education_office_library_owner",
        "url": GUNWI_LIBRARY_URL,
    },
    "GUNWI_ARTS": {
        "decision": "exclude_facility_performance_owner",
        "url": GUNWI_ARTS_URL,
    },
    "GUNWI_THEME_PARK": {
        "decision": "exclude_tourism_experience_owner",
        "url": GUNWI_THEME_PARK_URL,
    },
    "PRIVATE_BOUNDARY": {
        "decision": "never_request_or_persist",
        "reason": (
            "identity verification, application, confirmation, applicant, "
            "attachment and non-allowlisted page content are out of scope"
        ),
    },
}


class GunwiEducationContractError(ValueError):
    """Raised when an audited Gunwi source contract changes."""


class _TransientFetchError(RuntimeError):
    """Retryable transport or upstream response failure."""


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
Sleeper = Callable[[float], None]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


@dataclass(frozen=True)
class _ListedCourse:
    identity: str
    title: str
    partition: str
    apply_start: Optional[date]
    apply_end: Optional[date]
    start: date
    end: date
    education_hours: str
    capacity_total: int
    capacity_current: Optional[int]
    detail_button_flag: str
    action_message: str

    @property
    def signature(self) -> tuple[Any, ...]:
        return (
            self.identity,
            self.title,
            self.partition,
            self.apply_start.isoformat() if self.apply_start else "",
            self.apply_end.isoformat() if self.apply_end else "",
            self.start.isoformat(),
            self.end.isoformat(),
            self.education_hours,
            self.capacity_total,
            self.capacity_current,
            self.detail_button_flag,
            self.action_message,
        )


_PARTITIONS: Mapping[str, Mapping[str, str]] = {
    "ing": {
        "tab": "교육신청",
        "empty": "현재 신청중인 교육이 없습니다",
        "status": "OPEN",
        "message": "1",
    },
    "ready": {
        "tab": "접수준비",
        "empty": "접수 준비중인 교육이 없습니다",
        "status": "SCHEDULED",
        "message": "2",
    },
    "end": {
        "tab": "기간종료",
        "empty": "기간 종료된 교육이 없습니다",
        "status": "CLOSED",
        "message": "3",
    },
}
_LIST_TITLE = "군위군농업기술센터 온라인교육관리시스템"
_DETAIL_TITLE = GUNWI_BRANCH
_OWNERSHIP_ADDRESS = "대구광역시 군위군 효령면 효우로 97"
_IDENTITY_RE = re.compile(r"^20\d{2}-\d{3}$")
_DETAIL_CONTROL_RE = re.compile(
    r"^javascript:DetailView\(\s*['\"]([^'\"]+)['\"]\s*,\s*"
    r"['\"]([YN])['\"]\s*\);?$",
    re.IGNORECASE,
)
_ACTION_CONTROL_RE = re.compile(
    r"^javascript:page_link\(\s*['\"]([^'\"]+)['\"]\s*,\s*"
    r"['\"]([123])['\"]\s*\);?$",
    re.IGNORECASE,
)
_DATE_RANGE_RE = re.compile(
    r"^(20\d{2})-(\d{2})-(\d{2})\s*~\s*(20\d{2})-(\d{2})-(\d{2})$"
)
_UNKNOWN_DATE_RANGE = "0000-00-00 ~ 0000-00-00"
_INTEGER_RE = re.compile(r"\d[\d,]*")
_SPACE_RE = re.compile(r"\s+")
_PAGINATION_HREF_RE = re.compile(
    r"(?:[?&](?:page|pageNo|pageIndex|currentPage)=\d+|"
    r"javascript:(?:goPage|fnPage|pageMove)\s*\()",
    re.IGNORECASE,
)
_MAX_HTML_BYTES = 5_000_000
_DEFAULT_FETCH_ATTEMPTS = 2


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(
        " ", html.unescape(str(value or "")).replace("\xa0", " ")
    ).strip()


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", _clean(value))


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _canonical_query(value: str) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(parse_qsl(value, keep_blank_values=True)))


def _same_url(actual: str, expected: str) -> bool:
    left = urlparse(_clean(actual))
    right = urlparse(_clean(expected))
    return bool(
        left.scheme.lower() == right.scheme.lower() == "https"
        and (left.hostname or "").rstrip(".").lower()
        == (right.hostname or "").rstrip(".").lower()
        and left.port == right.port
        and left.username is None
        and left.password is None
        and left.path == right.path
        and not left.params
        and not right.params
        and _canonical_query(left.query) == _canonical_query(right.query)
        and not left.fragment
        and not right.fragment
    )


def is_gunwi_education_target(target: Any) -> bool:
    """Match only the exact canonical county-department education owner."""

    return bool(
        _provider(target) == GUNWI_EDUCATION_PROVIDER
        and _same_url(_target_url(target), GUNWI_EDUCATION_CANONICAL_URL)
    )


is_target = is_gunwi_education_target
is_daegu_gunwi_education_target = is_gunwi_education_target


def gunwi_partition_url(partition: str, *, probe_page: Optional[int] = None) -> str:
    if partition not in _PARTITIONS:
        raise GunwiEducationContractError(f"unknown status partition {partition!r}")
    query: list[tuple[str, str]] = [("view", partition)]
    if probe_page is not None:
        if probe_page < 1:
            raise GunwiEducationContractError("probe page must be positive")
        query.append(("page", str(probe_page)))
    return (
        f"https://{GUNWI_EDUCATION_HOST}{GUNWI_EDUCATION_LIST_PATH}?"
        + urlencode(query)
    )


def gunwi_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        return ""
    return (
        f"https://{GUNWI_EDUCATION_HOST}{GUNWI_EDUCATION_DETAIL_PATH}?"
        + urlencode({"mng_no": value, "btn_dp": "N"})
    )


def gunwi_application_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        return ""
    return (
        f"https://{GUNWI_EDUCATION_HOST}{GUNWI_EDUCATION_APPLICATION_PATH}?"
        + urlencode({"pageNum": "4", "manage_no_sel": value})
    )


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    return current


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    return session.get(
        url,
        timeout=timeout,
        verify=True,
        allow_redirects=False,
        headers={"Accept": "text/html,application/xhtml+xml"},
    )


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _decode_response(response: Any) -> str:
    content = getattr(response, "content", None)
    headers = getattr(response, "headers", {}) or {}
    content_type = _clean(headers.get("content-type") or headers.get("Content-Type"))
    if content_type and "html" not in content_type.casefold():
        raise GunwiEducationContractError(
            f"unexpected response content type {content_type!r}"
        )
    if isinstance(content, bytes):
        if len(content) > _MAX_HTML_BYTES:
            raise GunwiEducationContractError("HTML response exceeds safety limit")
        lowered = content_type.casefold()
        encodings = (
            ("euc-kr", "cp949", "utf-8")
            if "euc-kr" in lowered or "euckr" in lowered
            else ("utf-8", "cp949")
        )
        for encoding in encodings:
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise GunwiEducationContractError("HTML response encoding is invalid")
    text = getattr(response, "text", None)
    if text is None:
        raise GunwiEducationContractError("HTML response has no body")
    rendered = str(text)
    if len(rendered.encode("utf-8")) > _MAX_HTML_BYTES:
        raise GunwiEducationContractError("HTML response exceeds safety limit")
    return rendered


def _fetch_soup(
    session: Any,
    url: str,
    timeout: int,
    *,
    fetcher: Fetcher,
    attempts: int,
    sleeper: Sleeper,
    audit: dict[str, int],
    label: str,
) -> BeautifulSoup:
    last_error: Optional[BaseException] = None
    for attempt in range(1, attempts + 1):
        audit["network_requests"] += 1
        try:
            response = fetcher(session, url, timeout)
            if isinstance(response, BeautifulSoup):
                return response
            try:
                status = int(getattr(response, "status_code", 0))
            except (TypeError, ValueError):
                status = 0
            if status >= 500 or status in {408, 425, 429}:
                raise _TransientFetchError(f"{label}: HTTP {status}")
            if status != 200:
                raise GunwiEducationContractError(f"{label}: unexpected HTTP {status}")
            if getattr(response, "history", None):
                raise GunwiEducationContractError(f"{label}: redirects are not accepted")
            final_url = _clean(getattr(response, "url", ""))
            if final_url and not _same_url(final_url, url):
                raise GunwiEducationContractError(
                    f"{label}: response URL left the exact requested source"
                )
            return BeautifulSoup(_decode_response(response), "html.parser")
        except (requests.RequestException, _TransientFetchError) as exc:
            last_error = exc
            if attempt >= attempts:
                break
            audit["network_retry_count"] += 1
            sleeper(min(0.2 * attempt, 0.5))
    raise GunwiEducationContractError(
        f"{label}: request failed after {attempts} attempts: {_clean(last_error)}"
    )


def _parse_date_range(
    value: Any,
    label: str,
    *,
    allow_unknown: bool = False,
) -> tuple[Optional[date], Optional[date]]:
    rendered = _clean(value)
    if allow_unknown and rendered == _UNKNOWN_DATE_RANGE:
        return None, None
    match = _DATE_RANGE_RE.fullmatch(rendered)
    if not match:
        raise GunwiEducationContractError(f"{label}: invalid date range {rendered!r}")
    try:
        start = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        end = date(int(match.group(4)), int(match.group(5)), int(match.group(6)))
    except ValueError as exc:
        raise GunwiEducationContractError(f"{label}: invalid calendar date") from exc
    if start > end:
        raise GunwiEducationContractError(f"{label}: reversed date range")
    return start, end


def _one_integer(value: Any, label: str) -> int:
    matches = _INTEGER_RE.findall(_clean(value))
    if len(matches) != 1:
        raise GunwiEducationContractError(f"{label}: expected one integer")
    return int(matches[0].replace(",", ""))


def _definition_pairs(container: Tag, label: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for item in container.select("dl"):
        key_node = item.find("dt", recursive=False)
        value_node = item.find("dd", recursive=False)
        if key_node is None or value_node is None:
            raise GunwiEducationContractError(f"{label}: malformed definition row")
        key = _compact(key_node.get_text(" ", strip=True))
        if not key or key in pairs:
            raise GunwiEducationContractError(f"{label}: duplicate or empty field")
        pairs[key] = _clean(value_node.get_text(" ", strip=True))
    return pairs


def _allowlisted_detail_pairs(containers: tuple[Tag, ...], label: str) -> dict[str, str]:
    """Read values only for public programme labels, never private/attachment rows."""

    allowed = {
        "교육명",
        "교육기간",
        "교육시간",
        "교육대상및인원",
        "교육장소",
        "교육신청방법",
        "과정소개",
    }
    seen: set[str] = set()
    pairs: dict[str, str] = {}
    for container in containers:
        for item in container.select("dl"):
            key_node = item.find("dt", recursive=False)
            if key_node is None:
                raise GunwiEducationContractError(f"{label}: malformed field label")
            key = _compact(key_node.get_text(" ", strip=True))
            if not key or key in seen:
                raise GunwiEducationContractError(f"{label}: duplicate or empty field")
            seen.add(key)
            if key not in allowed:
                # In particular, do not read attachment names or any future
                # non-allowlisted value into an intermediate Python object.
                continue
            value_node = item.find("dd", recursive=False)
            if value_node is None:
                raise GunwiEducationContractError(f"{label}: malformed public field")
            pairs[key] = _clean(value_node.get_text(" ", strip=True))
    return pairs


def _validate_list_shell(soup: BeautifulSoup, partition: str) -> Tag:
    config = _PARTITIONS[partition]
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != _LIST_TITLE:
        raise GunwiEducationContractError(
            f"partition {partition}: unexpected official page title"
        )
    page_text = _clean(soup.get_text(" ", strip=True))
    if _LIST_TITLE not in page_text or _OWNERSHIP_ADDRESS not in page_text:
        raise GunwiEducationContractError(
            f"partition {partition}: county ownership markers are missing"
        )
    selected = soup.select(".btn_mg.btn_sel > a")
    if len(selected) != 1 or _compact(selected[0].get_text(" ", strip=True)) != config["tab"]:
        raise GunwiEducationContractError(
            f"partition {partition}: exact selected status tab mismatch"
        )
    lesson = soup.select_one(".lesson > ul")
    if not isinstance(lesson, Tag):
        raise GunwiEducationContractError(
            f"partition {partition}: education ledger container is missing"
        )
    return lesson


def _parse_card(card: Tag, partition: str) -> _ListedCourse:
    config = _PARTITIONS[partition]
    title_link = card.select_one(".cont .tit > a[href]")
    if not isinstance(title_link, Tag):
        raise GunwiEducationContractError(
            f"partition {partition}: course detail control is missing"
        )
    detail_match = _DETAIL_CONTROL_RE.fullmatch(_clean(title_link.get("href")))
    if not detail_match:
        raise GunwiEducationContractError(
            f"partition {partition}: invalid course detail control"
        )
    identity, detail_button_flag = detail_match.groups()
    if not _IDENTITY_RE.fullmatch(identity):
        raise GunwiEducationContractError(
            f"partition {partition}: invalid public course identity"
        )
    if partition != "ing" and detail_button_flag.upper() != "N":
        raise GunwiEducationContractError(
            f"partition {partition}: non-open detail exposes an application button"
        )
    title = _clean(title_link.get_text(" ", strip=True))
    if not title:
        raise GunwiEducationContractError(f"{identity}: empty course title")

    field_container = card.select_one(".cont .sm_box")
    if not isinstance(field_container, Tag):
        raise GunwiEducationContractError(f"{identity}: list fields are missing")
    pairs = _definition_pairs(field_container, f"{identity} list")
    required = {"접수기간", "운영기간", "총교육시간", "모집인원"}
    if not required.issubset(pairs):
        raise GunwiEducationContractError(f"{identity}: required list fields are missing")
    unexpected = set(pairs) - (required | {"현재접수"})
    if unexpected:
        raise GunwiEducationContractError(
            f"{identity}: unexpected list fields {sorted(unexpected)!r}"
        )
    apply_start, apply_end = _parse_date_range(
        pairs["접수기간"], f"{identity} application period", allow_unknown=True
    )
    start, end = _parse_date_range(pairs["운영기간"], f"{identity} education period")
    if start is None or end is None:
        raise GunwiEducationContractError(f"{identity}: education period is unknown")
    capacity_total = _one_integer(pairs["모집인원"], f"{identity} capacity")
    capacity_current = (
        _one_integer(pairs["현재접수"], f"{identity} current applications")
        if "현재접수" in pairs
        else None
    )
    if capacity_current is not None and capacity_current > capacity_total:
        raise GunwiEducationContractError(
            f"{identity}: current applications exceed capacity"
        )

    action_link = card.select_one(".btn_box > a[href]")
    if not isinstance(action_link, Tag):
        raise GunwiEducationContractError(f"{identity}: status action is missing")
    action_match = _ACTION_CONTROL_RE.fullmatch(_clean(action_link.get("href")))
    if not action_match or action_match.group(1) != identity:
        raise GunwiEducationContractError(f"{identity}: status action identity mismatch")
    message = action_match.group(2)
    if message != config["message"]:
        raise GunwiEducationContractError(f"{identity}: status action partition mismatch")
    button_text = _compact(action_link.get_text(" ", strip=True))
    expected_button = config["tab"]
    if partition == "ing":
        valid_button = button_text in {"교육신청", "신청"}
    else:
        valid_button = button_text == expected_button
    if not valid_button:
        raise GunwiEducationContractError(f"{identity}: status button text mismatch")

    return _ListedCourse(
        identity=identity,
        title=title,
        partition=partition,
        apply_start=apply_start,
        apply_end=apply_end,
        start=start,
        end=end,
        education_hours=_clean(pairs["총교육시간"]),
        capacity_total=capacity_total,
        capacity_current=capacity_current,
        detail_button_flag=detail_button_flag.upper(),
        action_message=message,
    )


def _parse_partition(
    soup: BeautifulSoup,
    partition: str,
) -> tuple[list[_ListedCourse], bool]:
    lesson = _validate_list_shell(soup, partition)
    direct_children = [item for item in lesson.find_all("li", recursive=False)]
    empty_marker = lesson.select(".no_img > img[alt]")
    if direct_children:
        if empty_marker:
            raise GunwiEducationContractError(
                f"partition {partition}: rows and empty sentinel coexist"
            )
    else:
        expected = _PARTITIONS[partition]["empty"]
        if len(empty_marker) != 1 or _clean(empty_marker[0].get("alt")) != expected:
            raise GunwiEducationContractError(
                f"partition {partition}: explicit empty sentinel mismatch"
            )

    pagination_detected = bool(
        lesson.select(".pagination, .paging, .paginate, .page_num, .pageing")
        or any(
            _PAGINATION_HREF_RE.search(_clean(link.get("href")))
            for link in lesson.select("a[href]")
        )
    )
    rows = [_parse_card(card, partition) for card in direct_children]
    identities = [row.identity for row in rows]
    if len(identities) != len(set(identities)):
        raise GunwiEducationContractError(
            f"partition {partition}: duplicate public course identity"
        )
    return rows, pagination_detected


def _partition_signature(rows: list[_ListedCourse]) -> tuple[tuple[Any, ...], ...]:
    return tuple(row.signature for row in rows)


def _parse_detail(soup: BeautifulSoup, listed: _ListedCourse) -> dict[str, str]:
    page_title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if page_title != _DETAIL_TITLE:
        raise GunwiEducationContractError(
            f"{listed.identity}: unexpected official detail title"
        )
    if soup.select("form, input, select, textarea, .applicant, .applicants"):
        raise GunwiEducationContractError(
            f"{listed.identity}: detail crossed into a private/application surface"
        )
    heading = soup.select_one("body > .sub_tit")
    if not isinstance(heading, Tag) or _clean(heading.get_text(" ", strip=True)) != listed.title:
        raise GunwiEducationContractError(f"{listed.identity}: detail heading mismatch")
    primary = soup.select_one("body > .list2")
    secondary = soup.select_one("body > .list1")
    if not isinstance(primary, Tag) or not isinstance(secondary, Tag):
        raise GunwiEducationContractError(
            f"{listed.identity}: public detail field containers are missing"
        )
    pairs = _allowlisted_detail_pairs(
        (primary, secondary), f"{listed.identity} public detail"
    )
    required = {"교육명", "교육기간", "교육시간", "교육대상및인원", "교육장소"}
    if not required.issubset(pairs):
        raise GunwiEducationContractError(
            f"{listed.identity}: required public detail fields are missing"
        )
    if pairs["교육명"] != listed.title:
        raise GunwiEducationContractError(f"{listed.identity}: detail title mismatch")
    detail_start, detail_end = _parse_date_range(
        pairs["교육기간"], f"{listed.identity} detail education period"
    )
    if detail_start != listed.start or detail_end != listed.end:
        raise GunwiEducationContractError(f"{listed.identity}: detail period mismatch")
    if pairs["교육시간"] != listed.education_hours:
        raise GunwiEducationContractError(
            f"{listed.identity}: detail education hours mismatch"
        )
    return {
        "target": pairs.get("교육대상및인원", ""),
        "venue_name": pairs.get("교육장소", ""),
        "application_method": pairs.get("교육신청방법", ""),
        "description": pairs.get("과정소개", ""),
    }


def _row(listed: _ListedCourse, detail: Mapping[str, str]) -> dict[str, Any]:
    raw_url = gunwi_detail_url(listed.identity)
    application_available = listed.partition == "ing"
    application_url = (
        gunwi_application_url(listed.identity) if application_available else ""
    )
    apply_start = listed.apply_start.isoformat() if listed.apply_start else ""
    apply_end = listed.apply_end.isoformat() if listed.apply_end else ""
    capacity_current = listed.capacity_current
    return {
        "provider": GUNWI_EDUCATION_PROVIDER,
        "provider_course_id": (
            f"{GUNWI_EDUCATION_PROVIDER}:education:{listed.identity}"
        ),
        "prefer_incoming_provider_course_id": True,
        "title": listed.title,
        "branch": GUNWI_BRANCH,
        "branch_code": "GUNWI_AGRITECH",
        "preserve_branch": True,
        "provider_organizer": GUNWI_BRANCH,
        "category": "농업교육",
        "program_type": "강좌",
        "raw_url": raw_url,
        "application_url": application_url,
        "application_type": (
            "ONLINE_RESERVATION" if application_available else "INFO_ONLY"
        ),
        "reservation_available": application_available,
        "application_method_raw": _clean(detail.get("application_method")),
        "status": _PARTITIONS[listed.partition]["status"],
        "fee": "",
        "period": f"{listed.start.isoformat()} ~ {listed.end.isoformat()}",
        "start_date": listed.start.isoformat(),
        "end_date": listed.end.isoformat(),
        "apply_period": (
            f"{apply_start} ~ {apply_end}" if apply_start and apply_end else ""
        ),
        "apply_start": apply_start,
        "apply_end": apply_end,
        "schedule_raw": listed.education_hours,
        "target": _clean(detail.get("target")),
        "capacity": (
            f"{capacity_current}/{listed.capacity_total}"
            if capacity_current is not None
            else str(listed.capacity_total)
        ),
        "capacity_current": capacity_current,
        "capacity_total": listed.capacity_total,
        "venue_name": _clean(detail.get("venue_name")),
        "description": _clean(detail.get("description")) or listed.title,
        "collection_category": "평생학습",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": GUNWI_EDUCATION_PARSER,
        "municipality_code": GUNWI_MUNICIPALITY_CODE,
        "municipality_full_name": GUNWI_MUNICIPALITY_NAME,
        "raw_fields": {
            "course_identity": listed.identity,
            "list_partition": listed.partition,
            "source_status_label": _PARTITIONS[listed.partition]["tab"],
            "detail_verified": True,
            "application_control_present": application_available,
            "detail_button_flag": listed.detail_button_flag,
            "source_owner": GUNWI_BRANCH,
        },
    }


def _failed_meta(error: str = "") -> dict[str, Any]:
    return {
        "source_total": 0,
        "source_rows": 0,
        "partition_counts": {},
        "partition_empty_sentinels": 0,
        "pages": 0,
        "list_pages": 0,
        "required_list_requests": 0,
        "pagination_probe_requests": 0,
        "stability_rechecks": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "required_detail_requests": 0,
        "network_requests": 0,
        "network_retry_count": 0,
        "current_count": 0,
        "expired_count": 0,
        "returned_count": 0,
        "application_control_count": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "nonpagination_probes_complete": False,
        "stable_recheck_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "configured_collection_error": error,
        "errors": [error] if error else [],
        "no_current_data": False,
        "no_current_reason": "",
        "parser": GUNWI_EDUCATION_PARSER,
        "ownership_scope": GUNWI_EDUCATION_OWNERSHIP_SCOPE,
        "municipality_code": GUNWI_MUNICIPALITY_CODE,
        "municipality_full_name": GUNWI_MUNICIPALITY_NAME,
        "official_ownership_evidence_urls": list(
            GUNWI_EDUCATION_DISCOVERY_AUDIT["official_ownership_chain"]
        ),
        "application_pages_requested": 0,
        "identity_verification_pages_requested": 0,
        "applicant_pages_requested": 0,
        "pii_payload_persisted": False,
    }


def collect_gunwi_education(
    target: Any,
    timeout: int = 20,
    max_pages: int = 1,
    detail_limit: int = 50,
    *,
    today: Optional[date | datetime | str] = None,
    fetch_attempts: int = _DEFAULT_FETCH_ATTEMPTS,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    sleeper: Sleeper = time.sleep,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one atomic current/future snapshot from the county ledger.

    ``max_pages`` must be positive but the audited source is explicitly
    non-paginated.  ``detail_limit`` caps the complete public source, not the
    returned current subset, because all details are part of the census proof.
    """

    if not is_gunwi_education_target(target):
        return (
            [],
            GUNWI_EDUCATION_PARSER,
            _failed_meta("target does not match the exact canonical Gunwi owner"),
        )
    if timeout < 1 or max_pages < 1 or detail_limit < 0:
        return (
            [],
            GUNWI_EDUCATION_PARSER,
            _failed_meta("invalid timeout, max_pages, or detail_limit"),
        )
    if fetch_attempts < 1:
        return (
            [],
            GUNWI_EDUCATION_PARSER,
            _failed_meta("fetch_attempts must be positive"),
        )

    reference_day = _today(today)
    factory = session_factory or _default_session_factory
    page_fetcher = fetcher or _default_fetcher
    session = factory()
    audit = {"network_requests": 0, "network_retry_count": 0}
    partition_rows: dict[str, list[_ListedCourse]] = {}
    partition_counts: dict[str, int] = {}
    empty_sentinels = 0
    pagination_probe_requests = 0
    stability_rechecks = 0
    details_by_identity: dict[str, dict[str, str]] = {}

    try:
        for partition in _PARTITIONS:
            soup = _fetch_soup(
                session,
                gunwi_partition_url(partition),
                timeout,
                fetcher=page_fetcher,
                attempts=fetch_attempts,
                sleeper=sleeper,
                audit=audit,
                label=f"partition {partition}",
            )
            rows, pagination_detected = _parse_partition(soup, partition)
            if pagination_detected:
                raise GunwiEducationContractError(
                    f"partition {partition}: unexpected pagination controls appeared"
                )
            partition_rows[partition] = rows
            partition_counts[partition] = len(rows)
            if not rows:
                empty_sentinels += 1

        # The audited endpoint ignores page parameters because each partition
        # is already a finite complete list.  Requiring an identical page-2
        # probe fails closed if real pagination is introduced later.
        for partition, original in partition_rows.items():
            probe = _fetch_soup(
                session,
                gunwi_partition_url(partition, probe_page=2),
                timeout,
                fetcher=page_fetcher,
                attempts=fetch_attempts,
                sleeper=sleeper,
                audit=audit,
                label=f"partition {partition} non-pagination probe",
            )
            pagination_probe_requests += 1
            probe_rows, pagination_detected = _parse_partition(probe, partition)
            if pagination_detected or _partition_signature(probe_rows) != _partition_signature(original):
                raise GunwiEducationContractError(
                    f"partition {partition}: non-pagination probe changed the census"
                )

        for partition, original in partition_rows.items():
            recheck = _fetch_soup(
                session,
                gunwi_partition_url(partition),
                timeout,
                fetcher=page_fetcher,
                attempts=fetch_attempts,
                sleeper=sleeper,
                audit=audit,
                label=f"partition {partition} stable recheck",
            )
            stability_rechecks += 1
            recheck_rows, pagination_detected = _parse_partition(recheck, partition)
            if pagination_detected or _partition_signature(recheck_rows) != _partition_signature(original):
                raise GunwiEducationContractError(
                    f"partition {partition}: census changed during stable recheck"
                )

        source_rows = [
            row for partition in _PARTITIONS for row in partition_rows[partition]
        ]
        identities = [row.identity for row in source_rows]
        if len(identities) != len(set(identities)):
            raise GunwiEducationContractError(
                "duplicate official identity across status partitions"
            )
        if len(source_rows) > detail_limit:
            meta = {
                **_failed_meta(
                    f"source detail count {len(source_rows)} exceeds detail_limit {detail_limit}"
                ),
                "source_total": len(source_rows),
                "source_rows": len(source_rows),
                "partition_counts": partition_counts,
                "partition_empty_sentinels": empty_sentinels,
                "pages": len(_PARTITIONS),
                "list_pages": len(_PARTITIONS),
                "required_list_requests": audit["network_requests"],
                "pagination_probe_requests": pagination_probe_requests,
                "stability_rechecks": stability_rechecks,
                "network_requests": audit["network_requests"],
                "network_retry_count": audit["network_retry_count"],
                "pagination_complete": True,
                "nonpagination_probes_complete": True,
                "stable_recheck_complete": True,
                "source_cap_reached": True,
            }
            return [], GUNWI_EDUCATION_PARSER, meta

        for listed in source_rows:
            detail = _fetch_soup(
                session,
                gunwi_detail_url(listed.identity),
                timeout,
                fetcher=page_fetcher,
                attempts=fetch_attempts,
                sleeper=sleeper,
                audit=audit,
                label=f"detail {listed.identity}",
            )
            details_by_identity[listed.identity] = _parse_detail(detail, listed)
        if len(details_by_identity) != len(source_rows):
            raise GunwiEducationContractError(
                "not every public source row received one verified detail"
            )

        current = [row for row in source_rows if row.end >= reference_day]
        rows = [_row(row, details_by_identity[row.identity]) for row in current]
        if dedupe_rows is not None:
            rows = list(dedupe_rows(rows))
            if len(rows) != len(current):
                raise GunwiEducationContractError(
                    "shared row dedupe changed official current identity cardinality"
                )
        course_ids = [_clean(row.get("provider_course_id")) for row in rows]
        raw_urls = [_clean(row.get("raw_url")) for row in rows]
        if len(course_ids) != len(set(course_ids)) or len(raw_urls) != len(set(raw_urls)):
            raise GunwiEducationContractError(
                "generated course IDs or detail URLs are not unique"
            )

        status_counts = dict(Counter(_clean(row.get("status")) for row in rows))
        source_status_counts = {
            _PARTITIONS[key]["status"]: value
            for key, value in partition_counts.items()
            if value
        }
        no_current = not rows
        no_current_reason = ""
        if no_current:
            if not partition_counts.get("ing") and not partition_counts.get("ready"):
                no_current_reason = (
                    "official ing and ready partitions are empty and every ended "
                    "course expired before the reference day"
                )
            else:
                no_current_reason = (
                    "every course in the complete official status partitions "
                    "ended before the reference day"
                )
        meta = {
            **_failed_meta(),
            "source_total": len(source_rows),
            "source_rows": len(source_rows),
            "partition_counts": partition_counts,
            "source_status_counts": source_status_counts,
            "partition_empty_sentinels": empty_sentinels,
            "pages": len(_PARTITIONS),
            "list_pages": len(_PARTITIONS),
            "required_list_requests": 9,
            "pagination_probe_requests": pagination_probe_requests,
            "stability_rechecks": stability_rechecks,
            "detail_attempts": len(source_rows),
            "detail_pages": len(source_rows),
            "required_detail_requests": len(source_rows),
            "network_requests": audit["network_requests"],
            "network_retry_count": audit["network_retry_count"],
            "current_count": len(current),
            "expired_count": len(source_rows) - len(current),
            "returned_count": len(rows),
            "application_control_count": sum(
                1 for row in rows if row.get("reservation_available")
            ),
            "status_counts": status_counts,
            "branch_counts": {GUNWI_BRANCH: len(rows)} if rows else {},
            "pagination_detected": False,
            "pagination_complete": True,
            "nonpagination_probes_complete": True,
            "stable_recheck_complete": True,
            "details_complete": True,
            "snapshot_complete": True,
            "full_snapshot_validated": True,
            "configured_collection_error": "",
            "errors": [],
            "no_current_data": no_current,
            "no_current_reason": no_current_reason,
        }
        return rows, GUNWI_EDUCATION_PARSER, meta
    except Exception as exc:
        error = _clean(exc)
        source_count = sum(partition_counts.values())
        meta = {
            **_failed_meta(error),
            "source_total": source_count,
            "source_rows": source_count,
            "partition_counts": partition_counts,
            "partition_empty_sentinels": empty_sentinels,
            "pages": len(partition_counts),
            "list_pages": len(partition_counts),
            "required_list_requests": audit["network_requests"]
            - len(details_by_identity),
            "pagination_probe_requests": pagination_probe_requests,
            "stability_rechecks": stability_rechecks,
            "detail_attempts": len(details_by_identity),
            "detail_pages": len(details_by_identity),
            "network_requests": audit["network_requests"],
            "network_retry_count": audit["network_retry_count"],
        }
        return [], GUNWI_EDUCATION_PARSER, meta
    finally:
        _close_quietly(session)


collect = collect_gunwi_education
collect_daegu_gunwi_education = collect_gunwi_education
