"""Fail-closed collector for Yeosu City's OK integrated education catalogue.

The public ``/newok`` page is a Vue shell.  Its actual catalogue is exposed by
the official, unauthenticated ``/newok/api/client`` JSON API.  This module owns
the complete education subtree of that API:

* seven unique lecture institutions (two menu aliases are collapsed by the
  institution UID), and
* the two reservation-style health education services under ``보건소``.

Forest healing and the safety centre are reservation experiences, not
education.  The family centre, ``/edu`` and the city library are independent
external owners.  They are verified in the menu tree but never followed.

Every declared list page, an immediate empty sentinel and a stable page-zero
recheck are mandatory.  Every current/future lecture also requires matching
detail and public form metadata.  Reservation-style health education requires
matching detail plus complete current/next-month aggregate calendars.  The
hour endpoint is deliberately not called because it exposes applicant names in
``clientList``.  Manager/instructor/contact data, privacy terms, form fields,
payment account data and free-form HTML are likewise discarded.
"""

from __future__ import annotations

import calendar
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


YEOSU_PROVIDER = "MUNI_WWW_YEOSU_GO_KR_E2EAB68F"
YEOSU_CANONICAL_CANDIDATE_ID = "MUNI_IR_BBEB8411EC3E"
YEOSU_CANONICAL_URL = "https://www.yeosu.go.kr/newok"
YEOSU_HOST = "www.yeosu.go.kr"
YEOSU_PATH = "/newok"
YEOSU_API_BASE = f"{YEOSU_CANONICAL_URL}/api/client"
YEOSU_SITE_MENU_UID = "00070154-eb1d-4972-97b0-03365762fcc1"
YEOSU_MENU_TREE_URL = f"{YEOSU_API_BASE}/menu/{YEOSU_SITE_MENU_UID}/list"
YEOSU_EDUCATION_ROOT_UID = "9ca8998d-edc7-4268-b8f9-5ffde04dee68"
YEOSU_FOREST_HEALING_EMPTY_LECTURE_UID = (
    "ab4cee3a-5744-4733-97a7-a1d86277f11e"
)
YEOSU_MUNICIPALITY_CODE = "1213000000"
YEOSU_MUNICIPALITY_NAME = "전남광주통합특별시 여수시"
YEOSU_SIDO = "전남광주통합특별시"
YEOSU_SIGUNGU = "여수시"
YEOSU_PAGE_SIZE = 100
YEOSU_MAX_WORKERS = 4
YEOSU_FETCH_ATTEMPTS = 3
YEOSU_SESSION_REQUEST_LIMIT = 80
YEOSU_PARSER = (
    "yeosu_ok_integrated_education_api+all_pages+empty_sentinels+"
    "stable_rechecks+current_details+public_form_and_calendar_controls+"
    "education_experience_split+pii_allowlist"
)


@dataclass(frozen=True)
class YeosuLectureSource:
    key: str
    label: str
    menu_uid: str
    institution_uid: str
    aliases: tuple[tuple[str, str], ...] = ()

    @property
    def menu_contracts(self) -> tuple[tuple[str, str], ...]:
        return ((self.menu_uid, self.label),) + self.aliases


@dataclass(frozen=True)
class YeosuReservationSource:
    key: str
    label: str
    menu_uid: str
    category_uid: str
    education: bool


YEOSU_LECTURE_SOURCES: tuple[YeosuLectureSource, ...] = (
    YeosuLectureSource(
        "health",
        "보건소",
        "80a40577-75d9-47ff-8c4b-29319063bb8b",
        "a03d57f0-55ed-4c01-a73e-eef75fad16e9",
        (("e0a73222-95e2-4729-ba7b-3367bcb58c10", "보건소교육"),),
    ),
    YeosuLectureSource(
        "lifelong",
        "평생학습관",
        "1f7e02a1-8a8b-4d36-928f-2d0e4909484c",
        "81eb4f99-df27-4f8d-89de-9e64916dd117",
    ),
    YeosuLectureSource(
        "foreign_language",
        "시민외국어교육",
        "21f5d56a-1871-48b2-ae1f-2dbfa99f4418",
        "771f92d8-33d8-47df-b453-e1f1ec98874f",
    ),
    YeosuLectureSource(
        "agriculture",
        "농업기술센터",
        "48aca803-6a10-4738-8749-0fe2b95abccc",
        "09cea3e6-9bd1-4440-82ce-0f035e146897",
    ),
    YeosuLectureSource(
        "women_culture",
        "여성문화회관",
        "a4edf3b2-c4f7-4b31-8eb8-3f2d24c5b3d8",
        "fef55a20-763b-4c52-8fd5-16213ee4ae36",
    ),
    YeosuLectureSource(
        "digital",
        "시민정보화교육",
        "4a638621-1ec6-4ff7-9bf4-45ff8856b2f1",
        "beb4f078-7f7a-4c55-8deb-11706656d621",
        (("4cf6673f-72c3-4a83-a65f-03b6b9f27fd6", "강좌리스트"),),
    ),
    YeosuLectureSource(
        "living_culture",
        "여수시 생활문화센터",
        "99f09fd1-9f63-409d-9d1d-add8c59d9a8e",
        "009d1f9b-695b-4e42-a7c9-127f5cbe102f",
    ),
)

YEOSU_RESERVATION_SOURCES: tuple[YeosuReservationSource, ...] = (
    YeosuReservationSource(
        "cpr",
        "보건소 > 심폐소생술",
        "7ae7e14c-45d9-499a-8de2-96fd78c1eb59",
        "c71bbf94-a546-4eed-bb32-39aa28218807",
        True,
    ),
    YeosuReservationSource(
        "hypertension_diabetes",
        "보건소 > 고혈압·당뇨병",
        "42bf09ad-d840-4227-b31b-fd949963ebfe",
        "c7112689-266c-4e9d-8b6a-fd9a70edc771",
        True,
    ),
    YeosuReservationSource(
        "forest_healing",
        "산림치유프로그램",
        "ea318c5d-3bd9-4cdd-bcd5-b7f1d3bb1811",
        "a1787769-841a-4c26-aa5d-3dc5948e7bc8",
        False,
    ),
    YeosuReservationSource(
        "safety_experience",
        "생활안전체험관",
        "362faa07-80d5-47cd-ae59-d7f5167e7030",
        "35476386-6c30-4596-a30a-b4e3dea749f0",
        False,
    ),
)

YEOSU_HEALTH_EDUCATION_SOURCES = tuple(
    source for source in YEOSU_RESERVATION_SOURCES if source.education
)
YEOSU_EXPERIENCE_SOURCES = tuple(
    source for source in YEOSU_RESERVATION_SOURCES if not source.education
)

YEOSU_EXTERNAL_OWNER_LINKS: Mapping[str, tuple[str, str]] = {
    "3ffb986d-f6de-4027-b1d6-1d662f112658": (
        "여수시 가족센터",
        "https://yeosu.familynet.or.kr/center/index.do",
    ),
    "dc130a8b-d12e-4bcc-a192-ba4fe80556d3": (
        "행복교육지원센터",
        "https://www.yeosu.go.kr/edu",
    ),
    "d5826fcf-c62d-4712-853d-8f27cc4e481f": (
        "시립도서관",
        "https://yslib.yeosu.go.kr/front/index.php?g_page=culture&m_page=culture01&allLec=Y",
    ),
}

