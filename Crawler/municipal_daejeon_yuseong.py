"""Atomic collector for Daejeon Yuseong-gu's official learning catalogue.

The public root and the ``SEARCH`` list are aliases, not complete stores.  The
official landing menu exposes nine disjoint education leaves.  In the live
audit on 2026-07-21 their union contained 210 identities while ``SEARCH``
contained 205; the five omissions were all in ``별별인문학``.  ``REGULAR`` is
another alias and is exactly the union of the Gu-am and Jeonmin leaves.

This module therefore treats the nine menu leaves as one owner.  It emits a
snapshot only after validating the landing menu, every page of all leaves and
both aggregate aliases, the immediate post-last sentinels, stable page-one
rechecks, alias/set relationships, and every current/future education detail.
Instructor names, contacts, applicant counts, descriptions, and attachments
are deliberately excluded from persisted rows.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import html
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


DAEJEON_YUSEONG_PROVIDER = "MUNI_LIFELONG_YUSEONG_GO_KR_E36DECD2"
DAEJEON_YUSEONG_CANONICAL_CANDIDATE_ID = "MUNI_IR_6F8200A35D1B"
DAEJEON_YUSEONG_CANDIDATE_IDS = (
    "MUNI_IR_6F8200A35D1B",
    "MUNI_IR_70BBAFE5E162",
    "MUNI_IR_BDE4E2B1625F",
)
DAEJEON_YUSEONG_LEGACY_PROVIDERS = (
    "MUNI_LIFELONG_YUSEONG_GO_KR_B72EE2A7",
    "MUNI_LIFELONG_YUSEONG_GO_KR_D5551B66",
)
DAEJEON_YUSEONG_HOST = "lifelong.yuseong.go.kr"
DAEJEON_YUSEONG_MUNICIPALITY_CODE = "3020000000"
DAEJEON_YUSEONG_MUNICIPALITY_NAME = "대전광역시 유성구"
DAEJEON_YUSEONG_LANDING_URL = f"https://{DAEJEON_YUSEONG_HOST}/"
DAEJEON_YUSEONG_LANDING_PATH = "/lly/lly/index.do"
DAEJEON_YUSEONG_CANONICAL_PATH = (
    "/lly/prog/lctr/lly/sub02_01/SEARCH/classList.do"
)
DAEJEON_YUSEONG_CANONICAL_URL = (
    f"https://{DAEJEON_YUSEONG_HOST}{DAEJEON_YUSEONG_CANONICAL_PATH}"
)
DAEJEON_YUSEONG_DETAIL_PATH = (
    "/lly/prog/lctr/lly/sub02_01/SEARCH/classDetail.do"
)
DAEJEON_YUSEONG_APPLICATION_PATH = (
    "/lly/prog/lctrAply/lly/sub02_01/SEARCH/classReceive.do"
)
DAEJEON_YUSEONG_PAGE_SIZE = 10
DAEJEON_YUSEONG_MAX_WORKERS = 6
DAEJEON_YUSEONG_FETCH_ATTEMPTS = 2
DAEJEON_YUSEONG_PARSER = (
    "daejeon_yuseong_official_9_leaf_union+aggregate_alias_audit+"
    "all_pages+empty_sentinels+stable_rechecks+current_details+pii_allowlist"
)
DAEJEON_YUSEONG_OWNERSHIP_SCOPE = (
    "yuseong_lifelong_official_nine_disjoint_education_menus"
)


@dataclass(frozen=True)
class YuseongCatalogue:
    key: str
    heading: str
    branch: str
    branch_code: str
    path: str
    group_type: str
    group_code: str
    leaf: bool

    @property
    def list_url(self) -> str:
        return f"https://{DAEJEON_YUSEONG_HOST}{self.path}"


DAEJEON_YUSEONG_CATALOGUES: tuple[YuseongCatalogue, ...] = (
    YuseongCatalogue(
        "all",
        "내게맞는 강좌 찾기",
        "",
        "",
        DAEJEON_YUSEONG_CANONICAL_PATH,
        "",
        "",
        False,
    ),
    YuseongCatalogue(
        "regular",
        "정규강좌",
        "",
        "",
        "/lly/prog/lctr/lly/sub02_02/REGULAR/classList.do",
        "",
        "REGULAR",
        False,
    ),
    YuseongCatalogue(
        "guam",
        "구암센터",
        "구암평생학습센터",
        "YUSEONG_LIFELONG_001",
        "/lly/prog/lctr/lly/sub02_02_03/LIFELONG_001/classList.do",
        "01",
        "LIFELONG_001",
        True,
    ),
    YuseongCatalogue(
        "jeonmin",
        "전민센터",
        "전민평생학습센터",
        "YUSEONG_LIFELONG_002",
        "/lly/prog/lctr/lly/sub02_02_04/LIFELONG_002/classList.do",
        "01",
        "LIFELONG_002",
        True,
    ),
    YuseongCatalogue(
        "youth_5060",
        "5060청춘대학",
        "5060 청춘대학",
        "YUSEONG_LIFELONG_003",
        "/lly/prog/lctr/lly/sub02_03/LIFELONG_003/classList.do",
        "02",
        "LIFELONG_003",
        True,
    ),
    YuseongCatalogue(
        "linku",
        "링크유마을캠퍼스",
        "링크유마을캠퍼스",
        "YUSEONG_LIFELONG_025",
        "/lly/prog/lctr/lly/sub02_04/LIFELONG_025/classList.do",
        "02",
        "LIFELONG_025",
        True,
    ),
    YuseongCatalogue(
        "special",
        "특별강좌",
        "특별강좌",
        "YUSEONG_LIFELONG_008",
        "/lly/prog/lctr/lly/sub02_07/LIFELONG_008/classList.do",
        "02",
        "LIFELONG_008",
        True,
    ),
    YuseongCatalogue(
        "oneday",
        "원데이클래스",
        "원데이클래스",
        "YUSEONG_LIFELONG_016",
        "/lly/prog/lctr/lly/sub02_06/LIFELONG_016/classList.do",
        "02",
        "LIFELONG_016",
        True,
    ),
    YuseongCatalogue(
        "humanities",
        "별별인문학",
        "별별인문학",
        "YUSEONG_LIFELONG_004",
        "/lly/prog/lctr/lly/sub02_05/LIFELONG_004/classList.do",
        "02",
        "LIFELONG_004",
        True,
    ),
    YuseongCatalogue(
        "disabled",
        "장애인 평생교육",
        "장애인 평생교육",
        "YUSEONG_LIFELONG_024",
        "/lly/prog/lctr/lly/sub02_12/LIFELONG_024/classList.do",
        "02",
        "LIFELONG_024",
        True,
    ),
    YuseongCatalogue(
        "slow_learner",
        "느린학습자",
        "느린학습자",
        "YUSEONG_LIFELONG_026",
        "/lly/prog/lctr/lly/sub02_14/LIFELONG_026/classList.do",
        "02",
        "LIFELONG_026",
        True,
    ),
)
DAEJEON_YUSEONG_CATALOGUE_BY_KEY = {
    item.key: item for item in DAEJEON_YUSEONG_CATALOGUES
}
DAEJEON_YUSEONG_LEAF_CATALOGUES = tuple(
    item for item in DAEJEON_YUSEONG_CATALOGUES if item.leaf
)
DAEJEON_YUSEONG_OFFICIAL_BRANCH_NAMES = tuple(
    item.branch for item in DAEJEON_YUSEONG_LEAF_CATALOGUES
)
DAEJEON_YUSEONG_REGULAR_URL = DAEJEON_YUSEONG_CATALOGUE_BY_KEY[
    "regular"
].list_url
DAEJEON_YUSEONG_JEONMIN_URL = DAEJEON_YUSEONG_CATALOGUE_BY_KEY[
    "jeonmin"
].list_url
DAEJEON_YUSEONG_SPECIAL_URL = DAEJEON_YUSEONG_CATALOGUE_BY_KEY[
    "special"
].list_url
DAEJEON_YUSEONG_OWNED_ALIAS_URLS = (
    DAEJEON_YUSEONG_LANDING_URL,
    f"https://{DAEJEON_YUSEONG_HOST}{DAEJEON_YUSEONG_LANDING_PATH}",
    DAEJEON_YUSEONG_REGULAR_URL,
    *(item.list_url for item in DAEJEON_YUSEONG_LEAF_CATALOGUES),
)

_EXPECTED_LANDING_PATHS = frozenset(
    item.path for item in DAEJEON_YUSEONG_CATALOGUES
)
_EXPECTED_MENU_TEXT: Mapping[str, frozenset[str]] = {
    DAEJEON_YUSEONG_CANONICAL_PATH: frozenset(
        {"온라인신청", "내게맞는 강좌 찾기"}
    ),
    **{
        item.path: frozenset({item.heading})
        for item in DAEJEON_YUSEONG_CATALOGUES
        if item.key != "all"
    },
}


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DIGITS_RE = re.compile(r"\d+")
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)")
_DATE_TIME_RE = re.compile(
    r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?:\s+(\d{2}:\d{2}))?(?!\d)"
)
_TOTAL_RE = re.compile(
    r"Total\s*([\d,]+)\s*개.*?페이지\s*\(\s*(\d+)\s*/\s*(\d+)\s*\)",
    flags=re.IGNORECASE,
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2})[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_KNOWN_STATUSES = frozenset(
    {"접수예정", "접수중", "대기자 접수중", "접수마감", "폐강"}
)
_OPEN_STATUSES = frozenset({"접수중", "대기자 접수중"})
_STATUS_MAP: Mapping[str, str] = {
    "접수예정": "SCHEDULED",
    "접수중": "OPEN",
    "대기자 접수중": "OPEN",
    "접수마감": "CLOSED",
    "폐강": "CLOSED",
}
_REQUIRED_CARD_FIELDS = frozenset(
    {"접수기간", "수강료", "교육기간", "모집인원", "교육일시", "교육대상"}
)
_SAFE_ROW_KEYS = frozenset(
    {
        "provider",
        "provider_course_id",
        "title",
        "branch",
        "branch_code",
        "category",
        "raw_url",
        "application_url",
        "status",
        "fee",
        "period",
        "start_date",
        "end_date",
        "apply_period",
        "schedule_raw",
        "target",
        "capacity",
        "raw_fields",
    }
)
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "catalogue",
        "official_menu_name",
        "source_page",
        "source_status",
        "source_education_period",
        "source_application_period",
        "partition",
        "detail_verified",
        "application_control_present",
        "application_control_contract",
    }
)


@dataclass
class PageAudit:
    total: int
    current_page: int
    last_page: int
    rows: list[dict[str, Any]]
    signature: str
    errors: list[str]


@dataclass
class LandingAudit:
    menu_paths: frozenset[str]
    errors: list[str]


@dataclass
class DetailAudit:
    identity: str
    application_control: bool
    errors: list[str]


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(
        " ", html.unescape(str(value or "")).replace("\xa0", " ")
    ).strip()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _exact_https_url(value: Any, path: str, *, allow_query: bool = False) -> bool:
    parsed = urlparse(_clean(value))
    return bool(
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == DAEJEON_YUSEONG_HOST
        and parsed.port is None
        and parsed.path == path
        and (allow_query or not parsed.query)
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


def is_daejeon_yuseong_education_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")) == DAEJEON_YUSEONG_PROVIDER
        and _exact_https_url(
            _target_value(target, "url"), DAEJEON_YUSEONG_CANONICAL_PATH
        )
    )


def is_daejeon_yuseong_owned_alias_target(target: Any) -> bool:
    value = _clean(_target_value(target, "url"))
    if value in DAEJEON_YUSEONG_OWNED_ALIAS_URLS:
        return True
    parsed = urlparse(value)
    if not _exact_https_url(value, parsed.path, allow_query=True):
        return False
    if parsed.path != DAEJEON_YUSEONG_DETAIL_PATH:
        return False
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    return bool(
        len(pairs) == 1
        and pairs[0][0] == "lctrNo"
        and _DIGITS_RE.fullmatch(pairs[0][1])
    )


is_target = is_daejeon_yuseong_education_target


def daejeon_yuseong_list_url(catalogue_key: Any, page: Any = 1) -> str:
    catalogue = DAEJEON_YUSEONG_CATALOGUE_BY_KEY.get(_clean(catalogue_key))
    raw_page = _clean(page)
    if catalogue is None or not raw_page.isdigit() or int(raw_page) < 1:
        return ""
    return catalogue.list_url + "?" + urlencode({"pageIndex": int(raw_page)})


def daejeon_yuseong_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _DIGITS_RE.fullmatch(value):
        return ""
    return (
        f"https://{DAEJEON_YUSEONG_HOST}{DAEJEON_YUSEONG_DETAIL_PATH}?"
        + urlencode({"lctrNo": value})
    )


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(ZoneInfo("Asia/Seoul")).date()
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(_clean(value))


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    return current


def _default_fetcher(current: Any, url: str, timeout: int) -> Any:
    last_error: Optional[Exception] = None
    for _attempt in range(DAEJEON_YUSEONG_FETCH_ATTEMPTS):
        try:
            response = current.get(url, timeout=timeout)
            response.raise_for_status()
            final = urlparse(_clean(getattr(response, "url", url)))
            if (final.hostname or "").rstrip(".").lower() != DAEJEON_YUSEONG_HOST:
                raise ValueError("official request redirected off host")
            return response
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _response_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    status = getattr(value, "status_code", None)
    if status is not None and not (200 <= int(status) < 300):
        raise ValueError(f"unexpected HTTP status {status}")
    if isinstance(value, (str, bytes, bytearray)):
        payload = value
    else:
        payload = getattr(value, "content", None)
        if payload is None:
            payload = getattr(value, "text", None)
    if not payload:
        raise ValueError("empty HTML response")
    return BeautifulSoup(payload, "lxml")


def _validate_final_url(response: Any, requested: str, expected_path: str) -> list[str]:
    value = _clean(getattr(response, "url", requested) or requested)
    parsed = urlparse(value)
    errors: list[str] = []
    if not _exact_https_url(value, expected_path, allow_query=True):
        errors.append("response final URL changed")
        return errors
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    requested_pairs = parse_qsl(urlparse(requested).query, keep_blank_values=True)
    if expected_path == DAEJEON_YUSEONG_LANDING_PATH:
        if pairs:
            errors.append("landing final URL gained query parameters")
    elif pairs != requested_pairs:
        errors.append("response query changed")
    return errors


def _fetch_parsed_many(
    items: Iterable[tuple[Any, str]],
    *,
    parser: Callable[[Any, str, Any], Any],
    fetcher: Fetcher,
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
) -> tuple[dict[Any, Any], list[str]]:
    materialized = list(items)
    results: dict[Any, Any] = {}
    errors: list[str] = []

    def work(key: Any, url: str) -> tuple[Any, Any]:
        current = session_factory()
        try:
            response = fetcher(current, url, timeout)
            return key, parser(key, url, response)
        finally:
            _close_quietly(current)

    workers = max(1, min(max_workers, len(materialized) or 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(work, key, url): (key, url) for key, url in materialized
        }
        for future in as_completed(futures):
            key, _url = futures[future]
            try:
                result_key, parsed = future.result()
                results[result_key] = parsed
            except Exception as exc:
                errors.append(f"{key}: {_clean(exc)}")
    return results, errors


def _parse_landing(_key: Any, requested: str, response: Any) -> LandingAudit:
    errors = _validate_final_url(
        response, f"https://{DAEJEON_YUSEONG_HOST}{DAEJEON_YUSEONG_LANDING_PATH}",
        DAEJEON_YUSEONG_LANDING_PATH,
    )
    soup = _response_soup(response)
    page_title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if "유성구 평생학습센터" not in page_title:
        errors.append("landing official title changed")
    texts: dict[str, set[str]] = {}
    for link in soup.select("a[href]"):
        absolute = urljoin(requested, _clean(link.get("href")))
        parsed = urlparse(absolute)
        if (
            (parsed.hostname or "").rstrip(".").lower()
            == DAEJEON_YUSEONG_HOST
            and parsed.path.startswith("/lly/prog/lctr/")
            and parsed.path.endswith("/classList.do")
        ):
            texts.setdefault(parsed.path, set()).add(
                _clean(link.get_text(" ", strip=True))
            )
    paths = frozenset(texts)
    if paths != _EXPECTED_LANDING_PATHS:
        errors.append("landing official education menu fan-out changed")
    for path, expected in _EXPECTED_MENU_TEXT.items():
        if path in texts and not (texts[path] & expected):
            errors.append(f"landing menu label changed for {path}")
    return LandingAudit(paths, errors)


def _date_values(value: Any) -> list[date]:
    return [date(int(y), int(m), int(d)) for y, m, d in _DATE_RE.findall(_clean(value))]


def _date_time_values(value: Any) -> list[str]:
    return [f"{day}{' ' + clock if clock else ''}" for day, clock in _DATE_TIME_RE.findall(_clean(value))]


def _partition(title: Any) -> str:
    value = _clean(title)
    if value == "재능기부자 모집":
        return "recruitment"
    if re.search(r"(?:대관|시설\s*(?:예약|이용)|체력단련실)", value):
        return "facility"
    if re.search(r"(?:\[공연\]|공연\s*관람|영화\s*상영|콘서트)", value):
        return "performance"
    if re.search(r"(?:\[체험\]|체험\s*예약)", value):
        return "experience"
    return "education"


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> str:
    values = [
        "\x1f".join(
            (
                _clean(row.get("raw_fields", {}).get("identity")),
                _clean(row.get("title")),
                _clean(row.get("start_date")),
                _clean(row.get("end_date")),
            )
        )
        for row in rows
    ]
    return hashlib.sha256("\x1e".join(values).encode("utf-8")).hexdigest()


def _parse_card(
    card: Any,
    catalogue: YuseongCatalogue,
    page: int,
) -> tuple[Optional[dict[str, Any]], list[str]]:
    identity = _clean(card.get("data-key-no"))
    label = f"{catalogue.key} page {page} identity {identity or '?'}"
    errors: list[str] = []
    if not _DIGITS_RE.fullmatch(identity):
        return None, [f"{label}: invalid official identity"]
    title_node = card.select_one(".title")
    title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
    if not title:
        errors.append(f"{label}: empty title")

    fields: dict[str, str] = {}
    for item in card.select("li"):
        key_node = item.select_one(".tit")
        if key_node is None:
            continue
        key = _clean(key_node.get_text(" ", strip=True))
        value_node = item.select_one(".txt")
        value = _clean(value_node.get_text(" ", strip=True) if value_node else "")
        if key in fields:
            errors.append(f"{label}: duplicate card field {key}")
        fields[key] = value
    missing = sorted(_REQUIRED_CARD_FIELDS - fields.keys())
    if missing:
        errors.append(f"{label}: missing card fields {','.join(missing)}")

    education_dates = _date_values(fields.get("교육기간"))
    if len(education_dates) != 2:
        errors.append(f"{label}: education period is not exactly two dates")
        start = end = date.min
    else:
        start, end = education_dates
        if end < start:
            errors.append(f"{label}: reversed education period")
    application_values = _date_time_values(fields.get("접수기간"))
    if fields.get("접수기간") and len(application_values) != 2:
        errors.append(f"{label}: application period is not exactly two values")
    apply_period = (
        f"{application_values[0]} ~ {application_values[1]}"
        if len(application_values) == 2
        else ""
    )

    badges = [
        _clean(node.get_text(" ", strip=True))
        for node in card.select(".status-wrap .status")
    ]
    source_status = badges[-1] if badges else ""
    if source_status not in _KNOWN_STATUSES:
        errors.append(f"{label}: unknown source status")

    row = {
        "provider": DAEJEON_YUSEONG_PROVIDER,
        "provider_course_id": f"{DAEJEON_YUSEONG_PROVIDER}:{identity}",
        "title": title,
        "branch": catalogue.branch or (badges[0] if len(badges) > 1 else ""),
        "branch_code": catalogue.branch_code,
        "category": "교육",
        "raw_url": daejeon_yuseong_detail_url(identity),
        "application_url": "",
        "status": _STATUS_MAP.get(source_status, "CLOSED"),
        "fee": _clean(fields.get("수강료")),
        "period": (
            f"{start.isoformat()} ~ {end.isoformat()}"
            if start != date.min
            else ""
        ),
        "start_date": start.isoformat() if start != date.min else "",
        "end_date": end.isoformat() if end != date.min else "",
        "apply_period": apply_period,
        "schedule_raw": _clean(fields.get("교육일시")),
        "target": _clean(fields.get("교육대상")),
        "capacity": _clean(fields.get("모집인원")),
        "raw_fields": {
            "identity": identity,
            "catalogue": catalogue.key,
            "official_menu_name": catalogue.heading,
            "source_page": page,
            "source_status": source_status,
            "source_education_period": _clean(fields.get("교육기간")),
            "source_application_period": _clean(fields.get("접수기간")),
            "partition": _partition(title),
            "detail_verified": False,
            "application_control_present": False,
            "application_control_contract": "detail_form_lctrNo+button_write",
        },
    }
    return row, errors


def _parse_list_page(
    key: Any,
    requested: str,
    response: Any,
) -> PageAudit:
    _kind, catalogue_key, page, _purpose = key
    catalogue = DAEJEON_YUSEONG_CATALOGUE_BY_KEY[catalogue_key]
    errors = _validate_final_url(response, requested, catalogue.path)
    soup = _response_soup(response)
    label = f"{catalogue.key} page {page}"
    page_title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if "유성구 평생학습센터" not in page_title:
        errors.append(f"{label}: official page title changed")
    heading = soup.select_one("h2.page__title")
    if _clean(heading.get_text(" ", strip=True) if heading else "") != catalogue.heading:
        errors.append(f"{label}: official heading changed")

    form_nodes = soup.select("form#searchForm")
    if len(form_nodes) != 1:
        errors.append(f"{label}: search form missing or duplicated")
    else:
        form = form_nodes[0]
        if _clean(form.get("method")).lower() != "post":
            errors.append(f"{label}: search form method changed")
        if _clean(form.get("action")) != catalogue.path:
            errors.append(f"{label}: search form action changed")
        expected_hidden = {
            "pageIndex": str(page),
            "lctrNo": "",
            "searchLctrNo": "",
            "lctrGroupType": catalogue.group_type,
            "searchLctrGroupCd": catalogue.group_code,
        }
        for name, expected in expected_hidden.items():
            nodes = form.select(f'input[name="{name}"]')
            if len(nodes) != 1 or _clean(nodes[0].get("value")) != expected:
                errors.append(f"{label}: hidden field {name} changed")

    count = soup.select_one(".program--count")
    match = _TOTAL_RE.search(_clean(count.get_text(" ", strip=True) if count else ""))
    if match is None:
        total, observed_page, last = 0, page, 1
        errors.append(f"{label}: advertised total/page marker missing")
    else:
        total = int(match.group(1).replace(",", ""))
        observed_page = int(match.group(2))
        last = int(match.group(3))
        expected_last = max(1, math.ceil(total / DAEJEON_YUSEONG_PAGE_SIZE))
        if observed_page != page:
            errors.append(f"{label}: observed page number changed")
        if last != expected_last:
            errors.append(f"{label}: advertised last page mismatch")

    cards = soup.select("a.button_view[data-key-no]")
    rows: list[dict[str, Any]] = []
    for card in cards:
        row, card_errors = _parse_card(card, catalogue, page)
        errors.extend(card_errors)
        if row is not None:
            rows.append(row)
    identities = [row["raw_fields"]["identity"] for row in rows]
    if len(identities) != len(set(identities)):
        errors.append(f"{label}: duplicate identities within page")
    return PageAudit(total, observed_page, last, rows, _page_signature(rows), errors)


def _parse_detail(
    row: Mapping[str, Any],
    requested: str,
    response: Any,
) -> DetailAudit:
    identity = _clean(row.get("raw_fields", {}).get("identity"))
    label = f"detail {identity}"
    errors = _validate_final_url(response, requested, DAEJEON_YUSEONG_DETAIL_PATH)
    soup = _response_soup(response)
    page_title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if "유성구 평생학습센터" not in page_title:
        errors.append(f"{label}: official page title changed")
    hidden = soup.select('input#lctrNo[name="lctrNo"]')
    if len(hidden) != 1 or _clean(hidden[0].get("value")) != identity:
        errors.append(f"{label}: course-bound hidden identity mismatch")
    title_nodes = soup.select(".view-wrap strong.title")
    if len(title_nodes) != 1 or _clean(title_nodes[0].get_text(" ", strip=True)) != _clean(
        row.get("title")
    ):
        errors.append(f"{label}: detail title mismatch")

    info: dict[str, str] = {}
    for item in soup.select(".view-wrap .info-list li"):
        key_node = item.select_one(".subjact")
        value_node = item.select_one(".con")
        if key_node is None:
            continue
        key = _clean(key_node.get_text(" ", strip=True))
        if key in info:
            errors.append(f"{label}: duplicate detail field {key}")
        info[key] = _clean(value_node.get_text(" ", strip=True) if value_node else "")
    detail_dates = _date_values(info.get("교육기간"))
    expected_dates = [
        date.fromisoformat(_clean(row.get("start_date"))),
        date.fromisoformat(_clean(row.get("end_date"))),
    ]
    if detail_dates != expected_dates:
        errors.append(f"{label}: detail education period mismatch")

    buttons = soup.select("button.button_write")
    if len(buttons) > 1:
        errors.append(f"{label}: duplicated application control")
    application_control = len(buttons) == 1
    source_status = _clean(row.get("raw_fields", {}).get("source_status"))
    if (source_status in _OPEN_STATUSES) != application_control:
        errors.append(f"{label}: source status/application control mismatch")
    if application_control:
        if _clean(buttons[0].get_text(" ", strip=True)) != "수강 신청하기":
            errors.append(f"{label}: application control label changed")
        scripts = "\n".join(
            script.get_text(" ", strip=False) for script in soup.select("script")
        )
        if DAEJEON_YUSEONG_APPLICATION_PATH not in scripts:
            errors.append(f"{label}: application endpoint contract changed")
    return DetailAudit(identity, application_control, errors)


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    identity = _clean(row.get("raw_fields", {}).get("identity"))
    errors: list[str] = []
    extra = set(row) - _SAFE_ROW_KEYS
    if extra:
        errors.append(f"identity {identity}: unsafe row keys {','.join(sorted(extra))}")
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping):
        return [*errors, f"identity {identity}: raw_fields is not a mapping"]
    raw_extra = set(raw) - _SAFE_RAW_FIELDS
    if raw_extra:
        errors.append(
            f"identity {identity}: unsafe raw fields {','.join(sorted(raw_extra))}"
        )

    def values(value: Any) -> Iterable[str]:
        if isinstance(value, Mapping):
            for nested in value.values():
                yield from values(nested)
        elif isinstance(value, (list, tuple, set, frozenset)):
            for nested in value:
                yield from values(nested)
        elif isinstance(value, str):
            yield value

    payload = " ".join(values(row))
    if _PHONE_RE.search(payload) or _EMAIL_RE.search(payload):
        errors.append(f"identity {identity}: phone/email leaked into safe payload")
    return errors


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def collect_daejeon_yuseong_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 300,
    detail_limit: int = 500,
    today: Optional[date | datetime | str] = None,
    max_workers: int = DAEJEON_YUSEONG_MAX_WORKERS,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one complete current/future Yuseong education snapshot."""

    cutoff = _today(today)
    current_factory = session_factory or _default_session_factory
    current_fetcher = fetcher or _default_fetcher
    errors: list[str] = []
    meta: dict[str, Any] = {
        "pages": 0,
        "landing_requests": 0,
        "list_requests": 0,
        "sentinel_requests": 0,
        "stability_rechecks": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "application_control_count": 0,
        "source_cap_reached": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "returned_count": 0,
        "configured_collection_error": "",
    }
    if not is_daejeon_yuseong_education_target(target):
        meta["configured_collection_error"] = "non-canonical Yuseong owner target"
        return [], DAEJEON_YUSEONG_PARSER, meta
    if (
        isinstance(max_pages, bool)
        or not isinstance(max_pages, int)
        or max_pages < 1
        or isinstance(detail_limit, bool)
        or not isinstance(detail_limit, int)
        or detail_limit < 0
        or isinstance(max_workers, bool)
        or not isinstance(max_workers, int)
        or max_workers < 1
    ):
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = "invalid max_pages/detail_limit/max_workers cap"
        return [], DAEJEON_YUSEONG_PARSER, meta

    bootstrap_items: list[tuple[Any, str]] = [(('landing',), DAEJEON_YUSEONG_LANDING_URL)]
    bootstrap_items.extend(
        (("list", item.key, 1, "data"), item.list_url)
        for item in DAEJEON_YUSEONG_CATALOGUES
    )

    def bootstrap_parser(key: Any, requested: str, response: Any) -> Any:
        if key[0] == "landing":
            return _parse_landing(key, requested, response)
        return _parse_list_page(key, requested, response)

    fetched, fetch_errors = _fetch_parsed_many(
        bootstrap_items,
        parser=bootstrap_parser,
        fetcher=current_fetcher,
        session_factory=current_factory,
        timeout=timeout,
        max_workers=max_workers,
    )
    errors.extend(fetch_errors)
    meta["pages"] += len(fetched)
    meta["landing_requests"] = int(("landing",) in fetched)
    meta["list_requests"] = sum(key[0] == "list" for key in fetched)

    landing = fetched.get(("landing",))
    if landing is None:
        errors.append("official landing response missing")
    else:
        errors.extend(landing.errors)
    first_pages: dict[str, PageAudit] = {}
    totals: dict[str, int] = {}
    lasts: dict[str, int] = {}
    for catalogue in DAEJEON_YUSEONG_CATALOGUES:
        audit = fetched.get(("list", catalogue.key, 1, "data"))
        if audit is None:
            errors.append(f"{catalogue.key}: first page response missing")
            continue
        errors.extend(audit.errors)
        first_pages[catalogue.key] = audit
        totals[catalogue.key] = audit.total
        lasts[catalogue.key] = audit.last_page

    if len(totals) != len(DAEJEON_YUSEONG_CATALOGUES):
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], DAEJEON_YUSEONG_PARSER, meta
    required_page_requests = 1 + sum(last + 2 for last in lasts.values())
    meta["required_page_requests"] = required_page_requests
    meta["source_totals"] = dict(totals)
    meta["declared_pages"] = dict(lasts)
    if required_page_requests > max_pages:
        meta["source_cap_reached"] = True
        errors.append(
            f"max_pages cap allows {max_pages} of {required_page_requests} required requests"
        )
    if errors:
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], DAEJEON_YUSEONG_PARSER, meta

    remaining_items: list[tuple[Any, str]] = []
    for catalogue in DAEJEON_YUSEONG_CATALOGUES:
        last = lasts[catalogue.key]
        remaining_items.extend(
            (
                ("list", catalogue.key, page, "data"),
                daejeon_yuseong_list_url(catalogue.key, page),
            )
            for page in range(2, last + 1)
        )
        remaining_items.extend(
            [
                (
                    ("list", catalogue.key, last + 1, "sentinel"),
                    daejeon_yuseong_list_url(catalogue.key, last + 1),
                ),
                (
                    ("list", catalogue.key, 1, "recheck"),
                    daejeon_yuseong_list_url(catalogue.key, 1),
                ),
            ]
        )
    remaining, remaining_errors = _fetch_parsed_many(
        remaining_items,
        parser=_parse_list_page,
        fetcher=current_fetcher,
        session_factory=current_factory,
        timeout=timeout,
        max_workers=max_workers,
    )
    fetched.update(remaining)
    errors.extend(remaining_errors)
    meta["pages"] += len(remaining)
    meta["list_requests"] += len(remaining)
    meta["sentinel_requests"] = sum(
        ("list", item.key, lasts[item.key] + 1, "sentinel") in fetched
        for item in DAEJEON_YUSEONG_CATALOGUES
    )
    meta["stability_rechecks"] = sum(
        ("list", item.key, 1, "recheck") in fetched
        for item in DAEJEON_YUSEONG_CATALOGUES
    )

    rows_by_catalogue: dict[str, list[dict[str, Any]]] = {}
    page_counts: dict[str, dict[int, int]] = {}
    for catalogue in DAEJEON_YUSEONG_CATALOGUES:
        total = totals[catalogue.key]
        last = lasts[catalogue.key]
        rows: list[dict[str, Any]] = []
        signatures: list[str] = []
        page_counts[catalogue.key] = {}
        for page in range(1, last + 1):
            audit = (
                first_pages[catalogue.key]
                if page == 1
                else fetched.get(("list", catalogue.key, page, "data"))
            )
            if audit is None:
                errors.append(f"{catalogue.key} page {page}: response missing")
                continue
            errors.extend(audit.errors)
            if (audit.total, audit.last_page) != (total, last):
                errors.append(f"{catalogue.key} page {page}: total/last changed")
            expected = (
                DAEJEON_YUSEONG_PAGE_SIZE
                if page < last
                else total - DAEJEON_YUSEONG_PAGE_SIZE * (last - 1)
            )
            if len(audit.rows) != expected:
                errors.append(f"{catalogue.key} page {page}: row count mismatch")
            rows.extend(audit.rows)
            signatures.append(audit.signature)
            page_counts[catalogue.key][page] = len(audit.rows)
        if len(rows) != total:
            errors.append(f"{catalogue.key}: advertised total does not match parsed rows")
        identities = [row["raw_fields"]["identity"] for row in rows]
        if len(identities) != len(set(identities)):
            errors.append(f"{catalogue.key}: duplicate catalogue identities")
        if len(signatures) != len(set(signatures)):
            errors.append(f"{catalogue.key}: duplicate data-page signatures")

        sentinel = fetched.get(("list", catalogue.key, last + 1, "sentinel"))
        recheck = fetched.get(("list", catalogue.key, 1, "recheck"))
        if sentinel is None or recheck is None:
            errors.append(f"{catalogue.key}: sentinel/page-one recheck missing")
        else:
            errors.extend(sentinel.errors)
            errors.extend(recheck.errors)
            if (
                (sentinel.total, sentinel.last_page) != (total, last)
                or sentinel.rows
            ):
                errors.append(f"{catalogue.key}: immediate post-last page is not empty")
            if (
                (recheck.total, recheck.last_page) != (total, last)
                or recheck.signature != first_pages[catalogue.key].signature
            ):
                errors.append(f"{catalogue.key}: page-one recheck changed")
        rows_by_catalogue[catalogue.key] = rows

    leaf_rows = [
        row
        for catalogue in DAEJEON_YUSEONG_LEAF_CATALOGUES
        for row in rows_by_catalogue.get(catalogue.key, [])
    ]
    leaf_identities = [row["raw_fields"]["identity"] for row in leaf_rows]
    identity_duplicate_count = len(leaf_identities) - len(set(leaf_identities))
    if identity_duplicate_count:
        errors.append("official leaf menus contain duplicate identities")
    leaf_sets = {
        item.key: {
            row["raw_fields"]["identity"]
            for row in rows_by_catalogue.get(item.key, [])
        }
        for item in DAEJEON_YUSEONG_LEAF_CATALOGUES
    }
    union_ids = set().union(*leaf_sets.values()) if leaf_sets else set()
    all_ids = {
        row["raw_fields"]["identity"] for row in rows_by_catalogue.get("all", [])
    }
    regular_ids = {
        row["raw_fields"]["identity"]
        for row in rows_by_catalogue.get("regular", [])
    }
    canonical_extra = all_ids - union_ids
    canonical_omissions = union_ids - all_ids
    humanities_ids = leaf_sets.get("humanities", set())
    regular_expected = leaf_sets.get("guam", set()) | leaf_sets.get("jeonmin", set())
    if canonical_extra:
        errors.append("SEARCH aggregate contains identities outside official leaves")
    if not canonical_omissions.issubset(humanities_ids):
        errors.append("SEARCH aggregate omission escaped 별별인문학")
    if regular_ids != regular_expected:
        errors.append("REGULAR alias is not exactly Gu-am plus Jeonmin")

    semantic = Counter(
        (
            re.sub(r"\W+", "", _clean(row.get("title"))).lower(),
            _clean(row.get("start_date")),
            _clean(row.get("end_date")),
        )
        for row in leaf_rows
    )
    semantic_duplicate_groups = sum(count > 1 for count in semantic.values())
    semantic_duplicate_excess = sum(max(0, count - 1) for count in semantic.values())
    source_partitions = Counter(
        _clean(row["raw_fields"]["partition"]) for row in leaf_rows
    )
    current_rows = [
        row
        for row in leaf_rows
        if row.get("end_date")
        and date.fromisoformat(_clean(row.get("end_date"))) >= cutoff
    ]
    current_partitions = Counter(
        _clean(row["raw_fields"]["partition"]) for row in current_rows
    )
    education_rows = [
        row for row in current_rows if row["raw_fields"]["partition"] == "education"
    ]
    list_complete = bool(
        not errors
        and len(leaf_rows) == sum(totals[item.key] for item in DAEJEON_YUSEONG_LEAF_CATALOGUES)
        and len(union_ids) == len(leaf_rows)
    )
    required_details = len(education_rows)
    if required_details > detail_limit:
        meta["source_cap_reached"] = True
        errors.append(
            f"detail_limit cap allows {detail_limit} of {required_details} required details"
        )

    detail_audits: dict[Any, DetailAudit] = {}
    detail_errors: list[str] = []
    if list_complete and not errors:
        rows_for_detail = {
            row["raw_fields"]["identity"]: row for row in education_rows
        }

        def detail_parser(key: Any, requested: str, response: Any) -> DetailAudit:
            return _parse_detail(rows_for_detail[key[1]], requested, response)

        detail_items = [
            (("detail", identity), row["raw_url"])
            for identity, row in rows_for_detail.items()
        ]
        meta["detail_attempts"] = len(detail_items)
        detail_audits, detail_fetch_errors = _fetch_parsed_many(
            detail_items,
            parser=detail_parser,
            fetcher=current_fetcher,
            session_factory=current_factory,
            timeout=timeout,
            max_workers=max_workers,
        )
        detail_errors.extend(detail_fetch_errors)
        meta["pages"] += len(detail_audits)
        for key, audit in detail_audits.items():
            detail_errors.extend(audit.errors)
            if not audit.errors:
                row = rows_for_detail[key[1]]
                row["raw_fields"]["detail_verified"] = True
                row["raw_fields"]["application_control_present"] = (
                    audit.application_control
                )
                if audit.application_control:
                    row["application_url"] = row["raw_url"]
                    meta["application_control_count"] += 1
                meta["detail_pages"] += 1
    errors.extend(detail_errors)
    meta["detail_errors"] = len(detail_errors)
    details_complete = bool(
        list_complete
        and meta["detail_attempts"] == required_details
        and meta["detail_pages"] == required_details
        and not detail_errors
    )

    result: list[dict[str, Any]] = []
    if list_complete and details_complete and not errors:
        for row in education_rows:
            errors.extend(_privacy_errors(row))
        if not errors:
            deduper = dedupe_rows or _dedupe_default
            result = list(deduper(education_rows))
            if len(result) != len(education_rows):
                errors.append("dedupe changed official current identity cardinality")
                result = []
    snapshot_complete = bool(list_complete and details_complete and not errors)
    if not snapshot_complete:
        result = []

    branch_counts = Counter(_clean(row.get("branch")) for row in education_rows)
    source_status_counts = Counter(
        _clean(row["raw_fields"]["source_status"]) for row in education_rows
    )
    meta.update(
        {
            "ownership_scope": DAEJEON_YUSEONG_OWNERSHIP_SCOPE,
            "canonical_candidate_id": DAEJEON_YUSEONG_CANONICAL_CANDIDATE_ID,
            "candidate_ids": list(DAEJEON_YUSEONG_CANDIDATE_IDS),
            "legacy_alias_providers": list(DAEJEON_YUSEONG_LEGACY_PROVIDERS),
            "official_branch_names": list(DAEJEON_YUSEONG_OFFICIAL_BRANCH_NAMES),
            "official_leaf_urls": [item.list_url for item in DAEJEON_YUSEONG_LEAF_CATALOGUES],
            "aggregate_alias_urls": [
                DAEJEON_YUSEONG_CANONICAL_URL,
                DAEJEON_YUSEONG_REGULAR_URL,
            ],
            "source_totals": dict(totals),
            "official_leaf_totals": {
                item.key: totals[item.key] for item in DAEJEON_YUSEONG_LEAF_CATALOGUES
            },
            "declared_pages": dict(lasts),
            "page_counts": page_counts,
            "source_rows": len(leaf_rows),
            "source_partition_counts": dict(source_partitions),
            "current_source_count": len(current_rows),
            "current_education_count": len(education_rows),
            "current_partition_counts": dict(current_partitions),
            "expired_count": len(leaf_rows) - len(current_rows),
            "current_branch_counts": dict(branch_counts),
            "current_status_counts": dict(source_status_counts),
            "identity_duplicate_count": identity_duplicate_count,
            "semantic_duplicate_group_count": semantic_duplicate_groups,
            "semantic_duplicate_excess_rows": semantic_duplicate_excess,
            "semantic_duplicate_policy": "preserve_distinct_official_leaf_identities",
            "aggregate_alias_duplicate_rows": len(all_ids) + len(regular_ids),
            "canonical_aggregate_count": len(all_ids),
            "canonical_omission_count": len(canonical_omissions),
            "canonical_omission_humanities_count": len(
                canonical_omissions & humanities_ids
            ),
            "canonical_extra_count": len(canonical_extra),
            "canonical_subset_verified": not canonical_extra
            and canonical_omissions.issubset(humanities_ids),
            "regular_alias_count": len(regular_ids),
            "regular_alias_verified": regular_ids == regular_expected,
            "legacy_jeonmin_alias_count": len(leaf_sets.get("jeonmin", set())),
            "legacy_special_alias_count": len(leaf_sets.get("special", set())),
            "pagination_complete": list_complete,
            "details_complete": details_complete,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": snapshot_complete,
            "returned_count": len(result),
            "no_current_data": bool(snapshot_complete and not education_rows),
            "municipality_coverage": [DAEJEON_YUSEONG_MUNICIPALITY_CODE],
            "pii_payload_persisted": False,
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
    )
    return result, DAEJEON_YUSEONG_PARSER, meta


collect = collect_daejeon_yuseong_education


__all__ = [
    "DAEJEON_YUSEONG_APPLICATION_PATH",
    "DAEJEON_YUSEONG_CANONICAL_CANDIDATE_ID",
    "DAEJEON_YUSEONG_CANONICAL_URL",
    "DAEJEON_YUSEONG_CANDIDATE_IDS",
    "DAEJEON_YUSEONG_CATALOGUES",
    "DAEJEON_YUSEONG_DETAIL_PATH",
    "DAEJEON_YUSEONG_HOST",
    "DAEJEON_YUSEONG_JEONMIN_URL",
    "DAEJEON_YUSEONG_LANDING_URL",
    "DAEJEON_YUSEONG_LEAF_CATALOGUES",
    "DAEJEON_YUSEONG_LEGACY_PROVIDERS",
    "DAEJEON_YUSEONG_MUNICIPALITY_CODE",
    "DAEJEON_YUSEONG_MUNICIPALITY_NAME",
    "DAEJEON_YUSEONG_OFFICIAL_BRANCH_NAMES",
    "DAEJEON_YUSEONG_OWNED_ALIAS_URLS",
    "DAEJEON_YUSEONG_PARSER",
    "DAEJEON_YUSEONG_PROVIDER",
    "DAEJEON_YUSEONG_REGULAR_URL",
    "DAEJEON_YUSEONG_SPECIAL_URL",
    "DetailAudit",
    "LandingAudit",
    "PageAudit",
    "YuseongCatalogue",
    "collect",
    "collect_daejeon_yuseong_education",
    "daejeon_yuseong_detail_url",
    "daejeon_yuseong_list_url",
    "is_daejeon_yuseong_education_target",
    "is_daejeon_yuseong_owned_alias_target",
    "is_target",
]
