"""Fail-closed collector for Yeongju's official indoor-playground calendar.

The official Yeongju integrated-reservation portal exposes the indoor
playground as two calendar partitions: group reservations (``reserve_uid=1``)
and individual reservations (``reserve_uid=2``).  A calendar cell is the
public record: date, time, aggregate capacity, and source status are present on
that page.  There is no separate public, identity-bearing programme detail.

Only HTTPS calendar GETs are allowlisted.  Authentication/application controls
embedded in the calendar are validated and may be returned as links, but they
are never requested.  Login, identity, applicant, member/day-detail,
attachment, download, and every other route fail closed.
"""

from __future__ import annotations

from calendar import monthrange
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag


YEONGJU_EXPERIENCE_PROVIDER = "MUNI_WWW_YEONGJU_GO_KR_773329F7"
YEONGJU_EXPERIENCE_CANDIDATE_ID = "MUNI_IR_8A649BCE7DD2"
YEONGJU_EXPERIENCE_HOST = "www.yeongju.go.kr"
YEONGJU_EXPERIENCE_PATH = "/open_content/yeyak/page.do"
YEONGJU_EXPERIENCE_MENU_ID = "11565"
YEONGJU_EXPERIENCE_CODE_ID = "48"
YEONGJU_EXPERIENCE_URL = (
    f"https://{YEONGJU_EXPERIENCE_HOST}{YEONGJU_EXPERIENCE_PATH}?"
    f"mnu_uid={YEONGJU_EXPERIENCE_MENU_ID}&code_uid={YEONGJU_EXPERIENCE_CODE_ID}"
)
YEONGJU_EXPERIENCE_MUNICIPALITY_CODE = "4721000000"
YEONGJU_EXPERIENCE_MUNICIPALITY_NAME = "경상북도 영주시"
YEONGJU_EXPERIENCE_BRANCH = "아이! 신나 실내놀이터"
YEONGJU_EXPERIENCE_ADDRESS = "경상북도 영주시 중앙로 7"
YEONGJU_EXPERIENCE_MAX_HTML_BYTES = 1_000_000
YEONGJU_EXPERIENCE_PARSER = (
    "yeongju_official_indoor_playground_two_calendar_partitions+"
    "current_through_direct_next_year+exact_empty_post_window_sentinels+"
    "stable_current_last_nonempty_and_sentinel_edges+calendar_cell_identity+"
    "aggregate_capacity+public_calendar_details+locked_experience+"
    "application_controls_observed_not_called+no_login_identity_applicant_"
    "member_day_attachment_download_or_pii_calls+atomic_snapshot"
)
YEONGJU_EXPERIENCE_OWNERSHIP_SCOPE = (
    "yeongju_integrated_reservation_indoor_playground_complete_calendar"
)

YEONGJU_EXPERIENCE_LIVE_AUDIT_BASELINE: Mapping[str, Any] = {
    "checked_at": "2026-08-05",
    "current_month": "2026-08",
    "last_nonempty_month": "2026-09",
    "post_window_sentinel_month": "2028-01",
    "current_count": 111,
    "open_count": 98,
    "closed_count": 13,
    "partition_counts": {"1": 62, "2": 49},
    "application_endpoint_requests": 0,
    "pii_endpoint_requests": 0,
}


@dataclass(frozen=True)
class YeongjuExperiencePartition:
    code: str
    label: str


YEONGJU_EXPERIENCE_PARTITIONS: tuple[YeongjuExperiencePartition, ...] = (
    YeongjuExperiencePartition("1", "단체예약"),
    YeongjuExperiencePartition("2", "개인예약"),
)
YEONGJU_EXPERIENCE_PARTITION_BY_CODE = {
    partition.code: partition for partition in YEONGJU_EXPERIENCE_PARTITIONS
}

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE = re.compile(r"\s+")
_YEAR = re.compile(r"20\d{2}")
_TIME = re.compile(r"(?:[01]?\d|2[0-3]):[0-5]\d")
_OPEN_TEXT = re.compile(
    r"((?:[01]?\d|2[0-3]):[0-5]\d)\s*\(([0-9,]+)/([0-9,]+)\)"
)
_CLOSED_TEXT = re.compile(r"((?:[01]?\d|2[0-3]):[0-5]\d)\s+마감")
_AUTH_CONTROL = re.compile(
    r"javascript:fnPopupAuth\('([^']+)'\)\s*;?",
    re.IGNORECASE,
)
_WEEKDAYS = ("일", "월", "화", "수", "목", "금", "토")
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