# The seven promotion-review rows are aliases, false positives or a separate
# existing owner.  None should become a second executable owner.
YEOSU_CANDIDATE_AUDIT: Mapping[str, Mapping[str, str]] = {
    "MUNI_IR_EC8644414C46": {
        "url": "https://www.yeosu.go.kr/newok/board/34ed7b41-3792-44f3-83b6-d14cd007246c",
        "decision": "excluded_information_board",
        "owner": YEOSU_PROVIDER,
    },
    "MUNI_IR_5C3CA888F738": {
        "url": "https://www.yeosu.go.kr/newok/lecture/21f5d56a-1871-48b2-ae1f-2dbfa99f4418",
        "decision": "subset_alias",
        "owner": YEOSU_PROVIDER,
    },
    "MUNI_IR_F238D031B0CF": {
        "url": "https://www.yeosu.go.kr/newok/lecture/4a638621-1ec6-4ff7-9bf4-45ff8856b2f1",
        "decision": "subset_alias",
        "owner": YEOSU_PROVIDER,
    },
    "MUNI_IR_4FD9ACDC1635": {
        "url": "https://www.yeosu.go.kr/newok/lecture/99f09fd1-9f63-409d-9d1d-add8c59d9a8e",
        "decision": "subset_alias",
        "owner": YEOSU_PROVIDER,
    },
    "MUNI_IR_F750D9CEDF75": {
        "url": "https://www.yeosu.go.kr/newok/reservation/096d86a2-ab00-4037-b574-8f815360126e",
        "decision": "excluded_campground_non_education",
        "owner": "EXPERIENCE_TARGETS",
    },
    "MUNI_IR_01EBA3E2FBDC": {
        "url": "https://www.yeosu.go.kr/newok/reservation/52fdefec-146a-4ccb-8694-6a8021f7aafb",
        "decision": "excluded_indoor_playground_experience",
        "owner": "EXPERIENCE_TARGETS",
    },
    "MUNI_IR_C075FDAFD0E7": {
        "url": "https://www.yumcorp.or.kr/sports/lecture/guide.do",
        "decision": "subset_of_existing_sports_owner",
        "owner": "MUNI_WWW_YUMCORP_OR_KR_FD06010A",
    },
}


JsonGetter = Callable[[Any, str, Optional[Mapping[str, Any]], int], Any]
HtmlFetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
_DATE_RE = re.compile(r"20\d{2}-\d{2}-\d{2}")
_DATETIME_RE = re.compile(r"20\d{2}-\d{2}-\d{2} [0-2]\d:[0-5]\d:[0-5]\d(?:[.]\d+)?")
_TIME_RE = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?")
_LECTURE_STATUS: Mapping[int, tuple[str, str]] = {
    0: ("SCHEDULED", "대기중"),
    1: ("OPEN", "모집중"),
    2: ("CLOSED", "모집완료"),
    3: ("CLOSED", "수업중"),
    4: ("CLOSED", "수업완료"),
}
_WEEKDAYS = ("일", "월", "화", "수", "목", "금", "토")
_LECTURE_ITEM_KEYS = frozenset(
    {
        "uid",
        "categoryName",
        "name",
        "maxPeople",
        "freeStatus",
        "price",
        "recruitStart",
        "recruitEnd",
        "classStart",
        "classEnd",
        "classStartTime",
        "classEndTime",
        "onlineStatus",
        "directStatus",
        "callStatus",
        "currentPeople",
        "waitingStatus",
        "status",
        "recruitType",
        "recruitmentTarget",
        "place",
        "classDay",
    }
)
_LECTURE_DETAIL_KEYS = frozenset(
    {
        "uid",
        "name",
        "maxPeople",
        "freeStatus",
        "price",
        "recruitStart",
        "recruitEnd",
        "classStart",
        "classEnd",
        "classStartTime",
        "classEndTime",
        "onlineStatus",
        "directStatus",
        "callStatus",
        "institutionUid",
        "classDay",
        "waitingStatus",
        "status",
    }
)
_LECTURE_FORM_KEYS = frozenset(
    {
        "uid",
        "institutionUid",
        "name",
        "price",
        "freeStatus",
        "classStart",
        "classEnd",
        "classStartTime",
        "classEndTime",
        "waitingStatus",
        "classDay",
    }
)
_RESERVATION_ITEM_KEYS = frozenset(
    {
        "uid",
        "name",
        "onlineStatus",
        "directStatus",
        "callStatus",
        "price",
        "rangeInfiniteStatus",
        "reserveEnable",
        "freeStatus",
        "menuUid",
    }
)
_RESERVATION_DETAIL_KEYS = frozenset(
    {
        "uid",
        "name",
        "category",
        "onlineStatus",
        "directStatus",
        "callStatus",
        "price",
        "useStartDate",
        "useEndDate",
        "rangeInfiniteStatus",
        "reserveEnable",
        "maxPeople",
        "freeStatus",
        "address",
        "addressDetail",
    }
)
_CALENDAR_KEYS = frozenset(
    {
        "waitingMaxCount",
        "closeStatus",
        "seatStatus",
        "holidayStatus",
        "reservationNowCount",
        "standbyStatus",
        "waitingNowCount",
        "reservationMaxCount",
        "waitingStatus",
    }
)
YEOSU_PII_FIELDS_DISCARDED = (
    "teacherName",
    "managerName",
    "manager",
    "concatNumber",
    "inquiryNumber",
    "inquiries",
    "detailDescription",
    "restriction",
    "termsPrvcContent",
    "termsPrvc2Content",
    "clientFields",
    "serviceTermsList",
    "kgStoreId",
    "bankName",
    "accountNo",
    "depositor",
    "clientList",
)


class YeosuContractError(ValueError):
    """The live source no longer satisfies the audited collection contract."""


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", _clean(value).casefold())


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def is_yeosu_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != YEOSU_PROVIDER:
        return False
    parsed = urlparse(_clean(_target_value(target, "url")))
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == "https"
        and parsed.hostname == YEOSU_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == YEOSU_PATH
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


is_target = is_yeosu_target


def yeosu_lecture_url(source: YeosuLectureSource, identity: Any) -> str:
    uid = _clean(identity).lower()
    if not _UUID_RE.fullmatch(uid):
        return ""
    return f"{YEOSU_CANONICAL_URL}/lecture/{source.menu_uid}/{uid}"


def yeosu_lecture_application_url(
    source: YeosuLectureSource, identity: Any
) -> str:
    uid = _clean(identity).lower()
    if not _UUID_RE.fullmatch(uid):
        return ""
    return f"{YEOSU_CANONICAL_URL}/lecture/form/{source.menu_uid}/{uid}"


def yeosu_reservation_url(
    source: YeosuReservationSource, identity: Any
) -> str:
    uid = _clean(identity).lower()
    if not _UUID_RE.fullmatch(uid):
        return ""
    return f"{YEOSU_CANONICAL_URL}/reservation/{source.menu_uid}/{uid}/"


def yeosu_reservation_application_url(
    source: YeosuReservationSource, identity: Any
) -> str:
    uid = _clean(identity).lower()
    if not _UUID_RE.fullmatch(uid):
        return ""
    return f"{YEOSU_CANONICAL_URL}/reservation/form/{source.menu_uid}/{uid}"


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        try:
            return date.fromisoformat(_clean(value))
        except ValueError as exc:
            raise YeosuContractError("invalid today override") from exc
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _as_date(value: Any, field: str) -> date:
    raw = _clean(value)
    if not _DATE_RE.fullmatch(raw):
        raise YeosuContractError(f"{field} is not an ISO date")
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise YeosuContractError(f"{field} is not a valid date") from exc


def _as_datetime(value: Any, field: str) -> str:
    raw = _clean(value)
    if not _DATETIME_RE.fullmatch(raw):
        raise YeosuContractError(f"{field} is not an ISO datetime")
    return raw.split(".", 1)[0]


def _as_time(value: Any, field: str) -> str:
    raw = _clean(value)
    if not _TIME_RE.fullmatch(raw):
        raise YeosuContractError(f"{field} is not a time")
    return raw[:5]


