"""Fail-closed collector for Jecheon City's official lifelong courses.

The municipal-search candidate is a third-party news article.  The article
points to Jecheon City's real, structured catalogue at
``/okjcedu/class_list``.  The older welfare announcement target is also only
one recruitment notice and must not own course rows.

The catalogue does not expose education dates on its cards.  Consequently a
complete run reads every advertised page, the immediate empty page after the
last page, stable first/last boundary rechecks, and every course detail.  Only
courses whose education end date is current or future are returned.  Any
missing page/detail, identity drift, or application-control mismatch discards
the whole snapshot.

Instructor names, contact numbers, attachments, images, and free-form detail
copy are intentionally ignored.  Persisted rows are built from a small
allowlist of structured course fields.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


JECHEON_PROVIDER = "MUNI_WWW_JECHEON_GO_KR_A4E8D5CB"
JECHEON_CANONICAL_CANDIDATE_ID = "MUNI_IR_427A861B331C"
JECHEON_REVIEW_CANDIDATE_ID = "MUNI_IR_6AC02AE4EEE2"
JECHEON_MUNICIPALITY_CODE = "4315000000"
JECHEON_MUNICIPALITY_NAME = "충청북도 제천시"
JECHEON_BRANCH = "제천시평생학습관"
JECHEON_HOST = "www.jecheon.go.kr"
JECHEON_LIST_PATH = "/okjcedu/class_list"
JECHEON_BOARD_PATH = "/okjcedu/bbs/board.php"
JECHEON_CANONICAL_URL = f"https://{JECHEON_HOST}{JECHEON_LIST_PATH}"
JECHEON_PAGE_SIZE = 8
JECHEON_FETCH_ATTEMPTS = 2
JECHEON_MAX_WORKERS = 8
JECHEON_MAX_HTML_BYTES = 4_000_000
JECHEON_PARSER = (
    "jecheon_lifelong_complete_pages+empty_sentinel+stable_first_last+"
    "all_details+course_bound_handler_application_state+pii_allowlist"
)
JECHEON_OWNERSHIP_SCOPE = "jecheon_official_lifelong_course_catalogue"

JECHEON_ANNOUNCEMENT_PROVIDER = "MUNI_WWW_JECHEON_GO_KR_7E415824"
JECHEON_ANNOUNCEMENT_URL = (
    "https://www.jecheon.go.kr/bokjidadam/www/selectBbsNttView.do?"
    "key=43&bbsNo=5&nttNo=234"
)
JECHEON_LEGACY_URL = "https://okjcedu.jecheon.go.kr/"

JECHEON_CANDIDATE_AUDIT: Mapping[str, Mapping[str, str]] = {
    "MUNI_IR_DF2CDE484821": {
        "decision": "excluded_third_party_news_article",
        "provider": "MUNI_WWW_INEWS365_COM_A96539E0",
        "url": "https://www.inews365.com/news/article.html?no=849849",
        "owner": "",
    },
    JECHEON_REVIEW_CANDIDATE_ID: {
        "decision": "excluded_third_party_news_article",
        "provider": "MUNI_WWW_ELOVEJC_KR_A7D60B6C",
        "url": (
            "http:" "//www.elovejc.kr/bbs/board.php?"
            "bo_table=news&wr_id=16718"
        ),
        "owner": "",
    },
    "MUNI_IR_B80A9F0F48BA": {
        "decision": "excluded_single_official_announcement_evidence_only",
        "provider": JECHEON_ANNOUNCEMENT_PROVIDER,
        "url": JECHEON_ANNOUNCEMENT_URL,
        "owner": JECHEON_PROVIDER,
    },
    "MUNI_IR_2C268712AE7E": {
        "decision": "excluded_personal_blog",
        "provider": "MUNI_M_BLOG_NAVER_COM_BDF7F0F4",
        "url": "https://m.blog.naver.com/jskoo88/223764446188",
        "owner": "",
    },
}

JECHEON_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "canonical_url": JECHEON_CANONICAL_URL,
    "historical_rows": 65,
    "data_pages": 9,
    "immediate_empty_page": 10,
    "current_or_future_rows": 13,
    "all_details_verified": 65,
    "current_details_verified": 13,
    "status_counts": {"접수대기": 11, "접수마감": 2},
    "visible_current_application_controls": 0,
    "conclusion": "official_catalogue_supersedes_news_and_announcement_targets",
}

JECHEON_PII_FIELDS_DISCARDED = (
    "강사",
    "강사명",
    "문의",
    "문의전화",
    "전화번호",
    "이메일",
    "첨부파일",
    "이미지",
    "상세 정보",
    "프로그램 일정표",
    "source_html",
)


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class JecheonContractError(ValueError):
    """Raised when the official source no longer satisfies its contract."""


@dataclass(frozen=True)
class _ListPage:
    page: int
    rows: tuple[dict[str, Any], ...]
    category_counts: tuple[tuple[str, int], ...]
    last: int
    displayed_page: Optional[int]


_SPACE_RE = re.compile(r"\s+")
_TITLE_RE = re.compile(
    r"^평생학습관 강좌\s+(?P<page>\d+)\s+페이지\s*\|\s*제천시평생학습관$"
)
_CATEGORY_COUNT_RE = re.compile(r"^(?P<name>.+?)\s*\((?P<count>[\d,]+)\)$")
_IDENTITY_RE = re.compile(r"^[1-9]\d*$")
_DATE_RE = re.compile(
    r"(?P<year>20\d{2})년\s*(?P<month>\d{1,2})월\s*"
    r"(?P<day>\d{1,2})일(?:\([^)]*\))?"
    r"(?:\s*(?P<time>[0-2]\d:[0-5]\d))?"
)
_CAPACITY_RE = re.compile(
    r"^\(신청가능인원/최대인원\s*-\s*"
    r"(?P<remaining>\d{1,6})/(?P<total>\d{1,6})\)\s*"
    r"\(예비신청가능인원/예비최대인원\s*-\s*"
    r"(?P<wait_remaining>\d{1,6})/(?P<wait_total>\d{1,6})\)$"
)
_HANDLER_URL_RE = re.compile(
    r"function\s+check_reservation\s*\(\s*\)\s*\{.*?"
    r"location\.href\s*=\s*['\"](?P<url>[^'\"]+)['\"]\s*;?.*?\}",
    flags=re.DOTALL,
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2})[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_STATUS_MAP: Mapping[str, tuple[str, str]] = {
    "접수대기": ("SCHEDULED", "stay"),
    "접수중": ("OPEN", "ing"),
    "접수마감": ("CLOSED", "close"),
}
_DETAIL_FIELD_SET = frozenset(
    {"접수 기간", "교육 기간", "시간", "교육비", "정원", "장소", "강사", "문의"}
)
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "list_page",
        "source_program_group",
        "source_status",
        "source_application_period",
        "source_education_period",
        "source_schedule",
        "source_venue",
        "source_fee",
        "capacity_remaining",
        "capacity_total",
        "waitlist_remaining",
        "waitlist_total",
        "detail_verified",
        "application_control_present",
        "application_actionable",
        "application_control_contract",
        "application_handler_verified",
        "target_evidence",
        "service_family",
    }
)
_FORBIDDEN_PERSISTED_KEYS = frozenset(
    {
        "instructor",
        "instructor_name",
        "teacher",
        "contact",
        "contact_name",
        "phone",
        "email",
        "attachments",
        "attachment_urls",
        "detail_description",
        "source_html",
        "raw_html",
    }
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", _clean(value)).lower()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _safe_parsed_port(parsed: Any) -> Optional[int]:
    try:
        return parsed.port
    except ValueError:
        return -1


def is_jecheon_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != JECHEON_PROVIDER:
        return False
    parsed = urlparse(_clean(_target_value(target, "url")))
    return bool(
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == JECHEON_HOST
        and _safe_parsed_port(parsed) is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == JECHEON_LIST_PATH
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


is_target = is_jecheon_education_target


def is_jecheon_superseded_announcement_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")) == JECHEON_ANNOUNCEMENT_PROVIDER
        and _clean(_target_value(target, "url")) == JECHEON_ANNOUNCEMENT_URL
    )


def is_jecheon_excluded_candidate(target: Any) -> bool:
    candidate = _clean(_target_value(target, "candidate_id"))
    audit = JECHEON_CANDIDATE_AUDIT.get(candidate)
    return bool(audit and _clean(audit.get("decision")).startswith("excluded_"))


def jecheon_list_url(page: int) -> str:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    if page == 1:
        return JECHEON_CANONICAL_URL
    return (
        f"https://{JECHEON_HOST}{JECHEON_BOARD_PATH}?"
        + urlencode((("bo_table", "class_list"), ("page", str(page))))
    )


def jecheon_detail_url(identity: str) -> str:
    value = _clean(identity)
    if _IDENTITY_RE.fullmatch(value) is None:
        raise ValueError("course identity must be a positive integer")
    return (
        f"https://{JECHEON_HOST}{JECHEON_BOARD_PATH}?"
        + urlencode(
            (
                ("bo_table", "class_list"),
                ("mode", "view"),
                ("rm_ix", value),
            )
        )
    )


def jecheon_application_url(identity: str) -> str:
    value = _clean(identity)
    if _IDENTITY_RE.fullmatch(value) is None:
        raise ValueError("course identity must be a positive integer")
    return (
        f"https://{JECHEON_HOST}{JECHEON_BOARD_PATH}?"
        + urlencode(
            (
                ("bo_table", "class_list"),
                ("mode", "write"),
                ("rm_ix", value),
            )
        )
    )


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is not None:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            "Referer": JECHEON_CANONICAL_URL,
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    response = session.get(url, timeout=timeout, allow_redirects=False)
    status = int(getattr(response, "status_code", 0))
    if status != 200:
        raise JecheonContractError(f"unexpected HTTP status {status}")
    if getattr(response, "headers", {}).get("Location"):
        raise JecheonContractError("redirect response is not accepted")
    content = getattr(response, "content", b"")
    if not content:
        raise JecheonContractError("empty HTTP response")
    if len(content) > JECHEON_MAX_HTML_BYTES:
        raise JecheonContractError("HTTP response exceeded the HTML byte cap")
    return response


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, bytes):
        if len(value) > JECHEON_MAX_HTML_BYTES:
            raise JecheonContractError("HTML fixture exceeded the byte cap")
        return BeautifulSoup(value, "lxml")
    if isinstance(value, str):
        if len(value.encode("utf-8")) > JECHEON_MAX_HTML_BYTES:
            raise JecheonContractError("HTML fixture exceeded the byte cap")
        return BeautifulSoup(value, "lxml")
    content = getattr(value, "content", None)
    if content is None:
        raise TypeError("fetcher returned neither HTML nor a response")
    if len(content) > JECHEON_MAX_HTML_BYTES:
        raise JecheonContractError("HTTP response exceeded the HTML byte cap")
    return BeautifulSoup(content, "lxml")


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _fetch_soup(
    url: str,
    *,
    timeout: int,
    fetcher: Fetcher,
    session_factory: SessionFactory,
) -> BeautifulSoup:
    last_error: Optional[Exception] = None
    for _attempt in range(JECHEON_FETCH_ATTEMPTS):
        session: Any = None
        try:
            session = session_factory()
            return _coerce_soup(fetcher(session, url, timeout))
        except Exception as exc:
            last_error = exc
        finally:
            _close_quietly(session)
    assert last_error is not None
    raise last_error


def _parse_categories(root: Any) -> tuple[tuple[str, int], ...]:
    anchors = root.select(".tabType_1 > ul > li > a[href]")
    if len(anchors) < 2 or _clean(anchors[0].get_text(" ", strip=True)) != "전체":
        raise JecheonContractError("course category tabs changed")
    categories: list[tuple[str, int]] = []
    seen_names: set[str] = set()
    seen_codes: set[str] = set()
    for anchor in anchors[1:]:
        label = _clean(anchor.get_text(" ", strip=True))
        match = _CATEGORY_COUNT_RE.fullmatch(label)
        if match is None:
            raise JecheonContractError("course category count label changed")
        name = _clean(match.group("name"))
        count = int(match.group("count").replace(",", ""))
        parsed = urlparse(urljoin(JECHEON_CANONICAL_URL, _clean(anchor.get("href"))))
        query = parse_qs(parsed.query, keep_blank_values=True)
        codes = query.get("sca", [])
        if (
            not name
            or count < 1
            or name in seen_names
            or parsed.scheme != "https"
            or parsed.hostname != JECHEON_HOST
            or parsed.path != JECHEON_LIST_PATH
            or set(query) != {"sca"}
            or len(codes) != 1
            or not codes[0].isdigit()
            or codes[0] in seen_codes
            or parsed.fragment
        ):
            raise JecheonContractError("course category ownership/count changed")
        seen_names.add(name)
        seen_codes.add(codes[0])
        categories.append((name, count))
    return tuple(categories)


def _detail_identity_from_href(value: Any) -> str:
    parsed = urlparse(urljoin(JECHEON_CANONICAL_URL, _clean(value)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    bo_tables = query.get("bo_table", [])
    modes = query.get("mode", [])
    identities = query.get("rm_ix", [])
    allowed_keys = {"bo_table", "mode", "rm_ix"}
    if "page" in query:
        allowed_keys.add("page")
    page_values = query.get("page", [])
    if (
        parsed.scheme != "https"
        or parsed.hostname != JECHEON_HOST
        or _safe_parsed_port(parsed) is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != JECHEON_BOARD_PATH
        or set(query) != allowed_keys
        or not bo_tables
        or set(bo_tables) != {"class_list"}
        or modes != ["view"]
        or len(identities) != 1
        or _IDENTITY_RE.fullmatch(identities[0]) is None
        or (
            page_values
            and (
                len(page_values) != 1
                or not page_values[0].isdigit()
                or int(page_values[0]) < 1
            )
        )
        or parsed.fragment
    ):
        raise JecheonContractError("course detail link changed")
    return identities[0]


def _single_text(root: Any, selector: str, label: str) -> str:
    nodes = root.select(selector)
    if len(nodes) != 1:
        raise JecheonContractError(f"{label} missing or duplicated")
    value = _clean(nodes[0].get_text(" ", strip=True))
    if not value:
        raise JecheonContractError(f"{label} is empty")
    return value


def _parse_card(card: Any, page: int, categories: set[str]) -> dict[str, Any]:
    detail_anchors = card.select('a[href*="rm_ix="]')
    identities = {_detail_identity_from_href(node.get("href")) for node in detail_anchors}
    if len(identities) != 1:
        raise JecheonContractError(f"page {page}: course identity link changed")
    identity = next(iter(identities))
    title = _single_text(card, ".con_wrap > .title", f"course {identity} title")
    category = _single_text(card, ".con_wrap .cate", f"course {identity} category")
    source_status = _single_text(card, ".img_wrap > .status", f"course {identity} status")
    # The plug-in exposes only its ten newest programme-group tabs while the
    # unfiltered archive continues beyond them.  Older, valid cards therefore
    # need not be represented in the visible tab subset.
    if not category:
        raise JecheonContractError(f"course {identity}: empty programme group")
    if source_status not in _STATUS_MAP:
        raise JecheonContractError(f"course {identity}: unknown status")
    normalized_status, expected_class = _STATUS_MAP[source_status]
    card_classes = {_clean(value) for value in card.get("class", [])}
    status_classes = {value[1] for value in _STATUS_MAP.values()}
    if card_classes & status_classes != {expected_class}:
        raise JecheonContractError(f"course {identity}: status class mismatch")
    return {
        "identity": identity,
        "title": title,
        "source_program_group": category,
        "source_status": source_status,
        "status": normalized_status,
        "list_page": page,
        "detail_url": jecheon_detail_url(identity),
    }


def _parse_list(soup: BeautifulSoup, page: int) -> _ListPage:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    title_match = _TITLE_RE.fullmatch(title)
    if title_match is None or int(title_match.group("page")) != page:
        raise JecheonContractError(f"page {page}: official catalogue title changed")
    roots = soup.select("#class_wrap.list")
    if len(roots) != 1:
        raise JecheonContractError(f"page {page}: catalogue root changed")
    root = roots[0]
    lists = root.select("ul#class_list")
    if len(lists) != 1:
        raise JecheonContractError(f"page {page}: course list root changed")
    categories = _parse_categories(root)
    # The booking plug-in closes ``#class_wrap`` before emitting its pager;
    # the pager is still inside the same municipal content container but is a
    # sibling, not a descendant, of the catalogue root.
    navs = soup.select("nav.pg_wrap")
    if len(navs) != 1:
        raise JecheonContractError(f"page {page}: pagination root changed")
    current = navs[0].select(".pg_current")
    if len(current) > 1:
        raise JecheonContractError(f"page {page}: current-page marker duplicated")
    displayed_page: Optional[int] = None
    if current:
        digits = re.findall(r"\d+", _clean(current[0].get_text(" ", strip=True)))
        if len(digits) != 1:
            raise JecheonContractError(f"page {page}: current-page marker changed")
        displayed_page = int(digits[0])
    linked_pages: set[int] = set()
    for anchor in navs[0].select("a[href]"):
        parsed = urlparse(urljoin(JECHEON_CANONICAL_URL, _clean(anchor.get("href"))))
        query = parse_qs(parsed.query, keep_blank_values=True)
        values = query.get("page", [])
        if (
            parsed.scheme != "https"
            or parsed.hostname != JECHEON_HOST
            or parsed.path != JECHEON_BOARD_PATH
            or len(values) != 1
            or not values[0].isdigit()
            or int(values[0]) < 1
        ):
            raise JecheonContractError(f"page {page}: pagination link changed")
        linked_pages.add(int(values[0]))
    last = max({displayed_page or 0, *linked_pages})
    if last < 1:
        raise JecheonContractError(f"page {page}: last-page boundary missing")
    cards = lists[0].find_all("li", recursive=False)
    category_names = {name for name, _count in categories}
    rows = tuple(_parse_card(card, page, category_names) for card in cards)
    return _ListPage(
        page=page,
        rows=rows,
        category_counts=categories,
        last=last,
        displayed_page=displayed_page,
    )


def _date_pair(value: str, label: str, *, require_time: bool) -> tuple[date, date]:
    matches = list(_DATE_RE.finditer(_clean(value)))
    if len(matches) != 2:
        raise JecheonContractError(f"{label} date pair changed")
    if require_time and any(not match.group("time") for match in matches):
        raise JecheonContractError(f"{label} time pair changed")
    values = [
        date(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )
        for match in matches
    ]
    if values[0] > values[1]:
        raise JecheonContractError(f"{label} is reversed")
    return values[0], values[1]


def _detail_fields(root: Any, identity: str) -> dict[str, str]:
    items = root.select("ul.class_info > li")
    fields: dict[str, str] = {}
    for item in items:
        labels = item.select(":scope > b")
        values = item.select(":scope > p")
        if len(labels) != 1 or len(values) != 1:
            raise JecheonContractError(f"course {identity}: detail field structure changed")
        label = _clean(labels[0].get_text(" ", strip=True))
        value = _clean(values[0].get_text(" ", strip=True))
        if not label or label in fields:
            raise JecheonContractError(f"course {identity}: detail field duplicated")
        fields[label] = value
    if set(fields) != _DETAIL_FIELD_SET:
        raise JecheonContractError(f"course {identity}: detail field set changed")
    if any(not fields[key] for key in _DETAIL_FIELD_SET):
        raise JecheonContractError(f"course {identity}: detail field is empty")
    return fields


def _application_identity_from_url(value: str) -> str:
    parsed = urlparse(urljoin(JECHEON_CANONICAL_URL, _clean(value)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme != "https"
        or parsed.hostname != JECHEON_HOST
        or _safe_parsed_port(parsed) is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != JECHEON_BOARD_PATH
        or set(query) != {"bo_table", "mode", "rm_ix"}
        or query.get("bo_table") != ["class_list"]
        or query.get("mode") != ["write"]
        or len(query.get("rm_ix", [])) != 1
        or _IDENTITY_RE.fullmatch(query["rm_ix"][0]) is None
        or parsed.fragment
    ):
        raise JecheonContractError("course application handler URL changed")
    return query["rm_ix"][0]


def _application_contract(
    soup: BeautifulSoup,
    root: Any,
    identity: str,
    normalized_status: str,
) -> tuple[str, bool, str, bool]:
    scripts = "\n".join(node.get_text("\n") for node in soup.select("script:not([src])"))
    handler_urls = [match.group("url") for match in _HANDLER_URL_RE.finditer(scripts)]
    if len(handler_urls) != 1:
        raise JecheonContractError(f"course {identity}: application handler changed")
    handler_identity = _application_identity_from_url(handler_urls[0])
    if handler_identity != identity:
        raise JecheonContractError(f"course {identity}: application handler identity mismatch")
    application_url = jecheon_application_url(identity)

    controls: list[Any] = []
    for node in soup.select(".btn_confirm.write_div a, .btn_confirm.write_div button, .btn_confirm.write_div input"):
        classes = {_clean(value) for value in node.get("class", [])}
        if "btn_prev" in classes:
            continue
        text = _clean(node.get("value") or node.get_text(" ", strip=True))
        onclick = _clean(node.get("onclick"))
        href = _clean(node.get("href"))
        if (
            "check_reservation" in onclick
            or "신청" in text
            or "접수" in text
            or "mode=write" in href
        ):
            controls.append(node)
    if normalized_status == "OPEN":
        if len(controls) > 1:
            raise JecheonContractError(
                f"course {identity}: open course has multiple public application controls"
            )
        if controls:
            control = controls[0]
            onclick = _clean(control.get("onclick"))
            href = _clean(control.get("href"))
            if "check_reservation" not in onclick:
                if not href or _application_identity_from_url(href) != identity:
                    raise JecheonContractError(
                        f"course {identity}: application control is not course-bound"
                    )
            return (
                application_url,
                True,
                "visible_control_with_course_bound_handler",
                True,
            )
        return (
            application_url,
            True,
            "course_bound_handler_without_visible_control",
            False,
        )
    if controls:
        raise JecheonContractError(
            f"course {identity}: inactive course exposes an application control"
        )
    return "", False, "inactive_control_absent_with_course_bound_handler", False


def _fee_amount(value: str, identity: str) -> int:
    cleaned = _clean(value).replace(",", "")
    if cleaned in {"무료", "0원", "0 원"}:
        return 0
    numbers = re.findall(r"\d+", cleaned)
    if len(numbers) != 1:
        raise JecheonContractError(f"course {identity}: education fee changed")
    return int(numbers[0])


def _branch_code(value: str) -> str:
    return "jecheon:" + hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]


def _parse_detail(
    listed: Mapping[str, Any], soup: BeautifulSoup, cutoff: date
) -> dict[str, Any]:
    identity = _clean(listed.get("identity"))
    roots = soup.select("#class_view")
    if len(roots) != 1:
        raise JecheonContractError(f"course {identity}: detail root changed")
    root = roots[0]
    title_nodes = root.select(".txt_subject > p")
    if not title_nodes:
        raise JecheonContractError(f"course {identity}: detail title changed")
    title = _clean(title_nodes[0].get_text(" ", strip=True))
    if title != _clean(listed.get("title")):
        raise JecheonContractError(f"course {identity}: list/detail title mismatch")
    source_status = _single_text(root, ".img_wrap > .status", f"course {identity} detail status")
    if source_status != _clean(listed.get("source_status")):
        raise JecheonContractError(f"course {identity}: list/detail status mismatch")
    normalized_status, expected_class = _STATUS_MAP[source_status]
    root_classes = {_clean(value) for value in root.get("class", [])}
    status_classes = {value[1] for value in _STATUS_MAP.values()}
    if root_classes & status_classes != {expected_class}:
        raise JecheonContractError(f"course {identity}: detail status class mismatch")

    fields = _detail_fields(root, identity)
    apply_start, apply_end = _date_pair(
        fields["접수 기간"], f"course {identity} application period", require_time=True
    )
    start, end = _date_pair(
        fields["교육 기간"], f"course {identity} education period", require_time=False
    )
    if normalized_status == "OPEN" and not (apply_start <= cutoff <= apply_end):
        raise JecheonContractError(f"course {identity}: open status/date mismatch")
    if normalized_status == "SCHEDULED" and cutoff > apply_start:
        raise JecheonContractError(f"course {identity}: scheduled status/date mismatch")

    capacity_match = _CAPACITY_RE.fullmatch(fields["정원"])
    if capacity_match is None:
        raise JecheonContractError(f"course {identity}: capacity field changed")
    remaining = int(capacity_match.group("remaining"))
    total = int(capacity_match.group("total"))
    wait_remaining = int(capacity_match.group("wait_remaining"))
    wait_total = int(capacity_match.group("wait_total"))
    if total < 1 or remaining > total or wait_remaining > wait_total:
        raise JecheonContractError(f"course {identity}: capacity values are invalid")

    (
        application_url,
        application_actionable,
        application_contract,
        visible_application_control,
    ) = _application_contract(
        soup, root, identity, normalized_status
    )
    fee_amount = _fee_amount(fields["교육비"], identity)
    venue = _clean(fields["장소"])
    schedule = _clean(fields["시간"])
    row: dict[str, Any] = {
        "provider": JECHEON_PROVIDER,
        "provider_course_id": f"{JECHEON_PROVIDER}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": title,
        "branch": JECHEON_BRANCH,
        "branch_code": _branch_code(JECHEON_BRANCH),
        "preserve_branch": True,
        "category": "평생학습",
        "program_type": "교육",
        "raw_url": jecheon_detail_url(identity),
        "application_url": application_url,
        "application_type": (
            "ONLINE_RESERVATION_LOGIN_REQUIRED"
            if application_actionable
            else "INFO_ONLY"
        ),
        "application_method": "온라인",
        "application_methods": ["온라인"],
        "reservation_available": application_actionable,
        "status": normalized_status,
        "fee": fields["교육비"],
        "fee_amount": fee_amount,
        "period": f"{start.isoformat()} ~ {end.isoformat()}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": fields["접수 기간"],
        "apply_start": apply_start.isoformat(),
        "apply_end": apply_end.isoformat(),
        "schedule_raw": schedule,
        "capacity": f"{total}명",
        "capacity_current": total - remaining,
        "capacity_total": total,
        "target": "대상 별도 안내",
        "venue": venue,
        "venue_name": venue,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": JECHEON_PARSER,
        "municipality_code": JECHEON_MUNICIPALITY_CODE,
        "municipality_full_name": JECHEON_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "list_page": int(listed["list_page"]),
            "source_program_group": _clean(listed["source_program_group"]),
            "source_status": source_status,
            "source_application_period": fields["접수 기간"],
            "source_education_period": fields["교육 기간"],
            "source_schedule": schedule,
            "source_venue": venue,
            "source_fee": fields["교육비"],
            "capacity_remaining": remaining,
            "capacity_total": total,
            "waitlist_remaining": wait_remaining,
            "waitlist_total": wait_total,
            "detail_verified": True,
            "application_control_present": visible_application_control,
            "application_actionable": application_actionable,
            "application_control_contract": application_contract,
            "application_handler_verified": True,
            "target_evidence": "official_detail_omits_target_field",
            "service_family": "education",
        },
        "_source_end_date": end,
    }
    return row


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (
            _clean(row.get("identity")),
            _clean(row.get("title")),
            _clean(row.get("source_program_group")),
            _clean(row.get("source_status")),
        )
        for row in rows
    )


def _detail_signature(row: Mapping[str, Any]) -> tuple[str, ...]:
    raw = row.get("raw_fields", {})
    return (
        _clean(raw.get("identity")),
        _clean(row.get("title")),
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
        _clean(row.get("apply_start")),
        _clean(row.get("apply_end")),
        _clean(row.get("status")),
    )


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_PERSISTED_KEYS:
        errors.append("forbidden PII/detail keys persisted")
    raw_fields = row.get("raw_fields")
    if not isinstance(raw_fields, Mapping) or not set(raw_fields) <= _SAFE_RAW_FIELDS:
        errors.append("raw_fields exceeded the PII-safe allowlist")
    payload = repr(
        {
            key: value
            for key, value in row.items()
            if key not in {"raw_url", "application_url", "_source_end_date"}
        }
    )
    if _PHONE_RE.search(payload) or _EMAIL_RE.search(payload):
        errors.append("PII-like contact data persisted")
    if row.get("description") != row.get("title"):
        errors.append("free-form detail description persisted")
    if _clean((raw_fields or {}).get("service_family")) != "education":
        errors.append("non-education row reached the result")
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


def _base_meta(error: str = "") -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "data_pages": 0,
        "sentinel_requests": 0,
        "stability_rechecks": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "source_rows": 0,
        "current_source_count": 0,
        "expired_count": 0,
        "returned_count": 0,
        "identity_duplicate_count": 0,
        "raw_url_duplicate_count": 0,
        "source_cap_reached": False,
        "pagination_complete": False,
        "details_complete": False,
        "application_controls_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": error,
        "municipality_code": JECHEON_MUNICIPALITY_CODE,
        "municipality_name": JECHEON_MUNICIPALITY_NAME,
        "canonical_candidate_id": JECHEON_CANONICAL_CANDIDATE_ID,
        "review_candidate_id": JECHEON_REVIEW_CANDIDATE_ID,
        "canonical_url": JECHEON_CANONICAL_URL,
        "ownership_scope": JECHEON_OWNERSHIP_SCOPE,
    }


def collect_jecheon_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 100,
    detail_limit: int = 500,
    today: Optional[date | datetime | str] = None,
    max_workers: int = JECHEON_MAX_WORKERS,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future Jecheon education snapshot."""

    meta = _base_meta()
    if not is_jecheon_education_target(target):
        meta["configured_collection_error"] = "target does not match canonical Jecheon owner"
        return [], JECHEON_PARSER, meta
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
    ):
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": "invalid collection limits",
            }
        )
        return [], JECHEON_PARSER, meta
    try:
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], JECHEON_PARSER, meta

    current_fetcher = fetcher or _default_fetcher
    current_factory = session_factory or _default_session_factory
    workers = min(max_workers, JECHEON_MAX_WORKERS)
    errors: list[str] = []

    try:
        first_soup = _fetch_soup(
            jecheon_list_url(1),
            timeout=timeout,
            fetcher=current_fetcher,
            session_factory=current_factory,
        )
        first = _parse_list(first_soup, 1)
        meta["list_requests"] = 1
        meta["pages"] = 1
    except Exception as exc:
        meta["configured_collection_error"] = f"page 1: {type(exc).__name__}: {_clean(exc)}"
        return [], JECHEON_PARSER, meta

    last = first.last
    required_list_requests = last + 3
    meta.update(
        {
            "declared_pages": last,
            "required_list_requests": required_list_requests,
        }
    )
    if required_list_requests > max_pages:
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": (
                    f"max_pages cap allows {max_pages} of "
                    f"{required_list_requests} required list requests"
                ),
            }
        )
        return [], JECHEON_PARSER, meta
    jobs: list[tuple[tuple[str, int], int]] = [
        (("data", page), page) for page in range(2, last + 1)
    ]
    jobs.extend(
        [
            (("sentinel", last + 1), last + 1),
            (("first_recheck", 1), 1),
            (("last_recheck", last), last),
        ]
    )
    parsed_jobs: dict[tuple[str, int], _ListPage] = {}

    def fetch_list_job(job: tuple[tuple[str, int], int]) -> tuple[tuple[str, int], _ListPage]:
        key, page = job
        soup = _fetch_soup(
            jecheon_list_url(page),
            timeout=timeout,
            fetcher=current_fetcher,
            session_factory=current_factory,
        )
        return key, _parse_list(soup, page)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_list_job, job): job for job in jobs}
        for future in as_completed(futures):
            key, page = futures[future]
            try:
                parsed_key, parsed = future.result()
                parsed_jobs[parsed_key] = parsed
                meta["list_requests"] += 1
                meta["pages"] += 1
            except Exception as exc:
                errors.append(f"{key} page {page}: {type(exc).__name__}: {_clean(exc)}")

    page_rows: dict[int, tuple[dict[str, Any], ...]] = {1: first.rows}
    page_counts: dict[int, int] = {}
    expected_categories = first.category_counts
    for page in range(1, last + 1):
        parsed = first if page == 1 else parsed_jobs.get(("data", page))
        if parsed is None:
            errors.append(f"data page {page}: response missing")
            continue
        if parsed.last != last or parsed.category_counts != expected_categories:
            errors.append(f"data page {page}: advertised catalogue boundary changed")
        if page < last and len(parsed.rows) != JECHEON_PAGE_SIZE:
            errors.append(
                f"data page {page}: row count {len(parsed.rows)} != {JECHEON_PAGE_SIZE}"
            )
        if page == last and not (1 <= len(parsed.rows) <= JECHEON_PAGE_SIZE):
            errors.append(f"data page {page}: invalid final row count {len(parsed.rows)}")
        if parsed.displayed_page != page:
            errors.append(f"data page {page}: current-page marker changed")
        page_rows[page] = parsed.rows
        page_counts[page] = len(parsed.rows)

    sentinel = parsed_jobs.get(("sentinel", last + 1))
    if sentinel is None:
        errors.append("immediate post-last sentinel response missing")
    elif (
        sentinel.last != last
        or sentinel.category_counts != expected_categories
        or sentinel.rows
        or sentinel.displayed_page is not None
    ):
        errors.append("immediate post-last sentinel page is not a stable empty page")
    else:
        meta["sentinel_requests"] = 1

    first_recheck = parsed_jobs.get(("first_recheck", 1))
    last_recheck = parsed_jobs.get(("last_recheck", last))
    if first_recheck is None or last_recheck is None:
        errors.append("first/last stability recheck response missing")
    else:
        meta["stability_rechecks"] = 2
        if (
            first_recheck.last != last
            or first_recheck.category_counts != expected_categories
            or _page_signature(first_recheck.rows) != _page_signature(first.rows)
        ):
            errors.append("first-page stability recheck changed")
        expected_last = page_rows.get(last, ())
        if (
            last_recheck.last != last
            or last_recheck.category_counts != expected_categories
            or _page_signature(last_recheck.rows) != _page_signature(expected_last)
        ):
            errors.append("last-page stability recheck changed")

    listed = [row for page in range(1, last + 1) for row in page_rows.get(page, ())]
    total = len(listed)
    identities = [_clean(row.get("identity")) for row in listed]
    identity_duplicate_count = len(identities) - len(set(identities))
    if identity_duplicate_count:
        errors.append(f"{identity_duplicate_count} duplicate official identities")
    category_actual = Counter(_clean(row.get("source_program_group")) for row in listed)
    if any(category_actual.get(name, 0) != count for name, count in expected_categories):
        errors.append("advertised visible-category counts do not match parsed course rows")

    list_complete = bool(
        not errors
        and meta["list_requests"] == required_list_requests
        and meta["sentinel_requests"] == 1
        and meta["stability_rechecks"] == 2
    )
    meta.update(
        {
            "data_pages": len(page_counts),
            "page_counts": page_counts,
            "source_total": total,
            "source_rows": len(listed),
            "identity_duplicate_count": identity_duplicate_count,
            "pagination_complete": list_complete,
        }
    )
    if not list_complete:
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], JECHEON_PARSER, meta

    if total > detail_limit:
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": (
                    f"detail_limit cap allows {detail_limit} of "
                    f"{total} required details"
                ),
            }
        )
        return [], JECHEON_PARSER, meta

    meta["detail_attempts"] = len(listed)
    detailed: dict[str, dict[str, Any]] = {}
    detail_errors: list[str] = []

    def fetch_detail(row: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        identity = _clean(row.get("identity"))
        soup = _fetch_soup(
            _clean(row.get("detail_url")),
            timeout=timeout,
            fetcher=current_fetcher,
            session_factory=current_factory,
        )
        return identity, _parse_detail(row, soup, cutoff)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_detail, row): row for row in listed}
        for future in as_completed(futures):
            row = futures[future]
            identity = _clean(row.get("identity"))
            try:
                parsed_identity, parsed = future.result()
                if parsed_identity in detailed:
                    raise JecheonContractError("duplicate parsed detail identity")
                detailed[parsed_identity] = parsed
                meta["detail_pages"] += 1
                meta["pages"] += 1
            except Exception as exc:
                detail_errors.append(
                    f"detail {identity}: {type(exc).__name__}: {_clean(exc)}"
                )
    errors.extend(detail_errors)
    meta["detail_errors"] = len(detail_errors)
    details_complete = bool(
        not detail_errors
        and meta["detail_attempts"] == total
        and meta["detail_pages"] == total
        and len(detailed) == total
    )
    ordered_details = [detailed[identity] for identity in identities if identity in detailed]
    current_rows = [
        row for row in ordered_details if row["_source_end_date"] >= cutoff
    ]
    expired_count = len(ordered_details) - len(current_rows)
    raw_urls = [_clean(row.get("raw_url")) for row in current_rows]
    raw_url_duplicate_count = len(raw_urls) - len(set(raw_urls))
    if raw_url_duplicate_count:
        errors.append(f"{raw_url_duplicate_count} duplicate current detail URLs")

    application_controls_complete = bool(
        details_complete
        and all(
            bool(row.get("raw_fields", {}).get("application_handler_verified"))
            for row in ordered_details
        )
    )
    result: list[dict[str, Any]] = []
    if details_complete and application_controls_complete and not errors:
        for row in current_rows:
            errors.extend(_privacy_errors(row))
        if not errors:
            persistable = []
            for row in current_rows:
                clean_row = dict(row)
                clean_row.pop("_source_end_date", None)
                persistable.append(clean_row)
            deduper = dedupe_rows or _dedupe_default
            try:
                result = list(deduper(persistable))
            except Exception as exc:
                errors.append(f"dedupe failed: {type(exc).__name__}: {_clean(exc)}")
                result = []
            if len(result) != len(persistable):
                errors.append(
                    "dedupe changed official identity cardinality "
                    f"{len(persistable)} to {len(result)}"
                )
                result = []

    snapshot_complete = bool(
        list_complete
        and details_complete
        and application_controls_complete
        and not errors
    )
    if not snapshot_complete:
        result = []

    semantic_counter = Counter(
        (_normalized(row.get("title")), _clean(row.get("period")))
        for row in ordered_details
    )
    meta.update(
        {
            "current_source_count": len(current_rows),
            "expired_count": expired_count,
            "raw_url_duplicate_count": raw_url_duplicate_count,
            "semantic_duplicate_group_count": sum(
                count > 1 for count in semantic_counter.values()
            ),
            "semantic_duplicate_excess_rows": sum(
                max(0, count - 1) for count in semantic_counter.values()
            ),
            "semantic_duplicate_policy": "preserve_distinct_official_rm_ix",
            "branch_counts": dict(Counter(_clean(row.get("branch")) for row in result)),
            "status_counts": dict(Counter(_clean(row.get("status")) for row in result)),
            "application_control_count": sum(
                bool(row.get("raw_fields", {}).get("application_actionable"))
                for row in current_rows
            ),
            "details_complete": details_complete,
            "application_controls_complete": application_controls_complete,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": snapshot_complete,
            "returned_count": len(result),
            "no_current_data": bool(snapshot_complete and not current_rows),
            "no_current_reason": (
                "the complete official catalogue has no current/future courses"
                if snapshot_complete and not current_rows
                else ""
            ),
            "municipality_coverage": [JECHEON_MUNICIPALITY_CODE],
            "candidate_audit": {
                key: dict(value) for key, value in JECHEON_CANDIDATE_AUDIT.items()
            },
            "discovery_audit": dict(JECHEON_DISCOVERY_AUDIT),
            "superseded_providers": [JECHEON_ANNOUNCEMENT_PROVIDER],
            "pii_fields_discarded": list(JECHEON_PII_FIELDS_DISCARDED),
            "pii_payload_persisted": False,
            "network_concurrency": workers,
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
    )
    return result, JECHEON_PARSER, meta


collect = collect_jecheon_education


__all__ = [
    "JECHEON_ANNOUNCEMENT_PROVIDER",
    "JECHEON_ANNOUNCEMENT_URL",
    "JECHEON_BRANCH",
    "JECHEON_CANONICAL_CANDIDATE_ID",
    "JECHEON_CANONICAL_URL",
    "JECHEON_CANDIDATE_AUDIT",
    "JECHEON_DISCOVERY_AUDIT",
    "JECHEON_LEGACY_URL",
    "JECHEON_MUNICIPALITY_CODE",
    "JECHEON_MUNICIPALITY_NAME",
    "JECHEON_PAGE_SIZE",
    "JECHEON_PARSER",
    "JECHEON_PII_FIELDS_DISCARDED",
    "JECHEON_PROVIDER",
    "JECHEON_REVIEW_CANDIDATE_ID",
    "JecheonContractError",
    "collect",
    "collect_jecheon_education",
    "is_jecheon_education_target",
    "is_jecheon_excluded_candidate",
    "is_jecheon_superseded_announcement_target",
    "is_target",
    "jecheon_application_url",
    "jecheon_detail_url",
    "jecheon_list_url",
]