class YeongjuExperienceContractError(ValueError):
    """Raised whenever the audited public calendar contract changes."""


@dataclass(frozen=True)
class _CalendarSlot:
    partition: YeongjuExperiencePartition
    service_date: date
    service_time: str
    source_status: str
    capacity_current: Optional[int]
    capacity_total: Optional[int]
    application_url: str
    calendar_url: str

    @property
    def identity(self) -> str:
        return (
            f"{self.partition.code}:{self.service_date.isoformat()}:"
            f"{self.service_time}"
        )


@dataclass(frozen=True)
class _CalendarPage:
    year: int
    month: int
    partition: YeongjuExperiencePartition
    slots: tuple[_CalendarSlot, ...]
    non_slot_labels: tuple[str, ...]


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


def _parse_url(url: Any) -> tuple[Any, dict[str, str]]:
    parsed = urlparse(_clean(url))
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query = dict(pairs)
    if len(pairs) != len(query):
        raise YeongjuExperienceContractError("duplicate query key")
    if parsed.username or parsed.password or parsed.params or parsed.fragment:
        raise YeongjuExperienceContractError("unsafe URL authority or fragment")
    try:
        if parsed.port is not None:
            raise YeongjuExperienceContractError("explicit port is forbidden")
    except ValueError as exc:
        raise YeongjuExperienceContractError("invalid URL port") from exc
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


def _request_kind(method: str, url: str) -> str:
    parsed, query = _parse_url(url)
    if (
        method != "GET"
        or parsed.scheme != "https"
        or (parsed.hostname or "").lower() != YEONGJU_EXPERIENCE_HOST
        or parsed.path != YEONGJU_EXPERIENCE_PATH
    ):
        raise YeongjuExperienceContractError("HTTPS calendar request boundary changed")
    if query == {
        "mnu_uid": YEONGJU_EXPERIENCE_MENU_ID,
        "code_uid": YEONGJU_EXPERIENCE_CODE_ID,
    }:
        return "calendar"
    if set(query) != {
        "mnu_uid",
        "code_uid",
        "reserve_uid",
        "initYear",
        "initMonth",
        "initDay",
    }:
        raise YeongjuExperienceContractError(
            "application/login/identity/applicant/member/day/attachment/"
            "download/PII route refused"
        )
    if (
        query.get("mnu_uid") != YEONGJU_EXPERIENCE_MENU_ID
        or query.get("code_uid") != YEONGJU_EXPERIENCE_CODE_ID
        or query.get("reserve_uid")
        not in YEONGJU_EXPERIENCE_PARTITION_BY_CODE
        or query.get("initDay") != "1"
        or not _YEAR.fullmatch(query.get("initYear", ""))
    ):
        raise YeongjuExperienceContractError("calendar identity boundary changed")
    try:
        month = int(query.get("initMonth", ""))
    except ValueError as exc:
        raise YeongjuExperienceContractError("invalid calendar month") from exc
    if not 1 <= month <= 12 or query["initMonth"] != str(month):
        raise YeongjuExperienceContractError("invalid calendar month")
    return "calendar"


def is_yeongju_experience_target(target: Any) -> bool:
    try:
        return bool(
            _clean(_target_value(target, "provider"))
            == YEONGJU_EXPERIENCE_PROVIDER
            and _same_url(
                _clean(_target_value(target, "url")), YEONGJU_EXPERIENCE_URL
            )
        )
    except YeongjuExperienceContractError:
        return False


is_target = is_yeongju_experience_target


def _partition(value: YeongjuExperiencePartition | str) -> YeongjuExperiencePartition:
    if isinstance(value, YeongjuExperiencePartition):
        if value not in YEONGJU_EXPERIENCE_PARTITIONS:
            raise YeongjuExperienceContractError("unknown calendar partition")
        return value
    matched = YEONGJU_EXPERIENCE_PARTITION_BY_CODE.get(_clean(value))
    if matched is None:
        raise YeongjuExperienceContractError("unknown calendar partition")
    return matched


