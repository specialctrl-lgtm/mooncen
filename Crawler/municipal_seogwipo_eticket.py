"""Fail-closed education collector for Seogwipo City's E-Ticket portal.

The public ``/edu`` page is a current/future, single-page mixture of events,
viewing products, experiences, and education.  It does not publish a numeric
total.  Each card does, however, expose a stable Smartix goods identifier and
the linked ticket service exposes, for every goods identifier:

* an actual reservation page;
* authoritative goods and organisation detail APIs; and
* the complete current sale-goods list for that organisation.

This collector reads every card, validates every linked ticket page and both
detail APIs, and reconciles the union of all organisation sale-goods lists
with the E-Ticket card IDs.  Only titles with an explicit education/lecture
marker are returned.  In particular, the live ``자유관람`` products are not
silently classified as education merely because their descriptions mention
the word ``교육``.

The module is intentionally isolated from ``Crawler_MunicipalYaml`` so the
shared router can inject its managed, SSRF-safe session without a circular
import.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
import hashlib
import html
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


SEOGWIPO_ETICKET_PROVIDER = "MUNI_ETICKET_SEOGWIPO_GO_KR_C87B50AB"
SEOGWIPO_ETICKET_TARGET_URL = "https://eticket.seogwipo.go.kr/"
SEOGWIPO_ETICKET_LIST_URL = "https://eticket.seogwipo.go.kr/edu?bmcode=edu"
SEOGWIPO_ETICKET_HOST = "eticket.seogwipo.go.kr"
SEOGWIPO_TICKET_HOST = "ticket.seogwipo.go.kr"
SEOGWIPO_ETICKET_LIST_PATH = "/edu"
SEOGWIPO_TICKET_PATH_PREFIX = "/ticket/"
SEOGWIPO_GOODS_INFO_URL = (
    "https://ticket.seogwipo.go.kr/ticket/goodsInfo"
)
SEOGWIPO_GOODS_INFORMATION_URL = (
    "https://ticket.seogwipo.go.kr/ticket/goodsInformation"
)
SEOGWIPO_HEADER_INFO_URL = (
    "https://ticket.seogwipo.go.kr/ticket/headerInfo"
)
SEOGWIPO_ETICKET_PARSER = (
    "seogwipo_eticket_current_education+ticket_goods_detail+header_union"
)
SEOGWIPO_MUNICIPALITY_CODE = "5013000000"
SEOGWIPO_MUNICIPALITY_NAME = "제주특별자치도 서귀포시"

SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_GOODS_ID_RE = re.compile(r"GD\d{7,12}")
_DATE_RANGE_RE = re.compile(
    r"\s*(20\d{2}-\d{2}-\d{2})\s*~\s*(20\d{2}-\d{2}-\d{2})\s*"
)
_TIME_RANGE_RE = re.compile(r"\s*(\d{2}:\d{2})\s*~\s*(\d{2}:\d{2})\s*")
_DATETIME_RE = re.compile(r"20\d{10}")
_APPLICATION_CONTROL_RE = re.compile(
    r"\s*javascript\s*:\s*window\.open\(\s*"
    r"(?P<quote>['\"])(?P<url>https://ticket\.seogwipo\.go\.kr/ticket/"
    r"(?P<identity>GD\d{7,12}))(?P=quote)\s*,\s*"
    r"['\"]_blank['\"](?:\s*,\s*['\"][^'\"]*['\"])?\s*\)\s*;?\s*"
)
_EXPLICIT_EDUCATION_RE = re.compile(
    r"(?:교육|강좌|강의|수업|교실|아카데미|배움|학습)"
)
_EXPLICIT_NON_EDUCATION_RE = re.compile(
    r"(?:자유관람|일반관람|공연|전시|축제|캠핑|야영|숙박|주차|대관)"
)
_NOTICE_BOARD_RE = re.compile(
    r"(?:공지(?:사항)?|게시판|새소식|알림마당|보도자료)"
)
_REQUIRED_LIST_FIELDS = frozenset(
    {"행사기간", "행사시간", "접수기간", "행사장소"}
)
_EMPTY_MARKERS = (
    "신청 가능한 행사·교육이 없습니다",
    "등록된 행사·교육이 없습니다",
    "신청 가능한 행사가 없습니다",
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


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


def is_seogwipo_eticket_target(target: Any) -> bool:
    parsed = urlparse(_target_url(target))
    return (
        _provider(target) == SEOGWIPO_ETICKET_PROVIDER
        and parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == SEOGWIPO_ETICKET_HOST
        and parsed.port is None
        and parsed.path == "/"
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


is_target = is_seogwipo_eticket_target


def seogwipo_ticket_url(identity: Any) -> str:
    goods_id = _clean(identity)
    if not _GOODS_ID_RE.fullmatch(goods_id):
        return ""
    return f"https://{SEOGWIPO_TICKET_HOST}{SEOGWIPO_TICKET_PATH_PREFIX}{goods_id}"


def is_explicit_education_title(value: Any) -> bool:
    """Return true only for an unambiguous education/lecture title.

    An explicit education marker wins over generic venue wording such as
    ``체험관``.  Pure viewing/performance/facility products are excluded.
    """

    title = _clean(value)
    if (
        not title
        or _NOTICE_BOARD_RE.search(title)
        or not _EXPLICIT_EDUCATION_RE.search(title)
    ):
        return False
    if _EXPLICIT_NON_EDUCATION_RE.search(title) and not re.search(
        r"(?:교육|강좌|강의|수업|교실|아카데미|배움|학습)", title
    ):
        return False
    return True


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/125.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
        }
    )
    return current


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _validate_response(value: Any, *, expected_kind: str) -> None:
    try:
        status = int(getattr(value, "status_code", 200))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{expected_kind} HTTP status is invalid") from exc
    if 300 <= status < 400:
        raise ValueError(f"{expected_kind} redirects are not accepted")
    raise_for_status = getattr(value, "raise_for_status", None)
    if callable(raise_for_status):
        raise_for_status()
    elif status >= 400:
        raise ValueError(f"{expected_kind} HTTP status {status}")


def _coerce_soup(value: Any, *, expected_kind: str) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        if not value:
            raise ValueError(f"{expected_kind} response was empty")
        return BeautifulSoup(value, "lxml")
    _validate_response(value, expected_kind=expected_kind)
    content = getattr(value, "content", None)
    if content is None:
        content = getattr(value, "text", None)
    if not content:
        raise ValueError(f"{expected_kind} response was empty")
    return BeautifulSoup(content, "lxml")


def _coerce_json(value: Any, *, expected_kind: str) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        payload: Any = value
    else:
        _validate_response(value, expected_kind=expected_kind)
        json_method = getattr(value, "json", None)
        if not callable(json_method):
            raise TypeError(f"{expected_kind} response did not expose JSON")
        payload = json_method()
    if not isinstance(payload, Mapping) or not payload:
        raise ValueError(f"{expected_kind} payload must be a non-empty object")
    return payload


def _get_soup(current: Any, url: str, timeout: int, *, expected_kind: str) -> BeautifulSoup:
    response = current.get(
        url,
        timeout=timeout,
        allow_redirects=False,
        verify=True,
    )
    return _coerce_soup(response, expected_kind=expected_kind)


def _post_json(
    current: Any,
    endpoint: str,
    identity: str,
    timeout: int,
    *,
    expected_kind: str,
) -> Mapping[str, Any]:
    response = current.post(
        endpoint,
        files={"gdSeq": (None, identity)},
        headers={
            "Referer": seogwipo_ticket_url(identity),
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=timeout,
        allow_redirects=False,
        verify=True,
    )
    return _coerce_json(response, expected_kind=expected_kind)


def _parse_date_range(value: Any, *, label: str) -> tuple[date, date]:
    match = _DATE_RANGE_RE.fullmatch(_clean(value))
    if match is None:
        raise ValueError(f"malformed {label}")
    start = date.fromisoformat(match.group(1))
    end = date.fromisoformat(match.group(2))
    if start > end:
        raise ValueError(f"reversed {label}")
    return start, end


def _parse_api_datetime(value: Any, *, label: str) -> datetime:
    raw = _clean(value)
    if not _DATETIME_RE.fullmatch(raw):
        raise ValueError(f"malformed {label}")
    return datetime.strptime(raw, "%Y%m%d%H%M")


def _application_identity(value: Any) -> tuple[str, str]:
    raw = _clean(value)
    match = _APPLICATION_CONTROL_RE.fullmatch(raw)
    if match is None:
        return "", ""
    url = match.group("url")
    identity = match.group("identity")
    return identity, url


def _parse_list(soup: BeautifulSoup) -> tuple[list[dict[str, Any]], int]:
    container = soup.select_one(".parking-list")
    if container is None:
        raise ValueError("official event/education list container is missing")
    outer = container.find("ul", recursive=False)
    if outer is None:
        raise ValueError("official event/education list is missing")
    cards = outer.find_all("li", recursive=False)
    if not cards:
        text = _clean(container.get_text(" ", strip=True))
        if not any(marker in text for marker in _EMPTY_MARKERS):
            raise ValueError("empty list did not expose an official empty-state marker")
        return [], 0

    rows: list[dict[str, Any]] = []
    malformed = 0
    for card in cards:
        try:
            title_node = card.select_one("h5")
            capacity_node = card.select_one("p.count > span")
            detail_list = card.select_one("ul.p-detail")
            application = card.select_one("a.btn, button.btn")
            if not all((title_node, capacity_node, detail_list, application)):
                raise ValueError("card is missing a required node")

            title = _clean(title_node.get_text(" ", strip=True))
            capacity_raw = _clean(capacity_node.get_text(" ", strip=True))
            if not title or not re.fullmatch(r"\d[\d,]*", capacity_raw):
                raise ValueError("card title or capacity is malformed")

            pairs: dict[str, str] = {}
            for item in detail_list.find_all("li", recursive=False):
                label_node = item.select_one("p")
                value_node = item.select_one("span")
                label = _clean(label_node.get_text(" ", strip=True)) if label_node else ""
                detail_value = _clean(value_node.get_text(" ", strip=True)) if value_node else ""
                if not label or not detail_value or label in pairs:
                    raise ValueError("card detail labels are malformed")
                pairs[label] = detail_value
            if not _REQUIRED_LIST_FIELDS.issubset(pairs):
                raise ValueError("card is missing required event detail fields")

            event_start, event_end = _parse_date_range(
                pairs["행사기간"], label="event period"
            )
            registration_start, registration_end = _parse_date_range(
                pairs["접수기간"], label="registration period"
            )
            if _TIME_RANGE_RE.fullmatch(pairs["행사시간"]) is None:
                raise ValueError("malformed event time")

            application_label = _clean(application.get_text(" ", strip=True))
            application_href = _clean(application.get("href"))
            if application_label == "신청하기":
                identity, application_url = _application_identity(application_href)
                if not identity or application_url != seogwipo_ticket_url(identity):
                    raise ValueError("application control is not a canonical ticket URL")
                application_state = "linked"
            elif application_label == "신청마감" and not application_href:
                identity, application_url = "", ""
                application_state = "closed_unlinked"
            else:
                raise ValueError("application control state is not recognised")

            rows.append(
                {
                    "identity": identity,
                    "title": title,
                    "capacity_total": int(capacity_raw.replace(",", "")),
                    "event_start": event_start,
                    "event_end": event_end,
                    "registration_start": registration_start,
                    "registration_end": registration_end,
                    "event_time_raw": pairs["행사시간"],
                    "venue_raw": pairs["행사장소"],
                    "application_url": application_url,
                    "application_state": application_state,
                    "list_pairs": pairs,
                }
            )
        except Exception:
            malformed += 1
    return rows, malformed


def _mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _html_text(value: Any) -> str:
    raw = html.unescape(str(value or ""))
    if not raw:
        return ""
    return _clean(BeautifulSoup(raw, "lxml").get_text(" ", strip=True))


def _venue_fields(value: Any) -> tuple[str, str]:
    raw = _clean(value)
    match = re.fullmatch(r"(.+?)\s*\(([^()]*)\)", raw)
    if match is None:
        return raw, ""
    return _clean(match.group(1)), _clean(match.group(2))


def _branch_code(branch: str) -> str:
    digest = hashlib.sha1(_clean(branch).encode("utf-8")).hexdigest()[:12].upper()
    return f"SEOGWIPO_ETICKET_BRANCH_{digest}"


def _closed_list_identity(source: Mapping[str, Any]) -> str:
    """Return a stable identity for an official closed, unlinked education card.

    E-Ticket removes the Smartix goods link as soon as an application is
    closed, even while the event remains current/future on the official list.
    The title, event period, event time, and venue remain rendered by the
    official page and together identify one scheduled programme without using
    mutable registration/capacity values.
    """

    if source.get("application_state") != "closed_unlinked":
        raise ValueError("list-only identity requires an official closed control")
    title = _clean(source.get("title"))
    if not is_explicit_education_title(title):
        raise ValueError("list-only identity is restricted to explicit education")
    event_start = source.get("event_start")
    event_end = source.get("event_end")
    event_time = _clean(source.get("event_time_raw"))
    venue = _clean(source.get("venue_raw"))
    if not isinstance(event_start, date) or not isinstance(event_end, date):
        raise ValueError("list-only identity requires a valid official event period")
    if _TIME_RANGE_RE.fullmatch(event_time) is None or not venue:
        raise ValueError("list-only identity requires official time and venue fields")
    identity_basis = "\x1f".join(
        (title, event_start.isoformat(), event_end.isoformat(), event_time, venue)
    )
    digest = hashlib.sha256(identity_basis.encode("utf-8")).hexdigest()[:24].upper()
    return f"LIST-{digest}"


def _closed_list_row(source: dict[str, Any], cutoff: date) -> dict[str, Any]:
    """Build a non-actionable row from a current official closed card."""

    if source["event_end"] < cutoff:
        raise ValueError("list-only education card is no longer current")
    source_identity = _closed_list_identity(source)
    venue_name, venue_address = _venue_fields(source["venue_raw"])
    if not venue_name:
        raise ValueError("list-only education card has no official venue")
    time_match = _TIME_RANGE_RE.fullmatch(_clean(source["event_time_raw"]))
    if time_match is None:
        raise ValueError("list-only education card has malformed event time")
    branch = venue_name
    return {
        "provider_course_id": (
            f"{SEOGWIPO_ETICKET_PROVIDER}:{source_identity}"
        )[:100],
        "prefer_incoming_provider_course_id": True,
        "title": _clean(source["title"]),
        "branch": branch,
        "branch_code": _branch_code(branch),
        "provider_organizer": branch,
        "start_date": source["event_start"].isoformat(),
        "end_date": source["event_end"].isoformat(),
        "registration_start": source["registration_start"].isoformat(),
        "registration_end": source["registration_end"].isoformat(),
        "schedule_raw": f"{time_match.group(1)} ~ {time_match.group(2)}",
        "capacity_total": int(source["capacity_total"]),
        "venue_name": venue_name,
        "venue_address": venue_address,
        "room": venue_name,
        "status": "CLOSED",
        "raw_status": "신청마감",
        # There is deliberately no application_url.  The only remaining
        # official source is the public list page; inventing a ticket URL from
        # a hash would make a closed programme look actionable.
        "raw_url": SEOGWIPO_ETICKET_LIST_URL,
        "reservation_available": False,
        "municipality_code": SEOGWIPO_MUNICIPALITY_CODE,
        "municipality_name": SEOGWIPO_MUNICIPALITY_NAME,
        "municipality_full_name": SEOGWIPO_MUNICIPALITY_NAME,
        "municipality_region_verified": True,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "source_group": "municipal_reservation",
        "operator_type": "지자체/공공기관",
        "collection_type": "seogwipo_eticket_current_education",
        "raw_fields": {
            "parser": SEOGWIPO_ETICKET_PARSER,
            "source_url": SEOGWIPO_ETICKET_LIST_URL,
            "source_identity": source_identity,
            "source_identity_basis": {
                "title": _clean(source["title"]),
                "event_start": source["event_start"].isoformat(),
                "event_end": source["event_end"].isoformat(),
                "event_time": _clean(source["event_time_raw"]),
                "venue": _clean(source["venue_raw"]),
            },
            "list_pairs": source["list_pairs"],
            "application_state": "closed_unlinked",
            "explicit_education_title": True,
            "list_only_closed_education": True,
            "ticket_page_validated": False,
            "goods_detail_validated": False,
        },
    }


def _status(
    cutoff: date,
    registration_start: date,
    registration_end: date,
    paused: str,
) -> tuple[str, str]:
    if paused == "Y":
        return "CLOSED", "판매일시중지"
    if cutoff < registration_start:
        return "SCHEDULED", "접수예정"
    if cutoff > registration_end:
        return "CLOSED", "접수마감"
    return "OPEN", "접수중"


def _enrich_source_row(
    current: Any,
    source: dict[str, Any],
    timeout: int,
    cutoff: date,
) -> tuple[dict[str, Any], set[str], set[str], int]:
    identity = _clean(source["identity"])
    ticket_url = seogwipo_ticket_url(identity)
    ticket_soup = _get_soup(
        current, ticket_url, timeout, expected_kind=f"ticket page {identity}"
    )
    identity_node = ticket_soup.select_one("input#gdSeq[name=gdSeq]")
    if identity_node is None or _clean(identity_node.get("value")) != identity:
        raise ValueError(f"{identity}: ticket page identity mismatch")

    goods_payload = _post_json(
        current,
        SEOGWIPO_GOODS_INFO_URL,
        identity,
        timeout,
        expected_kind=f"goods info {identity}",
    )
    information_payload = _post_json(
        current,
        SEOGWIPO_GOODS_INFORMATION_URL,
        identity,
        timeout,
        expected_kind=f"goods information {identity}",
    )
    header_payload = _post_json(
        current,
        SEOGWIPO_HEADER_INFO_URL,
        identity,
        timeout,
        expected_kind=f"header info {identity}",
    )
    goods = _mapping(goods_payload.get("data"), label=f"{identity} goods data")
    information = _mapping(
        information_payload.get("data"), label=f"{identity} information data"
    )
    header = _mapping(header_payload.get("data"), label=f"{identity} header data")
    header_items = header_payload.get("dataList")
    if not isinstance(header_items, list) or not header_items:
        raise ValueError(f"{identity}: header sale-goods list is empty or malformed")

    if _clean(goods.get("gdSeq")) != identity:
        raise ValueError(f"{identity}: goods API identity mismatch")
    if _clean(goods.get("gdName")) != _clean(source["title"]):
        raise ValueError(f"{identity}: list/goods title mismatch")
    if _clean(information.get("gdName")) != _clean(source["title"]):
        raise ValueError(f"{identity}: list/information title mismatch")
    header_name = _clean(header.get("gdName"))
    if header_name and header_name != _clean(source["title"]):
        raise ValueError(f"{identity}: list/header title mismatch")

    event_start_at = _parse_api_datetime(goods.get("gdOpenDt"), label="goods start")
    event_end_at = _parse_api_datetime(goods.get("gdCloseDt"), label="goods end")
    sale_start_at = _parse_api_datetime(
        goods.get("gdSaleStartdt"), label="goods sale start"
    )
    sale_end_at = _parse_api_datetime(goods.get("gdSaleEnddt"), label="goods sale end")
    if event_start_at > event_end_at or sale_start_at > sale_end_at:
        raise ValueError(f"{identity}: reversed goods period")
    if event_start_at.date() != source["event_start"] or event_end_at.date() != source["event_end"]:
        raise ValueError(f"{identity}: list/goods event period mismatch")
    # The ticket engine stores a midnight-exclusive boundary for most goods,
    # while E-Ticket renders the preceding inclusive calendar date.  A small
    # subset is stored as the same calendar date.  Accept exactly those two
    # official representations; larger drift remains fail-closed.
    allowed_sale_end_dates = {
        source["registration_end"],
        source["registration_end"] + timedelta(days=1),
    }
    if (
        sale_start_at.date() != source["registration_start"]
        or sale_end_at.date() not in allowed_sale_end_dates
        or (sale_end_at.date() != source["registration_end"] and sale_end_at.time() != datetime.min.time())
    ):
        raise ValueError(f"{identity}: list/goods registration period mismatch")

    company = _mapping(goods.get("companyVO"), label=f"{identity} company data")
    branch_names = {
        _clean(company.get("companyName")),
        _clean(information.get("companyName")),
        _clean(header.get("companyName")),
    }
    branch_names.discard("")
    if len(branch_names) != 1:
        raise ValueError(f"{identity}: organisation identity mismatch")
    branch = next(iter(branch_names))

    header_ids: set[str] = set()
    header_education_ids: set[str] = set()
    selected_header_name = ""
    for item in header_items:
        if not isinstance(item, Mapping):
            raise ValueError(f"{identity}: malformed header sale-goods item")
        item_id = _clean(item.get("gdSeq"))
        item_name = _clean(item.get("gdName"))
        if not _GOODS_ID_RE.fullmatch(item_id) or not item_name:
            raise ValueError(f"{identity}: malformed header goods identity")
        if item_id in header_ids:
            raise ValueError(f"{identity}: duplicate header goods identity")
        header_ids.add(item_id)
        if is_explicit_education_title(item_name):
            header_education_ids.add(item_id)
        if item_id == identity:
            selected_header_name = item_name
    if selected_header_name != _clean(source["title"]):
        raise ValueError(f"{identity}: selected goods missing from header list")

    paused = _clean(goods.get("gdSalePauseYn"))
    if paused not in {"Y", "N"}:
        raise ValueError(f"{identity}: malformed sale-pause state")
    if _clean(goods.get("gdFrontViewYn")) != "Y":
        raise ValueError(f"{identity}: goods is not front-visible")
    normalized_status, raw_status = _status(
        cutoff,
        source["registration_start"],
        source["registration_end"],
        paused,
    )
    venue_name, venue_address = _venue_fields(source["venue_raw"])
    description = _html_text(information.get("gdDesc")) or _html_text(
        goods.get("gdRsNotice")
    )
    explicit_education = is_explicit_education_title(source["title"])

    row: dict[str, Any] = {
        "provider_course_id": f"{SEOGWIPO_ETICKET_PROVIDER}:{identity}"[:100],
        "prefer_incoming_provider_course_id": True,
        "title": _clean(source["title"]),
        "branch": branch,
        "branch_code": _branch_code(branch),
        "provider_organizer": branch,
        "start_date": source["event_start"].isoformat(),
        "end_date": source["event_end"].isoformat(),
        "registration_start": source["registration_start"].isoformat(),
        "registration_end": source["registration_end"].isoformat(),
        "schedule_raw": f"{event_start_at:%H:%M} ~ {event_end_at:%H:%M}",
        "capacity_total": int(source["capacity_total"]),
        "venue_name": venue_name,
        "venue_address": venue_address,
        "room": venue_name,
        "phone": _clean(information.get("tel")),
        "description": description[:2000],
        "status": normalized_status,
        "raw_status": raw_status,
        "raw_url": ticket_url,
        "application_url": ticket_url,
        "application_type": "ONLINE_RESERVATION",
        "reservation_available": normalized_status == "OPEN",
        "municipality_code": SEOGWIPO_MUNICIPALITY_CODE,
        "municipality_name": SEOGWIPO_MUNICIPALITY_NAME,
        "municipality_full_name": SEOGWIPO_MUNICIPALITY_NAME,
        "municipality_region_verified": True,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "source_group": "municipal_reservation",
        "operator_type": "지자체/공공기관",
        "collection_type": "seogwipo_eticket_current_education",
        "raw_fields": {
            "parser": SEOGWIPO_ETICKET_PARSER,
            "source_url": SEOGWIPO_ETICKET_LIST_URL,
            "goods_id": identity,
            "list_pairs": source["list_pairs"],
            "list_event_time": source["event_time_raw"],
            "goods_category": _clean(goods.get("gdCategory")),
            "goods_kind": _clean(goods.get("gdKind")),
            "goods_free_yn": _clean(goods.get("gdFreeYn")),
            "goods_front_view_yn": _clean(goods.get("gdFrontViewYn")),
            "goods_sale_pause_yn": paused,
            "goods_sale_end_at": sale_end_at.isoformat(timespec="minutes"),
            "explicit_education_title": explicit_education,
            "ticket_page_validated": True,
            "goods_detail_validated": True,
            "header_sale_goods_count": len(header_ids),
        },
    }
    return row, header_ids, header_education_ids, 3


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if value not in (None, "", [], {})
    }


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _failure(message: str) -> dict[str, Any]:
    return {
        "pages": 0,
        "detail_pages": 0,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "current_count": 0,
        "returned_count": 0,
        "no_current_data": False,
        "configured_collection_error": message,
    }


def collect_seogwipo_eticket_education(
    target: Any,
    timeout: int = 20,
    max_pages: int = 5,
    detail_limit: int = 100,
    *,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return an all-or-nothing current/future education snapshot."""

    if not is_seogwipo_eticket_target(target):
        return [], SEOGWIPO_ETICKET_PARSER, _failure(
            "target does not match the canonical Seogwipo E-Ticket provider route"
        )
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], SEOGWIPO_ETICKET_PARSER, _failure(
                "managed session_factory injection is required"
            )
        session_factory = _default_session_factory

    allowed_pages = max(0, int(max_pages))
    allowed_details = max(0, int(detail_limit))
    cutoff = _today(today)
    errors: list[str] = []
    source_rows: list[dict[str, Any]] = []
    education_rows: list[dict[str, Any]] = []
    header_union: set[str] = set()
    header_education_union: set[str] = set()
    detail_pages = 0
    detail_api_calls = 0
    detail_attempts = 0
    list_only_closed_count = 0
    malformed_count = 0
    duplicate_count = 0
    source_cap_reached = False
    list_page_fetched = False
    current: Any = None

    if allowed_pages < 1:
        source_cap_reached = True
        errors.append("max_pages cap does not allow the official single page")
    else:
        try:
            current = session_factory()
            list_soup = _get_soup(
                current,
                SEOGWIPO_ETICKET_LIST_URL,
                timeout,
                expected_kind="Seogwipo E-Ticket list",
            )
            list_page_fetched = True
            source_rows, malformed_count = _parse_list(list_soup)
            identities = [
                _clean(row.get("identity"))
                for row in source_rows
                if _clean(row.get("identity"))
            ]
            duplicate_count = len(identities) - len(set(identities))
            if malformed_count:
                errors.append(f"{malformed_count} malformed official list cards")
            if duplicate_count:
                errors.append(f"{duplicate_count} duplicate official goods identities")
            current_education_sources = [
                source
                for source in source_rows
                if source["event_end"] >= cutoff
                and is_explicit_education_title(source["title"])
            ]
            linked_education_sources = [
                source
                for source in current_education_sources
                if _clean(source.get("identity"))
            ]
            required_details = len(linked_education_sources)
            if allowed_details < required_details:
                source_cap_reached = True
                errors.append(
                    f"detail_limit cap allows {allowed_details} of "
                    f"{required_details} required ticket details"
                )
            elif not errors:
                for source in current_education_sources:
                    try:
                        identity = _clean(source.get("identity"))
                        if identity:
                            detail_attempts += 1
                            (
                                row,
                                header_ids,
                                header_education_ids,
                                api_calls,
                            ) = _enrich_source_row(current, source, timeout, cutoff)
                            header_union.update(header_ids)
                            header_education_union.update(header_education_ids)
                            detail_pages += 1
                            detail_api_calls += api_calls
                        else:
                            row = _closed_list_row(source, cutoff)
                            list_only_closed_count += 1
                        education_rows.append(row)
                    except Exception as exc:
                        source_label = _clean(source.get("identity")) or _clean(
                            source.get("title")
                        )
                        errors.append(
                            f"{source_label}: "
                            f"detail validation {type(exc).__name__}: {_clean(exc)}"
                        )
                        break
        except Exception as exc:
            errors.append(f"list fetch/parse {type(exc).__name__}: {_clean(exc)}")
        finally:
            _close_quietly(current)

    current_education_sources = [
        source
        for source in source_rows
        if source["event_end"] >= cutoff
        and is_explicit_education_title(source["title"])
    ]
    linked_education_sources = [
        source
        for source in current_education_sources
        if _clean(source.get("identity"))
    ]
    list_only_education_sources = [
        source
        for source in current_education_sources
        if not _clean(source.get("identity"))
    ]
    current_linked_education_ids = {
        _clean(row.get("identity"))
        for row in linked_education_sources
        if _clean(row.get("identity"))
    }
    all_list_ids = {
        _clean(row.get("identity"))
        for row in source_rows
        if _clean(row.get("identity"))
    }
    if linked_education_sources and (
        header_education_union != current_linked_education_ids
    ):
        errors.append(
            "ticket header education sale-goods union does not match the "
            "official education list"
        )
    if detail_pages != len(linked_education_sources):
        errors.append(
            f"validated {detail_pages} of {len(linked_education_sources)} "
            "required education ticket details"
        )
    if list_only_closed_count != len(list_only_education_sources):
        errors.append(
            f"validated {list_only_closed_count} of "
            f"{len(list_only_education_sources)} required closed education cards"
        )

    expired_count = 0
    explicit_education_count = 0
    excluded_non_education_count = 0
    for source in source_rows:
        explicit = is_explicit_education_title(source["title"])
        if explicit:
            explicit_education_count += 1
        else:
            excluded_non_education_count += 1
        if source["event_end"] < cutoff:
            expired_count += 1
    list_complete = (
        list_page_fetched
        and malformed_count == 0
        and duplicate_count == 0
        and not source_cap_reached
        and len(education_rows) == len(current_education_sources)
        and not errors
    )
    details_complete = (
        detail_pages == len(linked_education_sources)
        and detail_api_calls == len(linked_education_sources) * 3
        and header_education_union == current_linked_education_ids
        and list_only_closed_count == len(list_only_education_sources)
        and not errors
    )
    cleaned = [_clean_row(row) for row in education_rows]
    dedupe = dedupe_rows or _dedupe_default
    if list_complete and details_complete:
        try:
            deduped = list(dedupe(cleaned))
        except Exception as exc:
            errors.append(f"dedupe failed {type(exc).__name__}")
            deduped = []
        if len(deduped) != len(cleaned):
            errors.append(
                f"dedupe changed complete row count {len(cleaned)} to {len(deduped)}"
            )
        cleaned = deduped

    snapshot_complete = list_complete and details_complete and not errors
    if not snapshot_complete:
        cleaned = []

    status_counts = Counter(_clean(row.get("status")) for row in education_rows)
    branch_counts = Counter(_clean(row.get("branch")) for row in education_rows)
    meta: dict[str, Any] = {
        "pages": 1 if list_page_fetched else 0,
        "required_list_requests": 1,
        "declared_pages": 1,
        "declared_total_available": False,
        "completeness_basis": (
            "official_single_page+relevant_ticket_header_education_union+"
            "closed_list_fingerprint"
        ),
        "source_total": len(source_rows),
        "discovered_links": len(all_list_ids),
        "historical_unlinked_count": sum(
            source.get("application_state") == "closed_unlinked"
            and source["event_end"] < cutoff
            for source in source_rows
        ),
        "header_union_count": len(header_union),
        "header_education_union_count": len(header_education_union),
        "malformed_count": malformed_count,
        "duplicate_count": duplicate_count,
        "explicit_education_count": explicit_education_count,
        "excluded_non_education_count": excluded_non_education_count,
        "expired_count": expired_count,
        "current_count": len(education_rows),
        "returned_count": len(cleaned),
        "current_education_source_count": len(current_education_sources),
        "linked_education_count": len(linked_education_sources),
        "required_detail_count": len(linked_education_sources),
        "detail_attempts": detail_attempts,
        "detail_pages": detail_pages,
        "detail_api_calls": detail_api_calls,
        "required_list_only_count": len(list_only_education_sources),
        "list_only_closed_education_count": list_only_closed_count,
        "ignored_current_non_education_count": sum(
            source["event_end"] >= cutoff
            and not is_explicit_education_title(source["title"])
            for source in source_rows
        ),
        "pagination_detected": False,
        "pagination_complete": list_complete,
        "pagination_exhausted": list_complete,
        "details_complete": details_complete,
        "snapshot_complete": snapshot_complete,
        "source_cap_reached": source_cap_reached,
        "recursion_depth": 0,
        "status_counts": dict(status_counts),
        "branch_count": len(branch_counts),
        "branch_counts": dict(branch_counts),
        "reservation_discovery_links": sum(
            bool(row.get("application_url")) for row in education_rows
        ),
        "no_current_data": snapshot_complete and not education_rows,
        "no_current_reason": (
            "the official current E-Ticket page has no explicit education/lecture products"
            if snapshot_complete and not education_rows
            else ""
        ),
    }
    if errors:
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
    return cleaned, SEOGWIPO_ETICKET_PARSER, meta


collect_seogwipo_eticket_target = collect_seogwipo_eticket_education


__all__ = [
    "SEOGWIPO_ETICKET_HOST",
    "SEOGWIPO_ETICKET_LIST_PATH",
    "SEOGWIPO_ETICKET_LIST_URL",
    "SEOGWIPO_ETICKET_PARSER",
    "SEOGWIPO_ETICKET_PROVIDER",
    "SEOGWIPO_ETICKET_TARGET_URL",
    "SEOGWIPO_GOODS_INFORMATION_URL",
    "SEOGWIPO_GOODS_INFO_URL",
    "SEOGWIPO_HEADER_INFO_URL",
    "SEOGWIPO_MUNICIPALITY_CODE",
    "SEOGWIPO_MUNICIPALITY_NAME",
    "SEOGWIPO_TICKET_HOST",
    "collect_seogwipo_eticket_education",
    "collect_seogwipo_eticket_target",
    "is_explicit_education_title",
    "is_seogwipo_eticket_target",
    "is_target",
    "seogwipo_ticket_url",
]
