"""Fail-closed collector for Namwon City's integrated education catalogue.

The official portal exposes its catalogue through a structured JSON endpoint.
This collector owns the exact integrated-reservation root and exhausts the two
education facilities plus the ``lecture`` slice of the mixed performance /
lecture menu.  It validates every declared API page, the immediate empty page,
and a page-one recheck before it reads every current/future detail page.

The museum and experience menus intentionally stay out of this module: their
official reservation type is ``EXPERIENCE``, while this provider is the
municipal education catalogue.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import re
import threading
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


NAMWON_PROVIDER = "MUNI_WWW_NAMWON_GO_KR_37D4EA88"
NAMWON_CANDIDATE_ID = "MUNI_IR_A69D8582681A"
NAMWON_URL = "https://www.namwon.go.kr/reserve"
NAMWON_ROOT_URL = f"{NAMWON_URL}/index.do"
NAMWON_HOST = "www.namwon.go.kr"
NAMWON_PATH = "/reserve"
NAMWON_MUNICIPALITY_CODE = "5219000000"
NAMWON_MUNICIPALITY_NAME = "전북특별자치도 남원시"
NAMWON_API_URL = (
    "https://www.namwon.go.kr/reserve/integr/rsvt/fclt/item/api/items.do"
)
NAMWON_API_PAGE_SIZE = 500
NAMWON_MAX_WORKERS = 6
NAMWON_SESSION_REQUEST_LIMIT = 80
NAMWON_PARSER = (
    "namwon_integrated_education_api_"
    "complete_pages+empty_sentinels+current_detail"
)

_LECTURE_TAG_UID = "ff80808190b9af790190be14c3d30249"


@dataclass(frozen=True)
class NamwonSource:
    code: str
    label: str
    list_menu_uid: str
    detail_menu_uid: str
    facility_name: str
    required_tag_uid: str = ""


NAMWON_SOURCES = (
    NamwonSource(
        "EDU",
        "시민참여교육",
        "ff8080818f95374c018f9f898bfc016f",
        "ff8080818f95374c018f9f8b3ea10175",
        "시민참여교육",
    ),
    NamwonSource(
        "LIFELONG",
        "평생학습관",
        "ff8080818f95374c018f9f5494560153",
        "ff8080818eee7f01018f2cc4177f0064",
        "평생학습관",
    ),
    NamwonSource(
        "FESTIVAL",
        "공연·강좌 신청(강좌)",
        "ff8080818eee7f01018f37ecc23e026d",
        "ff8080818eee7f01018f3813f8c10272",
        "공연",
        _LECTURE_TAG_UID,
    ),
)

Fetcher = Callable[[Any, str, int], Any]
JsonGetter = Callable[[Any, str, Mapping[str, Any], int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_ITEM_UID_RE = re.compile(r"[0-9a-f]{32}")
_MENU_UID_RE = re.compile(r"[0-9a-f]{32}")
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)")
_DATETIME_RE = re.compile(
    r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})\s+([01]\d|2[0-3]):([0-5]\d)(?!\d)"
)
_STATUS_MAP: Mapping[str, str] = {
    "SCHEDULED": "SCHEDULED",
    "PROCEEDING": "OPEN",
    "DEADLINE": "CLOSED",
}
_DETAIL_STATUS_MAP: Mapping[str, str] = {
    "진행예정": "SCHEDULED",
    "접수중": "OPEN",
    "접수마감": "CLOSED",
}
_REQUIRED_ITEM_KEYS = frozenset(
    {
        "itemUid",
        "itemTitle",
        "instUid",
        "fcltUid",
        "maxCapacity",
        "baseCapacity",
        "waitCapacity",
        "applyCount",
        "waitCount",
        "baseFee",
        "facilityInfo",
        "tags",
        "applyBeginDate",
        "applyEndDate",
        "beginDate",
        "endDate",
        "timeInfo",
        "itemInfo1",
        "itemInfo2",
        "itemInfo3",
        "itemInfo4",
        "useWaiting",
        "itemProgress",
        "itemApplyCountType",
    }
)


class NamwonContractError(ValueError):
    """The live source no longer satisfies the audited collection contract."""


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", _clean(value).casefold())


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def is_namwon_target(target: Any) -> bool:
    """Accept only the reviewed provider and canonical no-query root URL."""

    if _clean(_target_value(target, "provider")) != NAMWON_PROVIDER:
        return False
    parsed = urlparse(_clean(_target_value(target, "url")))
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == "https"
        and parsed.hostname == NAMWON_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == NAMWON_PATH
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


is_target = is_namwon_target


def namwon_landing_url(source: NamwonSource | str) -> str:
    item = _source(source)
    return (
        "https://www.namwon.go.kr/reserve/index.do?"
        + urlencode({"menuUid": item.list_menu_uid})
    )


def namwon_api_params(
    source: NamwonSource | str,
    page: int,
    *,
    page_size: Optional[int] = None,
) -> dict[str, Any]:
    item = _source(source)
    result: dict[str, Any] = {
        "rsvtType": "EDUCATION",
        "fcltCodes": item.code,
        "page": max(1, int(page)),
        "size": int(page_size or NAMWON_API_PAGE_SIZE),
        "sort": "registerDt,desc",
    }
    if item.required_tag_uid:
        result["tagUids"] = item.required_tag_uid
    return result


def namwon_detail_url(source: NamwonSource | str, identity: Any) -> str:
    item = _source(source)
    uid = _clean(identity).lower()
    if not _ITEM_UID_RE.fullmatch(uid):
        return ""
    query = urlencode(
        (
            ("menuUid", item.detail_menu_uid),
            ("itemUid", uid),
            ("historyPage", "1"),
            ("keyword", ""),
        )
    )
    return f"https://www.namwon.go.kr/reserve/index.do?{query}"


def _source(value: NamwonSource | str) -> NamwonSource:
    if isinstance(value, NamwonSource):
        return value
    for item in NAMWON_SOURCES:
        if item.code == value:
            return item
    raise KeyError(value)


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/125.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
            "Referer": NAMWON_ROOT_URL,
        }
    )
    return current


def _strict_response(response: Any) -> Any:
    if int(getattr(response, "status_code", 0)) != 200:
        raise NamwonContractError(
            f"unexpected HTTP status {getattr(response, 'status_code', None)}"
        )
    if getattr(response, "headers", {}).get("Location"):
        raise NamwonContractError("redirect response is not accepted")
    if not getattr(response, "content", b""):
        raise NamwonContractError("empty HTTP response")
    return response


def _default_fetcher(current_session: Any, url: str, timeout: int) -> Any:
    return _strict_response(
        current_session.get(url, timeout=timeout, allow_redirects=False)
    )


def _default_json_getter(
    current_session: Any,
    url: str,
    params: Mapping[str, Any],
    timeout: int,
) -> Any:
    return _strict_response(
        current_session.get(
            url,
            params=dict(params),
            headers={"X-Requested-With": "XMLHttpRequest"},
            timeout=timeout,
            allow_redirects=False,
        )
    )


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, bytes):
        return BeautifulSoup(value, "lxml")
    if isinstance(value, str):
        return BeautifulSoup(value, "lxml")
    content = getattr(value, "content", None)
    if content is None:
        raise TypeError("HTML fetcher returned neither HTML nor a response")
    return BeautifulSoup(content, "lxml")


def _coerce_json(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    loader = getattr(value, "json", None)
    if not callable(loader):
        raise TypeError("JSON getter returned neither a mapping nor a response")
    result = loader()
    if not isinstance(result, Mapping):
        raise TypeError("JSON response is not an object")
    return result


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _root_owned(soup: BeautifulSoup) -> bool:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    text = _clean(soup.get_text(" ", strip=True))
    if "통합예약" not in title or "남원시 통합예약포털" not in text:
        return False
    expected = {"시민참여교육", "평생학습관", "공연·강좌 신청"}
    visible = {
        _clean(anchor.get_text(" ", strip=True))
        for anchor in soup.select("a[href*='menuUid=']")
    }
    return expected.issubset(visible)


def _landing_owned(soup: BeautifulSoup, source: NamwonSource) -> bool:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    expected_title = "공연·강좌" if source.code == "FESTIVAL" else source.label
    if "통합예약" not in title or expected_title not in title:
        return False
    values: dict[str, str] = {}
    for name in ("rsvtType", "fcltCodes", "nextMenuUid", "sort"):
        nodes = soup.select(f"#{name}")
        if len(nodes) != 1:
            return False
        values[name] = _clean(nodes[0].get("value"))
    if values != {
        "rsvtType": "EDUCATION",
        "fcltCodes": source.code,
        "nextMenuUid": source.detail_menu_uid,
        "sort": "registerDt,desc",
    }:
        return False
    if source.required_tag_uid:
        tags = {_clean(node.get("value")) for node in soup.select("input[name='tags1']")}
        if source.required_tag_uid not in tags:
            return False
    return True


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise NamwonContractError(f"{field} is not a non-negative integer")
    return value


def _iso_date(value: Any, field: str) -> date:
    text = _clean(value)
    if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", text):
        raise NamwonContractError(f"{field} is not an ISO date")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise NamwonContractError(f"{field} is not a valid date") from exc


def _iso_datetime(value: Any, field: str) -> str:
    text = _clean(value)
    match = _DATETIME_RE.fullmatch(text)
    if match is None:
        raise NamwonContractError(f"{field} is not an official minute datetime")
    try:
        datetime.strptime(text, "%Y-%m-%d %H:%M")
    except ValueError as exc:
        raise NamwonContractError(f"{field} is not a valid datetime") from exc
    return text


def _tag_values(item: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    raw_tags = item.get("tags")
    if not isinstance(raw_tags, list):
        raise NamwonContractError("tags is not a list")
    result: list[tuple[str, str, str]] = []
    for raw in raw_tags:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("tagGroup"), Mapping):
            raise NamwonContractError("tag entry is malformed")
        tag_uid = _clean(raw.get("tagUid"))
        group_id = _clean(raw["tagGroup"].get("groupId"))
        tag_name = _clean(raw.get("tagName"))
        if not _MENU_UID_RE.fullmatch(tag_uid) or not group_id or not tag_name:
            raise NamwonContractError("tag identity/group/name is malformed")
        result.append((tag_uid, group_id, tag_name))
    return result


def _category(tags: list[tuple[str, str, str]], source: NamwonSource) -> str:
    for _uid, group, name in tags:
        if group == "LECTURE_CATEGORY":
            return name
    for _uid, group, name in tags:
        if group == "SHOWLECTURE":
            return name
    return source.label


def _status(item: Mapping[str, Any]) -> str:
    raw = _clean(item.get("itemProgress"))
    if raw not in _STATUS_MAP:
        raise NamwonContractError(f"unknown itemProgress {raw!r}")
    return _STATUS_MAP[raw]


def _branch_code(facility_uid: str, branch: str) -> str:
    digest = hashlib.sha1(_clean(branch).encode("utf-8")).hexdigest()[:12].upper()
    return f"{NAMWON_PROVIDER}:{facility_uid}:{digest}"[:100]


def _parse_item(
    target: Any,
    source: NamwonSource,
    item: Mapping[str, Any],
    cutoff: date,
) -> dict[str, Any]:
    missing = _REQUIRED_ITEM_KEYS.difference(item)
    if missing:
        raise NamwonContractError(f"item is missing keys {sorted(missing)!r}")
    identity = _clean(item.get("itemUid")).lower()
    facility_uid = _clean(item.get("fcltUid")).lower()
    institution_uid = _clean(item.get("instUid")).lower()
    title = _clean(item.get("itemTitle"))
    if not _ITEM_UID_RE.fullmatch(identity):
        raise NamwonContractError("itemUid is malformed")
    if not _ITEM_UID_RE.fullmatch(facility_uid) or not _ITEM_UID_RE.fullmatch(
        institution_uid
    ):
        raise NamwonContractError(f"item {identity} facility/institution identity is malformed")
    if not title:
        raise NamwonContractError(f"item {identity} title is empty")

    facility = item.get("facilityInfo")
    if not isinstance(facility, Mapping):
        raise NamwonContractError(f"item {identity} facilityInfo is malformed")
    if (
        _clean(facility.get("fcltUid")).lower() != facility_uid
        or _clean(facility.get("instUid")).lower() != institution_uid
        or _clean(facility.get("fcltCode")) != source.code
        or _clean(facility.get("rsvtType")) != "EDUCATION"
        or _clean(facility.get("fcltName")) != source.facility_name
    ):
        raise NamwonContractError(f"item {identity} facility ownership mismatch")
    method = _clean(facility.get("rsvtMthd"))
    if method not in {"FCFS", "LOTTERY"}:
        raise NamwonContractError(f"item {identity} reservation method changed")

    tags = _tag_values(item)
    if source.required_tag_uid and (
        source.required_tag_uid,
        "SHOWLECTURE",
        "강좌",
    ) not in tags:
        raise NamwonContractError(f"item {identity} is not a lecture")

    source_status = _clean(item.get("itemProgress"))
    if source_status not in _STATUS_MAP:
        raise NamwonContractError(f"unknown itemProgress {source_status!r}")
    start = _iso_date(item.get("beginDate"), "beginDate")
    end = _iso_date(item.get("endDate"), "endDate")
    apply_start_at = _iso_datetime(item.get("applyBeginDate"), "applyBeginDate")
    apply_end_at = _iso_datetime(item.get("applyEndDate"), "applyEndDate")
    if apply_end_at < apply_start_at:
        raise NamwonContractError(f"item {identity} application dates are reversed")

    # The live archive contains a few old rows with provable source defects.  We
    # retain their identities for complete-snapshot accounting, but never publish
    # or detail them.  The exception is deliberately bounded to DEADLINE rows for
    # which both application and every supplied education date predate the cutoff.
    original_start = start
    original_end = end
    historical_reasons: list[str] = []
    provably_expired = (
        source_status == "DEADLINE"
        and date.fromisoformat(apply_end_at[:10]) < cutoff
        and max(start, end) < cutoff
    )
    if end < start:
        if not provably_expired:
            raise NamwonContractError(f"item {identity} education dates are reversed")
        historical_reasons.append("official education dates are reversed")
        start, end = min(start, end), max(start, end)

    raw_wait_count = item.get("waitCount")
    wait_count = raw_wait_count
    if raw_wait_count == -1:
        if not provably_expired:
            raise NamwonContractError(
                f"item {identity} current/future waitCount sentinel is not accepted"
            )
        historical_reasons.append("official waitCount uses expired-row -1 sentinel")
        wait_count = 0

    numeric = {
        name: _integer(wait_count if name == "waitCount" else item.get(name), name)
        for name in (
            "maxCapacity",
            "baseCapacity",
            "waitCapacity",
            "applyCount",
            "waitCount",
            "baseFee",
        )
    }
    status = _status(item)
    detail_url = namwon_detail_url(source, identity)
    room = _clean(item.get("itemInfo3"))
    facility_label = _clean(item.get("itemDetailAddr")) or source.facility_name
    branch = f"{NAMWON_MUNICIPALITY_NAME} · {facility_label}"
    category = _category(tags, source)
    return {
        "provider": NAMWON_PROVIDER,
        "provider_course_id": f"{NAMWON_PROVIDER}:item:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "branch": branch,
        "branch_code": _branch_code(facility_uid, branch),
        "period": f"{start.isoformat()} ~ {end.isoformat()}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": f"{apply_start_at} ~ {apply_end_at}",
        "apply_start_date": apply_start_at[:10],
        "apply_end_date": apply_end_at[:10],
        "status": status,
        "category": category,
        "program_type": "강좌",
        "domain_category": "교육",
        "collection_category": "공공예약",
        "collection_type": "json_api+detail_html",
        "source_group": "municipal_reservation",
        "operator_type": "지자체/공공기관",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "instructor": _clean(item.get("itemInfo1")),
        "schedule_raw": _clean(item.get("timeInfo")),
        "target": _clean(item.get("itemInfo4")),
        "room": room,
        "venue": room,
        "address": _clean(item.get("itemAddr")),
        "phone": _clean(item.get("itemInfo2")),
        "description": _clean(item.get("explanation")),
        "fee": numeric["baseFee"],
        "price": numeric["baseFee"],
        "price_text": "무료" if not numeric["baseFee"] else f"{numeric['baseFee']:,}원",
        "capacity_total": numeric["maxCapacity"],
        "capacity_current": numeric["applyCount"],
        "capacity_remaining": max(0, numeric["maxCapacity"] - numeric["applyCount"]),
        "waitlist_total": numeric["waitCapacity"],
        "waitlist_current": numeric["waitCount"],
        "application_method": "선착순" if method == "FCFS" else "추첨",
        "application_methods": ["온라인"],
        "reservation_available": False,
        "application_url": "",
        "application_type": "",
        "raw_url": detail_url,
        "source_url": _clean(_target_value(target, "url")),
        "raw_fields": {
            "item_uid": identity,
            "facility_uid": facility_uid,
            "institution_uid": institution_uid,
            "facility_code": source.code,
            "facility_name": source.facility_name,
            "source_status": source_status,
            "source_tags": [
                {"tag_uid": uid, "group_id": group, "tag_name": name}
                for uid, group, name in tags
            ],
            "apply_start_at": apply_start_at,
            "apply_end_at": apply_end_at,
            "api_status": status,
            "historical_invalid": bool(historical_reasons),
            "historical_invalid_reasons": historical_reasons,
            "original_begin_date": original_start.isoformat(),
            "original_end_date": original_end.isoformat(),
            "original_wait_count": raw_wait_count,
        },
    }


def _page_payload(
    value: Any,
    source: NamwonSource,
    expected_page: int,
    *,
    expected_total: Optional[int] = None,
    expected_pages: Optional[int] = None,
) -> tuple[list[dict[str, Any]], int, int]:
    payload = _coerce_json(value)
    result = payload.get("result")
    if not isinstance(result, Mapping):
        raise NamwonContractError(f"{source.label} API result is missing")
    content = result.get("content")
    pageable = result.get("pageable")
    if not isinstance(content, list) or not isinstance(pageable, Mapping):
        raise NamwonContractError(f"{source.label} API page structure changed")
    page = _integer(result.get("number"), "number") + 1
    total = _integer(result.get("totalElements"), "totalElements")
    pages = _integer(result.get("totalPages"), "totalPages")
    size = _integer(result.get("size"), "size")
    count = _integer(result.get("numberOfElements"), "numberOfElements")
    pageable_number = _integer(pageable.get("pageNumber"), "pageable.pageNumber") + 1
    pageable_size = _integer(pageable.get("pageSize"), "pageable.pageSize")
    offset = _integer(pageable.get("offset"), "pageable.offset")
    if (
        page != expected_page
        or pageable_number != expected_page
        or size != NAMWON_API_PAGE_SIZE
        or pageable_size != NAMWON_API_PAGE_SIZE
        or offset != (expected_page - 1) * NAMWON_API_PAGE_SIZE
        or count != len(content)
        or bool(result.get("empty")) != (not content)
        or bool(result.get("first")) != (expected_page == 1)
    ):
        raise NamwonContractError(f"{source.label} API page metadata changed")
    if expected_total is not None and total != expected_total:
        raise NamwonContractError(f"{source.label} API total changed during traversal")
    if expected_pages is not None and pages != expected_pages:
        raise NamwonContractError(f"{source.label} API page count changed during traversal")
    calculated_pages = (
        (total + NAMWON_API_PAGE_SIZE - 1) // NAMWON_API_PAGE_SIZE if total else 0
    )
    if pages != calculated_pages:
        raise NamwonContractError(f"{source.label} API total/page arithmetic changed")
    if content and expected_page > max(1, pages):
        raise NamwonContractError(f"{source.label} API overrun page is not empty")
    expected_last = pages == 0 or expected_page >= pages
    if bool(result.get("last")) != expected_last:
        raise NamwonContractError(f"{source.label} API last-page flag changed")
    if pages and expected_page <= pages:
        expected_count = (
            NAMWON_API_PAGE_SIZE
            if expected_page < pages
            else total - NAMWON_API_PAGE_SIZE * (pages - 1)
        )
        if len(content) != expected_count:
            raise NamwonContractError(f"{source.label} API page row count changed")
    return [dict(item) if isinstance(item, Mapping) else item for item in content], total, pages


def _fingerprint(items: list[Mapping[str, Any]]) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (
            _clean(item.get("itemUid")),
            _clean(item.get("itemTitle")),
            _clean(item.get("applyBeginDate")),
            _clean(item.get("applyEndDate")),
            _clean(item.get("beginDate")),
            _clean(item.get("endDate")),
            _clean(item.get("itemProgress")),
        )
        for item in items
    )


def _detail_pairs(soup: BeautifulSoup) -> dict[str, str]:
    blocks = soup.select(".txt_area")
    if len(blocks) != 1:
        raise NamwonContractError(f"expected one detail block, got {len(blocks)}")
    pairs: dict[str, str] = {}
    for li in blocks[0].select("ul.info_list > li"):
        label = li.select_one(":scope > strong")
        value = li.select_one(":scope > p")
        if label is None or value is None:
            continue
        key = _clean(label.get_text(" ", strip=True))
        if key in pairs:
            raise NamwonContractError(f"duplicate detail field {key!r}")
        pairs[key] = _clean(value.get_text(" ", strip=True))
    required = {"기관", "접수", "강사명", "일자", "수강료", "시간", "교육대상", "장소"}
    if not required.issubset(pairs):
        raise NamwonContractError("detail fields changed")
    return pairs


def _date_pair(value: Any, field: str) -> tuple[str, str]:
    values = ["-".join(match) for match in _DATE_RE.findall(_clean(value))]
    if len(values) != 2:
        raise NamwonContractError(f"detail {field} is not a date pair")
    return values[0], values[1]


def _enrich_detail(
    row: dict[str, Any],
    source: NamwonSource,
    soup: BeautifulSoup,
) -> None:
    identity = _clean(row.get("raw_fields", {}).get("item_uid"))
    title_nodes = soup.select(".txt_area .tit_area > strong")
    if len(title_nodes) != 1 or _normalized(title_nodes[0].get_text(" ", strip=True)) != _normalized(
        row.get("title")
    ):
        raise NamwonContractError(f"item {identity} detail title mismatch")
    page_title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    expected_title = "공연·강좌" if source.code == "FESTIVAL" else source.label
    if "통합예약" not in page_title or expected_title not in page_title:
        raise NamwonContractError(f"item {identity} detail ownership mismatch")

    pairs = _detail_pairs(soup)
    if _date_pair(pairs["접수"], "application period") != (
        row["apply_start_date"],
        row["apply_end_date"],
    ):
        raise NamwonContractError(f"item {identity} detail application period mismatch")
    if _date_pair(pairs["일자"], "education period") != (
        row["start_date"],
        row["end_date"],
    ):
        raise NamwonContractError(f"item {identity} detail education period mismatch")
    for label, key in (
        ("강사명", "instructor"),
        ("시간", "schedule_raw"),
        ("교육대상", "target"),
        ("장소", "room"),
        ("수강료", "price_text"),
    ):
        if _clean(pairs[label]) != _clean(row.get(key)):
            raise NamwonContractError(f"item {identity} detail {key} mismatch")

    status_nodes = soup.select(".txt_area .cate_box .status")
    if len(status_nodes) != 1:
        raise NamwonContractError(f"item {identity} detail status is missing")
    source_detail_status = _clean(status_nodes[0].get_text(" ", strip=True))
    detail_status = _DETAIL_STATUS_MAP.get(source_detail_status)
    if detail_status is None or detail_status != row["status"]:
        raise NamwonContractError(f"item {identity} API/detail status mismatch")

    category_nodes = {
        _clean(node.get_text(" ", strip=True))
        for node in soup.select(".txt_area .cate_box .cate")
    }
    if row["category"] not in category_nodes:
        raise NamwonContractError(f"item {identity} detail category mismatch")

    list_links = soup.select("a.btn_list[href]")
    if len(list_links) != 1:
        raise NamwonContractError(f"item {identity} detail list control changed")
    parsed_list = urlparse(list_links[0].get("href", ""))
    list_query = parse_qs(parsed_list.query, keep_blank_values=True)
    if (
        parsed_list.path != "/reserve/index.do"
        or list_query.get("menuUid") != [source.list_menu_uid]
    ):
        raise NamwonContractError(f"item {identity} detail points to another catalogue")

    encoded_marker = f"itemUid%3D{identity}"
    if not any(
        encoded_marker.lower() in _clean(node.get("href")).lower()
        for node in soup.select("a[href*='returnUrl=']")
    ):
        raise NamwonContractError(f"item {identity} detail identity marker is missing")

    capacity_nodes = soup.select(".txt_area .btn_area ul.info li:first-child .num")
    if len(capacity_nodes) != 1:
        raise NamwonContractError(f"item {identity} detail capacity is missing")
    capacity_match = re.fullmatch(
        r"([\d,]+)\s*/\s*([\d,]+)",
        _clean(capacity_nodes[0].get_text(" ", strip=True)),
    )
    if capacity_match is None:
        raise NamwonContractError(f"item {identity} detail capacity is malformed")
    current = int(capacity_match.group(1).replace(",", ""))
    total = int(capacity_match.group(2).replace(",", ""))
    if total != row["capacity_total"]:
        raise NamwonContractError(f"item {identity} detail capacity total mismatch")
    row["capacity_current"] = current
    row["capacity_remaining"] = max(0, total - current)

    action_nodes = soup.select(".txt_area .btn_area > a.button")
    if len(action_nodes) != 1:
        raise NamwonContractError(f"item {identity} detail action changed")
    action = _clean(action_nodes[0].get_text(" ", strip=True))
    if detail_status == "OPEN":
        if action != "신청하기":
            raise NamwonContractError(f"item {identity} open detail has no application control")
        row["reservation_available"] = True
        row["application_url"] = row["raw_url"]
        row["application_type"] = "ONLINE_RESERVATION"
    elif action not in {"접수마감", "진행예정"}:
        raise NamwonContractError(f"item {identity} non-open detail action mismatch")

    detail_node = soup.select_one("#detailTab01")
    row["description"] = _clean(
        detail_node.get_text(" ", strip=True) if detail_node is not None else ""
    ) or row["description"]
    row["raw_fields"].update(
        {
            "detail_pairs": pairs,
            "detail_source_status": source_detail_status,
            "detail_action": action,
        }
    )


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _failure(message: str, **extra: Any) -> dict[str, Any]:
    return {
        "pages": 0,
        "request_count": 0,
        "root_requests": 0,
        "landing_requests": 0,
        "api_requests": 0,
        "data_pages": 0,
        "sentinel_requests": 0,
        "page_one_rechecks": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "source_count": len(NAMWON_SOURCES),
        "source_rows": 0,
        "unique_id_count": 0,
        "duplicate_count": 0,
        "expired_count": 0,
        "current_count": 0,
        "returned_count": 0,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": message,
        **extra,
    }


def collect_namwon_education_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 10,
    detail_limit: int = 100,
    *,
    fetcher: Optional[Fetcher] = None,
    json_getter: Optional[JsonGetter] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = NAMWON_MAX_WORKERS,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return a complete current/future Namwon municipal education snapshot."""

    if not is_namwon_target(target):
        return [], NAMWON_PARSER, _failure(
            "target does not match the exact Namwon integrated reservation root"
        )
    if int(max_pages) < 1:
        return [], NAMWON_PARSER, _failure(
            "max_pages cap does not allow the first API request",
            source_cap_reached=True,
        )

    current_fetcher = fetcher or _default_fetcher
    current_json_getter = json_getter or _default_json_getter
    current_session_factory = session_factory or _default_session_factory
    current_dedupe = dedupe_rows or _dedupe_default
    cutoff = _today(today)
    errors: list[str] = []
    source_cap_reached = False
    root_requests = 0
    landing_requests = 0
    api_requests = 0
    sentinel_requests = 0
    page_one_rechecks = 0
    detail_attempts = 0
    detail_pages = 0
    sessions: list[Any] = []
    sessions_lock = threading.Lock()
    local = threading.local()

    def thread_session() -> Any:
        value = getattr(local, "session", None)
        used = int(getattr(local, "requests", 0))
        if value is None or used >= NAMWON_SESSION_REQUEST_LIMIT:
            if value is not None:
                _close_quietly(value)
            value = current_session_factory()
            local.session = value
            local.requests = 0
            with sessions_lock:
                sessions.append(value)
        local.requests = int(getattr(local, "requests", 0)) + 1
        return value

    def fetch_html(url: str) -> BeautifulSoup:
        return _coerce_soup(current_fetcher(thread_session(), url, timeout))

    def fetch_api(source: NamwonSource, page: int) -> Mapping[str, Any]:
        return _coerce_json(
            current_json_getter(
                thread_session(),
                NAMWON_API_URL,
                namwon_api_params(source, page),
                timeout,
            )
        )

    first_items: dict[str, list[dict[str, Any]]] = {}
    totals: dict[str, int] = {}
    total_pages: dict[str, int] = {}
    page_rows: dict[tuple[str, int], list[dict[str, Any]]] = {}

    try:
        try:
            root = fetch_html(NAMWON_ROOT_URL)
            root_requests = 1
        except Exception as exc:
            return [], NAMWON_PARSER, _failure(
                f"official root fetch failed: {type(exc).__name__}"
            )
        if not _root_owned(root):
            return [], NAMWON_PARSER, _failure(
                "official root ownership/navigation contract failed",
                pages=root_requests,
                request_count=root_requests,
                root_requests=root_requests,
            )

        for source in NAMWON_SOURCES:
            try:
                landing = fetch_html(namwon_landing_url(source))
                landing_requests += 1
            except Exception as exc:
                errors.append(f"{source.label} landing fetch {type(exc).__name__}")
                continue
            if not _landing_owned(landing, source):
                errors.append(f"{source.label} landing contract changed")

        if errors:
            requests_used = root_requests + landing_requests
            return [], NAMWON_PARSER, _failure(
                "; ".join(dict.fromkeys(errors)),
                pages=requests_used,
                request_count=requests_used,
                root_requests=root_requests,
                landing_requests=landing_requests,
            )

        for source in NAMWON_SOURCES:
            try:
                payload = fetch_api(source, 1)
                api_requests += 1
                items, total, pages = _page_payload(payload, source, 1)
            except Exception as exc:
                errors.append(f"{source.label} API page 1: {type(exc).__name__}: {_clean(exc)}")
                continue
            totals[source.code] = total
            total_pages[source.code] = pages
            first_items[source.code] = items
            page_rows[(source.code, 1)] = items
            if pages > int(max_pages):
                source_cap_reached = True
                errors.append(
                    f"{source.label}: max_pages cap {int(max_pages)} is below declared {pages} pages"
                )

        if errors:
            requests_used = root_requests + landing_requests + api_requests
            return [], NAMWON_PARSER, _failure(
                "; ".join(dict.fromkeys(errors)),
                pages=requests_used,
                request_count=requests_used,
                root_requests=root_requests,
                landing_requests=landing_requests,
                api_requests=api_requests,
                source_cap_reached=source_cap_reached,
                source_totals=totals,
                source_page_counts=total_pages,
            )

        api_tasks = [
            (source, page, False)
            for source in NAMWON_SOURCES
            for page in range(2, total_pages[source.code] + 1)
        ] + [
            (source, total_pages[source.code] + 1, True)
            for source in NAMWON_SOURCES
            if total_pages[source.code] > 0
        ]

        def fetch_api_page(
            task: tuple[NamwonSource, int, bool]
        ) -> tuple[NamwonSource, int, bool, Optional[Mapping[str, Any]], str]:
            source, page, sentinel = task
            try:
                return source, page, sentinel, fetch_api(source, page), ""
            except Exception as exc:
                return source, page, sentinel, None, f"{type(exc).__name__}: {_clean(exc)}"

        if api_tasks:
            workers = min(max(1, int(max_workers)), NAMWON_MAX_WORKERS, len(api_tasks))
            with ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="namwon-api"
            ) as pool:
                api_results = list(pool.map(fetch_api_page, api_tasks))
            for source, page, sentinel, payload, error in api_results:
                api_requests += 1
                if sentinel:
                    sentinel_requests += 1
                if error or payload is None:
                    errors.append(f"{source.label} API page {page}: {error}")
                    continue
                try:
                    items, _total, _pages = _page_payload(
                        payload,
                        source,
                        page,
                        expected_total=totals[source.code],
                        expected_pages=total_pages[source.code],
                    )
                except Exception as exc:
                    errors.append(
                        f"{source.label} API page {page}: {type(exc).__name__}: {_clean(exc)}"
                    )
                    continue
                if sentinel:
                    if items:
                        errors.append(f"{source.label} API sentinel page {page} is not empty")
                else:
                    page_rows[(source.code, page)] = items

        def recheck(
            source: NamwonSource,
        ) -> tuple[NamwonSource, Optional[Mapping[str, Any]], str]:
            try:
                return source, fetch_api(source, 1), ""
            except Exception as exc:
                return source, None, f"{type(exc).__name__}: {_clean(exc)}"

        workers = min(max(1, int(max_workers)), len(NAMWON_SOURCES))
        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="namwon-recheck"
        ) as pool:
            rechecks = list(pool.map(recheck, NAMWON_SOURCES))
        for source, payload, error in rechecks:
            api_requests += 1
            page_one_rechecks += 1
            if error or payload is None:
                errors.append(f"{source.label} API page 1 recheck: {error}")
                continue
            try:
                items, _total, _pages = _page_payload(
                    payload,
                    source,
                    1,
                    expected_total=totals[source.code],
                    expected_pages=total_pages[source.code],
                )
                if _fingerprint(items) != _fingerprint(first_items[source.code]):
                    raise NamwonContractError("page one changed during complete traversal")
            except Exception as exc:
                errors.append(
                    f"{source.label} API page 1 recheck: {type(exc).__name__}: {_clean(exc)}"
                )

        all_items: list[tuple[NamwonSource, dict[str, Any]]] = []
        for source in NAMWON_SOURCES:
            if total_pages[source.code] == 0:
                continue
            for page in range(1, total_pages[source.code] + 1):
                items = page_rows.get((source.code, page))
                if items is None:
                    errors.append(f"{source.label} API page {page} is missing")
                    continue
                all_items.extend((source, item) for item in items)
            actual = sum(
                len(page_rows.get((source.code, page), []))
                for page in range(1, total_pages[source.code] + 1)
            )
            if actual != totals[source.code]:
                errors.append(
                    f"{source.label} API rows {actual} != declared total {totals[source.code]}"
                )

        parsed_rows: list[tuple[NamwonSource, dict[str, Any]]] = []
        invalid_count = 0
        for source, item in all_items:
            try:
                parsed_rows.append((source, _parse_item(target, source, item, cutoff)))
            except Exception as exc:
                invalid_count += 1
                errors.append(
                    f"{source.label} item {_clean(item.get('itemUid')) or '?'}: "
                    f"{type(exc).__name__}: {_clean(exc)}"
                )

        identities = [
            _clean(row.get("raw_fields", {}).get("item_uid"))
            for _source_item, row in parsed_rows
        ]
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count:
            errors.append(f"duplicate itemUid across education sources: {duplicate_count}")

        historical_invalid_rows = [
            (source, row)
            for source, row in parsed_rows
            if bool(row.get("raw_fields", {}).get("historical_invalid"))
        ]
        valid_rows = [
            (source, row)
            for source, row in parsed_rows
            if not bool(row.get("raw_fields", {}).get("historical_invalid"))
        ]
        current_rows = [
            (source, row)
            for source, row in valid_rows
            if date.fromisoformat(row["end_date"]) >= cutoff
        ]
        expired_count = len(valid_rows) - len(current_rows)
        if int(detail_limit) < len(current_rows):
            source_cap_reached = True
            errors.append(
                f"detail_limit cap {int(detail_limit)} is below required {len(current_rows)} details"
            )

        detailed_rows: list[dict[str, Any]] = []
        detail_errors: list[str] = []
        if not errors and current_rows:
            detail_attempts = len(current_rows)

            def fetch_detail(
                pair: tuple[NamwonSource, dict[str, Any]]
            ) -> tuple[dict[str, Any], bool, str]:
                source, row = pair
                identity = _clean(row.get("raw_fields", {}).get("item_uid"))
                try:
                    detail = fetch_html(_clean(row.get("raw_url")))
                    _enrich_detail(row, source, detail)
                    return row, True, ""
                except Exception as exc:
                    return row, False, (
                        f"item {identity} detail: {type(exc).__name__}: {_clean(exc)}"
                    )

            workers = min(
                max(1, int(max_workers)), NAMWON_MAX_WORKERS, len(current_rows)
            )
            with ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="namwon-detail"
            ) as pool:
                results = list(pool.map(fetch_detail, current_rows))
            for row, fetched, error in results:
                detail_pages += int(fetched)
                if error:
                    detail_errors.append(error)
                elif fetched:
                    detailed_rows.append(row)
        errors.extend(detail_errors)
        if len(detailed_rows) != len(current_rows):
            errors.append(
                f"detail rows {len(detailed_rows)} != required {len(current_rows)}"
            )

        semantic: dict[tuple[str, ...], list[str]] = {}
        for row in detailed_rows:
            signature = (
                _normalized(row.get("title")),
                _clean(row.get("start_date")),
                _clean(row.get("end_date")),
                _normalized(row.get("branch")),
                _normalized(row.get("schedule_raw")),
            )
            semantic.setdefault(signature, []).append(
                _clean(row.get("provider_course_id"))
            )
        semantic_duplicate_count = sum(
            len(values) - 1 for values in semantic.values() if len(values) > 1
        )
        if semantic_duplicate_count:
            errors.append(
                f"semantic duplicate current education rows: {semantic_duplicate_count}"
            )

        cleaned = detailed_rows
        if not errors:
            try:
                deduped = list(current_dedupe(cleaned))
            except Exception as exc:
                errors.append(f"dedupe failed: {type(exc).__name__}: {_clean(exc)}")
                deduped = []
            if len(deduped) != len(cleaned):
                errors.append(
                    f"dedupe changed complete row count {len(cleaned)} to {len(deduped)}"
                )
            cleaned = deduped

        expected_data_pages = sum(total_pages.values()) + sum(
            1 for value in total_pages.values() if value == 0
        )
        pagination_complete = (
            not errors
            and len(page_rows)
            == sum(max(1, value) for value in total_pages.values())
            and sentinel_requests
            == sum(1 for value in total_pages.values() if value > 0)
            and page_one_rechecks == len(NAMWON_SOURCES)
        )
        details_complete = (
            not detail_errors
            and not source_cap_reached
            and detail_pages == len(current_rows)
            and len(detailed_rows) == len(current_rows)
        )
        snapshot_complete = pagination_complete and details_complete and not errors
        if not snapshot_complete:
            cleaned = []

        category_source_counts = Counter(
            source.label for source, _row in parsed_rows
        )
        category_current_counts = Counter(
            source.label for source, _row in current_rows
        )
        current_status_counts = Counter(
            _clean(row.get("raw_fields", {}).get("source_status"))
            for _source_item, row in current_rows
        )
        branch_counts = Counter(_clean(row.get("branch")) for row in detailed_rows)
        request_count = (
            root_requests + landing_requests + api_requests + detail_attempts
        )
        meta: dict[str, Any] = {
            "pages": api_requests,
            "request_count": request_count,
            "root_requests": root_requests,
            "landing_requests": landing_requests,
            "api_requests": api_requests,
            "data_pages": expected_data_pages,
            "sentinel_requests": sentinel_requests,
            "page_one_rechecks": page_one_rechecks,
            "detail_attempts": detail_attempts,
            "detail_pages": detail_pages,
            "source_count": len(NAMWON_SOURCES),
            "source_rows": len(all_items),
            "valid_count": len(valid_rows),
            "invalid_count": invalid_count,
            "historical_invalid_count": len(historical_invalid_rows),
            "historical_invalid_ids": [
                _clean(row.get("raw_fields", {}).get("item_uid"))
                for _source_item, row in historical_invalid_rows
            ],
            "validated_count": len(parsed_rows),
            "unique_id_count": len(set(identities)),
            "duplicate_count": duplicate_count,
            "semantic_duplicate_count": semantic_duplicate_count,
            "expired_count": expired_count,
            "current_count": len(current_rows),
            "returned_count": len(cleaned),
            "source_totals": totals,
            "source_page_counts": total_pages,
            "category_source_counts": dict(category_source_counts),
            "category_current_counts": dict(category_current_counts),
            "current_status_counts": dict(current_status_counts),
            "branch_counts": dict(branch_counts),
            "pagination_detected": any(value > 1 for value in total_pages.values()),
            "pagination_complete": pagination_complete,
            "pagination_exhausted": pagination_complete,
            "details_complete": details_complete,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": snapshot_complete,
            "source_cap_reached": source_cap_reached,
            "reservation_discovery_links": sum(
                bool(row.get("application_url")) for row in detailed_rows
            ),
            "recursion_depth": 0,
            "no_current_data": snapshot_complete and not current_rows,
            "no_current_reason": (
                "all complete Namwon municipal education API sources have no current/future rows"
                if snapshot_complete and not current_rows
                else ""
            ),
        }
        if errors:
            meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return cleaned, NAMWON_PARSER, meta
    finally:
        for value in sessions:
            _close_quietly(value)


collect_namwon_target = collect_namwon_education_courses
collect = collect_namwon_education_courses


__all__ = [
    "NAMWON_API_PAGE_SIZE",
    "NAMWON_API_URL",
    "NAMWON_CANDIDATE_ID",
    "NAMWON_HOST",
    "NAMWON_MAX_WORKERS",
    "NAMWON_MUNICIPALITY_CODE",
    "NAMWON_MUNICIPALITY_NAME",
    "NAMWON_PARSER",
    "NAMWON_PATH",
    "NAMWON_PROVIDER",
    "NAMWON_ROOT_URL",
    "NAMWON_SOURCES",
    "NAMWON_URL",
    "NamwonContractError",
    "NamwonSource",
    "collect",
    "collect_namwon_education_courses",
    "collect_namwon_target",
    "is_namwon_target",
    "is_target",
    "namwon_api_params",
    "namwon_detail_url",
    "namwon_landing_url",
]
