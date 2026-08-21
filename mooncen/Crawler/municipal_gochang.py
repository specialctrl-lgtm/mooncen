"""Fail-closed collector for Gochang County's complete education ledger.

The official integrated-reservation service partitions education into six
catalogues: Women's Center, Jeonbuk National University lifelong education,
library culture classes, Agricultural Technology Center, local-culture
academy, and lifelong learning.  A snapshot is returned only after every
declared page from all six catalogues, each immediate post-boundary request,
and stable first/last boundary rechecks agree.

The Gochang application clamps an out-of-range ``startPage`` to its final
rows (and removes the active-page marker), rather than returning a literal
empty page.  The collector therefore accepts only an exact final-page
fingerprint for non-empty catalogues.  The empty Agricultural Technology
Center catalogue must remain structurally empty on its overrun request.

Only current/future rows are opened.  Public detail-table fields are bound to
the exact list identity, title, periods, venue, target and capacity.  The
inline applicant form is inspected solely to prove an identity-bound visible
application control; its action is never requested.  Contacts, instructors,
free-form descriptions, attachments, applicant fields and source HTML are
never persisted.  Any pagination, detail, application or privacy drift makes
the whole six-catalogue snapshot atomically empty.
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
from urllib.parse import parse_qsl, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


GOCHANG_PROVIDER = "MUNI_WWW_GOCHANG_GO_KR_45FFAF60"
GOCHANG_CANDIDATE_ID = "MUNI_IR_D31E2CD73D94"
GOCHANG_MUNICIPALITY_CODE = "5279000000"
GOCHANG_MUNICIPALITY_NAME = "전북특별자치도 고창군"
GOCHANG_HOST = "www.gochang.go.kr"
GOCHANG_PATH = "/reserve/index.gochang"
GOCHANG_EMITTED_PATH = "/index.gochang"
GOCHANG_CANONICAL_MENU = "DOM_000002401000000000"
GOCHANG_CANONICAL_URL = (
    f"https://{GOCHANG_HOST}{GOCHANG_PATH}?"
    + urlencode({"menuCd": GOCHANG_CANONICAL_MENU})
)
GOCHANG_PAGE_SIZE = 10
GOCHANG_MAX_WORKERS = 4
GOCHANG_MAX_HTML_BYTES = 3_000_000
GOCHANG_PARSER = (
    "gochang_six_complete_education_catalogues+declared_totals+all_pages+"
    "exact_post_last_clamps+stable_first_last+current_details+"
    "identity_bound_application_controls+facility_branches+pii_allowlist"
)

GOCHANG_SPORTS_REVIEW_PROVIDER = "MUNI_WWW_GOCHANGSPORTS_OR_KR_75FC0D60"
GOCHANG_SPORTS_REVIEW_CANDIDATE_ID = "MUNI_IR_629D7F9D4DEB"
GOCHANG_SPORTS_REVIEW_URL = (
    "http:" "//www.gochangsports.or.kr/index.gochang?"
    "menuCd=DOM_000000106000000000"
)


@dataclass(frozen=True)
class GochangCatalogue:
    code: str
    label: str
    list_menu: str
    detail_menu: str
    default_branch: str


GOCHANG_CATALOGUES: tuple[GochangCatalogue, ...] = (
    GochangCatalogue(
        "WOMEN",
        "여성회관교육",
        "DOM_000002401001000000",
        "DOM_000002401001001000",
        "고창군여성회관",
    ),
    GochangCatalogue(
        "JBNU",
        "전북대평생교육",
        "DOM_000002401002000000",
        "DOM_000002401002001000",
        "전북대학교 고창캠퍼스",
    ),
    GochangCatalogue(
        "LIBRARY",
        "도서관문화강좌",
        "DOM_000002401004000000",
        "DOM_000002401004001000",
        "고창군 공공도서관",
    ),
    GochangCatalogue(
        "AGRI",
        "농업기술센터",
        "DOM_000002401006000000",
        "DOM_000002401006001000",
        "고창군 농업기술센터",
    ),
    GochangCatalogue(
        "CULTURE",
        "고창 지역문화 아카데미",
        "DOM_000002401007000000",
        "DOM_000002401007001000",
        "고창 지역문화 아카데미",
    ),
    GochangCatalogue(
        "LIFELONG",
        "평생학습",
        "DOM_000002401008000000",
        "DOM_000002401008001000",
        "고창군 평생학습",
    ),
)
GOCHANG_CATALOGUE_BY_CODE = {item.code: item for item in GOCHANG_CATALOGUES}
GOCHANG_CATALOGUE_BY_LIST_MENU = {
    item.list_menu: item for item in GOCHANG_CATALOGUES
}
GOCHANG_CATALOGUE_BY_DETAIL_MENU = {
    item.detail_menu: item for item in GOCHANG_CATALOGUES
}

GOCHANG_DISCOVERY_AUDIT: dict[str, Any] = {
    "canonical_owner": {
        "decision": "include_complete_six_catalogue_education_owner",
        "url": GOCHANG_CANONICAL_URL,
        "provider": GOCHANG_PROVIDER,
        "candidate_id": GOCHANG_CANDIDATE_ID,
        "catalogues": tuple(item.code for item in GOCHANG_CATALOGUES),
    },
    "sports_review_candidate": {
        "decision": "exclude_http_only_sports_facility_not_education_owner",
        "url": GOCHANG_SPORTS_REVIEW_URL,
        "provider": GOCHANG_SPORTS_REVIEW_PROVIDER,
        "candidate_id": GOCHANG_SPORTS_REVIEW_CANDIDATE_ID,
    },
}

GOCHANG_PII_FIELDS_NEVER_PERSISTED = (
    "신청자명",
    "생년월일",
    "성별",
    "주소",
    "연락처",
    "이메일",
    "강사명",
    "문의담당자",
    "문의전화",
    "교육내용",
    "강의자료",
    "첨부파일",
    "개인정보 동의 본문",
    "신청 액션",
    "원문 HTML",
)

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class GochangContractError(ValueError):
    """Raised when the audited public Gochang contract changes."""


@dataclass(frozen=True)
class _ListPage:
    catalogue: str
    requested: int
    observed: Optional[int]
    total: int
    last: int
    rows: tuple[dict[str, Any], ...]
    empty_marker: bool


_SPACE = re.compile(r"\s+")
_IDENTITY = re.compile(r"^GORE\d{7}$")
_PERIOD = re.compile(
    r"^(20\d{2})-(\d{2})-(\d{2})\s*~\s*"
    r"(20\d{2})-(\d{2})-(\d{2})$"
)
_CAPACITY = re.compile(r"^([\d,]+)\s*/\s*([\d,]+)$")
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_APPLICATION_ACTION = re.compile(
    r"^/user/[A-Za-z0-9]+Epr/traineeWriteAct\.gochang$"
)
_LIBRARY_BRANCH = re.compile(r"^(.+?도서관)(?:\s|$)")

# Two immutable archived library rows have their start/end values reversed in
# the official list.  They are retained in declared-total and identity proofs,
# but can never be treated as current without an explicit audit update.
_ARCHIVED_REVERSED_PERIOD_EXCEPTIONS: dict[str, tuple[str, str, str]] = {
    "GORE0000729": (
        "LIBRARY",
        "고창군립성호도서관 문화가 있는 날(5월) [말랑말랑 창의 놀이 뇌블럭]",
        "2026-05-29 ~ 2026-05-27",
    ),
    "GORE0000192": (
        "LIBRARY",
        "시 한 수, 나무 한 그루(흥덕)(화)",
        "2021-11-09 ~ 2021-10-26",
    ),
}
_ARCHIVED_REVERSED_APPLICATION_EXCEPTIONS: dict[
    str, tuple[str, str, str]
] = {
    "GORE0000376": (
        "LIBRARY",
        "성호도서관 문화행사 '책으로 크는 아이들'(8월)",
        "2023-08-01 ~ 2023-07-27",
    ),
}
_ARCHIVED_ZERO_CAPACITY_EXCEPTIONS: dict[str, tuple[str, str, str]] = {
    "GORE0000164": ("LIBRARY", "인문학에 물들다", "2/0"),
}

_LIST_QUERY_DEFAULTS = {
    "searchCondition": "RE_NAME",
    "searchKeyword": "",
    "orderField": "",
    "orderSort": "desc",
    "searchDateGubun": "3",
}
_CARD_LABELS = ("교육기간", "접수기간", "교육장", "모집대상")
_DETAIL_REQUIRED = {
    "강의명",
    "접수기간",
    "교육기간",
    "교육시간",
    "교육장",
    "수강료",
    "교육대상",
    "신청/정원",
    "접수상태",
}
_STATUS_CONTRACT: dict[str, tuple[frozenset[str], str, frozenset[str], bool]] = {
    "온라인 접수중": (
        frozenset({"rec", "rec02"}),
        "교육신청",
        frozenset({"possible", "possible01", "blink"}),
        True,
    ),
    "접수완료": (
        frozenset({"rec", "rec03"}),
        "접수마감",
        frozenset({"possible", "possible02"}),
        False,
    ),
    "교육중": (
        frozenset({"rec", "rec04"}),
        "접수마감",
        frozenset({"possible", "possible02"}),
        False,
    ),
    "교육종료": (
        frozenset({"rec", "rec04"}),
        "접수마감",
        frozenset({"possible", "possible02"}),
        False,
    ),
}

_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "catalogue",
        "catalogue_name",
        "list_page",
        "source_status",
        "source_status_classes",
        "source_period",
        "source_apply_period",
        "source_schedule",
        "source_venue",
        "source_target",
        "source_capacity_current",
        "source_capacity_total",
        "detail_verified",
        "application_control_present",
        "service_family",
    }
)
_FORBIDDEN_PERSISTED_KEYS = frozenset(
    {
        "phone",
        "email",
        "contact",
        "instructor",
        "staff",
        "attachments",
        "attachment_urls",
        "detail_description",
        "source_html",
        "raw_html",
        "application_action",
        "applicant_fields",
    }
)


def _clean(value: Any) -> str:
    return _SPACE.sub(
        " ", html.unescape(str(value or "")).replace("\xa0", " ")
    ).strip()


def _value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _query_map(parsed: Any) -> Optional[dict[str, str]]:
    try:
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return None
    if len(pairs) != len({key for key, _value in pairs}):
        return None
    return dict(pairs)


def _strict_https_owner(parsed: Any, *, path: str) -> bool:
    try:
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == GOCHANG_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == path
        and not parsed.params
        and not parsed.fragment
    )


def is_gochang_education_target(target: Any) -> bool:
    if _clean(_value(target, "provider")) != GOCHANG_PROVIDER:
        return False
    parsed = urlparse(_clean(_value(target, "url")))
    query = _query_map(parsed)
    return bool(
        _strict_https_owner(parsed, path=GOCHANG_PATH)
        and query == {"menuCd": GOCHANG_CANONICAL_MENU}
    )


is_target = is_gochang_education_target


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a positive integer")
    raw = _clean(value)
    if not raw.isdigit() or int(raw) < 1:
        raise ValueError(f"{label} must be a positive integer")
    return int(raw)


def gochang_list_url(catalogue: str, page: Any = 1) -> str:
    item = GOCHANG_CATALOGUE_BY_CODE.get(_clean(catalogue))
    if item is None:
        raise ValueError("unknown Gochang education catalogue")
    current = _positive_int(page, "page")
    query = {"menuCd": item.list_menu, **_LIST_QUERY_DEFAULTS, "startPage": current}
    return f"https://{GOCHANG_HOST}{GOCHANG_PATH}?" + urlencode(query)


def gochang_detail_url(
    catalogue: str,
    identity: Any,
    *,
    source_page: Any = 1,
) -> str:
    item = GOCHANG_CATALOGUE_BY_CODE.get(_clean(catalogue))
    course_id = _clean(identity)
    if item is None or not _IDENTITY.fullmatch(course_id):
        raise ValueError("invalid Gochang detail identity")
    page = _positive_int(source_page, "source_page")
    query = {
        "menuCd": item.detail_menu,
        "reUniqId": course_id,
        **_LIST_QUERY_DEFAULTS,
        "startPage": page,
    }
    return f"https://{GOCHANG_HOST}{GOCHANG_PATH}?" + urlencode(query)


def _request_url_allowed(value: str) -> bool:
    parsed = urlparse(value)
    if not _strict_https_owner(parsed, path=GOCHANG_PATH):
        return False
    query = _query_map(parsed)
    if query is None:
        return False
    menu = query.get("menuCd", "")
    if menu == GOCHANG_CANONICAL_MENU:
        return query == {"menuCd": GOCHANG_CANONICAL_MENU}
    if menu in GOCHANG_CATALOGUE_BY_LIST_MENU:
        expected_keys = {"menuCd", *_LIST_QUERY_DEFAULTS, "startPage"}
        if set(query) != expected_keys:
            return False
    elif menu in GOCHANG_CATALOGUE_BY_DETAIL_MENU:
        expected_keys = {
            "menuCd",
            "reUniqId",
            *_LIST_QUERY_DEFAULTS,
            "startPage",
        }
        if set(query) != expected_keys or not _IDENTITY.fullmatch(
            query.get("reUniqId", "")
        ):
            return False
    else:
        return False
    return bool(
        all(query.get(key) == value for key, value in _LIST_QUERY_DEFAULTS.items())
        and query.get("startPage", "").isdigit()
        and int(query["startPage"]) >= 1
    )


def _emitted_detail_link(
    href: Any,
    catalogue: GochangCatalogue,
    identity: str,
    source_page: int,
) -> bool:
    raw = _clean(href)
    parsed = urlparse(
        raw
        if raw.startswith("https://")
        else f"https://{GOCHANG_HOST}{raw if raw.startswith('/') else '/' + raw}"
    )
    if not _strict_https_owner(parsed, path=GOCHANG_EMITTED_PATH):
        return False
    query = _query_map(parsed)
    expected = {
        "menuCd": catalogue.detail_menu,
        "reUniqId": identity,
        **_LIST_QUERY_DEFAULTS,
        "startPage": str(source_page),
    }
    return query == expected


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MooncenMunicipalCrawler/1.0)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=False)


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _response_soup(value: Any, requested_url: str) -> BeautifulSoup:
    if not _request_url_allowed(requested_url):
        raise GochangContractError("non-canonical request URL refused")
    status = int(getattr(value, "status_code", 200))
    if status < 200 or status >= 300:
        raise GochangContractError(f"HTTP {status} is not a successful response")
    headers = getattr(value, "headers", {}) or {}
    if headers.get("Location"):
        raise GochangContractError("redirect response is not accepted")
    final_url = _clean(getattr(value, "url", requested_url)) or requested_url
    if not _request_url_allowed(final_url):
        raise GochangContractError("response left the audited HTTPS owner/path")
    content = getattr(value, "content", None)
    if content is None:
        content = str(getattr(value, "text", value)).encode("utf-8")
    if not isinstance(content, (bytes, bytearray)) or not content:
        raise GochangContractError("empty HTML response")
    if len(content) > GOCHANG_MAX_HTML_BYTES:
        raise GochangContractError("HTML response exceeds safety limit")
    return BeautifulSoup(bytes(content), "html.parser")


def _fetch_many(
    items: list[tuple[Any, str]],
    *,
    timeout: int,
    max_workers: int,
    session_factory: SessionFactory,
    fetcher: Fetcher,
) -> dict[Any, BeautifulSoup]:
    if not items:
        return {}
    if len({key for key, _url in items}) != len(items):
        raise GochangContractError("duplicate parallel request key")
    workers = min(max_workers, len(items))
    chunks: list[list[tuple[Any, str]]] = [[] for _ in range(workers)]
    for index, item in enumerate(items):
        chunks[index % workers].append(item)

    def run(
        chunk: list[tuple[Any, str]],
    ) -> tuple[dict[Any, BeautifulSoup], list[str]]:
        values: dict[Any, BeautifulSoup] = {}
        errors: list[str] = []
        session = session_factory()
        try:
            for key, url in chunk:
                try:
                    response = fetcher(session, url, timeout)
                    values[key] = _response_soup(response, url)
                except Exception as exc:
                    errors.append(f"{key}: {type(exc).__name__}: {_clean(exc)}")
        finally:
            _close_quietly(session)
        return values, errors

    values: dict[Any, BeautifulSoup] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(run, chunk) for chunk in chunks if chunk]
        for future in as_completed(futures):
            current, current_errors = future.result()
            values.update(current)
            errors.extend(current_errors)
    if errors:
        raise GochangContractError("; ".join(sorted(errors)))
    if len(values) != len(items):
        raise GochangContractError("parallel fetch cardinality changed")
    return values


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    raw = _clean(value)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        raise ValueError("today must be an ISO date")
    return date.fromisoformat(raw)


def _period(value: Any, *, label: str) -> tuple[date, date, str]:
    raw = _clean(value)
    match = _PERIOD.fullmatch(raw)
    if match is None:
        raise GochangContractError(f"invalid {label}")
    numbers = [int(part) for part in match.groups()]
    try:
        start = date(*numbers[:3])
        end = date(*numbers[3:])
    except ValueError as exc:
        raise GochangContractError(f"invalid {label}") from exc
    if end < start:
        raise GochangContractError(f"reversed {label}")
    return start, end, f"{start.isoformat()} ~ {end.isoformat()}"


def _safe_public_text(value: Any, *, label: str, required: bool = True) -> str:
    result = _clean(value)
    if required and not result:
        raise GochangContractError(f"empty {label}")
    if _PHONE.search(result) or _EMAIL.search(result):
        raise GochangContractError(f"PII/contact pattern in {label}")
    return result


def _capacity(
    value: Any, *, label: str, allow_zero_maximum: bool = False
) -> tuple[int, int]:
    match = _CAPACITY.fullmatch(_clean(value))
    if match is None:
        raise GochangContractError(f"invalid {label}")
    current, maximum = (int(part.replace(",", "")) for part in match.groups())
    if current < 0 or maximum < (0 if allow_zero_maximum else 1):
        raise GochangContractError(f"invalid {label}")
    return current, maximum


def _status_text(node: Any, count_text: str) -> str:
    values = [_clean(value) for value in node.stripped_strings]
    if count_text:
        removed = False
        filtered: list[str] = []
        for value in values:
            if not removed and value == count_text:
                removed = True
                continue
            filtered.append(value)
        values = filtered
    if len(values) != 1:
        raise GochangContractError("course status structure changed")
    return values[0]


def _parse_card(
    card: Any,
    catalogue: GochangCatalogue,
    requested: int,
) -> dict[str, Any]:
    title_link = card.select_one('dl > dt > a[href*="reUniqId="]')
    if title_link is None:
        raise GochangContractError(
            f"{catalogue.code} page {requested}: course identity missing"
        )
    parsed_link = urlparse(_clean(title_link.get("href")))
    query = _query_map(parsed_link)
    identity = _clean((query or {}).get("reUniqId"))
    if not _IDENTITY.fullmatch(identity):
        raise GochangContractError(
            f"{catalogue.code} page {requested}: invalid reUniqId"
        )
    if not _emitted_detail_link(
        title_link.get("href"), catalogue, identity, requested
    ):
        raise GochangContractError(
            f"{catalogue.code} page {requested}: unsafe detail link"
        )
    title = _safe_public_text(
        title_link.get_text(" ", strip=True), label="course title"
    )

    fields: dict[str, str] = {}
    for node in card.select("dl > dd"):
        strong = node.select_one(":scope > strong")
        strings = [_clean(value) for value in node.stripped_strings]
        if strong is None or len(strings) < 2:
            raise GochangContractError(
                f"{catalogue.code} page {requested}: card field changed"
            )
        label = _clean(strong.get_text(" ", strip=True))
        value = _clean(" ".join(strings[1:]))
        if label in fields:
            raise GochangContractError(
                f"{catalogue.code} page {requested}: duplicate {label}"
            )
        fields[label] = value
    if tuple(fields) != _CARD_LABELS:
        raise GochangContractError(
            f"{catalogue.code} page {requested}: card labels changed"
        )
    historical_invalid = False
    education_period = _clean(fields["교육기간"])
    exception = _ARCHIVED_REVERSED_PERIOD_EXCEPTIONS.get(identity)
    if exception is not None:
        if exception != (catalogue.code, title, education_period):
            raise GochangContractError(
                f"{identity}: archived reversed-period exception changed"
            )
        match = _PERIOD.fullmatch(education_period)
        if match is None:
            raise GochangContractError(
                f"{identity}: archived reversed-period shape changed"
            )
        numbers = [int(part) for part in match.groups()]
        first_date = date(*numbers[:3])
        second_date = date(*numbers[3:])
        if second_date >= first_date:
            raise GochangContractError(
                f"{identity}: archived exception is no longer reversed"
            )
        start, end, period = first_date, first_date, education_period
        historical_invalid = True
    else:
        start, end, period = _period(
            education_period, label="education period"
        )
    application_period = _clean(fields["접수기간"])
    apply_exception = _ARCHIVED_REVERSED_APPLICATION_EXCEPTIONS.get(identity)
    if apply_exception is not None:
        if apply_exception != (catalogue.code, title, application_period):
            raise GochangContractError(
                f"{identity}: archived reversed-application exception changed"
            )
        match = _PERIOD.fullmatch(application_period)
        if match is None:
            raise GochangContractError(
                f"{identity}: archived reversed-application shape changed"
            )
        numbers = [int(part) for part in match.groups()]
        if date(*numbers[3:]) >= date(*numbers[:3]):
            raise GochangContractError(
                f"{identity}: archived application exception is no longer reversed"
            )
        apply_period = application_period
        historical_invalid = True
    else:
        _apply_start, _apply_end, apply_period = _period(
            application_period, label="application period"
        )
    venue = _safe_public_text(fields["교육장"], label="education venue")
    target = _safe_public_text(fields["모집대상"], label="education target")

    status_node = card.select_one(":scope > p.rec")
    possible = card.select_one(":scope > a.possible")
    if status_node is None or possible is None:
        raise GochangContractError(
            f"{catalogue.code} page {requested}: status controls missing"
        )
    count_node = status_node.select_one(":scope > span")
    count_text = _clean(count_node.get_text(" ", strip=True) if count_node else "")
    current, maximum = _capacity(
        count_text,
        label="list application capacity",
        allow_zero_maximum=True,
    )
    capacity_exception = _ARCHIVED_ZERO_CAPACITY_EXCEPTIONS.get(identity)
    if maximum == 0:
        if capacity_exception != (catalogue.code, title, count_text):
            raise GochangContractError(
                f"{identity}: unaudited zero-capacity archive row"
            )
        historical_invalid = True
    elif capacity_exception is not None:
        raise GochangContractError(
            f"{identity}: archived zero-capacity exception changed"
        )
    source_status = _status_text(status_node, count_text)
    contract = _STATUS_CONTRACT.get(source_status)
    if contract is None:
        raise GochangContractError(
            f"{catalogue.code} page {requested}: unknown course status"
        )
    rec_classes, possible_text, possible_classes, is_open = contract
    if (
        frozenset(status_node.get("class", [])) != rec_classes
        or _clean(possible.get_text(" ", strip=True)) != possible_text
        or frozenset(possible.get("class", [])) != possible_classes
        or (
            is_open
            and not _emitted_detail_link(
                possible.get("href"), catalogue, identity, requested
            )
        )
        or (not is_open and possible.has_attr("href"))
    ):
        raise GochangContractError(
            f"{catalogue.code} page {requested}: status/application link drift"
        )
    return {
        "identity": identity,
        "catalogue": catalogue.code,
        "catalogue_name": catalogue.label,
        "list_page": requested,
        "title": title,
        "period": period,
        "start": start,
        "end": end,
        "apply_period": apply_period,
        "venue": venue,
        "target": target,
        "source_status": source_status,
        "source_status_classes": " ".join(status_node.get("class", [])),
        "capacity_current": current,
        "capacity_total": maximum,
        "is_open": is_open,
        "historical_invalid": historical_invalid,
    }


def _parse_list_page(
    soup: BeautifulSoup,
    catalogue: GochangCatalogue,
    requested: int,
) -> _ListPage:
    hidden = soup.select('form[name="listForm"] input[name="startPage"]')
    if len(hidden) != 1 or _clean(hidden[0].get("value")) != str(requested):
        raise GochangContractError(
            f"{catalogue.code} page {requested}: request identity changed"
        )
    total_nodes = soup.select(".search_result .last > span")
    if len(total_nodes) != 1:
        raise GochangContractError(
            f"{catalogue.code} page {requested}: declared total missing"
        )
    raw_total = _clean(total_nodes[0].get_text(" ", strip=True)).replace(",", "")
    if not raw_total.isdigit():
        raise GochangContractError(
            f"{catalogue.code} page {requested}: invalid declared total"
        )
    total = int(raw_total)
    last = max(1, math.ceil(total / GOCHANG_PAGE_SIZE))
    active = soup.select(".bbs_page > span.on")
    if len(active) > 1:
        raise GochangContractError(
            f"{catalogue.code} page {requested}: active page marker changed"
        )
    observed: Optional[int] = None
    if active:
        raw_observed = _clean(active[0].get_text(" ", strip=True))
        if not raw_observed.isdigit() or int(raw_observed) < 1:
            raise GochangContractError(
                f"{catalogue.code} page {requested}: invalid active page"
            )
        observed = int(raw_observed)

    container = soup.select(".bbs_list01 > ul")
    if len(container) != 1:
        raise GochangContractError(
            f"{catalogue.code} page {requested}: list container changed"
        )
    cards = [
        node
        for node in container[0].select(":scope > li")
        if node.select_one('dl > dt > a[href*="reUniqId="]') is not None
    ]
    rows = tuple(_parse_card(card, catalogue, requested) for card in cards)
    all_nodes = container[0].select(":scope > li")
    empty_marker = bool(
        not rows
        and len(all_nodes) == 1
        and _clean(all_nodes[0].get_text(" ", strip=True))
        == "검색된 자료가 없습니다."
    )
    if not rows and not empty_marker:
        raise GochangContractError(
            f"{catalogue.code} page {requested}: neither rows nor empty marker"
        )
    return _ListPage(
        catalogue=catalogue.code,
        requested=requested,
        observed=observed,
        total=total,
        last=last,
        rows=rows,
        empty_marker=empty_marker,
    )


def _page_signature(page: _ListPage, *, include_requested: bool = False) -> str:
    payload = [
        page.catalogue,
        str(page.requested if include_requested else ""),
        str(page.total),
        str(page.last),
        str(page.empty_marker),
    ]
    for row in page.rows:
        payload.append(
            "|".join(
                _clean(row.get(key))
                for key in (
                    "identity",
                    "title",
                    "period",
                    "apply_period",
                    "venue",
                    "target",
                    "source_status",
                    "source_status_classes",
                    "capacity_current",
                    "capacity_total",
                    "is_open",
                    "historical_invalid",
                )
            )
        )
    return hashlib.sha256("\n".join(payload).encode("utf-8")).hexdigest()


def _detail_fields(table: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in table.select("tbody > tr"):
        heading = row.select_one(":scope > th")
        value = row.select_one(":scope > td")
        if heading is None or value is None:
            raise GochangContractError("detail table structure changed")
        label = _clean(heading.get_text(" ", strip=True))
        if label in result:
            raise GochangContractError(f"duplicate detail label {label}")
        result[label] = _clean(value.get_text(" ", strip=True))
    return result


def _application_form_valid(form: Any, identity: str) -> bool:
    action = urlparse(_clean(form.get("action")))
    if (
        action.scheme
        or action.netloc
        or action.params
        or action.query
        or action.fragment
        or not _APPLICATION_ACTION.fullmatch(action.path)
        or _clean(form.get("method")).lower() != "post"
    ):
        return False
    identities = form.select('input[type="hidden"][name="reUniqId"]')
    menus = form.select('input[type="hidden"][name="menuCd"]')
    submit = form.select('input[type="submit"]')
    return bool(
        len(identities) == 1
        and _clean(identities[0].get("value")) == identity
        and len(menus) == 1
        and re.fullmatch(r"DOM_\d{18}", _clean(menus[0].get("value")))
        and len(submit) == 1
        and _clean(submit[0].get("value")) == "예약하기"
    )


def _facility_branch(catalogue: GochangCatalogue, venue: str) -> str:
    if catalogue.code != "LIBRARY":
        return catalogue.default_branch
    match = _LIBRARY_BRANCH.match(venue)
    if match is None:
        raise GochangContractError("library venue has no facility identity")
    return _clean(match.group(1))


def _branch_code(branch: str) -> str:
    digest = hashlib.sha1(_clean(branch).encode("utf-8")).hexdigest()[:12].upper()
    return f"{GOCHANG_PROVIDER}:{digest}"


def _parse_detail(listed: Mapping[str, Any], soup: BeautifulSoup) -> dict[str, Any]:
    identity = _clean(listed.get("identity"))
    catalogue = GOCHANG_CATALOGUE_BY_CODE.get(_clean(listed.get("catalogue")))
    if catalogue is None:
        raise GochangContractError(f"course {identity}: unknown catalogue")
    public_roots = [
        node for node in soup.select("div.res_box") if _clean(node.get("id")) != "writeId"
    ]
    if len(public_roots) != 1:
        raise GochangContractError(f"course {identity}: public detail shell changed")
    root = public_roots[0]
    title_node = root.select_one(":scope > div > h4")
    tables = root.select(":scope > div > table.view_table")
    if title_node is None or len(tables) != 1:
        raise GochangContractError(f"course {identity}: public detail table missing")
    title = _safe_public_text(
        title_node.get_text(" ", strip=True), label="detail title"
    )
    fields = _detail_fields(tables[0])
    if not _DETAIL_REQUIRED <= set(fields):
        raise GochangContractError(f"course {identity}: public detail fields missing")
    if title != _clean(listed.get("title")) or fields["강의명"] != title:
        raise GochangContractError(f"course {identity}: title identity drift")

    start, end, period = _period(fields["교육기간"], label="detail education period")
    _apply_start, _apply_end, apply_period = _period(
        fields["접수기간"], label="detail application period"
    )
    venue = _safe_public_text(fields["교육장"], label="detail venue")
    target = _safe_public_text(fields["교육대상"], label="detail target")
    schedule = _safe_public_text(fields["교육시간"], label="detail schedule")
    fee = _safe_public_text(fields["수강료"], label="detail fee")
    current, maximum = _capacity(fields["신청/정원"], label="detail capacity")
    if (
        start != listed.get("start")
        or end != listed.get("end")
        or period != _clean(listed.get("period"))
        or apply_period != _clean(listed.get("apply_period"))
        or venue != _clean(listed.get("venue"))
        or target != _clean(listed.get("target"))
        or current != int(listed.get("capacity_current", -1))
        or maximum != int(listed.get("capacity_total", -1))
        or fields["접수상태"] != _clean(listed.get("source_status"))
    ):
        raise GochangContractError(f"course {identity}: list/detail public identity drift")

    applicant_forms = [
        form
        for form in soup.select("form[action]")
        if "traineeWrite" in _clean(form.get("action"))
        or form.select_one('input[type="hidden"][name="reUniqId"]') is not None
    ]
    reservation_submits = [
        node
        for node in soup.select('input[type="submit"]')
        if _clean(node.get("value")) == "예약하기"
    ]
    is_open = bool(listed.get("is_open"))
    if is_open:
        if (
            len(applicant_forms) != 1
            or len(reservation_submits) != 1
            or not _application_form_valid(applicant_forms[0], identity)
            or reservation_submits[0].find_parent("form") is not applicant_forms[0]
        ):
            raise GochangContractError(
                f"course {identity}: identity-bound application control changed"
            )
    elif applicant_forms or reservation_submits:
        raise GochangContractError(
            f"course {identity}: closed course exposes applicant control"
        )

    detail_url = gochang_detail_url(
        catalogue.code, identity, source_page=int(listed["list_page"])
    )
    branch = _facility_branch(catalogue, venue)
    provider_course_id = f"{GOCHANG_PROVIDER}:education:{identity}"
    return {
        "provider": GOCHANG_PROVIDER,
        "provider_course_id": provider_course_id,
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": title,
        "branch": branch,
        "branch_code": _branch_code(branch),
        "preserve_branch": True,
        "category": catalogue.label,
        "program_type": "교육",
        "raw_url": detail_url,
        "application_url": detail_url if is_open else "",
        "application_type": "ONLINE_RESERVATION" if is_open else "INFO_ONLY",
        "application_method": "온라인" if is_open else "",
        "application_methods": ["온라인"] if is_open else [],
        "reservation_available": is_open,
        "status": "OPEN" if is_open else "CLOSED",
        "fee": fee,
        "period": period,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": apply_period,
        "schedule_raw": schedule,
        "capacity": f"{maximum}명",
        "capacity_current": current,
        "capacity_total": maximum,
        "waitlist_current": 0,
        "target": target,
        "venue": venue,
        "venue_name": venue,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": GOCHANG_PARSER,
        "municipality_code": GOCHANG_MUNICIPALITY_CODE,
        "municipality_full_name": GOCHANG_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "catalogue": catalogue.code,
            "catalogue_name": catalogue.label,
            "list_page": int(listed["list_page"]),
            "source_status": _clean(listed["source_status"]),
            "source_status_classes": _clean(listed["source_status_classes"]),
            "source_period": period,
            "source_apply_period": apply_period,
            "source_schedule": schedule,
            "source_venue": venue,
            "source_target": target,
            "source_capacity_current": current,
            "source_capacity_total": maximum,
            "detail_verified": True,
            "application_control_present": is_open,
            "service_family": "education",
        },
    }


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_PERSISTED_KEYS:
        errors.append("forbidden detail/PII key persisted")
    raw_fields = row.get("raw_fields")
    if not isinstance(raw_fields, Mapping) or not set(raw_fields) <= _SAFE_RAW_FIELDS:
        errors.append("raw_fields exceeded the PII-safe allowlist")
    payload = repr(
        {
            key: value
            for key, value in row.items()
            if key not in {"raw_url", "application_url", "provider_course_id"}
        }
    )
    if _PHONE.search(payload) or _EMAIL.search(payload):
        errors.append("PII-like contact data persisted")
    if row.get("description") != row.get("title"):
        errors.append("free-form detail description persisted")
    return errors


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _base_meta() -> dict[str, Any]:
    return {
        "provider": GOCHANG_PROVIDER,
        "canonical_candidate_id": GOCHANG_CANDIDATE_ID,
        "canonical_url": GOCHANG_CANONICAL_URL,
        "municipality_code": GOCHANG_MUNICIPALITY_CODE,
        "municipality_name": GOCHANG_MUNICIPALITY_NAME,
        "pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "data_pages": 0,
        "sentinel_requests": 0,
        "boundary_rechecks": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "source_total": 0,
        "source_rows": 0,
        "unique_identity_count": 0,
        "identity_duplicate_count": 0,
        "current_candidate_count": 0,
        "current_source_count": 0,
        "expired_count": 0,
        "archived_rows_skipped_before_detail": 0,
        "source_cap_reached": False,
        "pagination_complete": False,
        "details_complete": False,
        "application_controls_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "returned_count": 0,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": "",
        "forbidden_applicant_endpoint_requests": 0,
        "pii_payload_persisted": False,
        "pii_fields_never_persisted": list(GOCHANG_PII_FIELDS_NEVER_PERSISTED),
        "discovery_audit": GOCHANG_DISCOVERY_AUDIT,
    }


def collect_gochang_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 100,
    detail_limit: int = 500,
    max_workers: int = GOCHANG_MAX_WORKERS,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect all six official Gochang education catalogues atomically."""

    meta = _base_meta()
    if not is_gochang_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match the audited Gochang HTTPS education owner"
        )
        return [], GOCHANG_PARSER, meta
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, int)
        or timeout < 1
        or isinstance(max_pages, bool)
        or not isinstance(max_pages, int)
        or max_pages < 1
        or isinstance(detail_limit, bool)
        or not isinstance(detail_limit, int)
        or detail_limit < 0
        or isinstance(max_workers, bool)
        or not isinstance(max_workers, int)
        or max_workers < 1
        or max_workers > 16
    ):
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": "invalid collection limits",
            }
        )
        return [], GOCHANG_PARSER, meta

    try:
        cutoff = _today(today)
        factory = session_factory or _default_session_factory
        current_fetcher = fetcher or _default_fetcher

        first_items = [
            ((item.code, "first", 1), gochang_list_url(item.code, 1))
            for item in GOCHANG_CATALOGUES
        ]
        meta["list_requests"] = len(first_items)
        first_soups = _fetch_many(
            first_items,
            timeout=timeout,
            max_workers=max_workers,
            session_factory=factory,
            fetcher=current_fetcher,
        )
        pages: dict[tuple[str, int], _ListPage] = {}
        first_pages: dict[str, _ListPage] = {}
        for item in GOCHANG_CATALOGUES:
            first = _parse_list_page(
                first_soups[(item.code, "first", 1)], item, 1
            )
            if first.observed != 1:
                raise GochangContractError(
                    f"{item.code}: first-page identity changed"
                )
            first_pages[item.code] = first
            pages[(item.code, 1)] = first

        data_pages = sum(page.last for page in first_pages.values())
        meta["data_pages"] = data_pages
        if data_pages > max_pages:
            meta["source_cap_reached"] = True
            raise GochangContractError(
                f"max_pages cap allows {max_pages} of {data_pages} data pages"
            )

        tasks: list[tuple[Any, str]] = []
        for item in GOCHANG_CATALOGUES:
            first = first_pages[item.code]
            tasks.extend(
                (
                    (item.code, "data", page),
                    gochang_list_url(item.code, page),
                )
                for page in range(2, first.last + 1)
            )
            tasks.append(
                (
                    (item.code, "sentinel", first.last + 1),
                    gochang_list_url(item.code, first.last + 1),
                )
            )
            tasks.extend(
                (
                    (item.code, "recheck", page),
                    gochang_list_url(item.code, page),
                )
                for page in sorted({1, first.last})
            )
        meta["list_requests"] += len(tasks)
        meta["required_list_requests"] = meta["list_requests"]
        meta["sentinel_requests"] = len(GOCHANG_CATALOGUES)
        meta["boundary_rechecks"] = sum(
            len({1, first.last}) for first in first_pages.values()
        )
        fetched = _fetch_many(
            tasks,
            timeout=timeout,
            max_workers=max_workers,
            session_factory=factory,
            fetcher=current_fetcher,
        )

        sentinel_modes: dict[str, str] = {}
        page_sizes: dict[str, list[int]] = {}
        for item in GOCHANG_CATALOGUES:
            first = first_pages[item.code]
            for page in range(2, first.last + 1):
                parsed = _parse_list_page(
                    fetched[(item.code, "data", page)], item, page
                )
                if (
                    parsed.observed != page
                    or parsed.total != first.total
                    or parsed.last != first.last
                    or parsed.empty_marker
                ):
                    raise GochangContractError(
                        f"{item.code} page {page}: declared boundary changed"
                    )
                pages[(item.code, page)] = parsed

            expected_sizes = [
                GOCHANG_PAGE_SIZE
                if page < first.last
                else first.total - GOCHANG_PAGE_SIZE * (first.last - 1)
                for page in range(1, first.last + 1)
            ]
            if first.total == 0:
                expected_sizes = [0]
            observed_sizes = [
                len(pages[(item.code, page)].rows)
                for page in range(1, first.last + 1)
            ]
            if observed_sizes != expected_sizes:
                raise GochangContractError(
                    f"{item.code}: declared page sizes changed"
                )
            for page in range(1, first.last + 1):
                parsed = pages[(item.code, page)]
                if (
                    parsed.total != first.total
                    or parsed.last != first.last
                    or parsed.observed != page
                    or (first.total > 0 and parsed.empty_marker)
                    or (first.total == 0 and not parsed.empty_marker)
                ):
                    raise GochangContractError(
                        f"{item.code} page {page}: data-page contract changed"
                    )
            page_sizes[item.code] = observed_sizes

            sentinel_page = first.last + 1
            sentinel = _parse_list_page(
                fetched[(item.code, "sentinel", sentinel_page)],
                item,
                sentinel_page,
            )
            if sentinel.total != first.total or sentinel.last != first.last:
                raise GochangContractError(
                    f"{item.code}: post-last total boundary changed"
                )
            if first.total:
                last = pages[(item.code, first.last)]
                if (
                    sentinel.observed is not None
                    or sentinel.empty_marker
                    or _page_signature(sentinel) != _page_signature(last)
                ):
                    raise GochangContractError(
                        f"{item.code}: post-last exact clamp changed"
                    )
                sentinel_modes[item.code] = "exact_last_page_clamp"
            else:
                if (
                    sentinel.observed != 1
                    or sentinel.rows
                    or not sentinel.empty_marker
                ):
                    raise GochangContractError(
                        f"{item.code}: empty catalogue sentinel changed"
                    )
                sentinel_modes[item.code] = "structural_empty_catalogue_clamp"

            for page in sorted({1, first.last}):
                rechecked = _parse_list_page(
                    fetched[(item.code, "recheck", page)], item, page
                )
                if (
                    rechecked.observed != page
                    or _page_signature(rechecked, include_requested=True)
                    != _page_signature(
                        pages[(item.code, page)], include_requested=True
                    )
                ):
                    raise GochangContractError(
                        f"{item.code} page {page}: boundary stability changed"
                    )

        listed = [
            row
            for item in GOCHANG_CATALOGUES
            for page in range(1, first_pages[item.code].last + 1)
            for row in pages[(item.code, page)].rows
        ]
        declared_total = sum(first.total for first in first_pages.values())
        if len(listed) != declared_total:
            raise GochangContractError("six-catalogue declared total changed")
        identities = [_clean(row["identity"]) for row in listed]
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count:
            raise GochangContractError(
                f"{duplicate_count} duplicate reUniqId identities"
            )
        historical_invalid = [
            row for row in listed if bool(row.get("historical_invalid"))
        ]
        expected_historical_invalid = set(_ARCHIVED_REVERSED_PERIOD_EXCEPTIONS) | set(
            _ARCHIVED_REVERSED_APPLICATION_EXCEPTIONS
        ) | set(_ARCHIVED_ZERO_CAPACITY_EXCEPTIONS)
        if {
            _clean(row["identity"]) for row in historical_invalid
        } != expected_historical_invalid:
            raise GochangContractError(
                "archived reversed-period exception inventory changed"
            )
        if any(row["end"] >= cutoff for row in historical_invalid):
            raise GochangContractError(
                "an archived reversed-period exception entered the current window"
            )
        current = [
            row
            for row in listed
            if not row.get("historical_invalid") and row["end"] >= cutoff
        ]
        if len(current) > detail_limit:
            meta["source_cap_reached"] = True
            raise GochangContractError(
                f"detail_limit cap allows {detail_limit} of {len(current)} details"
            )

        source_counts = {
            item.code: sum(row["catalogue"] == item.code for row in listed)
            for item in GOCHANG_CATALOGUES
        }
        current_counts = {
            item.code: sum(row["catalogue"] == item.code for row in current)
            for item in GOCHANG_CATALOGUES
        }
        meta.update(
            {
                "source_total": declared_total,
                "source_rows": len(listed),
                "unique_identity_count": len(set(identities)),
                "identity_duplicate_count": duplicate_count,
                "historical_invalid_count": len(historical_invalid),
                "catalogue_source_counts": source_counts,
                "catalogue_current_counts": current_counts,
                "catalogue_page_counts": {
                    item.code: first_pages[item.code].last
                    for item in GOCHANG_CATALOGUES
                },
                "page_sizes": page_sizes,
                "sentinel_modes": sentinel_modes,
                "sentinel_mode_counts": dict(Counter(sentinel_modes.values())),
                "source_status_counts": dict(
                    Counter(_clean(row["source_status"]) for row in listed)
                ),
                "current_candidate_count": len(current),
                "expired_count": len(listed) - len(current),
                "archived_rows_skipped_before_detail": len(listed) - len(current),
                "pagination_complete": True,
                "detail_attempts": len(current),
            }
        )

        detail_items = [
            (
                (_clean(row["catalogue"]), _clean(row["identity"])),
                gochang_detail_url(
                    _clean(row["catalogue"]),
                    row["identity"],
                    source_page=int(row["list_page"]),
                ),
            )
            for row in current
        ]
        details = _fetch_many(
            detail_items,
            timeout=timeout,
            max_workers=max_workers,
            session_factory=factory,
            fetcher=current_fetcher,
        )
        rows = [
            _parse_detail(
                row, details[(_clean(row["catalogue"]), _clean(row["identity"]))]
            )
            for row in current
        ]
        meta["detail_pages"] = len(rows)
        meta["current_source_count"] = len(rows)

        privacy_errors = [error for row in rows for error in _privacy_errors(row)]
        if privacy_errors:
            raise GochangContractError("; ".join(dict.fromkeys(privacy_errors)))
        rows.sort(key=lambda row: (row["start_date"], row["provider_course_id"]))
        deduper = dedupe_rows or _dedupe_default
        try:
            result = list(deduper(rows))
        except Exception as exc:
            raise GochangContractError(
                f"dedupe failed: {type(exc).__name__}: {_clean(exc)}"
            ) from exc
        if len(result) != len(rows):
            raise GochangContractError(
                f"dedupe changed official identity cardinality {len(rows)} to {len(result)}"
            )

        meta.update(
            {
                "pages": meta["list_requests"] + len(result),
                "returned_count": len(result),
                "detail_pages": len(result),
                "details_complete": True,
                "application_controls_complete": True,
                "application_control_count": sum(
                    bool(row["raw_fields"]["application_control_present"])
                    for row in result
                ),
                "branch_counts": dict(
                    sorted(Counter(row["branch"] for row in result).items())
                ),
                "status_counts": dict(Counter(row["status"] for row in result)),
                "snapshot_complete": True,
                "full_snapshot_validated": True,
                "no_current_data": not result,
                "no_current_reason": (
                    "all six official Gochang education catalogues have ended"
                    if not result
                    else ""
                ),
            }
        )
        return result, GOCHANG_PARSER, meta
    except Exception as exc:
        meta.update(
            {
                "detail_errors": max(
                    int(meta.get("detail_errors") or 0),
                    1 if int(meta.get("detail_attempts") or 0) else 0,
                ),
                "pagination_complete": False,
                "details_complete": False,
                "application_controls_complete": False,
                "snapshot_complete": False,
                "full_snapshot_validated": False,
                "returned_count": 0,
                "configured_collection_error": (
                    f"{type(exc).__name__}: {_clean(exc)}"
                ),
            }
        )
        return [], GOCHANG_PARSER, meta


collect = collect_gochang_education


__all__ = [
    "GOCHANG_CANDIDATE_ID",
    "GOCHANG_CANONICAL_URL",
    "GOCHANG_CATALOGUES",
    "GOCHANG_CATALOGUE_BY_CODE",
    "GOCHANG_DISCOVERY_AUDIT",
    "GOCHANG_HOST",
    "GOCHANG_MAX_WORKERS",
    "GOCHANG_MUNICIPALITY_CODE",
    "GOCHANG_MUNICIPALITY_NAME",
    "GOCHANG_PAGE_SIZE",
    "GOCHANG_PARSER",
    "GOCHANG_PATH",
    "GOCHANG_PII_FIELDS_NEVER_PERSISTED",
    "GOCHANG_PROVIDER",
    "GOCHANG_SPORTS_REVIEW_CANDIDATE_ID",
    "GOCHANG_SPORTS_REVIEW_PROVIDER",
    "GOCHANG_SPORTS_REVIEW_URL",
    "GochangCatalogue",
    "GochangContractError",
    "collect",
    "collect_gochang_education",
    "gochang_detail_url",
    "gochang_list_url",
    "is_gochang_education_target",
    "is_target",
]