def yeongju_experience_calendar_url(
    year: int,
    month: int,
    partition: YeongjuExperiencePartition | str,
) -> str:
    current_partition = _partition(partition)
    if (
        not isinstance(year, int)
        or isinstance(year, bool)
        or not 2000 <= year <= 2099
        or not isinstance(month, int)
        or isinstance(month, bool)
        or not 1 <= month <= 12
    ):
        raise YeongjuExperienceContractError("invalid calendar period")
    return (
        f"https://{YEONGJU_EXPERIENCE_HOST}{YEONGJU_EXPERIENCE_PATH}?"
        + urlencode(
            (
                ("mnu_uid", YEONGJU_EXPERIENCE_MENU_ID),
                ("code_uid", YEONGJU_EXPERIENCE_CODE_ID),
                ("reserve_uid", current_partition.code),
                ("initYear", year),
                ("initMonth", month),
                ("initDay", 1),
            )
        )
    )


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
    return current.get(
        url,
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

    def soup(self, url: str) -> BeautifulSoup:
        kind = _request_kind("GET", url)
        self.meta["logical_requests"] += 1
        self.meta[f"{kind}_requests"] += 1
        response = self.fetcher(self.session, url, self.timeout)
        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            raise YeongjuExperienceContractError(f"HTTP {status}")
        if tuple(getattr(response, "history", ()) or ()):
            raise YeongjuExperienceContractError("redirect history is forbidden")
        headers = getattr(response, "headers", {}) or {}
        if any(
            str(key).lower() == "location" and value
            for key, value in headers.items()
        ):
            raise YeongjuExperienceContractError("redirect location is forbidden")
        final_url = _clean(getattr(response, "url", ""))
        if final_url and not _same_url(final_url, url):
            raise YeongjuExperienceContractError("response URL changed")
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
            raise YeongjuExperienceContractError("non-HTML calendar response")
        content = getattr(response, "content", None)
        if content is None:
            text = str(getattr(response, "text", ""))
            content = text.encode("utf-8")
        else:
            content = bytes(content)
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise YeongjuExperienceContractError(
                    "calendar is not strict UTF-8"
                ) from exc
        if not content or len(content) > YEONGJU_EXPERIENCE_MAX_HTML_BYTES:
            raise YeongjuExperienceContractError("calendar response size changed")
        if "Web firewall" in text or "웹 방화벽" in text:
            raise YeongjuExperienceContractError("web firewall response")
        soup = BeautifulSoup(text, "html.parser")
        if _clean(soup.title.get_text(" ", strip=True) if soup.title else "") != (
            "영주시 예약통합서비스"
        ):
            raise YeongjuExperienceContractError("official page title changed")
        return soup

    def close(self) -> None:
        close = getattr(self.session, "close", None)
        if callable(close):
            close()


def _control_query(href: str, base_url: str) -> tuple[Any, dict[str, str]]:
    return _parse_url(urljoin(base_url, href))


def _validate_partition_tabs(
    root: Tag,
    partition: YeongjuExperiencePartition,
    page_url: str,
) -> int:
    links = root.select(".reserve_tab li a[href]")
    if len(links) != len(YEONGJU_EXPERIENCE_PARTITIONS):
        raise YeongjuExperienceContractError("calendar partition registry changed")
    observed: dict[str, str] = {}
    active: list[str] = []
    expected_keys = {
        "cmd",
        "apply_date",
        "code_uid",
        "apply_time",
        "listType",
        "mnu_uid",
        "reserve_uid",
    }
    for link in links:
        label = _clean(link.get_text(" ", strip=True))
        parsed, query = _control_query(_clean(link.get("href")), page_url)
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower() != YEONGJU_EXPERIENCE_HOST
            or parsed.path != YEONGJU_EXPERIENCE_PATH
            or set(query) != expected_keys
            or query.get("cmd") != "1"
            or query.get("mnu_uid") != YEONGJU_EXPERIENCE_MENU_ID
            or query.get("code_uid") != YEONGJU_EXPERIENCE_CODE_ID
            or any(query.get(key) for key in ("apply_date", "apply_time", "listType"))
        ):
            raise YeongjuExperienceContractError("calendar partition control changed")
        code = query.get("reserve_uid", "")
        expected = YEONGJU_EXPERIENCE_PARTITION_BY_CODE.get(code)
        if expected is None or expected.label != label or code in observed:
            raise YeongjuExperienceContractError("calendar partition identity changed")
        observed[code] = label
        parent = link.find_parent("li")
        if parent is not None and "on" in (parent.get("class") or []):
            active.append(code)
    if observed != {
        item.code: item.label for item in YEONGJU_EXPERIENCE_PARTITIONS
    } or active != [partition.code]:
        raise YeongjuExperienceContractError("active calendar partition changed")
    return len(links)