def _as_int(value: Any, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise YeosuContractError(f"{field} is not an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise YeosuContractError(f"{field} is not an integer") from exc
    if result < minimum:
        raise YeosuContractError(f"{field} is below {minimum}")
    return result


def _as_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise YeosuContractError(f"{field} is not boolean")
    return value


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise YeosuContractError(f"{label} is not an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise YeosuContractError(f"{label} is not a list")
    return value


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if hasattr(value, "text"):
        value = value.text
    if not isinstance(value, (str, bytes)):
        raise YeosuContractError("root response is not HTML")
    return BeautifulSoup(value, "html.parser")


def _coerce_json(value: Any) -> Any:
    if hasattr(value, "json"):
        value = value.json()
    if not isinstance(value, (Mapping, list)):
        raise YeosuContractError("API response is not JSON")
    return value


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MoonCen/1.0; +https://www.yeosu.go.kr/newok)",
            "Accept": "application/json, text/plain, */*",
        }
    )
    return session


def _default_html_fetcher(session: Any, url: str, timeout: int) -> Any:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return response


def _default_json_getter(
    session: Any,
    url: str,
    params: Optional[Mapping[str, Any]],
    timeout: int,
) -> Any:
    response = session.get(url, params=params or None, timeout=timeout)
    response.raise_for_status()
    return response


def _close_quietly(value: Any) -> None:
    try:
        value.close()
    except Exception:
        pass


def _root_contract(soup: BeautifulSoup) -> None:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    scripts = [_clean(node.get("src")) for node in soup.select("script[src]")]
    if "여수시ok통합예약시스템" not in _normalized(title):
        raise YeosuContractError("official root title changed")
    if not any(
        re.fullmatch(r"/newok/js/app\.[0-9a-f]+[.]js", value)
        for value in scripts
    ):
        raise YeosuContractError("official Vue application asset missing")


def _walk_menu(nodes: Iterable[Any]) -> Iterable[Mapping[str, Any]]:
    for raw in nodes:
        node = _mapping(raw, "menu node")
        yield node
        children = _list(node.get("children"), "menu children")
        yield from _walk_menu(children)


def _menu_tree_contract(value: Any) -> None:
    nodes = _list(value, "menu tree")
    roots = [
        node
        for node in (_mapping(item, "root menu") for item in nodes)
        if _clean(node.get("uid")) == YEOSU_EDUCATION_ROOT_UID
    ]
    if len(roots) != 1:
        raise YeosuContractError("education root menu missing or duplicated")
    root = roots[0]
    if _clean(root.get("name")) != "교육강좌":
        raise YeosuContractError("education root name changed")
    direct = {
        _clean(node.get("uid")): node
        for node in (
            _mapping(item, "education child")
            for item in _list(root.get("children"), "education children")
        )
    }
    expected_direct = {
        source.menu_uid for source in YEOSU_LECTURE_SOURCES
    } | {source.menu_uid for source in YEOSU_EXPERIENCE_SOURCES} | set(
        YEOSU_EXTERNAL_OWNER_LINKS
    )
    if set(direct) != expected_direct:
        raise YeosuContractError("education direct-owner fan-out changed")

    all_nodes = { _clean(node.get("uid")): node for node in _walk_menu([root]) }
    expected_lecture = {
        uid
        for source in YEOSU_LECTURE_SOURCES
        for uid, _name in source.menu_contracts
    }
    expected_reservation = {
        source.menu_uid for source in YEOSU_RESERVATION_SOURCES
    }
    actual_lecture = {
        uid
        for uid, node in all_nodes.items()
        if _clean(node.get("menuType")) == "LECTURE"
    }
    actual_reservation = {
        uid
        for uid, node in all_nodes.items()
        if _clean(node.get("menuType")) == "RESERVATION"
    }
    if actual_lecture != expected_lecture:
        raise YeosuContractError("lecture menu fan-out changed")
    if actual_reservation != expected_reservation:
        raise YeosuContractError("reservation menu fan-out changed")

    for source in YEOSU_LECTURE_SOURCES:
        for uid, name in source.menu_contracts:
            node = all_nodes.get(uid)
            if node is None or _clean(node.get("name")) != name:
                raise YeosuContractError(f"lecture menu {uid} identity changed")
    for source in YEOSU_RESERVATION_SOURCES:
        node = all_nodes.get(source.menu_uid)
        expected_name = source.label.rsplit(" > ", 1)[-1]
        if node is None or _clean(node.get("name")) != expected_name:
            raise YeosuContractError(
                f"reservation menu {source.menu_uid} identity changed"
            )
    for uid, (name, href) in YEOSU_EXTERNAL_OWNER_LINKS.items():
        node = direct.get(uid)
        if (
            node is None
            or _clean(node.get("name")) != name
            or _clean(node.get("menuType")) != "HREF"
            or _clean(node.get("href")) != href
        ):
            raise YeosuContractError(f"external owner {name} contract changed")


def _menu_detail_contract(
    value: Any,
    *,
    uid: str,
    name: str,
    kind: str,
    owner_uid: str,
    other_owner_uid: str = "",
) -> None:
    payload = _mapping(value, f"menu {uid}")
    if (
        _clean(payload.get("uid")) != uid
        or _clean(payload.get("name")) != name
        or _clean(payload.get("menuType")) != kind
    ):
        raise YeosuContractError(f"menu {uid} detail identity changed")
    field = "lecture" if kind == "LECTURE" else "reservation"
    owner = _mapping(payload.get(field), f"menu {uid} {field}")
    if _clean(owner.get("uid")) != owner_uid:
        raise YeosuContractError(f"menu {uid} owner UID changed")
    other = "reservation" if field == "lecture" else "lecture"
    if other_owner_uid:
        other_owner = _mapping(payload.get(other), f"menu {uid} {other}")
        if _clean(other_owner.get("uid")) != other_owner_uid:
            raise YeosuContractError(f"menu {uid} secondary owner UID changed")
    elif payload.get(other) is not None:
        raise YeosuContractError(f"menu {uid} unexpectedly mixes owner types")


def _page_payload(
    value: Any,
    *,
    label: str,
    page: int,
    expected_total: Optional[int] = None,
    expected_pages: Optional[int] = None,
) -> tuple[list[dict[str, Any]], int, int]:
    payload = _mapping(value, f"{label} page {page}")
    items = [
        dict(_mapping(item, f"{label} page {page} item"))
        for item in _list(payload.get("content"), f"{label} content")
    ]
    total = _as_int(payload.get("totalElements"), f"{label} totalElements")
    pages = _as_int(payload.get("totalPages"), f"{label} totalPages")
    expected_from_total = math.ceil(total / YEOSU_PAGE_SIZE) if total else 0
    if pages != expected_from_total:
        raise YeosuContractError(f"{label} declared page count is inconsistent")
    if _as_int(payload.get("number"), f"{label} number") != page:
        raise YeosuContractError(f"{label} page echo changed")
    if _as_int(payload.get("size"), f"{label} size") != YEOSU_PAGE_SIZE:
        raise YeosuContractError(f"{label} page size changed")
    if _as_int(
        payload.get("numberOfElements"), f"{label} numberOfElements"
    ) != len(items):
        raise YeosuContractError(f"{label} row count echo changed")
    if _as_bool(payload.get("empty"), f"{label} empty") != (not items):
        raise YeosuContractError(f"{label} empty flag changed")
    if expected_total is not None and total != expected_total:
        raise YeosuContractError(f"{label} total changed during traversal")
    if expected_pages is not None and pages != expected_pages:
        raise YeosuContractError(f"{label} pages changed during traversal")
    return items, total, pages


def _fingerprint(items: Iterable[Mapping[str, Any]]) -> str:
    value = "\n".join(
        "|".join(
            (
                _clean(item.get("uid")),
                _clean(item.get("name")),
                _clean(item.get("classEnd")),
                _clean(item.get("status")),
                _clean(item.get("currentPeople")),
            )
        )
        for item in items
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _list_pages(
    api: Callable[[str, Optional[Mapping[str, Any]]], Any],
    *,
    url: str,
    params: Mapping[str, Any],
    label: str,
    max_pages: int,
    item_validator: Callable[[dict[str, Any]], None],
) -> tuple[list[dict[str, Any]], int, int, int]:
    first = _coerce_json(
        api(url, {**params, "page": 0, "size": YEOSU_PAGE_SIZE})
    )
    first_items, total, pages = _page_payload(
        first, label=label, page=0
    )
    if pages > max_pages:
        raise YeosuContractError(
            f"{label}: max_pages cap {max_pages} below declared {pages}"
        )
    rows = list(first_items)
    for page in range(1, pages):
        value = _coerce_json(
            api(url, {**params, "page": page, "size": YEOSU_PAGE_SIZE})
        )
        page_items, _total, _pages = _page_payload(
            value,
            label=label,
            page=page,
            expected_total=total,
            expected_pages=pages,
        )
        rows.extend(page_items)
    sentinel_page = pages if pages else 1
    sentinel = _coerce_json(
        api(
            url,
            {**params, "page": sentinel_page, "size": YEOSU_PAGE_SIZE},
        )
    )
    sentinel_items, _total, _pages = _page_payload(
        sentinel,
        label=label,
        page=sentinel_page,
        expected_total=total,
        expected_pages=pages,
    )
    if sentinel_items:
        raise YeosuContractError(f"{label} sentinel page is not empty")
    recheck = _coerce_json(
        api(url, {**params, "page": 0, "size": YEOSU_PAGE_SIZE})
    )
    recheck_items, _total, _pages = _page_payload(
        recheck,
        label=label,
        page=0,
        expected_total=total,
        expected_pages=pages,
    )
    if _fingerprint(recheck_items) != _fingerprint(first_items):
        raise YeosuContractError(f"{label} page zero changed during traversal")
    if len(rows) != total:
        raise YeosuContractError(
            f"{label} collected {len(rows)} rows, declared {total}"
        )
    for item in rows:
        item_validator(item)
    return rows, total, pages, (max(1, pages) + 2)


def _validate_lecture_item(item: dict[str, Any]) -> None:
    missing = _LECTURE_ITEM_KEYS - set(item)
    if missing:
        raise YeosuContractError(
            f"lecture item missing fields: {', '.join(sorted(missing))}"
        )
    uid = _clean(item.get("uid")).lower()
    if not _UUID_RE.fullmatch(uid) or not _clean(item.get("name")):
        raise YeosuContractError("lecture item identity changed")
    start = _as_date(item.get("classStart"), f"lecture {uid} classStart")
    end = _as_date(item.get("classEnd"), f"lecture {uid} classEnd")
    if start > end:
        raise YeosuContractError(f"lecture {uid} has reversed class dates")
    recruit_start = _as_datetime(
        item.get("recruitStart"), f"lecture {uid} recruitStart"
    )
    recruit_end = _as_datetime(
        item.get("recruitEnd"), f"lecture {uid} recruitEnd"
    )
    if recruit_start > recruit_end:
        raise YeosuContractError(f"lecture {uid} has reversed recruit dates")
    _as_time(item.get("classStartTime"), f"lecture {uid} classStartTime")
    _as_time(item.get("classEndTime"), f"lecture {uid} classEndTime")
    status = _as_int(item.get("status"), f"lecture {uid} status")
    if status not in _LECTURE_STATUS:
        raise YeosuContractError(f"lecture {uid} has unknown status {status}")
    recruit_type = _as_int(
        item.get("recruitType"), f"lecture {uid} recruitType"
    )
    if recruit_type not in {1, 2, 3}:
        raise YeosuContractError(
            f"lecture {uid} has unknown recruitType {recruit_type}"
        )
    days = _list(item.get("classDay"), f"lecture {uid} classDay")
    if any(not isinstance(day, int) or isinstance(day, bool) or day not in range(7) for day in days):
        raise YeosuContractError(f"lecture {uid} has invalid classDay")
    for field in ("onlineStatus", "directStatus", "callStatus", "freeStatus", "waitingStatus"):
        _as_bool(item.get(field), f"lecture {uid} {field}")
    if not any(
        bool(item.get(field))
        for field in ("onlineStatus", "directStatus", "callStatus")
    ):
        raise YeosuContractError(f"lecture {uid} has no application channel")
    _as_int(item.get("maxPeople"), f"lecture {uid} maxPeople")
    _as_int(item.get("currentPeople"), f"lecture {uid} currentPeople")
    _as_int(item.get("price"), f"lecture {uid} price")


def _validate_reservation_item(
    item: dict[str, Any], source: YeosuReservationSource
) -> None:
    missing = _RESERVATION_ITEM_KEYS - set(item)
    if missing:
        raise YeosuContractError(
            f"{source.label} item missing fields: {', '.join(sorted(missing))}"
        )
    uid = _clean(item.get("uid")).lower()
    if not _UUID_RE.fullmatch(uid) or not _clean(item.get("name")):
        raise YeosuContractError(f"{source.label} item identity changed")
    if _clean(item.get("menuUid")) != source.menu_uid:
        raise YeosuContractError(f"{source.label} item menu changed")
    for field in (
        "onlineStatus",
        "directStatus",
        "callStatus",
        "freeStatus",
        "rangeInfiniteStatus",
        "reserveEnable",
    ):
        _as_bool(item.get(field), f"reservation {uid} {field}")
    if not any(
        bool(item.get(field))
        for field in ("onlineStatus", "directStatus", "callStatus")
    ):
        raise YeosuContractError(f"reservation {uid} has no application channel")
    _as_int(item.get("price"), f"reservation {uid} price")


def _lecture_row(
    target: Any,
    source: YeosuLectureSource,
    item: Mapping[str, Any],
) -> dict[str, Any]:
    uid = _clean(item.get("uid")).lower()
    start = _as_date(item.get("classStart"), f"lecture {uid} classStart")
    end = _as_date(item.get("classEnd"), f"lecture {uid} classEnd")
    recruit_start = _as_datetime(
        item.get("recruitStart"), f"lecture {uid} recruitStart"
    )
    recruit_end = _as_datetime(
        item.get("recruitEnd"), f"lecture {uid} recruitEnd"
    )
    start_time = _as_time(
        item.get("classStartTime"), f"lecture {uid} classStartTime"
    )
    end_time = _as_time(
        item.get("classEndTime"), f"lecture {uid} classEndTime"
    )
    days = [int(value) for value in item.get("classDay") or []]
    source_status = _as_int(item.get("status"), f"lecture {uid} status")
    status, status_label = _LECTURE_STATUS[source_status]
    waiting = bool(item.get("waitingStatus"))
    open_control = source_status == 1 and bool(item.get("onlineStatus"))
    free = bool(item.get("freeStatus")) or int(item.get("price") or 0) == 0
    price = int(item.get("price") or 0)
    raw_url = yeosu_lecture_url(source, uid)
    source_venue = _clean(item.get("place"))
    row: dict[str, Any] = {
        "provider": _clean(_target_value(target, "provider")),
        "provider_course_id": f"LECTURE:{uid}",
        "title": _clean(item.get("name")),
        "branch": source.label,
        "category": _clean(item.get("categoryName")) or source.label,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "period": f"{start.isoformat()} ~ {end.isoformat()}",
        "apply_start": recruit_start,
        "apply_end": recruit_end,
        "apply_period": f"{recruit_start} ~ {recruit_end}",
        "schedule_raw": " ".join(
            part
            for part in (
                ",".join(_WEEKDAYS[day] for day in days),
                f"{start_time} ~ {end_time}",
            )
            if part
        ),
        "venue_name": source_venue or source.label,
        "target": _clean(item.get("recruitmentTarget")),
        "capacity": int(item.get("maxPeople") or 0),
        "fee": "무료" if free else f"{price:,}원",
        "status": status,
        "reservation_available": open_control,
        "raw_url": raw_url,
        "application_url": (
            yeosu_lecture_application_url(source, uid) if open_control else ""
        ),
        "application_type": (
            "WAITLIST_APPLY" if open_control and waiting else
            "ONLINE_RESERVATION" if open_control else ""
        ),
        "municipality_code": YEOSU_MUNICIPALITY_CODE,
        "municipality_name": YEOSU_MUNICIPALITY_NAME,
        "sido": YEOSU_SIDO,
        "sigungu": YEOSU_SIGUNGU,
        "raw_fields": {
            "source_kind": "lecture",
            "source_uid": uid,
            "source_menu_uid": source.menu_uid,
            "source_institution_uid": source.institution_uid,
            "source_status": source_status,
            "source_status_label": status_label,
            "recruit_type": int(item.get("recruitType") or 0),
            "class_days": days,
            "online_status": bool(item.get("onlineStatus")),
            "direct_status": bool(item.get("directStatus")),
            "call_status": bool(item.get("callStatus")),
            "waiting_status": waiting,
            "free_status": bool(item.get("freeStatus")),
            "source_price": price,
            "source_fee_conflict": bool(item.get("freeStatus")) and price > 0,
            "current_people": int(item.get("currentPeople") or 0),
            "max_people": int(item.get("maxPeople") or 0),
            "venue_fallback_to_source": not source_venue,
        },
    }
    return row


def _same_time(left: Any, right: Any) -> bool:
    return _as_time(left, "detail time") == _as_time(right, "list time")


def _validate_lecture_public_payload(
    value: Any,
    *,
    label: str,
    required_keys: frozenset[str],
    source: YeosuLectureSource,
    item: Mapping[str, Any],
) -> dict[str, Any]:
    payload = _mapping(value, f"lecture {label}")
    uid = _clean(item.get("uid")).lower()
    missing = required_keys - set(payload)
    if missing:
        raise YeosuContractError(
            f"lecture {uid} {label} missing {', '.join(sorted(missing))}"
        )
    if (
        _clean(payload.get("uid")).lower() != uid
        or _normalized(payload.get("name")) != _normalized(item.get("name"))
        or _clean(payload.get("institutionUid")) != source.institution_uid
    ):
        raise YeosuContractError(f"lecture {uid} {label} identity mismatch")
    for field in ("classStart", "classEnd"):
        if _clean(payload.get(field)) != _clean(item.get(field)):
            raise YeosuContractError(
                f"lecture {uid} {label} {field} mismatch"
            )
    for field in ("classStartTime", "classEndTime"):
        if not _same_time(payload.get(field), item.get(field)):
            raise YeosuContractError(
                f"lecture {uid} {label} {field} mismatch"
            )
    if bool(payload.get("freeStatus")) != bool(item.get("freeStatus")):
        raise YeosuContractError(
            f"lecture {uid} {label} freeStatus mismatch"
        )
    if _as_int(payload.get("price"), f"lecture {uid} {label} price") != int(
        item.get("price") or 0
    ):
        raise YeosuContractError(f"lecture {uid} {label} price mismatch")
    if list(payload.get("classDay") or []) != list(item.get("classDay") or []):
        raise YeosuContractError(f"lecture {uid} {label} classDay mismatch")
    if bool(payload.get("waitingStatus")) != bool(item.get("waitingStatus")):
        raise YeosuContractError(
            f"lecture {uid} {label} waitingStatus mismatch"
        )
    return payload


def _validate_lecture_detail_and_form(
    row: dict[str, Any],
    source: YeosuLectureSource,
    item: Mapping[str, Any],
    detail_value: Any,
    form_value: Any,
) -> None:
    uid = _clean(item.get("uid")).lower()
    detail = _validate_lecture_public_payload(
        detail_value,
        label="detail",
        required_keys=_LECTURE_DETAIL_KEYS,
        source=source,
        item=item,
    )
    _validate_lecture_public_payload(
        form_value,
        label="form",
        required_keys=_LECTURE_FORM_KEYS,
        source=source,
        item=item,
    )
    for field in (
        "recruitStart",
        "recruitEnd",
        "onlineStatus",
        "directStatus",
        "callStatus",
        "status",
        "maxPeople",
    ):
        if detail.get(field) != item.get(field):
            raise YeosuContractError(f"lecture {uid} detail {field} mismatch")
    row["raw_fields"].update(
        {
            "detail_verified": True,
            "form_verified": True,
            "application_control_verified": True,
        }
    )


def _validate_lecture_form_only(
    row: dict[str, Any],
    source: YeosuLectureSource,
    item: Mapping[str, Any],
    form_value: Any,
    *,
    status_code: int,
) -> None:
    _validate_lecture_public_payload(
        form_value,
        label="form",
        required_keys=_LECTURE_FORM_KEYS,
        source=source,
        item=item,
    )
    row["raw_fields"].update(
        {
            "detail_verified": False,
            "detail_api_unavailable_status": status_code,
            "detail_verification_mode": "stable_list+public_form",
            "form_verified": True,
            "application_control_verified": True,
        }
    )


def _http_error_status(exc: BaseException) -> int:
    if not isinstance(exc, requests.HTTPError):
        return 0
    response = getattr(exc, "response", None)
    try:
        return int(getattr(response, "status_code", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _month_strings(cutoff: date) -> tuple[str, str]:
    first = cutoff.strftime("%Y-%m")
    if cutoff.month == 12:
        next_month = date(cutoff.year + 1, 1, 1)
    else:
        next_month = date(cutoff.year, cutoff.month + 1, 1)
    return first, next_month.strftime("%Y-%m")


def _calendar_contract(
    value: Any, month: str, source: YeosuReservationSource
) -> dict[date, dict[str, int]]:
    payload = _mapping(value, f"{source.label} {month} calendar")
    year, month_number = (int(part) for part in month.split("-"))
    expected_dates = {
        date(year, month_number, day)
        for day in range(1, calendar.monthrange(year, month_number)[1] + 1)
    }
    actual_dates: dict[date, dict[str, int]] = {}
    for raw_day, raw_record in payload.items():
        parsed = _as_date(raw_day, f"{source.label} calendar date")
        record = _mapping(raw_record, f"{source.label} {raw_day} calendar row")
        if set(record) != _CALENDAR_KEYS:
            raise YeosuContractError(
                f"{source.label} {raw_day} calendar aggregate fields changed"
            )
        normalized = {
            key: _as_int(record.get(key), f"{source.label} {raw_day} {key}")
            for key in _CALENDAR_KEYS
        }
        for flag in (
            "closeStatus",
            "seatStatus",
            "holidayStatus",
            "standbyStatus",
            "waitingStatus",
        ):
            if normalized[flag] not in {0, 1}:
                raise YeosuContractError(
                    f"{source.label} {raw_day} {flag} is not binary"
                )
        actual_dates[parsed] = normalized
    if set(actual_dates) != expected_dates:
        raise YeosuContractError(f"{source.label} {month} calendar is incomplete")
    return actual_dates


def _reservation_row(
    target: Any,
    source: YeosuReservationSource,
    item: Mapping[str, Any],
    detail_value: Any,
    calendar_values: Mapping[str, Any],
    cutoff: date,
) -> dict[str, Any]:
    detail = _mapping(detail_value, f"{source.label} detail")
    uid = _clean(item.get("uid")).lower()
    missing = _RESERVATION_DETAIL_KEYS - set(detail)
    if missing:
        raise YeosuContractError(
            f"reservation {uid} detail missing {', '.join(sorted(missing))}"
        )
    category = _mapping(detail.get("category"), f"reservation {uid} category")
    if (
        _clean(detail.get("uid")).lower() != uid
        or _normalized(detail.get("name")) != _normalized(item.get("name"))
        or _clean(category.get("uid")) != source.category_uid
    ):
        raise YeosuContractError(f"reservation {uid} detail identity mismatch")
    for field in (
        "onlineStatus",
        "directStatus",
        "callStatus",
        "freeStatus",
        "rangeInfiniteStatus",
        "reserveEnable",
        "price",
    ):
        if detail.get(field) != item.get(field):
            raise YeosuContractError(f"reservation {uid} detail {field} mismatch")
    max_people = _as_int(detail.get("maxPeople"), f"reservation {uid} maxPeople")
    if max_people < 1:
        raise YeosuContractError(f"reservation {uid} has no capacity")

    calendars: dict[date, dict[str, int]] = {}
    for month, value in calendar_values.items():
        calendars.update(_calendar_contract(value, month, source))
    service_dates = sorted(
        day
        for day, record in calendars.items()
        if day >= cutoff
        and record["holidayStatus"] == 0
        and record["reservationMaxCount"] > 0
    )
    if not service_dates:
        raise YeosuContractError(
            f"reservation {uid} has no auditable current/next-month education dates"
        )
    available_dates = [
        day
        for day in service_dates
        if calendars[day]["closeStatus"] == 0
        and (
            calendars[day]["reservationNowCount"]
            < calendars[day]["reservationMaxCount"]
            or calendars[day]["waitingStatus"] == 1
        )
    ]
    start_raw = _clean(detail.get("useStartDate"))
    end_raw = _clean(detail.get("useEndDate"))
    source_period_conflict = False
    try:
        source_start = _as_date(start_raw, f"reservation {uid} useStartDate")
        source_end = _as_date(end_raw, f"reservation {uid} useEndDate")
        source_period_conflict = source_start > source_end
    except YeosuContractError:
        source_start = service_dates[0]
        source_end = service_dates[-1]
        source_period_conflict = True
    if source_period_conflict:
        start = service_dates[0]
        end = service_dates[-1]
    else:
        start, end = source_start, source_end
        if end < cutoff:
            if bool(detail.get("rangeInfiniteStatus")):
                start, end = service_dates[0], service_dates[-1]
                source_period_conflict = True
            else:
                raise YeosuContractError(f"reservation {uid} unexpectedly expired")
    open_control = bool(available_dates) and bool(detail.get("onlineStatus"))
    price = _as_int(detail.get("price"), f"reservation {uid} price")
    free = bool(detail.get("freeStatus")) or price == 0
    raw_url = yeosu_reservation_url(source, uid)
    return {
        "provider": _clean(_target_value(target, "provider")),
        "provider_course_id": f"RESERVATION:{uid}",
        "title": _clean(item.get("name")),
        "branch": source.label,
        "category": _clean(category.get("name")) or source.label,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "period": f"{start.isoformat()} ~ {end.isoformat()}",
        "apply_start": available_dates[0].isoformat() if available_dates else "",
        "apply_end": available_dates[-1].isoformat() if available_dates else "",
        "apply_period": (
            f"{available_dates[0].isoformat()} ~ {available_dates[-1].isoformat()}"
            if available_dates
            else ""
        ),
        "schedule_raw": (
            f"공식 예약 달력 {service_dates[0].isoformat()} ~ "
            f"{service_dates[-1].isoformat()}"
        ),
        "venue_name": _clean(detail.get("addressDetail")),
        "address": _clean(detail.get("address")),
        "target": "대상 제한 미기재",
        "capacity": max_people,
        "fee": "무료" if free else f"{price:,}원",
        "status": "OPEN" if available_dates else "CLOSED",
        "reservation_available": open_control,
        "raw_url": raw_url,
        "application_url": (
            yeosu_reservation_application_url(source, uid)
            if open_control
            else ""
        ),
        "application_type": "ONLINE_RESERVATION" if open_control else "",
        "municipality_code": YEOSU_MUNICIPALITY_CODE,
        "municipality_name": YEOSU_MUNICIPALITY_NAME,
        "sido": YEOSU_SIDO,
        "sigungu": YEOSU_SIGUNGU,
        "raw_fields": {
            "source_kind": "reservation_education",
            "source_uid": uid,
            "source_menu_uid": source.menu_uid,
            "source_category_uid": source.category_uid,
            "online_status": bool(detail.get("onlineStatus")),
            "direct_status": bool(detail.get("directStatus")),
            "call_status": bool(detail.get("callStatus")),
            "range_infinite_status": bool(detail.get("rangeInfiniteStatus")),
            "free_status": bool(detail.get("freeStatus")),
            "source_price": price,
            "source_period_start": start_raw,
            "source_period_end": end_raw,
            "source_period_conflict": source_period_conflict,
            "calendar_months": list(calendar_values),
            "calendar_service_date_count": len(service_dates),
            "calendar_available_date_count": len(available_dates),
            "calendar_verified": True,
            "application_control_verified": True,
            "hour_endpoint_skipped_for_pii": True,
            "target_not_published_by_source": True,
        },
    }


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
        "menu_requests": 0,
        "list_requests": 0,
        "sentinel_requests": 0,
        "page_one_rechecks": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "form_attempts": 0,
        "form_pages": 0,
        "calendar_requests": 0,
        "lecture_source_count": len(YEOSU_LECTURE_SOURCES),
        "health_reservation_source_count": len(YEOSU_HEALTH_EDUCATION_SOURCES),
        "experience_source_count": len(YEOSU_EXPERIENCE_SOURCES),
        "source_rows": 0,
        "current_count": 0,
        "returned_count": 0,
        "pagination_complete": False,
        "details_complete": False,
        "application_controls_complete": False,
        "education_experience_separated": False,
        "pii_excluded": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "configured_collection_error": message,
        **extra,
    }


def collect_yeosu_education(
    target: Any,
    timeout: int = 20,
    max_pages: int = 50,
    detail_limit: int = 500,
    *,
    html_fetcher: Optional[HtmlFetcher] = None,
    json_getter: Optional[JsonGetter] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = YEOSU_MAX_WORKERS,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return a complete, current Yeosu municipal education snapshot."""

    if not is_yeosu_target(target):
        return [], YEOSU_PARSER, _failure(
            "target does not match the exact Yeosu OK integrated root"
        )
    if int(max_pages) < 1:
        return [], YEOSU_PARSER, _failure(
            "max_pages cap does not allow collection", source_cap_reached=True
        )

    current_html_fetcher = html_fetcher or _default_html_fetcher
    current_json_getter = json_getter or _default_json_getter
    current_session_factory = session_factory or _default_session_factory
    current_dedupe = dedupe_rows or _dedupe_default
    cutoff = _today(today)
    source_cap_reached = False
    sessions: list[Any] = []
    sessions_lock = threading.Lock()
    local = threading.local()
    request_lock = threading.Lock()
    counts = Counter()

    def thread_session() -> Any:
        value = getattr(local, "session", None)
        used = int(getattr(local, "requests", 0))
        if value is None or used >= YEOSU_SESSION_REQUEST_LIMIT:
            if value is not None:
                _close_quietly(value)
            value = current_session_factory()
            local.session = value
            local.requests = 0
            with sessions_lock:
                sessions.append(value)
        local.requests = int(getattr(local, "requests", 0)) + 1
        return value

    def api(
        url: str, params: Optional[Mapping[str, Any]] = None, kind: str = "api"
    ) -> Any:
        last_error: Optional[BaseException] = None
        for attempt in range(1, YEOSU_FETCH_ATTEMPTS + 1):
            try:
                value = current_json_getter(thread_session(), url, params, timeout)
            except requests.RequestException as exc:
                last_error = exc
                with request_lock:
                    counts["request_attempts"] += 1
                    if attempt > 1:
                        counts["retry_attempts"] += 1
                failed_session = getattr(local, "session", None)
                if failed_session is not None:
                    _close_quietly(failed_session)
                local.session = None
                local.requests = 0
                if attempt < YEOSU_FETCH_ATTEMPTS:
                    time.sleep(0.2 * attempt)
                    continue
                raise
            else:
                with request_lock:
                    counts["request_attempts"] += 1
                    if attempt > 1:
                        counts["retry_attempts"] += 1
                    counts["request_count"] += 1
                    counts[kind] += 1
                return value
        assert last_error is not None
        raise last_error

    try:
        try:
            root = _coerce_soup(
                current_html_fetcher(thread_session(), YEOSU_CANONICAL_URL, timeout)
            )
            with request_lock:
                counts["request_count"] += 1
                counts["root_requests"] += 1
            _root_contract(root)
            menu_tree = _coerce_json(api(YEOSU_MENU_TREE_URL, kind="menu_requests"))
            _menu_tree_contract(menu_tree)

            for source in YEOSU_LECTURE_SOURCES:
                for uid, name in source.menu_contracts:
                    value = _coerce_json(
                        api(
                            f"{YEOSU_API_BASE}/menu/{uid}",
                            kind="menu_requests",
                        )
                    )
                    _menu_detail_contract(
                        value,
                        uid=uid,
                        name=name,
                        kind="LECTURE",
                        owner_uid=source.institution_uid,
                    )
            for source in YEOSU_RESERVATION_SOURCES:
                value = _coerce_json(
                    api(
                        f"{YEOSU_API_BASE}/menu/{source.menu_uid}",
                        kind="menu_requests",
                    )
                )
                _menu_detail_contract(
                    value,
                    uid=source.menu_uid,
                    name=source.label.rsplit(" > ", 1)[-1],
                    kind="RESERVATION",
                    owner_uid=source.category_uid,
                    other_owner_uid=(
                        YEOSU_FOREST_HEALING_EMPTY_LECTURE_UID
                        if source.key == "forest_healing"
                        else ""
                    ),
                )
        except Exception as exc:
            return [], YEOSU_PARSER, _failure(
                f"ownership/menu contract failed: {type(exc).__name__}: {_clean(exc)}",
                request_count=counts["request_count"],
                root_requests=counts["root_requests"],
                menu_requests=counts["menu_requests"],
            )

        lecture_items: list[tuple[YeosuLectureSource, dict[str, Any]]] = []
        reservation_items: list[
            tuple[YeosuReservationSource, dict[str, Any]]
        ] = []
        source_totals: dict[str, int] = {}
        source_page_counts: dict[str, int] = {}
        errors: list[str] = []

        for source in YEOSU_LECTURE_SOURCES:
            try:
                rows, total, pages, request_count = _list_pages(
                    lambda url, params: api(url, params, "list_requests"),
                    url=f"{YEOSU_API_BASE}/lecture-item",
                    params={"institutionUid": source.institution_uid},
                    label=source.label,
                    max_pages=int(max_pages),
                    item_validator=_validate_lecture_item,
                )
                lecture_items.extend((source, row) for row in rows)
                source_totals[f"lecture:{source.key}"] = total
                source_page_counts[f"lecture:{source.key}"] = pages
                counts["sentinel_requests"] += 1
                counts["page_one_rechecks"] += 1
                counts["data_pages"] += max(1, pages)
                if request_count != max(1, pages) + 2:
                    raise YeosuContractError(f"{source.label} request accounting changed")
            except Exception as exc:
                if "max_pages cap" in _clean(exc):
                    source_cap_reached = True
                errors.append(
                    f"{source.label} list: {type(exc).__name__}: {_clean(exc)}"
                )

        for source in YEOSU_HEALTH_EDUCATION_SOURCES:
            try:
                rows, total, pages, request_count = _list_pages(
                    lambda url, params: api(url, params, "list_requests"),
                    url=f"{YEOSU_API_BASE}/reservation-item",
                    params={"categoryUid": source.category_uid},
                    label=source.label,
                    max_pages=int(max_pages),
                    item_validator=lambda item, source=source: _validate_reservation_item(
                        item, source
                    ),
                )
                reservation_items.extend((source, row) for row in rows)
                source_totals[f"reservation:{source.key}"] = total
                source_page_counts[f"reservation:{source.key}"] = pages
                counts["sentinel_requests"] += 1
                counts["page_one_rechecks"] += 1
                counts["data_pages"] += max(1, pages)
                if request_count != max(1, pages) + 2:
                    raise YeosuContractError(f"{source.label} request accounting changed")
            except Exception as exc:
                if "max_pages cap" in _clean(exc):
                    source_cap_reached = True
                errors.append(
                    f"{source.label} list: {type(exc).__name__}: {_clean(exc)}"
                )

        # The forest-healing menu carries an obsolete LECTURE owner object even
        # though its public route and two live items are RESERVATION experiences.
        # Its lecture catalogue was empty in the ownership audit.  If it gains
        # rows, fail closed so the education/experience decision is reviewed.
        try:
            rows, total, pages, request_count = _list_pages(
                lambda url, params: api(url, params, "list_requests"),
                url=f"{YEOSU_API_BASE}/lecture-item",
                params={
                    "institutionUid": YEOSU_FOREST_HEALING_EMPTY_LECTURE_UID
                },
                label="산림치유프로그램(강좌 보조 소유자)",
                max_pages=int(max_pages),
                item_validator=_validate_lecture_item,
            )
            if rows or total:
                raise YeosuContractError(
                    "forest-healing lecture side is no longer empty"
                )
            source_totals["excluded:forest_healing_lecture"] = total
            source_page_counts["excluded:forest_healing_lecture"] = pages
            counts["sentinel_requests"] += 1
            counts["page_one_rechecks"] += 1
            counts["data_pages"] += max(1, pages)
            if request_count != max(1, pages) + 2:
                raise YeosuContractError(
                    "forest-healing empty-owner request accounting changed"
                )
        except Exception as exc:
            if "max_pages cap" in _clean(exc):
                source_cap_reached = True
            errors.append(
                "산림치유프로그램 education/experience split: "
                f"{type(exc).__name__}: {_clean(exc)}"
            )

        identities = [
            _clean(item.get("uid")).lower()
            for _source, item in lecture_items + reservation_items
        ]
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count:
            errors.append(f"duplicate source UID across owned education: {duplicate_count}")

        current_lectures = [
            (source, item)
            for source, item in lecture_items
            if _as_date(item.get("classEnd"), "lecture classEnd") >= cutoff
        ]
        required_details = len(current_lectures) + len(reservation_items)
        if int(detail_limit) < required_details:
            source_cap_reached = True
            errors.append(
                f"detail_limit cap {int(detail_limit)} below required {required_details}"
            )

        detailed_lectures: list[dict[str, Any]] = []
        if not errors and current_lectures:
            counts["detail_attempts"] += len(current_lectures)
            counts["form_attempts"] += len(current_lectures)

            def fetch_lecture(
                pair: tuple[YeosuLectureSource, dict[str, Any]]
            ) -> tuple[Optional[dict[str, Any]], str]:
                source, item = pair
                uid = _clean(item.get("uid")).lower()
                try:
                    detail: Any = None
                    detail_status = 0
                    try:
                        detail = _coerce_json(
                            api(
                                f"{YEOSU_API_BASE}/lecture-item/{uid}",
                                kind="detail_requests",
                            )
                        )
                    except requests.HTTPError as exc:
                        detail_status = _http_error_status(exc)
                        source_status = _as_int(
                            item.get("status"), f"lecture {uid} status"
                        )
                        starts = _as_date(
                            item.get("classStart"),
                            f"lecture {uid} classStart",
                        )
                        ends = _as_date(
                            item.get("classEnd"),
                            f"lecture {uid} classEnd",
                        )
                        if not (
                            detail_status == 500
                            and source.key == "foreign_language"
                            and source_status == 3
                            and starts <= cutoff <= ends
                        ):
                            raise
                    form = _coerce_json(
                        api(
                            f"{YEOSU_API_BASE}/lecture-item/form/{uid}",
                            kind="form_requests",
                        )
                    )
                    row = _lecture_row(target, source, item)
                    if detail is None:
                        _validate_lecture_form_only(
                            row,
                            source,
                            item,
                            form,
                            status_code=detail_status,
                        )
                    else:
                        _validate_lecture_detail_and_form(
                            row, source, item, detail, form
                        )
                    return row, ""
                except Exception as exc:
                    return None, (
                        f"lecture {uid}: {type(exc).__name__}: {_clean(exc)}"
                    )

            workers = min(
                max(1, int(max_workers)),
                YEOSU_MAX_WORKERS,
                len(current_lectures),
            )
            with ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="yeosu-lecture"
            ) as pool:
                results = list(pool.map(fetch_lecture, current_lectures))
            for row, error in results:
                if error:
                    errors.append(error)
                elif row is not None:
                    detailed_lectures.append(row)
                    if row.get("raw_fields", {}).get("detail_verified"):
                        counts["detail_pages"] += 1
                    else:
                        counts["form_only_detail_verifications"] += 1
                    counts["form_pages"] += 1

        detailed_reservations: list[dict[str, Any]] = []
        months = _month_strings(cutoff)
        if not errors:
            for source, item in reservation_items:
                uid = _clean(item.get("uid")).lower()
                counts["detail_attempts"] += 1
                try:
                    detail = _coerce_json(
                        api(
                            f"{YEOSU_API_BASE}/reservation-item/{uid}",
                            kind="detail_requests",
                        )
                    )
                    calendar_values: dict[str, Any] = {}
                    for month in months:
                        calendar_values[month] = _coerce_json(
                            api(
                                f"{YEOSU_API_BASE}/reservation-item/{uid}/calendar",
                                {"selectedMonth": month},
                                "calendar_requests",
                            )
                        )
                    row = _reservation_row(
                        target,
                        source,
                        item,
                        detail,
                        calendar_values,
                        cutoff,
                    )
                    detailed_reservations.append(row)
                    counts["detail_pages"] += 1
                except Exception as exc:
                    errors.append(
                        f"reservation {uid}: {type(exc).__name__}: {_clean(exc)}"
                    )

        if len(detailed_lectures) != len(current_lectures):
            errors.append(
                f"lecture details {len(detailed_lectures)} != required {len(current_lectures)}"
            )
        if len(detailed_reservations) != len(reservation_items):
            errors.append(
                "health reservation details "
                f"{len(detailed_reservations)} != required {len(reservation_items)}"
            )

        output = detailed_lectures + detailed_reservations
        if not errors:
            try:
                deduped = list(current_dedupe(output))
            except Exception as exc:
                errors.append(f"dedupe failed: {type(exc).__name__}: {_clean(exc)}")
                deduped = []
            if len(deduped) != len(output):
                errors.append(
                    f"dedupe changed complete row count {len(output)} to {len(deduped)}"
                )
            output = deduped

        expected_sources = (
            len(YEOSU_LECTURE_SOURCES)
            + len(YEOSU_HEALTH_EDUCATION_SOURCES)
            + 1  # audited empty forest-healing lecture owner
        )
        pagination_complete = (
            not source_cap_reached
            and counts["sentinel_requests"] == expected_sources
            and counts["page_one_rechecks"] == expected_sources
            and len(source_totals) == expected_sources
            and not any(" list:" in error for error in errors)
        )
        details_complete = (
            not source_cap_reached
            and (
                counts["detail_pages"]
                + counts["form_only_detail_verifications"]
                == required_details
            )
            and counts["form_pages"] == len(current_lectures)
            and len(detailed_lectures) == len(current_lectures)
            and len(detailed_reservations) == len(reservation_items)
        )
        application_controls_complete = (
            details_complete
            and all(
                bool(row.get("raw_fields", {}).get("application_control_verified"))
                for row in output
            )
        )
        separation_complete = (
            set(source.menu_uid for source in YEOSU_EXPERIENCE_SOURCES)
            .isdisjoint(
                {
                    _clean(row.get("raw_fields", {}).get("source_menu_uid"))
                    for row in output
                }
            )
            and len(YEOSU_EXTERNAL_OWNER_LINKS) == 3
        )
        pii_excluded = all(
            not any(
                forbidden.casefold() in repr(row).casefold()
                for forbidden in YEOSU_PII_FIELDS_DISCARDED
            )
            for row in output
        )
        snapshot_complete = (
            pagination_complete
            and details_complete
            and application_controls_complete
            and separation_complete
            and pii_excluded
            and not errors
        )
        if not snapshot_complete:
            output = []

        branch_counts = Counter(row.get("branch", "") for row in output)
        fee_conflict_count = sum(
            bool(row.get("raw_fields", {}).get("source_fee_conflict"))
            for row in detailed_lectures
        )
        meta: dict[str, Any] = {
            "pages": counts["list_requests"],
            "request_count": counts["request_count"],
            "request_attempts": counts["request_attempts"] + counts["root_requests"],
            "retry_attempts": counts["retry_attempts"],
            "root_requests": counts["root_requests"],
            "menu_requests": counts["menu_requests"],
            "list_requests": counts["list_requests"],
            "data_pages": counts["data_pages"],
            "sentinel_requests": counts["sentinel_requests"],
            "page_one_rechecks": counts["page_one_rechecks"],
            "detail_attempts": counts["detail_attempts"],
            "detail_pages": counts["detail_pages"],
            "form_only_detail_verifications": counts[
                "form_only_detail_verifications"
            ],
            "form_attempts": counts["form_attempts"],
            "form_pages": counts["form_pages"],
            "calendar_requests": counts["calendar_requests"],
            "lecture_source_count": len(YEOSU_LECTURE_SOURCES),
            "health_reservation_source_count": len(
                YEOSU_HEALTH_EDUCATION_SOURCES
            ),
            "experience_source_count": len(YEOSU_EXPERIENCE_SOURCES),
            "source_rows": len(lecture_items) + len(reservation_items),
            "lecture_history_count": len(lecture_items),
            "expired_lecture_count": len(lecture_items) - len(current_lectures),
            "current_lecture_count": len(current_lectures),
            "current_reservation_education_count": len(reservation_items),
            "current_count": required_details,
            "returned_count": len(output),
            "unique_id_count": len(set(identities)),
            "duplicate_count": duplicate_count,
            "source_totals": source_totals,
            "source_page_counts": source_page_counts,
            "branch_counts": dict(branch_counts),
            "fee_conflict_count": fee_conflict_count,
            "pagination_detected": any(
                count > 1 for count in source_page_counts.values()
            ),
            "pagination_complete": pagination_complete,
            "pagination_exhausted": pagination_complete,
            "details_complete": details_complete,
            "application_controls_complete": application_controls_complete,
            "education_experience_separated": separation_complete,
            "excluded_experience_menus": [
                source.menu_uid for source in YEOSU_EXPERIENCE_SOURCES
            ],
            "external_owner_links": dict(YEOSU_EXTERNAL_OWNER_LINKS),
            "pii_excluded": pii_excluded,
            "pii_fields_discarded": list(YEOSU_PII_FIELDS_DISCARDED),
            "hour_endpoint_called": False,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": snapshot_complete,
            "source_cap_reached": source_cap_reached,
            "no_current_data": snapshot_complete and not output,
            "no_current_reason": (
                "complete Yeosu OK education sources have no current/future rows"
                if snapshot_complete and not output
                else ""
            ),
        }
        if errors:
            meta["configured_collection_error"] = "; ".join(
                dict.fromkeys(errors)
            )
        return output, YEOSU_PARSER, meta
    finally:
        for session in sessions:
            _close_quietly(session)


collect_yeosu_target = collect_yeosu_education
collect = collect_yeosu_education


__all__ = [
    "YEOSU_PROVIDER",
    "YEOSU_CANONICAL_CANDIDATE_ID",
    "YEOSU_CANONICAL_URL",
    "YEOSU_CANDIDATE_AUDIT",
    "YEOSU_LECTURE_SOURCES",
    "YEOSU_RESERVATION_SOURCES",
    "YEOSU_HEALTH_EDUCATION_SOURCES",
    "YEOSU_EXPERIENCE_SOURCES",
    "YEOSU_EXTERNAL_OWNER_LINKS",
    "YEOSU_PARSER",
    "YeosuContractError",
    "collect_yeosu_education",
    "collect_yeosu_target",
    "collect",
    "is_yeosu_target",
    "is_target",
    "yeosu_lecture_url",
    "yeosu_lecture_application_url",
    "yeosu_reservation_url",
    "yeosu_reservation_application_url",
]
