"""Fail-closed collectors for Suncheon City's official education sources.

The municipality exposes three independent public education inventories:

* the lifelong-learning portal, which is a complete historical catalogue;
* the ``Suncheon reservation`` education menus, whose product pages must be
  joined with their public reservation calendars; and
* the Garden Support Centre's garden-education catalogue.

The lifelong catalogue is the canonical owner for the ``mode=search`` and
``mode=view`` aliases.  The reservation collector deliberately excludes the
swimming and community-sports menus even though the site nests them below
``/yeyak/edu``.  The garden article found by search is only a discovery page;
the linked Garden Support Centre table is the canonical education source.

Every collector proves its list boundary and an immediate sentinel before it
publishes a snapshot.  Production callers must inject the shared managed
fetcher and SafeSession factory.  No request disables TLS verification.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, NavigableString


SUNCHEON_MUNICIPALITY_CODE = "1215000000"
SUNCHEON_MUNICIPALITY_NAME = "전남광주통합특별시 순천시"
SUNCHEON_REGION = "전남광주통합특별시"

SUNCHEON_LMS_PROVIDER = "MUNI_LMS_SCHC_GO_KR_A117B76B"
SUNCHEON_LMS_URL = "https://lms.schc.go.kr/lms/class_01.do"
SUNCHEON_LMS_HOST = "lms.schc.go.kr"
SUNCHEON_LMS_PATH = "/lms/class_01.do"
SUNCHEON_LMS_PAGE_SIZE = 8
SUNCHEON_LMS_PARSER = (
    "suncheon_lms_complete_history+page1_replay_sentinel+current_detail"
)

SUNCHEON_RESERVATION_PROVIDER = "MUNI_WWW_SC_GO_KR_84C9C74F"
SUNCHEON_RESERVATION_URL = "https://www.sc.go.kr/yeyak/edu/0008/0001/"
SUNCHEON_RESERVATION_HOST = "www.sc.go.kr"
SUNCHEON_RESERVATION_PATH = "/yeyak/edu/0008/0001/"
SUNCHEON_RESERVATION_BASE = "https://www.suncheon.go.kr"
SUNCHEON_RESERVATION_PARSER = (
    "suncheon_reservation_education_sources+empty_sentinels+calendar_details"
)

SUNCHEON_GARDEN_PROVIDER = "MUNI_SCBAY_SUNCHEON_GO_KR_CC4EA34E"
SUNCHEON_GARDEN_URL = "https://scbay.suncheon.go.kr/gdcenter/0003/0001/"
SUNCHEON_GARDEN_HOST = "scbay.suncheon.go.kr"
SUNCHEON_GARDEN_PATH = "/gdcenter/0003/0001/"
SUNCHEON_GARDEN_PARSER = (
    "suncheon_garden_complete_table+query_empty_sentinel+auth_gate_detail"
)

SUNCHEON_LMS_CANDIDATE_ID = "MUNI_IR_0DA3E99601FA"
SUNCHEON_LMS_SEARCH_ALIAS_CANDIDATE_ID = "MUNI_IR_ED90929233DE"
SUNCHEON_RESERVATION_CANDIDATE_ID = "MUNI_IR_7CBC25EA1083"
SUNCHEON_GARDEN_DISCOVERY_CANDIDATE_ID = "MUNI_IR_D60A800C9D11"

SUNCHEON_LMS_DUPLICATE_ALIAS_URLS = (
    "https://lms.schc.go.kr/lms/class_01.do?mode=search",
    "https://lms.schc.go.kr/lms/class_01.do?mode=view&iClassIdx=2336",
    "https://lms.schc.go.kr/lms/class_01.do?mode=view&iClassIdx=2320",
)
SUNCHEON_RESERVATION_DUPLICATE_ALIAS_URLS = (
    "https://www.suncheon.go.kr/yeyak/edu",
    "https://www.suncheon.go.kr/yeyak/edu/0008/0001/",
    "https://www.sc.go.kr/yeyak/edu/0008/0001/",
)
SUNCHEON_RESERVATION_INTERNAL_EDUCATION_URLS = (
    "https://www.suncheon.go.kr/yeyak/edu/0008/0001/",
    "https://www.suncheon.go.kr/yeyak/safe/0012/",
    "https://www.suncheon.go.kr/yeyak/edu/0015/0002/",
)
SUNCHEON_WRONG_CATEGORY_URLS = (
    "https://www.suncheon.go.kr/yeyak/edu/swim/0001/",
    "https://www.suncheon.go.kr/yeyak/edu/swim/0002/",
    "https://www.suncheon.go.kr/yeyak/edu/sport/0001/",
    "https://www.suncheon.go.kr/yeyak/edu/sport/0002/",
)
SUNCHEON_STATIC_OR_DISCOVERY_URLS = (
    "https://www.suncheon.go.kr/yeyak/",
    "https://scbay.suncheon.go.kr/garden/0020/0013/0008/",
    "https://www.suncheon.go.kr/kr/news/0006/0001/?mode=view&seq=68887",
)


@dataclass(frozen=True)
class SuncheonReservationSource:
    code: str
    path: str
    product_id: str
    product_name: str
    branch: str
    category: str
    target: str
    fee: str
    info_url: str


SUNCHEON_RESERVATION_SOURCES: tuple[SuncheonReservationSource, ...] = (
    SuncheonReservationSource(
        "digital_literacy",
        "/yeyak/edu/0008/0001/",
        "RSV_A17",
        "시민정보화교육",
        "시청 교육장",
        "시민정보화교육",
        "순천시민",
        "무료",
        "https://www.suncheon.go.kr/yeyak/edu/0008/0002/",
    ),
    SuncheonReservationSource(
        "child_safety",
        "/yeyak/safe/0012/",
        "RSV_A18",
        "어린이안전교육",
        "어린이안전교육",
        "안전교육",
        "어린이 단체",
        "무료",
        "https://www.suncheon.go.kr/yeyak/safe/0011/",
    ),
    SuncheonReservationSource(
        "cpr",
        "/yeyak/edu/0015/0002/",
        "RSV0000030",
        "심폐소생술 상설교육",
        "연향건강생활지원센터",
        "심폐소생술교육",
        "만 10세 이상 시민",
        "무료",
        "https://www.suncheon.go.kr/yeyak/edu/0015/0001/",
    ),
)


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

SUNCHEON_SESSION_REQUEST_LIMIT = 120
_SPACE_RE = re.compile(r"\s+")
_LMS_VIEW_RE = re.compile(r"goView\('([^']+)',\s*'(\d+)'\)")
_LMS_SUMMARY_RE = re.compile(
    r"\[\s*전체\s*([\d,]+)\s*건\s*,\s*(\d+)\s*/\s*(\d+)\s*page\s*\]"
)
_SHORT_DATE_RE = re.compile(
    r"(?<!\d)(\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"
)
_FULL_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"
)
_RESERVATION_CARD_RE = re.compile(
    r"fncRsrvIng\('([^']+)'\s*,\s*'([^']+)'\s*,\s*'[^']*'\s*,\s*'([^']*)'\s*,\s*'([^']+)'\)"
)
_PAGE_MOVE_RE = re.compile(r"fncPageMove\('?(\d+)'?\)")
_CALENDAR_ENDPOINT_RE = re.compile(
    r"comAjax\.setUrl\(\"([^\"]*selectCalendarList\.json[^\"]*)\""
)
_GARDEN_ORDER_RE = re.compile(r"goOrder\('(\d+)'\)")
_CAPACITY_PAIR_RE = re.compile(r"([\d,]+)\s*/\s*([\d,]+)\s*명?")

_LMS_HEADERS = (
    "번호",
    "장소",
    "강좌명 (강사명)",
    "모집인원",
    "수강료",
    "접수기간",
    "교육일정",
    "모집방법 접수상태",
    "보기",
)
_LMS_REQUIRED_DETAIL_LABELS = frozenset(
    {
        "강의명",
        "강사",
        "모집구분",
        "교육대상",
        "접수기간",
        "수강료",
        "교육기간",
        "모집정원",
        "교육기관",
        "교육장소",
        "문의전화",
        "강의일수",
        "교육시간대",
        "교육일정",
        "교육내용",
    }
)
_GARDEN_HEADERS = (
    "교육명",
    "접수기간 (교육기간)",
    "요일/시간",
    "접수/정원",
    "수강료",
    "접수상태",
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _text(node: Any) -> str:
    if node is None:
        return ""
    getter = getattr(node, "get_text", None)
    return _clean(getter(" ", strip=True) if callable(getter) else node)


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", _clean(value).lower())


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


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
        }
    )
    return current


def _default_fetcher(current_session: Any, url: str, timeout: int) -> Any:
    return current_session.get(url, timeout=timeout, allow_redirects=False)


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        if not value:
            raise ValueError("empty HTML response")
        return BeautifulSoup(value, "lxml")
    status = int(getattr(value, "status_code", 200))
    if 300 <= status < 400:
        raise ValueError("HTTP redirects are not accepted")
    raise_for_status = getattr(value, "raise_for_status", None)
    if callable(raise_for_status):
        raise_for_status()
    content = getattr(value, "content", None)
    if content is None:
        content = getattr(value, "text", None)
    if not content:
        raise ValueError("empty HTTP response")
    return BeautifulSoup(content, "lxml")


def _coerce_json(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    status = int(getattr(value, "status_code", 200))
    if 300 <= status < 400:
        raise ValueError("HTTP redirects are not accepted")
    raise_for_status = getattr(value, "raise_for_status", None)
    if callable(raise_for_status):
        raise_for_status()
    json_method = getattr(value, "json", None)
    if callable(json_method):
        payload = json_method()
    else:
        raw = getattr(value, "text", None) or getattr(value, "content", None)
        payload = json.loads(raw)
    if not isinstance(payload, Mapping):
        raise ValueError("JSON response is not an object")
    return payload


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


class _Requester:
    """Rotate injected sessions before the shared per-session request cap."""

    def __init__(
        self,
        fetcher: Fetcher,
        session_factory: SessionFactory,
        timeout: int,
    ) -> None:
        self.fetcher = fetcher
        self.session_factory = session_factory
        self.timeout = timeout
        self.current: Any = None
        self.current_calls = 0
        self.calls = 0
        self.sessions = 0

    def reserve(self, needed: int = 1) -> None:
        needed = max(1, int(needed))
        if (
            self.current is None
            or self.current_calls + needed > SUNCHEON_SESSION_REQUEST_LIMIT
        ):
            _close_quietly(self.current)
            self.current = self.session_factory()
            self.current_calls = 0
            self.sessions += 1
            headers = getattr(self.current, "headers", None)
            if headers is not None and hasattr(headers, "update"):
                headers.update(
                    {
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
                        ),
                        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
                    }
                )

    def _used(self) -> None:
        self.current_calls += 1
        self.calls += 1

    def get(self, url: str) -> BeautifulSoup:
        self.reserve()
        self._used()
        return _coerce_soup(self.fetcher(self.current, url, self.timeout))

    def post_html(self, url: str, data: Mapping[str, Any]) -> BeautifulSoup:
        self.reserve()
        self._used()
        response = self.current.post(
            url,
            data=dict(data),
            timeout=self.timeout,
            allow_redirects=False,
        )
        return _coerce_soup(response)

    def post_json(
        self,
        url: str,
        payload: Mapping[str, Any],
        *,
        referer: str,
    ) -> Mapping[str, Any]:
        self.reserve()
        self._used()
        response = self.current.post(
            url,
            data=json.dumps(dict(payload), ensure_ascii=False),
            headers={
                "Content-Type": "application/json;charset=UTF-8",
                "Accept": "application/json",
                "Referer": referer,
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=self.timeout,
            allow_redirects=False,
        )
        return _coerce_json(response)

    def close(self) -> None:
        _close_quietly(self.current)
        self.current = None

    def renew(self) -> None:
        """Discard a broken transport session before a bounded retry."""

        _close_quietly(self.current)
        self.current = None
        self.current_calls = 0


def _failure(parser: str, reason: str) -> dict[str, Any]:
    return {
        "parser": parser,
        "pages": 0,
        "list_requests": 0,
        "request_count": 0,
        "session_count": 0,
        "detail_pages": 0,
        "source_total": 0,
        "source_rows": 0,
        "current_count": 0,
        "returned_count": 0,
        "duplicate_count": 0,
        "duplicate_url_count": 0,
        "semantic_duplicate_count": 0,
        "detail_errors": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "configured_collection_error": reason,
    }


def _strict_target(
    target: Any,
    *,
    provider: str,
    host: str,
    path: str,
) -> bool:
    parsed = urlparse(_target_url(target))
    return (
        _provider(target) == provider
        and parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == host
        and parsed.port is None
        and parsed.path == path
        and not parsed.query
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


def is_suncheon_lms_target(target: Any) -> bool:
    return _strict_target(
        target,
        provider=SUNCHEON_LMS_PROVIDER,
        host=SUNCHEON_LMS_HOST,
        path=SUNCHEON_LMS_PATH,
    )


def is_suncheon_reservation_target(target: Any) -> bool:
    return _strict_target(
        target,
        provider=SUNCHEON_RESERVATION_PROVIDER,
        host=SUNCHEON_RESERVATION_HOST,
        path=SUNCHEON_RESERVATION_PATH,
    )


def is_suncheon_garden_target(target: Any) -> bool:
    return _strict_target(
        target,
        provider=SUNCHEON_GARDEN_PROVIDER,
        host=SUNCHEON_GARDEN_HOST,
        path=SUNCHEON_GARDEN_PATH,
    )


def is_suncheon_education_target(target: Any) -> bool:
    return (
        is_suncheon_lms_target(target)
        or is_suncheon_reservation_target(target)
        or is_suncheon_garden_target(target)
    )


def _dates(value: Any, *, short_year: bool) -> list[date]:
    regex = _SHORT_DATE_RE if short_year else _FULL_DATE_RE
    result: list[date] = []
    for match in regex.finditer(_clean(value)):
        year, month, day = (int(part) for part in match.groups())
        if short_year:
            year += 2000
        try:
            result.append(date(year, month, day))
        except ValueError:
            continue
    return result


def _period(start: Optional[date], end: Optional[date]) -> str:
    if start is None or end is None:
        return ""
    return f"{start.isoformat()} ~ {end.isoformat()}"


def _first_number(value: Any) -> Optional[int]:
    match = re.search(r"[\d,]+", _clean(value))
    return int(match.group(0).replace(",", "")) if match else None


def _capacity_pair(value: Any) -> tuple[Optional[int], Optional[int]]:
    match = _CAPACITY_PAIR_RE.search(_clean(value))
    if not match:
        return None, None
    return (
        int(match.group(1).replace(",", "")),
        int(match.group(2).replace(",", "")),
    )


def _branch_code(value: Any) -> str:
    digest = hashlib.sha1(_normalized(value).encode("utf-8")).hexdigest()[:12].upper()
    return f"SUNCHEON_BRANCH_{digest}"


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _semantic_signature(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        _normalized(row.get("title")),
        _normalized(row.get("branch")),
        _clean(row.get("period")),
        _normalized(row.get("schedule_raw")),
        _normalized(row.get("venue_name")),
    )


def suncheon_lms_list_url(page: Any = 1) -> str:
    raw = _clean(page)
    if not raw.isdigit() or int(raw) < 1:
        return ""
    if int(raw) == 1:
        return SUNCHEON_LMS_URL
    return f"{SUNCHEON_LMS_URL}?{urlencode({'nowPage': int(raw)})}"


def suncheon_lms_detail_url(identity: Any, group: Any = "1") -> str:
    raw_identity = _clean(identity)
    raw_group = _clean(group)
    if not raw_identity.isdigit() or not raw_group.isdigit():
        return ""
    return f"{SUNCHEON_LMS_URL}?" + urlencode(
        (("mode", "view"), ("iClassIdx", raw_identity), ("iEduLgrpCd", raw_group))
    )


def _lms_status(value: Any) -> str:
    raw = _clean(value)
    if "접수준비" in raw or "접수예정" in raw:
        return "SCHEDULED"
    if "대기자접수중" in raw or "접수중" in raw:
        return "OPEN"
    if "접수마감" in raw or "정원마감" in raw:
        return "CLOSED"
    if "취소" in raw or "폐강" in raw:
        return "CANCELLED"
    return ""


def _lms_summary(soup: BeautifulSoup) -> tuple[Optional[int], Optional[int], Optional[int]]:
    table = soup.select_one("table.w100")
    if table is None:
        return None, None, None
    container = table.parent
    sibling = container.find_previous_sibling() if container is not None else None
    match = _LMS_SUMMARY_RE.search(_text(sibling))
    if not match:
        return None, None, None
    return (
        int(match.group(1).replace(",", "")),
        int(match.group(2)),
        int(match.group(3)),
    )


def _before_br(anchor: Any) -> str:
    values: list[str] = []
    for node in getattr(anchor, "contents", []):
        if getattr(node, "name", None) == "br":
            break
        if isinstance(node, NavigableString):
            values.append(str(node))
        elif getattr(node, "get_text", None):
            values.append(node.get_text(" ", strip=True))
    return _clean(" ".join(values))


def _lms_parse_page(
    soup: BeautifulSoup,
    provider: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    table = soup.select_one("table.w100")
    if table is None:
        return [], ["missing LMS course table"]
    headers = tuple(_text(node) for node in table.select("thead th"))
    if headers != _LMS_HEADERS:
        return [], [f"LMS headers changed: {headers!r}"]
    result: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, tr in enumerate(table.select("tbody > tr"), start=1):
        cells = tr.find_all("td", recursive=False)
        if len(cells) != 9:
            errors.append(f"LMS row {index}: expected 9 cells, got {len(cells)}")
            continue
        anchor = cells[2].select_one("a[href*='goView']")
        match = _LMS_VIEW_RE.search(_clean(anchor.get("href")) if anchor else "")
        if not match:
            errors.append(f"LMS row {index}: missing strict goView identity")
            continue
        group, identity = match.groups()
        title = _before_br(anchor)
        apply_dates = _dates(_text(cells[5]), short_year=True)
        education_dates = _dates(_text(cells[6]), short_year=True)
        if not title or len(apply_dates) != 2 or len(education_dates) != 2:
            errors.append(f"LMS {identity}: incomplete title or date ranges")
            continue
        apply_start, apply_end = apply_dates
        start, end = education_dates
        if apply_start > apply_end or start > end:
            errors.append(f"LMS {identity}: invalid date range")
            continue
        source_status = _text(cells[7])
        status = _lms_status(source_status)
        if not status:
            errors.append(f"LMS {identity}: unknown status {source_status!r}")
            continue
        branch = _text(cells[1])
        raw_url = suncheon_lms_detail_url(identity, group)
        capacity_text = _text(cells[3])
        row: dict[str, Any] = {
            "provider": provider,
            "provider_course_id": f"{provider}:class:{identity}",
            "title": title,
            "branch": branch,
            "branch_code": _branch_code(branch),
            "category": "평생학습",
            "raw_url": raw_url,
            "reservation_available": status == "OPEN",
            "status": status,
            "fee": _text(cells[4]),
            "period": _period(start, end),
            "apply_period": _period(apply_start, apply_end),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "apply_start_date": apply_start.isoformat(),
            "apply_end_date": apply_end.isoformat(),
            "schedule_raw": _text(cells[6]),
            "target": "전체",
            "capacity": capacity_text,
            "capacity_current": None,
            "capacity_total": _first_number(capacity_text),
            "venue_name": branch,
            "room": "",
            "description": title,
            "application_type": "ONLINE_RESERVATION",
            "municipality_code": SUNCHEON_MUNICIPALITY_CODE,
            "municipality_name": SUNCHEON_MUNICIPALITY_NAME,
            "region": SUNCHEON_REGION,
            "collection_type": "static_html+detail_html",
            "source_group": "municipal_lifelong_learning",
            "raw_fields": {
                "parser": SUNCHEON_LMS_PARSER,
                "class_idx": identity,
                "education_group": group,
                "list_number": _text(cells[0]),
                "list_branch": branch,
                "source_status": source_status,
                "list_schedule": _text(cells[6]),
            },
        }
        if status == "OPEN":
            row["application_url"] = raw_url
        else:
            row["raw_fields"]["clear_application_url"] = True
        result.append(row)
    return result, errors


def _table_pairs(soup: BeautifulSoup) -> dict[str, str]:
    result: dict[str, str] = {}
    for table in soup.select("table.w100"):
        for tr in table.select("tr"):
            key = ""
            for cell in tr.find_all(["th", "td"], recursive=False):
                if cell.name == "th":
                    key = _text(cell)
                elif key:
                    result[key] = _text(cell)
                    key = ""
    return result


def _lms_detail_contract(row: dict[str, Any], soup: BeautifulSoup) -> tuple[list[str], bool]:
    identity = _clean(row.get("provider_course_id")).rsplit(":", 1)[-1]
    errors: list[str] = []
    pairs = _table_pairs(soup)
    missing = _LMS_REQUIRED_DETAIL_LABELS - set(pairs)
    if missing:
        return [f"{identity}: missing LMS detail labels {sorted(missing)}"], False
    if pairs["강의명"] != _clean(row.get("title")):
        errors.append(f"{identity}: detail title mismatch")
    detail_education_dates = _dates(pairs["교육기간"], short_year=False)
    if len(detail_education_dates) != 2 or _period(*detail_education_dates) != _clean(
        row.get("period")
    ):
        errors.append(f"{identity}: detail education period mismatch")
    list_capacity = _first_number(row.get("capacity"))
    detail_capacity = _first_number(pairs["모집정원"])
    if list_capacity != detail_capacity:
        errors.append(f"{identity}: detail capacity mismatch")

    detail_apply_dates = _dates(pairs["접수기간"], short_year=False)
    detail_apply_period = (
        _period(*detail_apply_dates) if len(detail_apply_dates) == 2 else ""
    )
    apply_mismatch = bool(
        not detail_apply_period or detail_apply_period != _clean(row.get("apply_period"))
    )
    if not detail_apply_period:
        errors.append(f"{identity}: invalid detail application period")

    actions = {
        _clean(anchor.get("href"))
        for anchor in soup.select("a[href^='javascript:']")
        if _text(anchor) == "수강신청"
    }
    open_action = any("goSubmit" in action for action in actions)
    if row.get("status") == "OPEN" and not open_action:
        errors.append(f"{identity}: open course has no goSubmit application action")
    if row.get("status") in {"SCHEDULED", "CANCELLED"} and open_action:
        errors.append(
            f"{identity}: unavailable course unexpectedly has goSubmit action"
        )
    closed_action_retained = bool(
        row.get("status") == "CLOSED" and open_action
    )

    branch = pairs["교육기관"]
    row["branch"] = branch
    row["branch_code"] = _branch_code(branch)
    row["room"] = pairs["교육장소"]
    row["venue_name"] = pairs["교육장소"] or branch
    row["target"] = pairs["교육대상"]
    row["fee"] = pairs["수강료"]
    row["capacity"] = pairs["모집정원"]
    row["capacity_total"] = detail_capacity
    row["schedule_raw"] = pairs["교육일정"] or pairs["교육시간대"]
    row["description"] = row["title"]
    row["raw_fields"].update(
        {
            "detail_branch": branch,
            "detail_apply_period": detail_apply_period,
            "detail_apply_period_mismatch": apply_mismatch,
            "detail_labels": sorted(pairs),
            "application_actions": sorted(actions),
            "closed_application_action_retained": closed_action_retained,
            "instructor_discarded": True,
            "contact_discarded": True,
            "free_form_description_discarded": True,
        }
    )
    return errors, apply_mismatch


def collect_suncheon_lms_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 400,
    detail_limit: int = 300,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    if not is_suncheon_lms_target(target):
        return [], SUNCHEON_LMS_PARSER, _failure(
            SUNCHEON_LMS_PARSER,
            "target does not match the canonical Suncheon LMS catalogue",
        )
    if fetcher is None or session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], SUNCHEON_LMS_PARSER, _failure(
                SUNCHEON_LMS_PARSER,
                "managed fetcher and session_factory injection are required",
            )
        fetcher = fetcher or _default_fetcher
        session_factory = session_factory or _default_session_factory

    cutoff = _today(today)
    allowed_pages = max(0, int(max_pages))
    allowed_details = max(0, int(detail_limit))
    requester = _Requester(fetcher, session_factory, timeout)
    errors: list[str] = []
    page_soups: dict[int, BeautifulSoup] = {}
    page_counts: dict[int, int] = {}
    all_rows: list[dict[str, Any]] = []
    total = 0
    last = 0
    required_list_requests = 0
    detail_pages = 0
    detail_errors = 0
    detail_mismatch_count = 0
    source_cap_reached = False
    sentinel_mode = ""
    try:
        try:
            page_soups[1] = requester.get(suncheon_lms_list_url(1))
        except Exception as exc:
            errors.append(f"LMS page 1: fetch {type(exc).__name__}")
        if 1 in page_soups:
            source_total, current_page, source_last = _lms_summary(page_soups[1])
            if source_total is None or current_page != 1 or source_last is None:
                errors.append("LMS page 1: missing official total/page contract")
            else:
                total = source_total
                last = source_last
                expected_last = max(1, math.ceil(total / SUNCHEON_LMS_PAGE_SIZE))
                if total <= 0 or last != expected_last:
                    errors.append(
                        f"LMS official page count {last} != expected {expected_last}"
                    )
                advertised = {
                    int(match.group(1))
                    for anchor in page_soups[1].select(".pagination a[onclick]")
                    if (match := re.search(r"nextPage\((\d+)\)", _clean(anchor.get("onclick"))))
                }
                if not advertised or max(advertised) != last:
                    errors.append("LMS first-page navigation contract changed")
                required_list_requests = last + 1
                if required_list_requests > allowed_pages:
                    source_cap_reached = True
                    errors.append(
                        f"max_pages cap allows {allowed_pages} of "
                        f"{required_list_requests} required pages and sentinel"
                    )
        if not errors:
            for page in range(2, last + 2):
                try:
                    page_soups[page] = requester.get(suncheon_lms_list_url(page))
                except Exception as exc:
                    errors.append(f"LMS page {page}: fetch {type(exc).__name__}")
                    break

        if not errors:
            for page in range(1, last + 1):
                soup = page_soups.get(page)
                if soup is None:
                    errors.append(f"LMS page {page}: missing fetched page")
                    continue
                source_total, source_page, source_last = _lms_summary(soup)
                if (source_total, source_page, source_last) != (total, page, last):
                    errors.append(f"LMS page {page}: official pagination changed")
                rows, parse_errors = _lms_parse_page(soup, SUNCHEON_LMS_PROVIDER)
                errors.extend(f"page {page}: {message}" for message in parse_errors)
                expected_count = min(
                    SUNCHEON_LMS_PAGE_SIZE,
                    max(0, total - (page - 1) * SUNCHEON_LMS_PAGE_SIZE),
                )
                page_counts[page] = len(rows)
                if len(rows) != expected_count:
                    errors.append(
                        f"LMS page {page}: expected {expected_count} rows, got {len(rows)}"
                    )
                all_rows.extend(rows)

            sentinel = page_soups.get(last + 1)
            if sentinel is None:
                errors.append("LMS sentinel page was not fetched")
            else:
                sentinel_total, sentinel_page, sentinel_last = _lms_summary(sentinel)
                sentinel_rows, sentinel_errors = _lms_parse_page(
                    sentinel, SUNCHEON_LMS_PROVIDER
                )
                errors.extend(
                    f"sentinel: {message}" for message in sentinel_errors
                )
                page_counts[last + 1] = len(sentinel_rows)
                first_rows, _ = _lms_parse_page(
                    page_soups[1], SUNCHEON_LMS_PROVIDER
                )
                sentinel_ids = [row["provider_course_id"] for row in sentinel_rows]
                first_ids = [row["provider_course_id"] for row in first_rows]
                if (
                    (sentinel_total, sentinel_page, sentinel_last)
                    != (total, 1, last)
                    or sentinel_ids != first_ids
                ):
                    errors.append("LMS sentinel is not an exact page-1 replay")
                else:
                    sentinel_mode = "page1_replay"

        if len(all_rows) != total:
            errors.append(f"LMS official total {total} != parsed rows {len(all_rows)}")
        identities = [_clean(row.get("provider_course_id")) for row in all_rows]
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count:
            errors.append(f"{duplicate_count} duplicate LMS identities")
        urls = [_clean(row.get("raw_url")) for row in all_rows]
        duplicate_url_count = len(urls) - len(set(urls))
        if duplicate_url_count:
            errors.append(f"{duplicate_url_count} duplicate LMS URLs")

        current_rows: list[dict[str, Any]] = []
        expired_count = 0
        for row in all_rows:
            try:
                end = date.fromisoformat(_clean(row.get("end_date")))
            except ValueError:
                errors.append(f"{row.get('provider_course_id')}: invalid end date")
                continue
            if end < cutoff:
                expired_count += 1
            else:
                current_rows.append(row)
        if len(current_rows) > allowed_details:
            source_cap_reached = True
            errors.append(
                f"detail_limit cap allows {allowed_details} of "
                f"{len(current_rows)} required current/future details"
            )
        if not errors:
            for row in current_rows:
                try:
                    detail = requester.get(_clean(row.get("raw_url")))
                    detail_pages += 1
                    row_errors, mismatch = _lms_detail_contract(row, detail)
                    detail_errors += len(row_errors)
                    detail_mismatch_count += int(mismatch)
                    errors.extend(row_errors)
                except Exception as exc:
                    detail_errors += 1
                    errors.append(
                        f"{row.get('provider_course_id')}: detail fetch {type(exc).__name__}"
                    )

        signatures = [_semantic_signature(row) for row in current_rows]
        semantic_duplicate_count = len(signatures) - len(set(signatures))
        if semantic_duplicate_count:
            errors.append(
                f"{semantic_duplicate_count} duplicate current LMS semantic signatures"
            )
        result: list[dict[str, Any]] = []
        if not errors:
            deduper = dedupe_rows or _dedupe_default
            result = list(deduper(current_rows))
            if len(result) != len(current_rows):
                errors.append(
                    f"dedupe changed complete row count {len(current_rows)} to {len(result)}"
                )
                result = []

        branch_counts = Counter(_clean(row.get("branch")) for row in current_rows)
        status_counts = Counter(_clean(row.get("status")) for row in current_rows)
        snapshot_complete = not errors
        meta = {
            "parser": SUNCHEON_LMS_PARSER,
            "pages": len(page_soups),
            "list_requests": len(page_soups),
            "request_count": requester.calls,
            "session_count": requester.sessions,
            "detail_pages": detail_pages,
            "source_total": total,
            "source_rows": len(all_rows),
            "required_list_requests": required_list_requests,
            "page_counts": page_counts,
            "sentinel_page": last + 1 if last else 0,
            "sentinel_mode": sentinel_mode,
            "expired_count": expired_count,
            "current_count": len(current_rows),
            "returned_count": len(result),
            "branch_count": len(branch_counts),
            "branch_counts": dict(branch_counts),
            "status_counts": dict(status_counts),
            "duplicate_count": duplicate_count,
            "duplicate_url_count": duplicate_url_count,
            "semantic_duplicate_count": semantic_duplicate_count,
            "detail_errors": detail_errors,
            "detail_apply_period_mismatch_count": detail_mismatch_count,
            "closed_application_action_retained_count": sum(
                bool(
                    row.get("raw_fields", {}).get(
                        "closed_application_action_retained"
                    )
                )
                for row in current_rows
            ),
            "pagination_detected": last > 1,
            "pagination_complete": bool(
                snapshot_complete and len(page_soups) == required_list_requests
            ),
            "details_complete": bool(
                snapshot_complete and detail_pages == len(current_rows)
            ),
            "snapshot_complete": snapshot_complete,
            "source_cap_reached": source_cap_reached,
            "no_current_data": bool(snapshot_complete and not result),
            "no_current_reason": (
                "all complete Suncheon LMS courses have ended"
                if snapshot_complete and not result
                else ""
            ),
            "configured_collection_error": "; ".join(errors),
        }
        if errors:
            return [], SUNCHEON_LMS_PARSER, meta
        return result, SUNCHEON_LMS_PARSER, meta
    finally:
        requester.close()


def suncheon_reservation_list_url(
    source: SuncheonReservationSource,
    page: Any = 1,
) -> str:
    raw = _clean(page)
    if source not in SUNCHEON_RESERVATION_SOURCES or not raw.isdigit() or int(raw) < 1:
        return ""
    base = f"{SUNCHEON_RESERVATION_BASE}{source.path}"
    if int(raw) == 1:
        return base
    return f"{base}?{urlencode({'page': int(raw)})}"


def suncheon_reservation_application_url(detail_id: Any) -> str:
    raw = _clean(detail_id)
    if not re.fullmatch(r"[A-Z0-9_]+", raw):
        return ""
    return (
        f"{SUNCHEON_RESERVATION_BASE}/yeyak/program/redirect.do?"
        + urlencode({"dtlGoodsId": raw})
    )


def _reservation_page_count(soup: BeautifulSoup) -> int:
    values = {
        int(match.group(1))
        for anchor in soup.select("#cateList .pageNum a[href]")
        if (match := _PAGE_MOVE_RE.search(_clean(anchor.get("href"))))
    }
    return max(values or {1})


def _reservation_parse_page(
    soup: BeautifulSoup,
    source: SuncheonReservationSource,
    provider: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    container = soup.select_one("#cateList")
    if container is None:
        return [], [f"{source.code}: missing reservation product container"]
    result: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, li in enumerate(container.select(":scope > ul > li"), start=1):
        anchor = li.select_one("a[href*='fncRsrvIng']")
        match = _RESERVATION_CARD_RE.search(
            _clean(anchor.get("href")) if anchor else ""
        )
        if not match:
            errors.append(f"{source.code} row {index}: invalid reservation card action")
            continue
        product_id, detail_id, action_title, goods_type = match.groups()
        title = _text(li.select_one(".title"))
        category = _text(li.select_one(".cate"))
        if product_id != source.product_id:
            errors.append(
                f"{source.code} {detail_id}: product {product_id} != {source.product_id}"
            )
            continue
        if not title or title != _clean(action_title) or not category:
            errors.append(f"{source.code} {detail_id}: card title/category mismatch")
            continue
        raw_url = suncheon_reservation_application_url(detail_id)
        result.append(
            {
                "provider": provider,
                "provider_course_id": f"{provider}:product:{detail_id}",
                "title": title,
                "branch": source.branch,
                "branch_code": _branch_code(source.branch),
                "category": source.category,
                "raw_url": raw_url,
                "application_url": raw_url,
                "reservation_available": False,
                "status": "CLOSED",
                "fee": source.fee,
                "period": "",
                "apply_period": "",
                "start_date": "",
                "end_date": "",
                "apply_start_date": "",
                "apply_end_date": "",
                "schedule_raw": "",
                "target": source.target,
                "capacity": "",
                "capacity_current": None,
                "capacity_total": None,
                "venue_name": source.branch,
                "room": "",
                "description": title,
                "application_type": "ONLINE_RESERVATION",
                "municipality_code": SUNCHEON_MUNICIPALITY_CODE,
                "municipality_name": SUNCHEON_MUNICIPALITY_NAME,
                "region": SUNCHEON_REGION,
                "collection_type": "static_html+calendar_json",
                "source_group": "municipal_integrated_reservation",
                "raw_fields": {
                    "parser": SUNCHEON_RESERVATION_PARSER,
                    "source_code": source.code,
                    "product_id": product_id,
                    "detail_id": detail_id,
                    "goods_type": goods_type,
                    "source_category": category,
                    "info_url": source.info_url,
                },
                "_source": source,
                "_detail_id": detail_id,
                "_goods_type": goods_type,
            }
        )
    return result, errors


def _redirect_contract(
    row: Mapping[str, Any],
    soup: BeautifulSoup,
) -> tuple[dict[str, str], list[str]]:
    detail_id = _clean(row.get("_detail_id"))
    source = row.get("_source")
    errors: list[str] = []
    form = soup.select_one("form#listForm")
    if form is None or urlparse(_clean(form.get("action"))).path != "/yeyak/program/calendar01/index.jsp":
        return {}, [f"{detail_id}: invalid reservation redirect form"]
    values = {
        _clean(node.get("name")): _clean(node.get("value"))
        for node in form.select("input[name]")
    }
    if not isinstance(source, SuncheonReservationSource):
        errors.append(f"{detail_id}: missing reservation source binding")
    else:
        if values.get("rsvGoodsId") != source.product_id:
            errors.append(f"{detail_id}: redirect product mismatch")
    if values.get("dtlGoodsId") != detail_id:
        errors.append(f"{detail_id}: redirect detail identity mismatch")
    if not values.get("goodsSeCd"):
        errors.append(f"{detail_id}: redirect missing goods type")
    return values, errors


def _calendar_contract(
    row: dict[str, Any],
    payload: Mapping[str, Any],
    cutoff: date,
) -> tuple[list[str], int, int]:
    errors: list[str] = []
    source = row.get("_source")
    detail_id = _clean(row.get("_detail_id"))
    calendar = payload.get("calendarList")
    management = payload.get("rsvGoodsMgmt")
    if not isinstance(source, SuncheonReservationSource):
        return [f"{detail_id}: missing source binding"], 0, 0
    if not isinstance(calendar, list) or not isinstance(management, Mapping):
        return [f"{detail_id}: invalid calendar JSON contract"], 0, 0
    if _clean(management.get("rsvGoodsId")) != source.product_id:
        errors.append(f"{detail_id}: calendar product mismatch")
    if _clean(management.get("rsvGoodsNm")) != source.product_name:
        errors.append(f"{detail_id}: calendar product name mismatch")

    events: list[dict[str, Any]] = []
    past_events = 0
    seen: set[tuple[str, str, str, str]] = set()
    for index, event in enumerate(calendar, start=1):
        if not isinstance(event, Mapping):
            errors.append(f"{detail_id}: calendar event {index} is not an object")
            continue
        if _clean(event.get("url")) != "#" or not _clean(event.get("rsvYmd")):
            continue
        try:
            event_date = date.fromisoformat(_clean(event.get("rsvYmd")))
        except ValueError:
            errors.append(f"{detail_id}: invalid calendar date")
            continue
        event_detail = _clean(event.get("dtlGoodsId"))
        if event_detail and event_detail != detail_id:
            errors.append(f"{detail_id}: calendar event escaped detail identity")
            continue
        identity = (
            event_date.isoformat(),
            _clean(event.get("apntdtNo")),
            _clean(event.get("rsvStartTime")),
            _clean(event.get("rsvEndTime")),
        )
        if identity in seen:
            errors.append(f"{detail_id}: duplicate actionable calendar event")
            continue
        seen.add(identity)
        if event_date < cutoff:
            past_events += 1
            continue
        events.append(dict(event))

    if not events:
        row["raw_fields"].update(
            {
                "calendar_event_count": 0,
                "past_calendar_event_count": past_events,
                "calendar_horizon_days": _clean(payload.get("rsvPosblPd")),
                "management_status": _clean(management.get("sttusCd")),
            }
        )
        return errors, 0, past_events

    event_dates = sorted(date.fromisoformat(_clean(event["rsvYmd"])) for event in events)
    capacities: list[tuple[int, int]] = []
    schedule_parts: list[str] = []
    for event in events:
        current_capacity, total_capacity = _capacity_pair(event.get("title"))
        if current_capacity is not None and total_capacity is not None:
            capacities.append((current_capacity, total_capacity))
        timing = " ~ ".join(
            value
            for value in (
                _clean(event.get("rsvStartTime")),
                _clean(event.get("rsvEndTime")),
            )
            if value
        )
        schedule_parts.append(
            f"{_clean(event.get('rsvYmd'))}{(' ' + timing) if timing else ''}"
        )
    totals = {total for _current, total in capacities}
    if len(totals) > 1:
        errors.append(f"{detail_id}: inconsistent per-session capacities")
    total_capacity = next(iter(totals), None)
    row.update(
        {
            "reservation_available": True,
            "status": "OPEN",
            "period": _period(event_dates[0], event_dates[-1]),
            "start_date": event_dates[0].isoformat(),
            "end_date": event_dates[-1].isoformat(),
            "schedule_raw": ", ".join(schedule_parts),
            "capacity": (
                f"회차별 {total_capacity}명" if total_capacity is not None else "별도 안내"
            ),
            "capacity_current": None,
            "capacity_total": total_capacity,
        }
    )
    row["raw_fields"].update(
        {
            "calendar_event_count": len(events),
            "past_calendar_event_count": past_events,
            "calendar_horizon_days": _clean(payload.get("rsvPosblPd")),
            "management_status": _clean(management.get("sttusCd")),
            "event_capacities": capacities,
            "event_dates": [value.isoformat() for value in event_dates],
        }
    )
    return errors, len(events), past_events


def collect_suncheon_reservation_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 10,
    detail_limit: int = 20,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    if not is_suncheon_reservation_target(target):
        return [], SUNCHEON_RESERVATION_PARSER, _failure(
            SUNCHEON_RESERVATION_PARSER,
            "target does not match the canonical Suncheon reservation education route",
        )
    if fetcher is None or session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], SUNCHEON_RESERVATION_PARSER, _failure(
                SUNCHEON_RESERVATION_PARSER,
                "managed fetcher and session_factory injection are required",
            )
        fetcher = fetcher or _default_fetcher
        session_factory = session_factory or _default_session_factory

    cutoff = _today(today)
    allowed_pages = max(0, int(max_pages))
    allowed_details = max(0, int(detail_limit))
    requester = _Requester(fetcher, session_factory, timeout)
    errors: list[str] = []
    first_pages: dict[str, BeautifulSoup] = {}
    page_counts: dict[str, dict[int, int]] = {}
    source_last_pages: dict[str, int] = {}
    all_rows: list[dict[str, Any]] = []
    list_requests = 0
    required_list_requests = 0
    source_cap_reached = False
    detail_pages = 0
    calendar_landing_pages = 0
    calendar_api_requests = 0
    detail_errors = 0
    calendar_event_count = 0
    past_calendar_event_count = 0
    try:
        for source in SUNCHEON_RESERVATION_SOURCES:
            try:
                first_pages[source.code] = requester.get(
                    suncheon_reservation_list_url(source, 1)
                )
                list_requests += 1
            except Exception as exc:
                errors.append(f"{source.code} page 1: fetch {type(exc).__name__}")
        for source in SUNCHEON_RESERVATION_SOURCES:
            first = first_pages.get(source.code)
            if first is None:
                continue
            last = _reservation_page_count(first)
            source_last_pages[source.code] = last
            required_list_requests += last + 1
        if required_list_requests > allowed_pages:
            source_cap_reached = True
            errors.append(
                f"max_pages cap allows {allowed_pages} of "
                f"{required_list_requests} required source pages and sentinels"
            )

        if not errors:
            for source in SUNCHEON_RESERVATION_SOURCES:
                last = source_last_pages[source.code]
                page_counts[source.code] = {}
                for page in range(1, last + 1):
                    if page == 1:
                        soup = first_pages[source.code]
                    else:
                        soup = requester.get(suncheon_reservation_list_url(source, page))
                        list_requests += 1
                    rows, parse_errors = _reservation_parse_page(
                        soup, source, SUNCHEON_RESERVATION_PROVIDER
                    )
                    errors.extend(parse_errors)
                    page_counts[source.code][page] = len(rows)
                    all_rows.extend(rows)
                sentinel = requester.get(
                    suncheon_reservation_list_url(source, last + 1)
                )
                list_requests += 1
                sentinel_rows, sentinel_errors = _reservation_parse_page(
                    sentinel, source, SUNCHEON_RESERVATION_PROVIDER
                )
                errors.extend(sentinel_errors)
                page_counts[source.code][last + 1] = len(sentinel_rows)
                if sentinel_rows:
                    errors.append(f"{source.code}: immediate sentinel is not empty")

        identities = [_clean(row.get("provider_course_id")) for row in all_rows]
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count:
            errors.append(f"{duplicate_count} duplicate reservation identities")
        urls = [_clean(row.get("raw_url")) for row in all_rows]
        duplicate_url_count = len(urls) - len(set(urls))
        if duplicate_url_count:
            errors.append(f"{duplicate_url_count} duplicate reservation URLs")
        if len(all_rows) > allowed_details:
            source_cap_reached = True
            errors.append(
                f"detail_limit cap allows {allowed_details} of "
                f"{len(all_rows)} required product calendars"
            )

        if not errors:
            for row in all_rows:
                detail_id = _clean(row.get("_detail_id"))
                for attempt in range(3):
                    try:
                        # Redirect, landing, and JSON endpoint share a JSESSIONID.
                        # Reserve the trio on one SafeSession and retry the whole
                        # read-only transaction twice if its transport is severed.
                        requester.reserve(3)
                        redirect = requester.get(_clean(row.get("raw_url")))
                        form_values, redirect_errors = _redirect_contract(row, redirect)
                        detail_errors += len(redirect_errors)
                        errors.extend(redirect_errors)
                        if redirect_errors:
                            break
                        calendar_url = (
                            f"{SUNCHEON_RESERVATION_BASE}"
                            "/yeyak/program/calendar01/index.jsp"
                        )
                        landing = requester.post_html(calendar_url, form_values)
                        endpoint_matches = []
                        for script in landing.select("script"):
                            endpoint_matches.extend(
                                match.group(1)
                                for match in _CALENDAR_ENDPOINT_RE.finditer(
                                    script.get_text("\n")
                                )
                            )
                        endpoints = sorted(set(endpoint_matches))
                        if len(endpoints) != 1:
                            errors.append(
                                f"{detail_id}: expected one calendar JSON endpoint"
                            )
                            detail_errors += 1
                            break
                        endpoint = urljoin(SUNCHEON_RESERVATION_BASE, endpoints[0])
                        parsed_endpoint = urlparse(endpoint)
                        if (
                            parsed_endpoint.scheme != "https"
                            or parsed_endpoint.hostname != "www.suncheon.go.kr"
                            or not parsed_endpoint.path.startswith(
                                "/yeyak/program/selectCalendarList.json"
                            )
                        ):
                            errors.append(
                                f"{detail_id}: calendar endpoint escaped official host"
                            )
                            detail_errors += 1
                            break
                        payload = requester.post_json(
                            endpoint,
                            form_values,
                            referer=calendar_url,
                        )
                        row_errors, events, past_events = _calendar_contract(
                            row, payload, cutoff
                        )
                        detail_pages += 1
                        calendar_landing_pages += 1
                        calendar_api_requests += 1
                        detail_errors += len(row_errors)
                        calendar_event_count += events
                        past_calendar_event_count += past_events
                        errors.extend(row_errors)
                        break
                    except Exception as exc:
                        if attempt < 2:
                            requester.renew()
                            continue
                        detail_errors += 1
                        errors.append(
                            f"{detail_id}: calendar detail {type(exc).__name__}"
                        )

        current_rows = [row for row in all_rows if row.get("status") == "OPEN"]
        inactive_product_count = len(all_rows) - len(current_rows)
        for row in all_rows:
            row.pop("_source", None)
            row.pop("_detail_id", None)
            row.pop("_goods_type", None)
        signatures = [_semantic_signature(row) for row in current_rows]
        semantic_duplicate_count = len(signatures) - len(set(signatures))
        if semantic_duplicate_count:
            errors.append(
                f"{semantic_duplicate_count} duplicate reservation semantic signatures"
            )
        result: list[dict[str, Any]] = []
        if not errors:
            deduper = dedupe_rows or _dedupe_default
            result = list(deduper(current_rows))
            if len(result) != len(current_rows):
                errors.append(
                    f"dedupe changed complete row count {len(current_rows)} to {len(result)}"
                )
                result = []

        branch_counts = Counter(_clean(row.get("branch")) for row in current_rows)
        snapshot_complete = not errors
        meta = {
            "parser": SUNCHEON_RESERVATION_PARSER,
            "pages": list_requests,
            "list_requests": list_requests,
            "request_count": requester.calls,
            "session_count": requester.sessions,
            "detail_pages": detail_pages,
            "calendar_landing_pages": calendar_landing_pages,
            "calendar_api_requests": calendar_api_requests,
            "source_total": len(all_rows),
            "source_rows": len(all_rows),
            "required_list_requests": required_list_requests,
            "source_last_pages": source_last_pages,
            "page_counts": page_counts,
            "sentinel_mode": "empty",
            "inactive_product_count": inactive_product_count,
            "calendar_event_count": calendar_event_count,
            "past_calendar_event_count": past_calendar_event_count,
            "current_count": len(current_rows),
            "returned_count": len(result),
            "branch_count": len(branch_counts),
            "branch_counts": dict(branch_counts),
            "status_counts": dict(Counter(row.get("status") for row in current_rows)),
            "duplicate_count": duplicate_count,
            "duplicate_url_count": duplicate_url_count,
            "semantic_duplicate_count": semantic_duplicate_count,
            "detail_errors": detail_errors,
            "pagination_detected": any(page > 1 for page in source_last_pages.values()),
            "pagination_complete": bool(
                snapshot_complete and list_requests == required_list_requests
            ),
            "details_complete": bool(
                snapshot_complete
                and detail_pages == len(all_rows)
                and calendar_api_requests == len(all_rows)
            ),
            "snapshot_complete": snapshot_complete,
            "source_cap_reached": source_cap_reached,
            "no_current_data": bool(snapshot_complete and not result),
            "no_current_reason": (
                "all complete Suncheon reservation education calendars are empty"
                if snapshot_complete and not result
                else ""
            ),
            "configured_collection_error": "; ".join(errors),
        }
        if errors:
            return [], SUNCHEON_RESERVATION_PARSER, meta
        return result, SUNCHEON_RESERVATION_PARSER, meta
    finally:
        requester.close()


def suncheon_garden_detail_url(identity: Any) -> str:
    raw = _clean(identity)
    if not raw.isdigit():
        return ""
    return f"{SUNCHEON_GARDEN_URL}?" + urlencode(
        (("mode", "info"), ("eduIdx", raw))
    )


def suncheon_garden_sentinel_url() -> str:
    return f"{SUNCHEON_GARDEN_URL}?" + urlencode(
        {"eduNm": "__MOONCEN_SENTINEL_9C70__"}
    )


def _garden_status(value: Any) -> str:
    raw = _clean(value)
    if "접수중" in raw:
        return "OPEN"
    if "접수마감" in raw or "정원마감" in raw:
        return "CLOSED"
    if "접수예정" in raw or "접수준비" in raw:
        return "SCHEDULED"
    if "취소" in raw or "폐강" in raw:
        return "CANCELLED"
    return ""


def _garden_parse_page(
    soup: BeautifulSoup,
    provider: str,
) -> tuple[list[dict[str, Any]], list[str], bool]:
    table = soup.select_one("table")
    if table is None:
        return [], ["missing garden education table"], False
    headers = tuple(_text(node) for node in table.select("thead th"))
    if headers != _GARDEN_HEADERS:
        return [], [f"garden headers changed: {headers!r}"], False
    result: list[dict[str, Any]] = []
    errors: list[str] = []
    no_data = False
    body_rows = table.select("tbody > tr")
    for index, tr in enumerate(body_rows, start=1):
        cells = tr.find_all("td", recursive=False)
        if len(cells) == 1 and "교육 데이터가 없습니다" in _text(cells[0]):
            no_data = True
            continue
        if len(cells) != 6:
            errors.append(f"garden row {index}: expected 6 cells, got {len(cells)}")
            continue
        action = _clean(tr.get("onclick"))
        anchor = cells[0].select_one("a[onclick]")
        match = _GARDEN_ORDER_RE.fullmatch(action)
        anchor_match = _GARDEN_ORDER_RE.fullmatch(
            _clean(anchor.get("onclick")) if anchor else ""
        )
        if not match or not anchor_match or match.group(1) != anchor_match.group(1):
            errors.append(f"garden row {index}: invalid goOrder identity")
            continue
        identity = match.group(1)
        values = _dates(_text(cells[1]), short_year=True)
        if len(values) != 4:
            errors.append(f"garden {identity}: expected four dates, got {len(values)}")
            continue
        apply_start, apply_end, start, end = values
        if apply_start > apply_end or start > end:
            errors.append(f"garden {identity}: invalid date range")
            continue
        source_status = _text(cells[5])
        status = _garden_status(source_status)
        if not status:
            errors.append(f"garden {identity}: unknown status {source_status!r}")
            continue
        capacity_current, capacity_total = _capacity_pair(_text(cells[3]))
        if capacity_current is None or capacity_total is None:
            errors.append(f"garden {identity}: invalid capacity")
            continue
        title = _text(anchor)
        raw_url = suncheon_garden_detail_url(identity)
        row: dict[str, Any] = {
            "provider": provider,
            "provider_course_id": f"{provider}:garden:{identity}",
            "title": title,
            "branch": "순천시정원지원센터",
            "branch_code": _branch_code("순천시정원지원센터"),
            "category": "정원교육",
            "raw_url": raw_url,
            "reservation_available": status == "OPEN",
            "status": status,
            "fee": _text(cells[4]),
            "period": _period(start, end),
            "apply_period": _period(apply_start, apply_end),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "apply_start_date": apply_start.isoformat(),
            "apply_end_date": apply_end.isoformat(),
            "schedule_raw": _text(cells[2]),
            "target": "전체",
            "capacity": _text(cells[3]),
            "capacity_current": capacity_current,
            "capacity_total": capacity_total,
            "venue_name": "순천시정원지원센터",
            "room": "",
            "description": title,
            "application_type": "ONLINE_RESERVATION_AUTH_REQUIRED",
            "municipality_code": SUNCHEON_MUNICIPALITY_CODE,
            "municipality_name": SUNCHEON_MUNICIPALITY_NAME,
            "region": SUNCHEON_REGION,
            "collection_type": "static_html+auth_gate_detail",
            "source_group": "municipal_garden_education",
            "raw_fields": {
                "parser": SUNCHEON_GARDEN_PARSER,
                "education_idx": identity,
                "source_status": source_status,
                "authentication_required": True,
                "test_record": title.lower().startswith("(test)"),
            },
        }
        if status == "OPEN":
            row["application_url"] = raw_url
        else:
            row["raw_fields"]["clear_application_url"] = True
        result.append(row)
    if no_data and len(body_rows) != 1:
        errors.append("garden no-data row is mixed with course rows")
    return result, errors, no_data


def _garden_detail_contract(row: Mapping[str, Any], soup: BeautifulSoup) -> list[str]:
    identity = _clean(row.get("provider_course_id")).rsplit(":", 1)[-1]
    title = _text(soup.title)
    login_links = {
        urlparse(urljoin(SUNCHEON_GARDEN_URL, _clean(anchor.get("href")))).path
        for anchor in soup.select("a[href]")
        if _text(anchor) == "로그인"
    }
    errors: list[str] = []
    if "정원교육 예약" not in title:
        errors.append(f"{identity}: garden detail auth-gate title changed")
    if "/scbay/login/index.jsp" not in login_links:
        errors.append(f"{identity}: garden detail auth gate missing login")
    if soup.select_one("table tbody tr[onclick*='goOrder']") is not None:
        errors.append(f"{identity}: garden auth detail unexpectedly replayed list")
    return errors


def collect_suncheon_garden_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 5,
    detail_limit: int = 20,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    if not is_suncheon_garden_target(target):
        return [], SUNCHEON_GARDEN_PARSER, _failure(
            SUNCHEON_GARDEN_PARSER,
            "target does not match the canonical Suncheon garden catalogue",
        )
    if fetcher is None or session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], SUNCHEON_GARDEN_PARSER, _failure(
                SUNCHEON_GARDEN_PARSER,
                "managed fetcher and session_factory injection are required",
            )
        fetcher = fetcher or _default_fetcher
        session_factory = session_factory or _default_session_factory

    cutoff = _today(today)
    allowed_pages = max(0, int(max_pages))
    allowed_details = max(0, int(detail_limit))
    requester = _Requester(fetcher, session_factory, timeout)
    errors: list[str] = []
    rows: list[dict[str, Any]] = []
    detail_pages = 0
    detail_errors = 0
    source_cap_reached = False
    duplicate_count = 0
    duplicate_url_count = 0
    semantic_duplicate_count = 0
    sentinel_mode = ""
    try:
        if allowed_pages < 2:
            source_cap_reached = True
            errors.append("max_pages cap must allow list and immediate sentinel")
        if not errors:
            try:
                first = requester.get(SUNCHEON_GARDEN_URL)
                sentinel = requester.get(suncheon_garden_sentinel_url())
                rows, parse_errors, no_data = _garden_parse_page(
                    first, SUNCHEON_GARDEN_PROVIDER
                )
                errors.extend(parse_errors)
                sentinel_rows, sentinel_errors, sentinel_no_data = _garden_parse_page(
                    sentinel, SUNCHEON_GARDEN_PROVIDER
                )
                errors.extend(sentinel_errors)
                if no_data or sentinel_rows or not sentinel_no_data:
                    errors.append("garden immediate query sentinel contract changed")
                else:
                    sentinel_mode = "query_empty"
            except Exception as exc:
                errors.append(f"garden list/sentinel fetch {type(exc).__name__}")

        identities = [_clean(row.get("provider_course_id")) for row in rows]
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count:
            errors.append(f"{duplicate_count} duplicate garden identities")
        urls = [_clean(row.get("raw_url")) for row in rows]
        duplicate_url_count = len(urls) - len(set(urls))
        if duplicate_url_count:
            errors.append(f"{duplicate_url_count} duplicate garden URLs")

        current_rows: list[dict[str, Any]] = []
        expired_count = 0
        test_record_count = 0
        for row in rows:
            is_test = bool(row.get("raw_fields", {}).get("test_record"))
            test_record_count += int(is_test)
            try:
                end = date.fromisoformat(_clean(row.get("end_date")))
            except ValueError:
                errors.append(f"{row.get('provider_course_id')}: invalid end date")
                continue
            if end < cutoff or is_test:
                expired_count += 1
            else:
                current_rows.append(row)
        if len(current_rows) > allowed_details:
            source_cap_reached = True
            errors.append(
                f"detail_limit cap allows {allowed_details} of "
                f"{len(current_rows)} current garden auth details"
            )
        if not errors:
            for row in current_rows:
                try:
                    detail = requester.get(_clean(row.get("raw_url")))
                    detail_pages += 1
                    row_errors = _garden_detail_contract(row, detail)
                    detail_errors += len(row_errors)
                    errors.extend(row_errors)
                except Exception as exc:
                    detail_errors += 1
                    errors.append(
                        f"{row.get('provider_course_id')}: garden detail {type(exc).__name__}"
                    )

        signatures = [_semantic_signature(row) for row in current_rows]
        semantic_duplicate_count = len(signatures) - len(set(signatures))
        if semantic_duplicate_count:
            errors.append(
                f"{semantic_duplicate_count} duplicate garden semantic signatures"
            )
        result: list[dict[str, Any]] = []
        if not errors:
            deduper = dedupe_rows or _dedupe_default
            result = list(deduper(current_rows))
            if len(result) != len(current_rows):
                errors.append(
                    f"dedupe changed complete row count {len(current_rows)} to {len(result)}"
                )
                result = []

        branch_counts = Counter(_clean(row.get("branch")) for row in current_rows)
        status_counts = Counter(_clean(row.get("status")) for row in current_rows)
        snapshot_complete = not errors
        meta = {
            "parser": SUNCHEON_GARDEN_PARSER,
            "pages": min(requester.calls, 2),
            "list_requests": min(requester.calls, 2),
            "request_count": requester.calls,
            "session_count": requester.sessions,
            "detail_pages": detail_pages,
            "source_total": len(rows),
            "source_rows": len(rows),
            "required_list_requests": 2,
            "sentinel_mode": sentinel_mode,
            "expired_count": expired_count,
            "test_record_count": test_record_count,
            "current_count": len(current_rows),
            "returned_count": len(result),
            "branch_count": len(branch_counts),
            "branch_counts": dict(branch_counts),
            "status_counts": dict(status_counts),
            "duplicate_count": duplicate_count,
            "duplicate_url_count": duplicate_url_count,
            "semantic_duplicate_count": semantic_duplicate_count,
            "detail_errors": detail_errors,
            "pagination_detected": False,
            "pagination_complete": bool(snapshot_complete and sentinel_mode),
            "details_complete": bool(
                snapshot_complete and detail_pages == len(current_rows)
            ),
            "snapshot_complete": snapshot_complete,
            "source_cap_reached": source_cap_reached,
            "no_current_data": bool(snapshot_complete and not result),
            "no_current_reason": (
                "all complete Suncheon garden courses have ended"
                if snapshot_complete and not result
                else ""
            ),
            "configured_collection_error": "; ".join(errors),
        }
        if errors:
            return [], SUNCHEON_GARDEN_PARSER, meta
        return result, SUNCHEON_GARDEN_PARSER, meta
    finally:
        requester.close()


def collect_suncheon_education_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 400,
    detail_limit: int = 300,
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    if is_suncheon_lms_target(target):
        return collect_suncheon_lms_courses(
            target,
            timeout=timeout,
            max_pages=max_pages,
            detail_limit=detail_limit,
            **kwargs,
        )
    if is_suncheon_reservation_target(target):
        return collect_suncheon_reservation_courses(
            target,
            timeout=timeout,
            max_pages=max_pages,
            detail_limit=detail_limit,
            **kwargs,
        )
    if is_suncheon_garden_target(target):
        return collect_suncheon_garden_courses(
            target,
            timeout=timeout,
            max_pages=max_pages,
            detail_limit=detail_limit,
            **kwargs,
        )
    return [], SUNCHEON_LMS_PARSER, _failure(
        SUNCHEON_LMS_PARSER,
        "target does not match a canonical Suncheon education provider route",
    )


is_target = is_suncheon_education_target
collect = collect_suncheon_education_courses


__all__ = [
    "SUNCHEON_GARDEN_DISCOVERY_CANDIDATE_ID",
    "SUNCHEON_GARDEN_PARSER",
    "SUNCHEON_GARDEN_PROVIDER",
    "SUNCHEON_GARDEN_URL",
    "SUNCHEON_LMS_CANDIDATE_ID",
    "SUNCHEON_LMS_DUPLICATE_ALIAS_URLS",
    "SUNCHEON_LMS_PAGE_SIZE",
    "SUNCHEON_LMS_PARSER",
    "SUNCHEON_LMS_PROVIDER",
    "SUNCHEON_LMS_SEARCH_ALIAS_CANDIDATE_ID",
    "SUNCHEON_LMS_URL",
    "SUNCHEON_MUNICIPALITY_CODE",
    "SUNCHEON_MUNICIPALITY_NAME",
    "SUNCHEON_RESERVATION_CANDIDATE_ID",
    "SUNCHEON_RESERVATION_DUPLICATE_ALIAS_URLS",
    "SUNCHEON_RESERVATION_INTERNAL_EDUCATION_URLS",
    "SUNCHEON_RESERVATION_PARSER",
    "SUNCHEON_RESERVATION_PROVIDER",
    "SUNCHEON_RESERVATION_SOURCES",
    "SUNCHEON_RESERVATION_URL",
    "SUNCHEON_STATIC_OR_DISCOVERY_URLS",
    "SUNCHEON_WRONG_CATEGORY_URLS",
    "SuncheonReservationSource",
    "collect_suncheon_education_courses",
    "collect_suncheon_garden_courses",
    "collect_suncheon_lms_courses",
    "collect_suncheon_reservation_courses",
    "is_suncheon_education_target",
    "is_suncheon_garden_target",
    "is_suncheon_lms_target",
    "is_suncheon_reservation_target",
    "suncheon_garden_detail_url",
    "suncheon_garden_sentinel_url",
    "suncheon_lms_detail_url",
    "suncheon_lms_list_url",
    "suncheon_reservation_application_url",
    "suncheon_reservation_list_url",
]