def _calendar_nav_query(
    href: str,
    page_url: str,
    partition: YeongjuExperiencePartition,
) -> dict[str, str]:
    parsed, query = _control_query(href, page_url)
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != YEONGJU_EXPERIENCE_HOST
        or parsed.path != YEONGJU_EXPERIENCE_PATH
        or query.get("mnu_uid") != YEONGJU_EXPERIENCE_MENU_ID
        or query.get("code_uid") != YEONGJU_EXPERIENCE_CODE_ID
        or query.get("reserve_uid") != partition.code
        or any(query.get(key) for key in ("apply_date", "apply_time", "listType"))
    ):
        raise YeongjuExperienceContractError("calendar navigation escaped scope")
    return query


def _validate_calendar_navigation(
    root: Tag,
    year: int,
    month: int,
    partition: YeongjuExperiencePartition,
    page_url: str,
) -> int:
    month_tab = root.select_one(".reserList .monthTab")
    if month_tab is None:
        raise YeongjuExperienceContractError("calendar navigation missing")
    year_box = month_tab.find("div", recursive=False)
    if year_box is None:
        raise YeongjuExperienceContractError("calendar year registry missing")
    previous = year_box.select_one("a.prev[href]")
    selected = year_box.find("p", recursive=False)
    following = year_box.select_one("a.next[href]")
    if previous is None or selected is None or following is None:
        raise YeongjuExperienceContractError("calendar year boundary changed")
    if (
        _clean(previous.get_text(" ", strip=True)) != f"{year - 1}년"
        or _clean(selected.get_text(" ", strip=True)) != f"{year}년"
        or _clean(following.get_text(" ", strip=True)) != f"{year + 1}년"
    ):
        raise YeongjuExperienceContractError("calendar year labels changed")
    for link, expected_year in ((previous, year - 1), (following, year + 1)):
        query = _calendar_nav_query(
            _clean(link.get("href")), page_url, partition
        )
        if (
            set(query)
            != {
                "apply_date",
                "code_uid",
                "apply_time",
                "listType",
                "mnu_uid",
                "reserve_uid",
                "initYear",
                "initMonth",
            }
            or query.get("initYear") != str(expected_year)
            or query.get("initMonth") != "1"
        ):
            raise YeongjuExperienceContractError("calendar year control changed")

    month_links = month_tab.select("ul > li > a[href]")
    if len(month_links) != 12:
        raise YeongjuExperienceContractError("calendar month registry changed")
    observed_months: list[int] = []
    active_months: list[int] = []
    for index, link in enumerate(month_links, start=1):
        if _clean(link.get_text(" ", strip=True)) != f"{index}월":
            raise YeongjuExperienceContractError("calendar month label changed")
        query = _calendar_nav_query(_clean(link.get("href")), page_url, partition)
        if (
            set(query)
            != {
                "apply_date",
                "code_uid",
                "apply_time",
                "listType",
                "mnu_uid",
                "reserve_uid",
                "initYear",
                "initMonth",
                "initDay",
            }
            or query.get("initYear") != str(year)
            or query.get("initMonth") != str(index)
            or query.get("initDay") != "1"
        ):
            raise YeongjuExperienceContractError("calendar month control changed")
        observed_months.append(index)
        parent = link.find_parent("li")
        if parent is not None and "on" in (parent.get("class") or []):
            active_months.append(index)
    if observed_months != list(range(1, 13)) or active_months != [month]:
        raise YeongjuExperienceContractError("active calendar month changed")
    return len(month_links) + 2


def _application_control(
    href: str,
    text: str,
    partition: YeongjuExperiencePartition,
    service_date: date,
) -> tuple[str, int, int, str]:
    wrapper = _AUTH_CONTROL.fullmatch(href)
    text_match = _OPEN_TEXT.fullmatch(text)
    if wrapper is None or text_match is None:
        raise YeongjuExperienceContractError("application control shape changed")
    service_time = text_match.group(1)
    current_count = int(text_match.group(2).replace(",", ""))
    total_count = int(text_match.group(3).replace(",", ""))
    absolute = urljoin(YEONGJU_EXPERIENCE_URL, wrapper.group(1))
    parsed, query = _parse_url(absolute)
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != YEONGJU_EXPERIENCE_HOST
        or parsed.path != YEONGJU_EXPERIENCE_PATH
        or set(query)
        != {
            "mnu_uid",
            "code_uid",
            "reserve_uid",
            "cmd",
            "apply_date",
            "apply_time",
        }
        or query.get("mnu_uid") != YEONGJU_EXPERIENCE_MENU_ID
        or query.get("code_uid") != YEONGJU_EXPERIENCE_CODE_ID
        or query.get("reserve_uid") != partition.code
        or query.get("cmd") != "4"
        or query.get("apply_date") != service_date.isoformat()
        or query.get("apply_time") != service_time
        or total_count < 1
        or not 0 <= current_count <= total_count
    ):
        raise YeongjuExperienceContractError("application control identity changed")
    return service_time, current_count, total_count, absolute


