"""Fail-closed collectors for Miryang City's official education catalogues.

Miryang has two distinct municipal education inventories.  The integrated
reservation service owns five internal catalogues (child learning, digital
literacy, city library, women's centre, and resident-centre programmes).  Its
homepage JSON endpoint is only a small current-course carousel and is not a
complete source.  Separately, the lifelong-learning portal owns a 220-row
historical catalogue; its ``st=e`` route means *applications still open*, not
education still running, and therefore omits current courses after enrolment
closes.

The collectors below use the two complete catalogue anchors.  They publish
only courses whose education end date is today or later, but first prove the
entire historical inventory against the source total, every declared page,
and a post-boundary sentinel.  Miryang's integrated service clamps an
out-of-range page to its final page, so that sentinel is accepted only when it
is an exact identity-for-identity replay of the final page.  The lifelong
portal instead returns an empty sentinel.

This module intentionally does not import ``Crawler_MunicipalYaml`` so the
shared router can import it without a cycle.  Production callers must inject
the router's managed fetcher and SafeSession factory.  No request disables
TLS certificate verification.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, NavigableString


MIRYANG_MUNICIPALITY_CODE = "4827000000"
MIRYANG_MUNICIPALITY_NAME = "경상남도 밀양시"

MIRYANG_YEYAK_PROVIDER = "MUNI_YEYAK_MIRYANG_GO_KR_0741D829"
MIRYANG_YEYAK_URL = "https://yeyak.miryang.go.kr/"
MIRYANG_YEYAK_HOST = "yeyak.miryang.go.kr"
MIRYANG_YEYAK_PAGE_SIZE = 9
MIRYANG_YEYAK_PARSER = (
    "miryang_yeyak_complete_source_inventory+pages+clamped_sentinels+current_detail"
)

MIRYANG_LIFELONG_PROVIDER = "MUNI_WWW_MIRYANG_GO_KR_F66F2E07"
MIRYANG_LIFELONG_URL = (
    "https://www.miryang.go.kr/edu/nmprogram/curriculum/default.php"
)
MIRYANG_LIFELONG_HOST = "www.miryang.go.kr"
MIRYANG_LIFELONG_PATH = "/edu/nmprogram/curriculum/default.php"
MIRYANG_LIFELONG_PAGE_SIZE = 10
MIRYANG_LIFELONG_PARSER = (
    "miryang_lifelong_complete_pages+empty_sentinel+current_detail"
)

MIRYANG_SESSION_REQUEST_LIMIT = 150

# The official list and detail both publish ``2060`` for a four-week summer
# programme whose start date, application window, and description all say
# 2026.  Correct only this exact raw fingerprint and retain the source values
# in ``raw_fields``.  If the upstream site fixes it, the correction becomes a
# no-op automatically.
MIRYANG_YEYAK_DATE_CORRECTIONS: Mapping[str, tuple[str, str, str]] = {
    "LT002038": ("2026-07-28", "2060-08-25", "2026-08-25"),
}


@dataclass(frozen=True)
class MiryangYeyakSource:
    code: str
    name: str
    path: str
    inventory_path: str
    key_name: str
    key_value: str


MIRYANG_YEYAK_SOURCES: tuple[MiryangYeyakSource, ...] = (
    MiryangYeyakSource(
        "child_learning",
        "아이키움배움터",
        "/yeyak/00000/00071.web",
        "/yeyak/00000/00071.web",
        "fcd",
        "F002",
    ),
    MiryangYeyakSource(
        "digital_literacy",
        "시민정보화교육",
        "/yeyak/00000/00070.web",
        "/yeyak/00000/00070.web",
        "fcd",
        "F001",
    ),
    MiryangYeyakSource(
        "city_library",
        "시립도서관",
        "/yeyak/00000/00086/00087.web",
        "/yeyak/00000/00086.web",
        "fcd",
        "F010",
    ),
    MiryangYeyakSource(
        "womens_center",
        "여성회관",
        "/yeyak/00000/00073.web",
        "/yeyak/00000/00073.web",
        "fcd",
        "F004",
    ),
    MiryangYeyakSource(
        "resident_centers",
        "주민자치프로그램",
        "/yeyak/00000/00097.web",
        "/yeyak/00000/00097.web",
        "gubunCd",
        "FAC008",
    ),
)

# Audited aliases/exclusions.  They are constants so the shared target
# configuration can bind them without re-discovering source semantics.
MIRYANG_YEYAK_PARTIAL_API_URL = (
    "https://yeyak.miryang.go.kr/yeyak/json/yeyak/edu/lecture/list_main.do"
)
MIRYANG_YEYAK_DUPLICATE_ALIAS_URLS = (
    "https://yeyak.miryang.go.kr/yeyak/00000.web",
    "https://yeyak.miryang.go.kr/yeyak/00000/00086.web",
    "https://yeyak.miryang.go.kr/yeyak/00000/00086/00088.web",
    "https://yeyak.miryang.go.kr/yeyak/00000/00086/00090.web",
    "https://yeyak.miryang.go.kr/yeyak/00000/00086/00091.web",
    "https://yeyak.miryang.go.kr/yeyak/00000/00086/00273.web",
)
MIRYANG_YEYAK_WRONG_CATEGORY_URLS = (
    "https://yeyak.miryang.go.kr/yeyak/00001/00044/00063.web",
)
MIRYANG_LIFELONG_DUPLICATE_FILTER_URLS = (
    "https://miryang.go.kr/edu/nmprogram/curriculum/default.php?st=e",
    "https://www.miryang.go.kr/edu/nmprogram/curriculum/default.php?st=e",
)
MIRYANG_LIFELONG_RETIRED_ARCHIVE_URLS = (
    "https://www.miryang.go.kr/edu/nmprogram/curriculumkid/default.php",
)
MIRYANG_STATIC_INFO_URLS = (
    "https://www.miryang.go.kr/edu/nmprogram/sub/601000000.php",
)
MIRYANG_EXTERNAL_EDUCATION_DESTINATIONS = (
    "https://www.myfmc.or.kr/sub/06_03.php",
    "https://eng.myclib.or.kr/?page_id=main",
    "https://www.miryang.go.kr/agredu",
    "https://www.miryouth.net/",
    "https://miryang.familynet.or.kr/center/lay1/program/S295T322C451/recruitReceipt/list.do",
    "https://www.mycfe.or.kr/contents/edu",
)


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"(?:LT)?\d+")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"
)
_YEYAK_TOTAL_RE = re.compile(r"총\s*([\d,]+)\s*건의\s*자료")
_LIFELONG_TOTAL_RE = re.compile(r"총\s*([\d,]+)\s*개의\s*교육과정")
_YEYAK_TOP_PATH_RE = re.compile(r"^/yeyak/00000/\d{5}\.web$")
_MONEY_RE = re.compile(r"([\d,]+)\s*원")

_YEYAK_REQUIRED_LIST_LABELS = frozenset(
    {
        "교육분류",
        "접수기간",
        "교육기간",
        "요일시간",
        "온라인 정원/대기정원",
        "신청현황",
        "수강료",
        "교육장소",
    }
)
_YEYAK_REQUIRED_DETAIL_LABELS = frozenset(
    {
        "교육분류",
        "교육과정",
        "교육대상",
        "접수기간",
        "교육기간",
        "요일시간",
        "교육장소",
        "승인방식",
        "온라인 정원/대기정원",
        "신청현황",
        "수강료",
    }
)
_LIFELONG_HEADERS = (
    "순번",
    "구분",
    "교육강좌명",
    "접수 및 교육기간",
    "접수방법",
)
_LIFELONG_REQUIRED_DETAIL_LABELS = frozenset(
    {
        "위치",
        "접수기간",
        "교육기간",
        "접수처",
        "교육시간",
        "수강료",
        "준비물",
        "모집대상",
        "모집방법",
        "모집인원",
        "신청인원",
        "교육과정",
        "수강안내",
    }
)
_STATUS_MAP: Mapping[str, str] = {
    "접수중": "OPEN",
    "신청중": "OPEN",
    "접수대기": "SCHEDULED",
    "접수예정": "SCHEDULED",
    "접수마감": "CLOSED",
    "신청마감": "CLOSED",
    "접수완료": "CLOSED",
    "교육중": "CLOSED",
    "교육완료": "CLOSED",
    "종료": "CLOSED",
    "폐강": "CANCELLED",
    "취소": "CANCELLED",
}


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


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


def _target_branch(target: Any) -> str:
    return _clean(_target_value(target, "branch")) or MIRYANG_MUNICIPALITY_NAME


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


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


class _Requester:
    """Rotate injected SafeSessions before their shared request budget."""

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

    def get(self, url: str) -> BeautifulSoup:
        if self.current is None or self.current_calls >= MIRYANG_SESSION_REQUEST_LIMIT:
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
        self.current_calls += 1
        self.calls += 1
        return _coerce_soup(self.fetcher(self.current, url, self.timeout))

    def close(self) -> None:
        _close_quietly(self.current)
        self.current = None


def _date_values(value: Any) -> list[date]:
    result: list[date] = []
    for match in _DATE_RE.finditer(_clean(value)):
        try:
            result.append(date(*(int(part) for part in match.groups())))
        except ValueError:
            continue
    return result


def _date_range(value: Any) -> tuple[Optional[date], Optional[date]]:
    values = _date_values(value)
    if len(values) < 2:
        return None, None
    return values[0], values[1]


def _period(start: Optional[date], end: Optional[date]) -> str:
    if start is None or end is None:
        return ""
    return f"{start.isoformat()} ~ {end.isoformat()}"


def _correct_yeyak_period(
    identity: str,
    start: Optional[date],
    end: Optional[date],
) -> tuple[Optional[date], Optional[date], bool]:
    correction = MIRYANG_YEYAK_DATE_CORRECTIONS.get(identity)
    if start is None or end is None or correction is None:
        return start, end, False
    expected_start, bad_end, corrected_end = correction
    if start.isoformat() == expected_start and end.isoformat() == bad_end:
        return start, date.fromisoformat(corrected_end), True
    return start, end, False


def _first_number(value: Any) -> Optional[int]:
    match = re.search(r"[\d,]+", _clean(value))
    return int(match.group(0).replace(",", "")) if match else None


def _capacity_pair(value: Any) -> tuple[Optional[int], Optional[int]]:
    numbers = [int(raw.replace(",", "")) for raw in re.findall(r"[\d,]+", _clean(value))]
    if not numbers:
        return None, None
    if len(numbers) == 1:
        return numbers[0], None
    return numbers[0], numbers[1]


def _status(value: Any) -> str:
    raw = _clean(value)
    if raw in _STATUS_MAP:
        return _STATUS_MAP[raw]
    for label, normalized in _STATUS_MAP.items():
        if label and label in raw:
            return normalized
    return ""


def _branch_code(branch: Any) -> str:
    digest = hashlib.sha1(_normalized(branch).encode("utf-8")).hexdigest()[:10].upper()
    return f"MIRYANG_BRANCH_{digest}"


def _semantic_signature(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        _normalized(row.get("title")),
        _normalized(row.get("branch")),
        _clean(row.get("period")),
        _normalized(row.get("schedule_raw")),
        _normalized(row.get("venue_name")),
        _normalized(row.get("target")),
    )


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            result.append(row)
            seen.add(identity)
    return result


def _failure(parser: str, reason: str) -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
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
        "parser": parser,
    }


def is_miryang_yeyak_target(target: Any) -> bool:
    parsed = urlparse(_target_url(target))
    return (
        _provider(target) == MIRYANG_YEYAK_PROVIDER
        and parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == MIRYANG_YEYAK_HOST
        and parsed.port is None
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


def is_miryang_lifelong_target(target: Any) -> bool:
    parsed = urlparse(_target_url(target))
    return (
        _provider(target) == MIRYANG_LIFELONG_PROVIDER
        and parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == MIRYANG_LIFELONG_HOST
        and parsed.port is None
        and parsed.path == MIRYANG_LIFELONG_PATH
        and not parsed.query
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


def is_miryang_education_target(target: Any) -> bool:
    return is_miryang_yeyak_target(target) or is_miryang_lifelong_target(target)


def miryang_yeyak_list_url(source: MiryangYeyakSource, page: Any = 1) -> str:
    raw_page = _clean(page)
    if source not in MIRYANG_YEYAK_SOURCES or not raw_page.isdigit() or int(raw_page) < 1:
        return ""
    base = f"https://{MIRYANG_YEYAK_HOST}{source.path}"
    if int(raw_page) == 1:
        return base
    return f"{base}?" + urlencode({"cpage": int(raw_page)})


def miryang_yeyak_detail_url(source: MiryangYeyakSource, identity: Any) -> str:
    raw_identity = _clean(identity)
    if source not in MIRYANG_YEYAK_SOURCES or not _IDENTITY_RE.fullmatch(raw_identity):
        return ""
    return f"https://{MIRYANG_YEYAK_HOST}{source.path}?" + urlencode(
        (("amode", "view"), ("lectureId", raw_identity), (source.key_name, source.key_value))
    )


def miryang_yeyak_application_url(source: MiryangYeyakSource, identity: Any) -> str:
    raw_identity = _clean(identity)
    if source not in MIRYANG_YEYAK_SOURCES or not _IDENTITY_RE.fullmatch(raw_identity):
        return ""
    return f"https://{MIRYANG_YEYAK_HOST}{source.path}?" + urlencode(
        (("amode", "agree"), (source.key_name, source.key_value), ("lectureId", raw_identity))
    )


def miryang_lifelong_list_url(page: Any = 1) -> str:
    raw_page = _clean(page)
    if not raw_page.isdigit() or int(raw_page) < 1:
        return ""
    if int(raw_page) == 1:
        return MIRYANG_LIFELONG_URL
    return f"{MIRYANG_LIFELONG_URL}?" + urlencode({"page": int(raw_page)})


def miryang_lifelong_detail_url(identity: Any) -> str:
    raw_identity = _clean(identity)
    if not raw_identity.isdigit():
        return ""
    return f"{MIRYANG_LIFELONG_URL}?" + urlencode((('mod', 'o'), ('idx', raw_identity)))


def _query_page_values(soup: BeautifulSoup, name: str) -> set[int]:
    values: set[int] = set()
    for anchor in soup.select("a[href]"):
        query = parse_qs(urlparse(urljoin("https://example.invalid/", _clean(anchor.get("href")))).query)
        raw = (query.get(name) or [""])[0]
        if raw.isdigit() and int(raw) > 0:
            values.add(int(raw))
    return values


def _yeyak_total(soup: BeautifulSoup) -> Optional[int]:
    match = _YEYAK_TOTAL_RE.search(_clean(soup.get_text(" ", strip=True)))
    return int(match.group(1).replace(",", "")) if match else None


def _lifelong_total(soup: BeautifulSoup) -> Optional[int]:
    match = _LIFELONG_TOTAL_RE.search(_clean(soup.get_text(" ", strip=True)))
    return int(match.group(1).replace(",", "")) if match else None


def _yeyak_inventory(soup: BeautifulSoup) -> set[str]:
    result: set[str] = set()
    for anchor in soup.select("a[href]"):
        path = urlparse(urljoin(MIRYANG_YEYAK_URL, _clean(anchor.get("href")))).path
        if _YEYAK_TOP_PATH_RE.fullmatch(path):
            result.add(path)
    return result


def _pair_list(container: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in container.select("li") if container is not None else []:
        label = item.select_one(".t1")
        value = item.select_one(".t2")
        if label is not None and value is not None:
            result[_clean(label.get_text(" ", strip=True))] = _clean(
                value.get_text(" ", strip=True)
            )
    return result


def _direct_title(node: Any) -> str:
    if node is None:
        return ""
    parts = [
        _clean(child)
        for child in node.children
        if isinstance(child, NavigableString) and _clean(child)
    ]
    return _clean(" ".join(parts))


def _strict_single(query: Mapping[str, list[str]], name: str) -> str:
    values = query.get(name) or []
    return _clean(values[0]) if len(values) == 1 else ""


def _yeyak_link_identity(
    source: MiryangYeyakSource,
    href: Any,
) -> tuple[str, str]:
    parsed = urlparse(urljoin(f"https://{MIRYANG_YEYAK_HOST}{source.path}", _clean(href)))
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != MIRYANG_YEYAK_HOST
        or parsed.path != source.path
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        return "", "course link escaped canonical route"
    query = parse_qs(parsed.query, keep_blank_values=True)
    allowed = {"amode", "lectureId", source.key_name, "cpage"}
    if set(query) - allowed:
        return "", "course link has unexpected query keys"
    identity = _strict_single(query, "lectureId")
    if (
        _strict_single(query, "amode") != "view"
        or not _IDENTITY_RE.fullmatch(identity)
        or _strict_single(query, source.key_name) != source.key_value
    ):
        return "", "course link identity/source contract changed"
    return identity, ""


def _yeyak_parse_page(
    source: MiryangYeyakSource,
    soup: BeautifulSoup,
    provider: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    result: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, wrapper in enumerate(soup.select("div.lst"), start=1):
        anchor = wrapper.select_one("a[href]")
        if anchor is None or "lectureId" not in _clean(anchor.get("href")):
            continue
        identity, link_error = _yeyak_link_identity(source, anchor.get("href"))
        if link_error:
            errors.append(f"{source.code} row {index}: {link_error}")
            continue
        title = _direct_title(anchor.select_one("strong.h1"))
        pairs = _pair_list(anchor.select_one("ul.clist"))
        missing = _YEYAK_REQUIRED_LIST_LABELS - set(pairs)
        if not title or missing:
            errors.append(
                f"{source.code} {identity}: missing title/list labels {sorted(missing)}"
            )
            continue
        apply_start, apply_end = _date_range(pairs["접수기간"])
        start, end = _date_range(pairs["교육기간"])
        raw_start = start
        raw_end = end
        start, end, date_corrected = _correct_yeyak_period(identity, start, end)
        if None in {apply_start, apply_end, start, end} or start > end or apply_start > apply_end:
            errors.append(f"{source.code} {identity}: invalid date range")
            continue
        source_status = _clean(anchor.get("data-progress"))
        normalized_status = _status(source_status)
        if not normalized_status:
            errors.append(f"{source.code} {identity}: unknown status {source_status!r}")
            continue
        methods = [
            _clean(node.get("data-progress") or node.get_text(" ", strip=True))
            for node in anchor.select("div.g2s span")
            if _clean(node.get("data-progress") or node.get_text(" ", strip=True))
        ]
        capacity_total, waitlist_total = _capacity_pair(
            pairs["온라인 정원/대기정원"]
        )
        current_capacity = _first_number(pairs["신청현황"])
        branch = (
            pairs["교육장소"]
            if source.code == "resident_centers"
            else source.name
        )
        raw_url = miryang_yeyak_detail_url(source, identity)
        result.append(
            {
                "provider": provider,
                "provider_course_id": f"{provider}:lecture:{identity}",
                "title": title,
                "branch": branch,
                "branch_code": _branch_code(branch),
                "category": pairs["교육분류"] or "교육",
                "raw_url": raw_url,
                "reservation_available": False,
                "status": normalized_status,
                "fee": pairs["수강료"] or "별도 안내",
                "period": _period(start, end),
                "apply_period": _period(apply_start, apply_end),
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "apply_start_date": apply_start.isoformat(),
                "apply_end_date": apply_end.isoformat(),
                "schedule_raw": pairs["요일시간"] or "별도 안내",
                "target": "전체",
                "capacity": pairs["온라인 정원/대기정원"],
                "capacity_current": current_capacity,
                "capacity_total": capacity_total,
                "waitlist_total": waitlist_total,
                "venue_name": pairs["교육장소"],
                "room": pairs["교육장소"],
                "description": title,
                "application_method_raw": ", ".join(methods),
                "application_type": (
                    "ONLINE_RESERVATION"
                    if "인터넷" in methods
                    else "OFFLINE_APPLICATION"
                    if methods
                    else "INFORMATION_ONLY"
                ),
                "municipality_code": MIRYANG_MUNICIPALITY_CODE,
                "municipality_name": MIRYANG_MUNICIPALITY_NAME,
                "region": "경상남도",
                "collection_type": "static_html+detail_html",
                "source_group": "municipal_integrated_reservation",
                "raw_fields": {
                    "parser": MIRYANG_YEYAK_PARSER,
                    "source_code": source.code,
                    "source_name": source.name,
                    "source_key_name": source.key_name,
                    "source_key_value": source.key_value,
                    "source_status": source_status,
                    "list_pairs": pairs,
                    "source_start_date": raw_start.isoformat() if raw_start else "",
                    "source_end_date": raw_end.isoformat() if raw_end else "",
                    "date_corrected": date_corrected,
                    "application_methods": methods,
                    "clear_application_url": True,
                },
            }
        )
    return result, errors


def _table_pairs(table: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    if table is None:
        return result
    for row in table.select("tr"):
        children = row.find_all(["th", "td"], recursive=False)
        index = 0
        while index < len(children):
            if children[index].name != "th":
                index += 1
                continue
            label = _clean(children[index].get_text(" ", strip=True))
            index += 1
            values: list[str] = []
            while index < len(children) and children[index].name == "td":
                values.append(_clean(children[index].get_text(" ", strip=True)))
                index += 1
            if label:
                result[label] = _clean(" ".join(values))
    return result


def _yeyak_application_identity(
    source: MiryangYeyakSource,
    href: Any,
    identity: str,
) -> bool:
    parsed = urlparse(urljoin(miryang_yeyak_detail_url(source, identity), _clean(href)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == MIRYANG_YEYAK_HOST
        and parsed.path == source.path
        and not parsed.fragment
        and set(query) == {"amode", source.key_name, "lectureId"}
        and _strict_single(query, "amode") == "agree"
        and _strict_single(query, source.key_name) == source.key_value
        and _strict_single(query, "lectureId") == identity
    )


def _yeyak_detail_contract(
    source: MiryangYeyakSource,
    row: dict[str, Any],
    soup: BeautifulSoup,
) -> list[str]:
    identity = _clean(row.get("provider_course_id")).rsplit(":", 1)[-1]
    errors: list[str] = []
    table = soup.select_one("table.t3")
    pairs = _table_pairs(table)
    missing = _YEYAK_REQUIRED_DETAIL_LABELS - set(pairs)
    if missing:
        errors.append(f"{identity}: missing detail labels {sorted(missing)}")
        return errors
    caption = _clean(table.select_one("caption").get_text(" ", strip=True)) if table.select_one("caption") else ""
    if _normalized(row.get("title")) not in _normalized(caption):
        errors.append(f"{identity}: detail title mismatch")
    detail_start, detail_end = _date_range(pairs["교육기간"])
    raw_detail_start = detail_start
    raw_detail_end = detail_end
    detail_start, detail_end, detail_date_corrected = _correct_yeyak_period(
        identity, detail_start, detail_end
    )
    apply_start, apply_end = _date_range(pairs["접수기간"])
    if _period(detail_start, detail_end) != _clean(row.get("period")):
        errors.append(f"{identity}: detail education period mismatch")
    if _period(apply_start, apply_end) != _clean(row.get("apply_period")):
        errors.append(f"{identity}: detail application period mismatch")
    for label, field in (
        ("교육분류", "category"),
        ("요일시간", "schedule_raw"),
        ("교육장소", "venue_name"),
    ):
        if _normalized(pairs[label]) != _normalized(row.get(field)):
            errors.append(f"{identity}: detail {label} mismatch")
    reserve_links = soup.select("a.button.reserve[href]")
    if len(reserve_links) > 1:
        errors.append(f"{identity}: multiple application links")
    application_url = ""
    if reserve_links:
        if not _yeyak_application_identity(
            source, reserve_links[0].get("href"), identity
        ):
            errors.append(f"{identity}: application URL contract changed")
        else:
            application_url = miryang_yeyak_application_url(source, identity)
    methods = row.get("raw_fields", {}).get("application_methods", [])
    if row.get("status") == "OPEN" and "인터넷" in methods and not application_url:
        errors.append(f"{identity}: open online course has no application URL")

    capacity_total, waitlist_total = _capacity_pair(
        pairs["온라인 정원/대기정원"]
    )
    row["capacity_total"] = capacity_total
    row["waitlist_total"] = waitlist_total
    row["capacity_current"] = _first_number(pairs["신청현황"])
    row["target"] = pairs["교육대상"] or row["target"]
    row["fee"] = pairs["수강료"] or row["fee"]
    row["description"] = pairs["교육과정"] or row["title"]
    row["reservation_available"] = bool(
        row.get("status") == "OPEN" and application_url
    )
    if application_url:
        row["application_url"] = application_url
        row["raw_fields"].pop("clear_application_url", None)
    row["raw_fields"].update(
        {
            "detail_pairs": pairs,
            "canonical_application_url": application_url,
            "detail_source_start_date": (
                raw_detail_start.isoformat() if raw_detail_start else ""
            ),
            "detail_source_end_date": (
                raw_detail_end.isoformat() if raw_detail_end else ""
            ),
            "detail_date_corrected": detail_date_corrected,
        }
    )
    return errors


def collect_miryang_yeyak_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 80,
    detail_limit: int = 200,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    if not is_miryang_yeyak_target(target):
        return [], MIRYANG_YEYAK_PARSER, _failure(
            MIRYANG_YEYAK_PARSER,
            "target does not match the canonical Miryang integrated-reservation anchor",
        )
    if fetcher is None or session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], MIRYANG_YEYAK_PARSER, _failure(
                MIRYANG_YEYAK_PARSER,
                "managed fetcher and session_factory injection are required",
            )
        fetcher = fetcher or _default_fetcher
        session_factory = session_factory or _default_session_factory

    cutoff = _today(today)
    allowed_pages = max(0, int(max_pages))
    allowed_details = max(0, int(detail_limit))
    requester = _Requester(fetcher, session_factory, timeout)
    errors: list[str] = []
    page_soups: dict[tuple[str, int], BeautifulSoup] = {}
    summaries: dict[str, tuple[int, int]] = {}
    page_counts: dict[str, dict[int, int]] = {}
    source_totals: dict[str, int] = {}
    sentinel_modes: dict[str, str] = {}
    all_rows: list[dict[str, Any]] = []
    detail_pages = 0
    detail_errors = 0
    required_list_requests = 0
    source_cap_reached = False
    inventory: set[str] = set()
    try:
        if allowed_pages < len(MIRYANG_YEYAK_SOURCES) * 2:
            source_cap_reached = True
            errors.append(
                f"max_pages cap allows {allowed_pages} of at least "
                f"{len(MIRYANG_YEYAK_SOURCES) * 2} required pages and sentinels"
            )
        if not errors:
            for source in MIRYANG_YEYAK_SOURCES:
                try:
                    soup = requester.get(miryang_yeyak_list_url(source, 1))
                    page_soups[(source.code, 1)] = soup
                except Exception as exc:
                    errors.append(f"{source.code} page 1: fetch {type(exc).__name__}")
                    continue
                total = _yeyak_total(soup)
                if total is None:
                    errors.append(f"{source.code} page 1: missing official total")
                    continue
                expected_last = max(1, math.ceil(total / MIRYANG_YEYAK_PAGE_SIZE))
                advertised_last = max(_query_page_values(soup, "cpage") or {1})
                if advertised_last != expected_last:
                    errors.append(
                        f"{source.code}: advertised page {advertised_last} != expected {expected_last}"
                    )
                summaries[source.code] = (total, expected_last)
            first = page_soups.get((MIRYANG_YEYAK_SOURCES[0].code, 1))
            if first is not None:
                inventory = _yeyak_inventory(first)
                expected_inventory = {
                    source.inventory_path for source in MIRYANG_YEYAK_SOURCES
                }
                if inventory != expected_inventory:
                    errors.append(
                        "education source inventory changed: "
                        f"{len(expected_inventory - inventory)} missing, "
                        f"{len(inventory - expected_inventory)} unexpected"
                    )

        if len(summaries) == len(MIRYANG_YEYAK_SOURCES):
            required_list_requests = sum(last + 1 for _total, last in summaries.values())
            if required_list_requests > allowed_pages:
                source_cap_reached = True
                errors.append(
                    f"max_pages cap allows {allowed_pages} of "
                    f"{required_list_requests} required pages and sentinels"
                )

        if not errors:
            for source in MIRYANG_YEYAK_SOURCES:
                _total, last = summaries[source.code]
                for page in range(2, last + 2):
                    try:
                        page_soups[(source.code, page)] = requester.get(
                            miryang_yeyak_list_url(source, page)
                        )
                    except Exception as exc:
                        errors.append(
                            f"{source.code} page {page}: fetch {type(exc).__name__}"
                        )
                        break

        if not errors:
            for source in MIRYANG_YEYAK_SOURCES:
                total, last = summaries[source.code]
                source_totals[source.code] = total
                page_counts[source.code] = {}
                final_ids: list[str] = []
                for page in range(1, last + 2):
                    soup = page_soups.get((source.code, page))
                    if soup is None:
                        errors.append(f"{source.code} page {page}: missing fetched page")
                        continue
                    if _yeyak_total(soup) != total:
                        errors.append(f"{source.code} page {page}: official total changed")
                    rows, parse_errors = _yeyak_parse_page(
                        source, soup, MIRYANG_YEYAK_PROVIDER
                    )
                    errors.extend(parse_errors)
                    page_counts[source.code][page] = len(rows)
                    identities = [_clean(row.get("provider_course_id")) for row in rows]
                    if page <= last:
                        expected_count = (
                            0
                            if total == 0
                            else min(
                                MIRYANG_YEYAK_PAGE_SIZE,
                                total - (page - 1) * MIRYANG_YEYAK_PAGE_SIZE,
                            )
                        )
                        if len(rows) != expected_count:
                            errors.append(
                                f"{source.code} page {page}: expected {expected_count} rows, got {len(rows)}"
                            )
                        all_rows.extend(rows)
                        if page == last:
                            final_ids = identities
                    else:
                        if not identities:
                            sentinel_modes[source.code] = "empty"
                        elif identities == final_ids:
                            sentinel_modes[source.code] = "clamped_final_page"
                        else:
                            errors.append(
                                f"{source.code}: sentinel is neither empty nor an exact final-page clamp"
                            )

        declared_total = sum(source_totals.values())
        if source_totals and len(all_rows) != declared_total:
            errors.append(
                f"declared aggregate total {declared_total} != parsed rows {len(all_rows)}"
            )
        identities = [_clean(row.get("provider_course_id")) for row in all_rows]
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count:
            errors.append(f"{duplicate_count} duplicate provider course identities")
        urls = [_clean(row.get("raw_url")) for row in all_rows]
        duplicate_url_count = len(urls) - len(set(urls))
        if duplicate_url_count:
            errors.append(f"{duplicate_url_count} duplicate canonical course URLs")

        current_rows: list[dict[str, Any]] = []
        expired_count = 0
        for row in all_rows:
            try:
                end = date.fromisoformat(_clean(row.get("end_date")))
            except ValueError:
                errors.append(f"{_clean(row.get('provider_course_id'))}: invalid end date")
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
            source_by_code = {source.code: source for source in MIRYANG_YEYAK_SOURCES}
            for row in current_rows:
                source = source_by_code[_clean(row["raw_fields"].get("source_code"))]
                try:
                    soup = requester.get(_clean(row.get("raw_url")))
                    detail_pages += 1
                    row_errors = _yeyak_detail_contract(source, row, soup)
                    detail_errors += len(row_errors)
                    errors.extend(row_errors)
                except Exception as exc:
                    detail_errors += 1
                    errors.append(
                        f"{_clean(row.get('provider_course_id'))}: detail fetch {type(exc).__name__}"
                    )

        signatures = [_semantic_signature(row) for row in current_rows]
        semantic_duplicate_count = len(signatures) - len(set(signatures))
        if semantic_duplicate_count:
            errors.append(
                f"{semantic_duplicate_count} duplicate current semantic course signatures"
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
        current_source_counts = Counter(
            _clean(row.get("raw_fields", {}).get("source_code"))
            for row in current_rows
        )
        snapshot_complete = not errors
        meta = {
            "parser": MIRYANG_YEYAK_PARSER,
            "pages": len(page_soups),
            "list_requests": len(page_soups),
            "request_count": requester.calls,
            "session_count": requester.sessions,
            "detail_pages": detail_pages,
            "source_count": len(MIRYANG_YEYAK_SOURCES),
            "inventory_count": len(inventory),
            "source_totals": source_totals,
            "source_total": declared_total,
            "source_rows": len(all_rows),
            "required_list_requests": required_list_requests,
            "page_counts": page_counts,
            "sentinel_modes": sentinel_modes,
            "expired_count": expired_count,
            "current_count": len(current_rows),
            "returned_count": len(result),
            "current_source_counts": dict(current_source_counts),
            "date_correction_count": sum(
                bool(row.get("raw_fields", {}).get("date_corrected"))
                for row in all_rows
            ),
            "branch_count": len(branch_counts),
            "branch_counts": dict(branch_counts),
            "status_counts": dict(status_counts),
            "duplicate_count": duplicate_count,
            "duplicate_url_count": duplicate_url_count,
            "semantic_duplicate_count": semantic_duplicate_count,
            "detail_errors": detail_errors,
            "discovered_links": len(all_rows),
            "reservation_discovery_links": sum(
                bool(row.get("application_url")) for row in result
            ),
            "pagination_detected": any(last > 1 for _total, last in summaries.values()),
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
                "all complete Miryang integrated-reservation education courses have ended"
                if snapshot_complete and not result
                else ""
            ),
            "configured_collection_error": "; ".join(errors),
        }
        if errors:
            return [], MIRYANG_YEYAK_PARSER, meta
        return result, MIRYANG_YEYAK_PARSER, meta
    finally:
        requester.close()


def _lifelong_link_identity(href: Any) -> tuple[str, str]:
    parsed = urlparse(urljoin(MIRYANG_LIFELONG_URL, _clean(href)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    identity = _strict_single(query, "idx")
    allowed = {"mod", "idx", "ci", "kind", "ky", "wd", "st", "page"}
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != MIRYANG_LIFELONG_HOST
        or parsed.path != MIRYANG_LIFELONG_PATH
        or parsed.fragment
        or set(query) - allowed
        or _strict_single(query, "mod") != "o"
        or not identity.isdigit()
    ):
        return "", "lifelong detail link contract changed"
    return identity, ""


def _lifelong_parse_page(
    soup: BeautifulSoup,
    provider: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    table = soup.select_one("table.basic_edu")
    if table is None:
        return [], ["missing lifelong course table"]
    headers = tuple(_clean(node.get_text(" ", strip=True)) for node in table.select("thead th"))
    if headers != _LIFELONG_HEADERS:
        return [], [f"lifelong headers changed: {headers!r}"]
    result: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, tr in enumerate(table.select("tbody > tr"), start=1):
        cells = tr.find_all("td", recursive=False)
        if len(cells) != 5:
            errors.append(f"lifelong row {index}: expected 5 cells, got {len(cells)}")
            continue
        anchor = cells[2].find("a", href=lambda href: href and "mod=o" in href)
        if anchor is None:
            errors.append(f"lifelong row {index}: missing detail link")
            continue
        identity, link_error = _lifelong_link_identity(anchor.get("href"))
        if link_error:
            errors.append(f"lifelong row {index}: {link_error}")
            continue
        title = _clean(anchor.get_text(" ", strip=True))
        branch = _clean(cells[1].get_text(" ", strip=True)) or MIRYANG_MUNICIPALITY_NAME
        dates = _date_values(cells[3].get_text(" ", strip=True))
        if len(dates) != 4:
            errors.append(f"lifelong {identity}: expected four list dates, got {len(dates)}")
            continue
        apply_start, apply_end, start, end = dates
        if apply_start > apply_end or start > end:
            errors.append(f"lifelong {identity}: invalid date range")
            continue
        source_status = _clean(cells[4].get_text(" ", strip=True))
        normalized_status = _status(source_status)
        if not normalized_status:
            errors.append(f"lifelong {identity}: unknown status {source_status!r}")
            continue
        capacity_text = _clean(cells[2].get_text(" ", strip=True))
        raw_url = miryang_lifelong_detail_url(identity)
        result.append(
            {
                "provider": provider,
                "provider_course_id": f"{provider}:course:{identity}",
                "title": title,
                "branch": branch,
                "branch_code": _branch_code(branch),
                "category": "평생학습",
                "raw_url": raw_url,
                "reservation_available": False,
                "status": normalized_status,
                "fee": "별도 안내",
                "period": _period(start, end),
                "apply_period": _period(apply_start, apply_end),
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "apply_start_date": apply_start.isoformat(),
                "apply_end_date": apply_end.isoformat(),
                "schedule_raw": "별도 안내",
                "target": "전체",
                "capacity": capacity_text,
                "capacity_current": None,
                "capacity_total": None,
                "venue_name": branch,
                "room": "",
                "description": title,
                "application_type": "INFORMATION_ONLY",
                "municipality_code": MIRYANG_MUNICIPALITY_CODE,
                "municipality_name": MIRYANG_MUNICIPALITY_NAME,
                "region": "경상남도",
                "collection_type": "static_html+detail_html",
                "source_group": "municipal_lifelong_learning",
                "raw_fields": {
                    "parser": MIRYANG_LIFELONG_PARSER,
                    "source_status": source_status,
                    "list_capacity_text": capacity_text,
                    "clear_application_url": True,
                },
            }
        )
    return result, errors


def _description_value(value: str, label: str) -> str:
    match = re.search(
        rf"{re.escape(label)}\s*:\s*(.+?)(?=\s+[○●※]\s*|\s*/\s*[○●※]|$)",
        _clean(value),
    )
    return _clean(match.group(1)) if match else ""


def _lifelong_detail_contract(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    identity = _clean(row.get("provider_course_id")).rsplit(":", 1)[-1]
    errors: list[str] = []
    selected = None
    for table in soup.select("table"):
        labels = {_clean(node.get_text(" ", strip=True)) for node in table.select("th")}
        if "접수기간" in labels and "교육기간" in labels:
            selected = table
            break
    pairs = _table_pairs(selected)
    missing = _LIFELONG_REQUIRED_DETAIL_LABELS - set(pairs)
    if missing:
        errors.append(f"{identity}: missing lifelong detail labels {sorted(missing)}")
        return errors
    if _period(*_date_range(pairs["접수기간"])) != _clean(row.get("apply_period")):
        errors.append(f"{identity}: detail application period mismatch")
    if _period(*_date_range(pairs["교육기간"])) != _clean(row.get("period")):
        errors.append(f"{identity}: detail education period mismatch")
    description = pairs["교육과정"]
    if _normalized(row.get("title")) not in _normalized(description):
        errors.append(f"{identity}: detail title mismatch")
    venue = _description_value(description, "교육장소") or pairs["위치"]
    target = pairs["모집대상"] or _description_value(description, "교육대상")
    schedule = pairs["교육시간"]
    if not schedule:
        schedule_match = re.search(
            r"(?:매주\s*)?[월화수목금토일]+(?:요일)?\s*\d{1,2}:\d{2}\s*~\s*\d{1,2}:\d{2}",
            description,
        )
        schedule = _clean(schedule_match.group(0)) if schedule_match else "별도 안내"
    fee = pairs["수강료"]
    if "Warning" in fee or not fee:
        fee = "무료" if "무료" in description else "별도 안내"
    material_fee = ""
    material_match = re.search(r"(?:교재비|재료비)[^\d]{0,20}([\d,]+\s*원)", description)
    if material_match:
        material_fee = _clean(material_match.group(1))
    capacity_total = _first_number(pairs["모집인원"])
    capacity_current = _first_number(pairs["신청인원"])
    if venue:
        branch = re.sub(r"\s*\([^)]*\)\s*$", "", venue).strip() or venue
        row["branch"] = branch
        row["branch_code"] = _branch_code(branch)
        row["venue_name"] = venue
        row["room"] = venue
    row["target"] = target or row["target"]
    row["schedule_raw"] = schedule
    row["fee"] = fee
    row["capacity_total"] = capacity_total
    row["capacity_current"] = capacity_current
    row["description"] = description or row["title"]
    if material_fee:
        row["material_fee"] = material_fee
    row["raw_fields"]["detail_pairs"] = pairs

    application_candidates: list[str] = []
    for anchor in selected.select("a[href]") if selected is not None else []:
        text = _clean(anchor.get_text(" ", strip=True))
        href = _clean(anchor.get("href"))
        if "신청" in text and ("mod=w" in href or "apply" in href.lower()):
            candidate = urljoin(row["raw_url"], href)
            parsed_candidate = urlparse(candidate)
            candidate_query = parse_qs(
                parsed_candidate.query, keep_blank_values=True
            )
            if (
                parsed_candidate.path == MIRYANG_LIFELONG_PATH
                and _strict_single(candidate_query, "idx") == identity
            ):
                application_candidates.append(candidate)
    if len(set(application_candidates)) > 1:
        errors.append(f"{identity}: multiple lifelong application URLs")
    elif application_candidates:
        application_url = application_candidates[0]
        parsed = urlparse(application_url)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() != MIRYANG_LIFELONG_HOST:
            errors.append(f"{identity}: lifelong application URL escaped official host")
        else:
            row["application_url"] = application_url
            row["application_type"] = "ONLINE_RESERVATION"
            row["reservation_available"] = row.get("status") == "OPEN"
            row["raw_fields"].pop("clear_application_url", None)
    return errors


def collect_miryang_lifelong_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 30,
    detail_limit: int = 100,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    if not is_miryang_lifelong_target(target):
        return [], MIRYANG_LIFELONG_PARSER, _failure(
            MIRYANG_LIFELONG_PARSER,
            "target does not match the canonical Miryang lifelong catalogue",
        )
    if fetcher is None or session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], MIRYANG_LIFELONG_PARSER, _failure(
                MIRYANG_LIFELONG_PARSER,
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
    all_rows: list[dict[str, Any]] = []
    detail_pages = 0
    detail_errors = 0
    source_cap_reached = False
    total = 0
    last = 0
    required_list_requests = 0
    try:
        try:
            page_soups[1] = requester.get(miryang_lifelong_list_url(1))
        except Exception as exc:
            errors.append(f"lifelong page 1: fetch {type(exc).__name__}")
        if 1 in page_soups:
            source_total = _lifelong_total(page_soups[1])
            if source_total is None:
                errors.append("lifelong page 1: missing official total")
            else:
                total = source_total
                last = max(1, math.ceil(total / MIRYANG_LIFELONG_PAGE_SIZE))
                advertised_pages = _query_page_values(page_soups[1], "page")
                if (
                    1 not in advertised_pages
                    or max(advertised_pages or {0}) > last
                    or (last > 1 and max(advertised_pages or {0}) <= 1)
                ):
                    errors.append("lifelong first-page navigation contract changed")
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
                    page_soups[page] = requester.get(miryang_lifelong_list_url(page))
                except Exception as exc:
                    errors.append(f"lifelong page {page}: fetch {type(exc).__name__}")
                    break

        page_counts: dict[int, int] = {}
        if not errors:
            for page in range(1, last + 2):
                soup = page_soups.get(page)
                if soup is None:
                    errors.append(f"lifelong page {page}: missing fetched page")
                    continue
                if _lifelong_total(soup) != total:
                    errors.append(f"lifelong page {page}: official total changed")
                rows, parse_errors = _lifelong_parse_page(
                    soup, MIRYANG_LIFELONG_PROVIDER
                )
                errors.extend(parse_errors)
                page_counts[page] = len(rows)
                if page <= last:
                    expected_count = (
                        0
                        if total == 0
                        else min(
                            MIRYANG_LIFELONG_PAGE_SIZE,
                            total - (page - 1) * MIRYANG_LIFELONG_PAGE_SIZE,
                        )
                    )
                    if len(rows) != expected_count:
                        errors.append(
                            f"lifelong page {page}: expected {expected_count} rows, got {len(rows)}"
                        )
                    all_rows.extend(rows)
                elif rows:
                    errors.append("lifelong sentinel page is not empty")

        if len(all_rows) != total:
            errors.append(f"lifelong official total {total} != parsed rows {len(all_rows)}")
        identities = [_clean(row.get("provider_course_id")) for row in all_rows]
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count:
            errors.append(f"{duplicate_count} duplicate lifelong identities")
        urls = [_clean(row.get("raw_url")) for row in all_rows]
        duplicate_url_count = len(urls) - len(set(urls))
        if duplicate_url_count:
            errors.append(f"{duplicate_url_count} duplicate lifelong URLs")

        current_rows: list[dict[str, Any]] = []
        expired_count = 0
        for row in all_rows:
            try:
                end = date.fromisoformat(_clean(row.get("end_date")))
            except ValueError:
                errors.append(f"{_clean(row.get('provider_course_id'))}: invalid end date")
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
                    soup = requester.get(_clean(row.get("raw_url")))
                    detail_pages += 1
                    row_errors = _lifelong_detail_contract(row, soup)
                    detail_errors += len(row_errors)
                    errors.extend(row_errors)
                except Exception as exc:
                    detail_errors += 1
                    errors.append(
                        f"{_clean(row.get('provider_course_id'))}: detail fetch {type(exc).__name__}"
                    )

        signatures = [_semantic_signature(row) for row in current_rows]
        semantic_duplicate_count = len(signatures) - len(set(signatures))
        if semantic_duplicate_count:
            errors.append(
                f"{semantic_duplicate_count} duplicate current lifelong semantic signatures"
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
            "parser": MIRYANG_LIFELONG_PARSER,
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
            "sentinel_mode": "empty" if page_counts.get(last + 1) == 0 else "",
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
            "discovered_links": len(all_rows),
            "reservation_discovery_links": sum(
                bool(row.get("application_url")) for row in result
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
                "all complete Miryang lifelong-learning courses have ended"
                if snapshot_complete and not result
                else ""
            ),
            "configured_collection_error": "; ".join(errors),
        }
        if errors:
            return [], MIRYANG_LIFELONG_PARSER, meta
        return result, MIRYANG_LIFELONG_PARSER, meta
    finally:
        requester.close()


def collect_miryang_education_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 80,
    detail_limit: int = 200,
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    if is_miryang_yeyak_target(target):
        return collect_miryang_yeyak_courses(
            target,
            timeout=timeout,
            max_pages=max_pages,
            detail_limit=detail_limit,
            **kwargs,
        )
    if is_miryang_lifelong_target(target):
        return collect_miryang_lifelong_courses(
            target,
            timeout=timeout,
            max_pages=max_pages,
            detail_limit=detail_limit,
            **kwargs,
        )
    return [], MIRYANG_YEYAK_PARSER, _failure(
        MIRYANG_YEYAK_PARSER,
        "target does not match a canonical Miryang education provider route",
    )


is_target = is_miryang_education_target
collect = collect_miryang_education_courses


__all__ = [
    "MIRYANG_EXTERNAL_EDUCATION_DESTINATIONS",
    "MIRYANG_LIFELONG_DUPLICATE_FILTER_URLS",
    "MIRYANG_LIFELONG_PAGE_SIZE",
    "MIRYANG_LIFELONG_PARSER",
    "MIRYANG_LIFELONG_PROVIDER",
    "MIRYANG_LIFELONG_RETIRED_ARCHIVE_URLS",
    "MIRYANG_LIFELONG_URL",
    "MIRYANG_STATIC_INFO_URLS",
    "MIRYANG_YEYAK_DUPLICATE_ALIAS_URLS",
    "MIRYANG_YEYAK_DATE_CORRECTIONS",
    "MIRYANG_YEYAK_PAGE_SIZE",
    "MIRYANG_YEYAK_PARSER",
    "MIRYANG_YEYAK_PARTIAL_API_URL",
    "MIRYANG_YEYAK_PROVIDER",
    "MIRYANG_YEYAK_SOURCES",
    "MIRYANG_YEYAK_URL",
    "MIRYANG_YEYAK_WRONG_CATEGORY_URLS",
    "MiryangYeyakSource",
    "collect_miryang_education_courses",
    "collect_miryang_lifelong_courses",
    "collect_miryang_yeyak_courses",
    "is_miryang_education_target",
    "is_miryang_lifelong_target",
    "is_miryang_yeyak_target",
    "miryang_lifelong_detail_url",
    "miryang_lifelong_list_url",
    "miryang_yeyak_application_url",
    "miryang_yeyak_detail_url",
    "miryang_yeyak_list_url",
]