def _parse_calendar_page(
    soup: BeautifulSoup,
    *,
    year: int,
    month: int,
    partition: YeongjuExperiencePartition,
    page_url: str,
) -> tuple[_CalendarPage, int, int]:
    article = soup.select_one("main#container article.article")
    if article is None:
        raise YeongjuExperienceContractError("official calendar article changed")
    heading = article.select_one("h3")
    if _clean(heading.get_text(" ", strip=True) if heading else "") != "실내놀이터":
        raise YeongjuExperienceContractError("indoor-playground heading changed")
    partition_controls = _validate_partition_tabs(article, partition, page_url)
    navigation_controls = _validate_calendar_navigation(
        article, year, month, partition, page_url
    )
    table = article.select_one(".reserList .cal table")
    if table is None:
        raise YeongjuExperienceContractError("calendar table missing")
    caption = _clean(
        table.caption.get_text(" ", strip=True) if table.caption else ""
    )
    expected_caption = (
        f"{month:02d}월 실내놀이터 달력 - 일, 월, 화, 수, 목, 금, 토 "
        "순으로 나타낸 표입니다."
    )
    if caption != expected_caption:
        raise YeongjuExperienceContractError("calendar caption changed")
    headers = tuple(
        _clean(header.get_text(" ", strip=True))
        for header in table.select("thead th")
    )
    if headers != _WEEKDAYS:
        raise YeongjuExperienceContractError("calendar weekday registry changed")

    slots: list[_CalendarSlot] = []
    day_numbers: list[int] = []
    non_slot_labels: list[str] = []
    _, final_day = monthrange(year, month)
    for cell in table.select("tbody td"):
        day_node = cell.select_one("span.date")
        if day_node is None:
            if _clean(cell.get_text(" ", strip=True)) or cell.select("a[href]"):
                raise YeongjuExperienceContractError("calendar trailing cell changed")
            continue
        day_text = _clean(day_node.get_text(" ", strip=True))
        if "other_month" in (day_node.get("class") or []):
            if day_text or cell.select("a[href]"):
                raise YeongjuExperienceContractError("adjacent-month cell changed")
            continue
        if not day_text.isdigit():
            raise YeongjuExperienceContractError("calendar day changed")
        day_number = int(day_text)
        if not 1 <= day_number <= final_day:
            raise YeongjuExperienceContractError("calendar day escaped month")
        day_numbers.append(day_number)
        service_date = date(year, month, day_number)
        for link in cell.select("a[href]"):
            href = _clean(link.get("href"))
            text = _clean(link.get_text(" ", strip=True))
            if _AUTH_CONTROL.fullmatch(href):
                service_time, current_count, total_count, application_url = (
                    _application_control(
                        href, text, partition, service_date
                    )
                )
                slots.append(
                    _CalendarSlot(
                        partition=partition,
                        service_date=service_date,
                        service_time=service_time,
                        source_status="예약가능",
                        capacity_current=current_count,
                        capacity_total=total_count,
                        application_url=application_url,
                        calendar_url=page_url,
                    )
                )
                continue
            if href != "#self":
                raise YeongjuExperienceContractError(
                    "calendar link escaped the audited controls"
                )
            closed_match = _CLOSED_TEXT.fullmatch(text)
            if closed_match is not None:
                slots.append(
                    _CalendarSlot(
                        partition=partition,
                        service_date=service_date,
                        service_time=closed_match.group(1),
                        source_status="마감",
                        capacity_current=None,
                        capacity_total=None,
                        application_url="",
                        calendar_url=page_url,
                    )
                )
            elif _TIME.search(text):
                raise YeongjuExperienceContractError(
                    "unknown time-bearing calendar status"
                )
            elif text:
                non_slot_labels.append(text)
    if day_numbers != list(range(1, final_day + 1)):
        raise YeongjuExperienceContractError("calendar day coverage incomplete")
    identities = [slot.identity for slot in slots]
    if len(identities) != len(set(identities)):
        raise YeongjuExperienceContractError("duplicate calendar slot identity")
    return (
        _CalendarPage(
            year=year,
            month=month,
            partition=partition,
            slots=tuple(slots),
            non_slot_labels=tuple(sorted(non_slot_labels)),
        ),
        partition_controls,
        navigation_controls,
    )


def _page_signature(page: _CalendarPage) -> tuple[Any, ...]:
    return (
        page.year,
        page.month,
        page.partition.code,
        tuple(
            (
                slot.identity,
                slot.source_status,
                slot.capacity_current,
                slot.capacity_total,
                slot.application_url,
            )
            for slot in page.slots
        ),
        page.non_slot_labels,
    )


def _month_sequence(start: date, final_year: int) -> list[tuple[int, int]]:
    year, month = start.year, start.month
    result: list[tuple[int, int]] = []
    while (year, month) <= (final_year, 12):
        result.append((year, month))
        month += 1
        if month == 13:
            year += 1
            month = 1
    return result


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _identity_hash(values: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(sorted(values)).encode("utf-8")).hexdigest()


def _slot_row(slot: _CalendarSlot) -> dict[str, Any]:
    normalized_status = "OPEN" if slot.source_status == "예약가능" else "CLOSED"
    identity = slot.identity
    capacity = (
        f"{slot.capacity_current}/{slot.capacity_total}"
        if slot.capacity_current is not None and slot.capacity_total is not None
        else ""
    )
    return {
        "provider": YEONGJU_EXPERIENCE_PROVIDER,
        "municipality_code": YEONGJU_EXPERIENCE_MUNICIPALITY_CODE,
        "municipality_name": YEONGJU_EXPERIENCE_MUNICIPALITY_NAME,
        "provider_course_id": (
            f"{YEONGJU_EXPERIENCE_PROVIDER}:indoor-playground:{identity}"
        ),
        "source_course_id": f"indoor-playground:{identity}",
        "title": (
            f"{YEONGJU_EXPERIENCE_BRANCH} {slot.partition.label} "
            f"({slot.service_date.isoformat()} {slot.service_time})"
        ),
        "branch": YEONGJU_EXPERIENCE_BRANCH,
        "branch_code": YEONGJU_EXPERIENCE_CODE_ID,
        "branch_url": YEONGJU_EXPERIENCE_URL,
        "preserve_branch": True,
        "category": "영주시 예약통합서비스/체험·견학/실내놀이터",
        "collection_category": "공공예약",
        "domain_category": "체험·견학",
        "source_group": "municipal_reservation",
        "service_group": "체험",
        "service_group_policy": "locked",
        "classification_locked": True,
        "operator_type": "지자체/공공기관",
        "program_type": "체험",
        "source_status": slot.source_status,
        "status": normalized_status,
        "reservation_available": bool(
            normalized_status == "OPEN" and slot.application_url
        ),
        "period": f"{slot.service_date.isoformat()} {slot.service_time}",
        "start_date": slot.service_date,
        "end_date": slot.service_date,
        "schedule_raw": (
            f"{slot.service_date.isoformat()} {slot.service_time} / "
            f"{slot.partition.label}"
        ),
        "venue_name": YEONGJU_EXPERIENCE_BRANCH,
        "address": YEONGJU_EXPERIENCE_ADDRESS,
        "capacity": capacity,
        "capacity_current": slot.capacity_current,
        "capacity_total": slot.capacity_total,
        "application_url": slot.application_url,
        "raw_url": slot.calendar_url,
        "raw_fields": {
            "parser": YEONGJU_EXPERIENCE_PARSER,
            "official_slot_identity": identity,
            "official_reservation_partition": slot.partition.code,
            "official_reservation_partition_label": slot.partition.label,
            "official_source_status": slot.source_status,
            "official_calendar_month": slot.service_date.strftime("%Y-%m"),
            "public_calendar_detail": True,
            "separate_public_detail_endpoint": False,
            "application_control_observed_not_called": bool(slot.application_url),
        },
    }


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
        "partitions_complete": False,
        "details_complete": False,
        "logical_requests": 0,
        "calendar_requests": 0,
        "list_requests": 0,
        "detail_requests": 0,
        "application_endpoint_requests": 0,
        "login_endpoint_requests": 0,
        "identity_endpoint_requests": 0,
        "applicant_endpoint_requests": 0,
        "member_endpoint_requests": 0,
        "day_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "download_endpoint_requests": 0,
        "pii_endpoint_requests": 0,
    }


def collect_yeongju_experience(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 24,
    detail_limit: int = 300,
    today: Optional[date | datetime | str] = None,
    session_factory: SessionFactory = _default_session,
    dedupe_rows: DedupeRows = _dedupe_default,
    fetcher: Fetcher = _default_fetcher,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one atomic current/future snapshot of indoor-playground slots."""

    meta = _meta()
    if not is_yeongju_experience_target(target):
        meta["errors"] = ["target does not match the canonical indoor calendar"]
        meta["error_kind"] = "contract"
        return [], YEONGJU_EXPERIENCE_PARSER, meta
    if timeout < 1 or max_pages < 1 or detail_limit < 0:
        meta["errors"] = ["invalid collection limits"]
        meta["error_kind"] = "contract"
        return [], YEONGJU_EXPERIENCE_PARSER, meta

    cutoff = _today(today)
    months = _month_sequence(cutoff.replace(day=1), cutoff.year + 1)
    if len(months) > max_pages:
        meta["errors"] = [
            "max_pages truncates the current-through-next-year calendar window"
        ]
        meta["error_kind"] = "contract"
        return [], YEONGJU_EXPERIENCE_PARSER, meta
    sentinel_year, sentinel_month = _next_month(*months[-1])
    requester = _Requester(session_factory, fetcher, timeout, meta)
    try:
        pages: dict[tuple[str, int, int], _CalendarPage] = {}
        partition_control_count = 0
        navigation_control_count = 0
        for partition in YEONGJU_EXPERIENCE_PARTITIONS:
            for year, month in months:
                url = yeongju_experience_calendar_url(year, month, partition)
                page, partition_controls, navigation_controls = (
                    _parse_calendar_page(
                        requester.soup(url),
                        year=year,
                        month=month,
                        partition=partition,
                        page_url=url,
                    )
                )
                pages[(partition.code, year, month)] = page
                partition_control_count += partition_controls
                navigation_control_count += navigation_controls

        sentinel_pages: dict[str, _CalendarPage] = {}
        for partition in YEONGJU_EXPERIENCE_PARTITIONS:
            url = yeongju_experience_calendar_url(
                sentinel_year, sentinel_month, partition
            )
            page, partition_controls, navigation_controls = _parse_calendar_page(
                requester.soup(url),
                year=sentinel_year,
                month=sentinel_month,
                partition=partition,
                page_url=url,
            )
            if page.slots:
                raise YeongjuExperienceContractError(
                    "post-window calendar sentinel is not exact empty"
                )
            sentinel_pages[partition.code] = page
            partition_control_count += partition_controls
            navigation_control_count += navigation_controls

        # Recheck the current edge, each partition's final non-empty page, and
        # the exact empty post-window sentinel before publishing any rows.
        stability_keys: set[tuple[str, int, int]] = set()
        first_year, first_month = months[0]
        for partition in YEONGJU_EXPERIENCE_PARTITIONS:
            stability_keys.add((partition.code, first_year, first_month))
            nonempty = [
                (year, month)
                for year, month in months
                if pages[(partition.code, year, month)].slots
            ]
            if nonempty:
                stability_keys.add((partition.code, *nonempty[-1]))
        for code, year, month in sorted(stability_keys):
            partition = YEONGJU_EXPERIENCE_PARTITION_BY_CODE[code]
            url = yeongju_experience_calendar_url(year, month, partition)
            rechecked, _, _ = _parse_calendar_page(
                requester.soup(url),
                year=year,
                month=month,
                partition=partition,
                page_url=url,
            )
            if _page_signature(rechecked) != _page_signature(
                pages[(code, year, month)]
            ):
                raise YeongjuExperienceContractError(
                    "calendar current/final boundary changed during crawl"
                )
        for partition in YEONGJU_EXPERIENCE_PARTITIONS:
            url = yeongju_experience_calendar_url(
                sentinel_year, sentinel_month, partition
            )
            rechecked, _, _ = _parse_calendar_page(
                requester.soup(url),
                year=sentinel_year,
                month=sentinel_month,
                partition=partition,
                page_url=url,
            )
            if _page_signature(rechecked) != _page_signature(
                sentinel_pages[partition.code]
            ):
                raise YeongjuExperienceContractError(
                    "empty post-window sentinel changed during crawl"
                )

        all_slots = [
            slot
            for partition in YEONGJU_EXPERIENCE_PARTITIONS
            for year, month in months
            for slot in pages[(partition.code, year, month)].slots
        ]
        current_slots = [
            slot for slot in all_slots if slot.service_date >= cutoff
        ]
        identities = [slot.identity for slot in current_slots]
        if len(identities) != len(set(identities)):
            raise YeongjuExperienceContractError(
                "calendar partitions overlap or duplicate identities"
            )
        if len(current_slots) > detail_limit:
            raise YeongjuExperienceContractError(
                "detail_limit truncates the current/future slot ledger"
            )
        ordered_slots = sorted(
            current_slots,
            key=lambda slot: (
                slot.service_date,
                slot.service_time,
                slot.partition.code,
            ),
        )
        output = [_slot_row(slot) for slot in ordered_slots]
        privacy = [error for row in output for error in _privacy_errors(row)]
        if privacy:
            raise YeongjuExperienceContractError(
                f"PII/output allowlist violation: {privacy[0]}"
            )
        deduped = list(dedupe_rows(output))
        if len(deduped) != len(output):
            raise YeongjuExperienceContractError("dedupe changed complete output")

        status_counts = Counter(row["status"] for row in deduped)
        source_status_counts = Counter(slot.source_status for slot in current_slots)
        partition_counts = Counter(
            slot.partition.code for slot in current_slots
        )
        month_counts = Counter(
            slot.service_date.strftime("%Y-%m") for slot in current_slots
        )
        partition_month_counts = {
            partition.code: {
                f"{year:04d}-{month:02d}": len(
                    pages[(partition.code, year, month)].slots
                )
                for year, month in months
            }
            for partition in YEONGJU_EXPERIENCE_PARTITIONS
        }
        last_nonempty = {
            partition.code: next(
                (
                    f"{year:04d}-{month:02d}"
                    for year, month in reversed(months)
                    if pages[(partition.code, year, month)].slots
                ),
                "",
            )
            for partition in YEONGJU_EXPERIENCE_PARTITIONS
        }
        application_controls = sum(bool(slot.application_url) for slot in all_slots)
        meta.update(
            {
                "cutoff": cutoff.isoformat(),
                "calendar_window_start": f"{first_year:04d}-{first_month:02d}",
                "calendar_window_end": f"{months[-1][0]:04d}-{months[-1][1]:02d}",
                "post_window_sentinel_month": (
                    f"{sentinel_year:04d}-{sentinel_month:02d}"
                ),
                "partition_count": len(YEONGJU_EXPERIENCE_PARTITIONS),
                "partition_codes": [
                    partition.code for partition in YEONGJU_EXPERIENCE_PARTITIONS
                ],
                "calendar_months_per_partition": len(months),
                "calendar_partition_pages": len(months)
                * len(YEONGJU_EXPERIENCE_PARTITIONS),
                "empty_sentinel_pages": len(sentinel_pages),
                "stable_boundary_pages": len(stability_keys)
                + len(sentinel_pages),
                "partition_control_count": partition_control_count,
                "navigation_control_count": navigation_control_count,
                "application_controls_observed_not_called": application_controls,
                "source_slot_count": len(all_slots),
                "expired_slot_count": len(all_slots) - len(current_slots),
                "current_count": len(current_slots),
                "returned_count": len(deduped),
                "source_status_counts": dict(sorted(source_status_counts.items())),
                "status_counts": dict(sorted(status_counts.items())),
                "partition_counts": {
                    partition.code: partition_counts.get(partition.code, 0)
                    for partition in YEONGJU_EXPERIENCE_PARTITIONS
                },
                "month_counts": dict(sorted(month_counts.items())),
                "partition_month_counts": partition_month_counts,
                "last_nonempty_month": last_nonempty,
                "empty_month_counts": {
                    partition.code: sum(
                        not pages[(partition.code, year, month)].slots
                        for year, month in months
                    )
                    for partition in YEONGJU_EXPERIENCE_PARTITIONS
                },
                "source_identity_sha256": _identity_hash(
                    slot.identity for slot in all_slots
                ),
                "current_identity_sha256": _identity_hash(identities),
                "duplicate_count": 0,
                "no_current_data": not deduped,
                "calendar_has_no_pagination": True,
                "separate_public_detail_endpoint": False,
                "safe_public_calendar_details_complete": True,
                "pagination_complete": True,
                "partitions_complete": True,
                "details_complete": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
            }
        )
        return deduped, YEONGJU_EXPERIENCE_PARSER, meta
    except Exception as exc:
        meta["errors"] = [f"{type(exc).__name__}: {exc}"]
        meta["error_kind"] = (
            "contract"
            if isinstance(exc, YeongjuExperienceContractError)
            else "network_or_parse"
        )
        return [], YEONGJU_EXPERIENCE_PARSER, meta
    finally:
        requester.close()


collect = collect_yeongju_experience


__all__ = [
    name for name in globals() if name.startswith("YEONGJU_EXPERIENCE_")
] + [
    "YeongjuExperienceContractError",
    "YeongjuExperiencePartition",
    "collect",
    "collect_yeongju_experience",
    "is_target",
    "is_yeongju_experience_target",
    "yeongju_experience_calendar_url",
]
