"""Fail-closed collector for Geumsan County's official education catalogue.

The public Geumsan lifelong-learning portal is the countywide authoritative
ledger.  It mixes Daragwon, libraries, youth facilities, resident centres,
and other county education operators in one paginated catalogue.  The
Daragwon page on the main county site is only an overlapping subset.

Applicant forms are deliberately never requested.  A public detail page is
used only to verify the course identity, structured public fields, and the
identity bound application control.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


GEUMSAN_PROVIDER = "MUNI_WWW_GEUMSAN_GO_KR_3E799FCC"
GEUMSAN_CANONICAL_CANDIDATE_ID = "MUNI_IR_9B585A6399AE"
GEUMSAN_MUNICIPALITY_CODE = "4471000000"
GEUMSAN_MUNICIPALITY_NAME = "충청남도 금산군"
GEUMSAN_HOST = "www.geumsan.go.kr"
GEUMSAN_LIST_PATH = "/lifelongedu/html/sub01/0102.html"
GEUMSAN_SITE_ALIAS_PATH = "/site/lifelongedu/html/sub01/0102.html"
GEUMSAN_CANONICAL_URL = f"https://{GEUMSAN_HOST}{GEUMSAN_LIST_PATH}"
GEUMSAN_PAGE_SIZE = 10
GEUMSAN_MAX_HTML_BYTES = 3_000_000
GEUMSAN_MAX_WORKERS = 10
GEUMSAN_PARSER = (
    "geumsan_lifelong_official_complete_catalogue+declared_last_page+"
    "post_last_clamp+stable_first_last+education_state_partition+"
    "current_details+scheduled_acceptance+identity_bound_application_controls+"
    "operator_and_venue_branches+pii_allowlist"
)

GEUMSAN_REJECTED_JOB_URL = "https://www.geumsan.go.kr/kr/html/sub05/05040703.html"
GEUMSAN_REJECTED_JOB_PROVIDER = "MUNI_WWW_GEUMSAN_GO_KR_E9DFD479"
GEUMSAN_REJECTED_JOB_CANDIDATE_ID = "MUNI_IR_920435073172"
GEUMSAN_SITE_ALIAS_URL = f"https://{GEUMSAN_HOST}{GEUMSAN_SITE_ALIAS_PATH}"
GEUMSAN_SITE_ALIAS_PROVIDER = "MUNI_WWW_GEUMSAN_GO_KR_B6EA7A59"
GEUMSAN_SITE_ALIAS_CANDIDATE_ID = "MUNI_IR_9D20FCFB369A"
GEUMSAN_DARAGWON_SUBSET_URL = "https://www.geumsan.go.kr/site/kr/html/sub05/05060101.html"
GEUMSAN_DARAGWON_SUBSET_PROVIDER = "MUNI_WWW_GEUMSAN_GO_KR_9B7008BF"
GEUMSAN_DARAGWON_SUBSET_CANDIDATE_ID = "MUNI_IR_9FBFEEE76A06"

GEUMSAN_DISCOVERY_AUDIT: dict[str, Any] = {
    "canonical_owner": "금산군 금산평생학습포털 프로그램 안내 및 신청",
    "canonical_url": GEUMSAN_CANONICAL_URL,
    "site_alias": {
        "url": GEUMSAN_SITE_ALIAS_URL,
        "decision": "byte-identical path alias; exclude as duplicate",
    },
    "daragwon_subset": {
        "url": GEUMSAN_DARAGWON_SUBSET_URL,
        "decision": "overlapping Daragwon-only subset of the countywide ledger",
    },
    "rejected_review_candidate": {
        "url": GEUMSAN_REJECTED_JOB_URL,
        "decision": "current Geumsan job-information board, not an education ledger",
    },
}

GEUMSAN_OWNER_BOUNDARY_AUDIT: dict[str, dict[str, str]] = {
    "lifelongedu": {
        "decision": "include",
        "reason": "countywide official education catalogue and application workflow",
    },
    "daragwon_main_site": {
        "decision": "exclude_duplicate_subset",
        "reason": "records share the same mng_no identities with the countywide ledger",
    },
    "job_information": {
        "decision": "exclude_wrong_service",
        "reason": "05040703 is the current recruitment/job board",
    },
}

GEUMSAN_PII_FIELDS_NEVER_PERSISTED = (
    "문의",
    "전화번호",
    "이메일",
    "신청자명",
    "생년월일",
    "주소",
    "휴대전화",
    "신청서 본문",
)

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class GeumsanContractError(ValueError):
    """Raised when the audited Geumsan public source contract changes."""


@dataclass(frozen=True)
class _Page:
    requested: int
    observed: int
    last: int
    rows: tuple[dict[str, Any], ...]


_SPACE = re.compile(r"\s+")
_IDENTITY = re.compile(r"^[1-9]\d*$")
_DATE = re.compile(r"20\d{2}-\d{2}-\d{2}")
_CAPACITY = re.compile(r"(?P<current>\d[\d,]*)\s*명?\s*/\s*(?P<total>\d[\d,]*)\s*명?")
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_SOURCE_STATES = {"교육대기", "교육중", "교육종료"}
_SOURCE_ACCEPT = {"접수예정", "접수중", "접수마감"}
_SOURCE_METHODS = {"인터넷", "혼합", "전화", "방문", "기타", ""}
_SOURCE_CATEGORIES = {
    "문화예술",
    "인문교양",
    "직업능력 향상교육",
    "시민참여교육",
    "성인문해교육",
    "학력보완교육",
    "",
}
_LIST_FIELDS = {
    "운영주체",
    "교육기간",
    "교육시간",
    "접수기간",
    "신청/정원",
    "교육장소",
    "교육대상",
    "교육주기",
}
_CURRENT_REQUIRED_FIELDS = {
    "교육기간",
    "교육시간",
    "접수기간",
    "신청/정원",
    "교육장소",
    "교육대상",
}
_DETAIL_FIELDS = _LIST_FIELDS | {"문의", "신청방법"}
_CURRENT_STATES = {"교육대기", "교육중"}
_GENERIC_VENUES = {"기타", "교육장소전체", ""}

_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "list_page",
        "source_category",
        "source_status",
        "source_education_state",
        "source_method",
        "source_operator",
        "source_venue",
        "source_apply_period",
        "source_education_period",
        "source_schedule",
        "source_target",
        "source_fee_omitted",
        "branch_basis",
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
        "attachments",
        "attachment_urls",
        "detail_description",
        "source_html",
        "raw_html",
    }
)


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"\s+", "", _clean(value)).casefold()


def _value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def is_geumsan_education_target(target: Any) -> bool:
    if _clean(_value(target, "provider")) != GEUMSAN_PROVIDER:
        return False
    parsed = urlparse(_clean(_value(target, "url")))
    try:
        port = parsed.port
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError):
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == GEUMSAN_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == GEUMSAN_LIST_PATH
        and not query
        and not parsed.fragment
    )


is_target = is_geumsan_education_target


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


def _coerce_soup(value: Any, requested_url: str) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    status = int(getattr(value, "status_code", 200))
    if status != 200:
        raise GeumsanContractError(f"unexpected HTTP status {status}")
    headers = getattr(value, "headers", {}) or {}
    if headers.get("Location"):
        raise GeumsanContractError("redirect response is not accepted")
    final_url = _clean(getattr(value, "url", requested_url)) or requested_url
    parsed = urlparse(final_url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != GEUMSAN_HOST:
        raise GeumsanContractError("response left the official Geumsan host")
    content = getattr(value, "content", None)
    if content is None:
        content = str(getattr(value, "text", value)).encode("utf-8")
    if not content:
        raise GeumsanContractError("empty official response")
    if len(content) > GEUMSAN_MAX_HTML_BYTES:
        raise GeumsanContractError("HTML size cap exceeded")
    return BeautifulSoup(content, "lxml")


def _request_soup(
    url: str,
    timeout: int,
    session_factory: SessionFactory,
    fetcher: Fetcher,
) -> BeautifulSoup:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != GEUMSAN_HOST
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.path != GEUMSAN_LIST_PATH
    ):
        raise GeumsanContractError("non-canonical request refused")
    last_error: Optional[Exception] = None
    for _attempt in range(2):
        session = session_factory()
        try:
            return _coerce_soup(fetcher(session, url, timeout), url)
        except (requests.RequestException, TimeoutError) as exc:
            last_error = exc
        finally:
            close = getattr(session, "close", None)
            if callable(close):
                close()
    assert last_error is not None
    raise last_error


def geumsan_list_url(page: int = 1) -> str:
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("page must be a positive integer")
    if page == 1:
        return GEUMSAN_CANONICAL_URL
    return f"{GEUMSAN_CANONICAL_URL}?{urlencode((('GotoPage', page),))}"


def geumsan_detail_url(identity: str) -> str:
    if _IDENTITY.fullmatch(str(identity)) is None:
        raise ValueError("invalid Geumsan course identity")
    return f"{GEUMSAN_CANONICAL_URL}?" + urlencode((("mode", "V"), ("mng_no", str(identity))))


def geumsan_application_url(identity: str) -> str:
    if _IDENTITY.fullmatch(str(identity)) is None:
        raise ValueError("invalid Geumsan course identity")
    return f"{GEUMSAN_CANONICAL_URL}?" + urlencode((("edu_mng_no", str(identity)), ("mode", "W")))


def _page_from_href(href: Any, label: str) -> int:
    parsed = urlparse(_clean(href))
    # The official template emits ``?&GotoPage=N``.  ``parse_qsl`` safely
    # ignores that empty leading component; the parsed pair is still checked
    # exactly below.
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if parsed.path:
        raise GeumsanContractError(f"{label} pagination link changed")
    if len(pairs) != 1 or pairs[0][0] != "GotoPage" or not pairs[0][1].isdigit():
        raise GeumsanContractError(f"{label} pagination identity changed")
    page = int(pairs[0][1])
    if page < 1:
        raise GeumsanContractError(f"{label} pagination page is invalid")
    return page


def _pagination(soup: BeautifulSoup) -> tuple[int, int]:
    active = soup.select(".pagination li.active a.page-link[href]")
    last_nodes = soup.select('.pagination a.page-link[aria-label="last"][href]')
    if len(active) != 1 or len(last_nodes) != 1:
        raise GeumsanContractError("pagination boundary controls changed")
    observed = _page_from_href(active[0].get("href"), "active")
    last = _page_from_href(last_nodes[0].get("href"), "last")
    if observed > last:
        raise GeumsanContractError("active page escaped declared last page")
    return observed, last


def _detail_identity(href: Any) -> str:
    absolute = urlparse(urljoin(GEUMSAN_CANONICAL_URL, _clean(href)))
    try:
        pairs = parse_qsl(absolute.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise GeumsanContractError("course detail query changed") from exc
    values = dict(pairs)
    identity = values.get("mng_no", "")
    if (
        absolute.scheme != "https"
        or (absolute.hostname or "").lower() != GEUMSAN_HOST
        or absolute.username is not None
        or absolute.password is not None
        or absolute.port is not None
        or absolute.path not in {GEUMSAN_LIST_PATH, GEUMSAN_SITE_ALIAS_PATH}
        or sorted(pairs) != [("mng_no", identity), ("mode", "V")]
        or absolute.fragment
        or _IDENTITY.fullmatch(identity) is None
    ):
        raise GeumsanContractError("course detail identity link changed")
    return identity


def _structured_fields(
    node: Any,
    *,
    allowed: set[str],
    discarded: set[str] = frozenset(),
    context: str,
) -> dict[str, str]:
    result: dict[str, str] = {}
    labels: set[str] = set()
    for item in node.select(".list_con > li"):
        label_node = item.find("span", recursive=False)
        if label_node is None:
            raise GeumsanContractError(f"{context}: structured field label missing")
        label = _clean(label_node.get_text(" ", strip=True)).rstrip(":").strip()
        if not label or label in labels or label not in allowed:
            raise GeumsanContractError(f"{context}: structured field set changed")
        labels.add(label)
        if label in discarded:
            # Contact values are deliberately never read into a Python string.
            continue
        value_nodes = item.find_all("em", recursive=False)
        value = _clean(value_nodes[0].get_text(" ", strip=True)) if value_nodes else ""
        if _PHONE.search(value) or _EMAIL.search(value):
            raise GeumsanContractError(f"{context}: contact-like data entered safe field {label}")
        result[label] = value
    if not labels or not labels <= allowed:
        raise GeumsanContractError(f"{context}: structured fields missing")
    return result


def _heading(node: Any, context: str) -> tuple[str, str]:
    title_node = node.select_one(".in_top .tit")
    state_node = title_node.select_one(".cond") if title_node else None
    title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
    state = _clean(state_node.get_text(" ", strip=True) if state_node else "")
    if state and title.endswith(state):
        title = title[: -len(state)].strip()
    if not title or state not in _SOURCE_STATES:
        raise GeumsanContractError(f"{context}: title or education state changed")
    if _PHONE.search(title) or _EMAIL.search(title):
        raise GeumsanContractError(f"{context}: contact-like title refused")
    return title, state


def _accept(node: Any, context: str) -> tuple[str, str]:
    accept = node.select_one(".accept")
    status_node = accept.select_one("span") if accept else None
    method_node = accept.find("em", recursive=False) if accept else None
    status = _clean(status_node.get_text(" ", strip=True) if status_node else "")
    method = _clean(method_node.get_text(" ", strip=True) if method_node else "")
    if status not in _SOURCE_ACCEPT or method not in _SOURCE_METHODS:
        raise GeumsanContractError(f"{context}: acceptance state or method changed")
    return status, method


def _parse_page(soup: BeautifulSoup, requested: int) -> _Page:
    observed, last = _pagination(soup)
    expected_observed = min(requested, last)
    if observed != expected_observed:
        raise GeumsanContractError(f"page {requested}: observed page {observed}, expected {expected_observed}")
    roots = soup.select(".program_con")
    if len(roots) != 1:
        raise GeumsanContractError(f"page {requested}: catalogue root changed")
    anchors = roots[0].find_all("a", href=True, recursive=False)
    if not anchors:
        raise GeumsanContractError(f"page {requested}: no course rows")
    rows: list[dict[str, Any]] = []
    for sequence, anchor in enumerate(anchors, start=1):
        identity = _detail_identity(anchor.get("href"))
        context = f"page {requested} course {identity}"
        title, education_state = _heading(anchor, context)
        source_status, method = _accept(anchor, context)
        category_node = anchor.select_one(".in_top .cate")
        category = _clean(category_node.get_text(" ", strip=True) if category_node else "")
        if category not in _SOURCE_CATEGORIES:
            raise GeumsanContractError(f"{context}: education category changed")
        fields = _structured_fields(anchor, allowed=_LIST_FIELDS, context=context)
        rows.append(
            {
                "identity": identity,
                "list_page": requested,
                "list_sequence": sequence,
                "list_title": title,
                "education_state": education_state,
                "source_status": source_status,
                "method": method,
                "category": category,
                "fields": fields,
                "detail_url": geumsan_detail_url(identity),
            }
        )
    if observed < last and len(rows) != GEUMSAN_PAGE_SIZE:
        raise GeumsanContractError(f"page {requested}: non-final page row count changed")
    if observed == last and not 1 <= len(rows) <= GEUMSAN_PAGE_SIZE:
        raise GeumsanContractError(f"page {requested}: final page row count changed")
    identities = [row["identity"] for row in rows]
    if len(identities) != len(set(identities)):
        raise GeumsanContractError(f"page {requested}: duplicate course identities")
    return _Page(requested, observed, last, tuple(rows))


def _page_signature(page: _Page) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            row["identity"],
            row["list_title"],
            row["education_state"],
            row["source_status"],
            row["method"],
            row["category"],
            tuple(sorted(row["fields"].items())),
        )
        for row in page.rows
    )


def _date_pair(value: str, context: str) -> tuple[date, date]:
    values = _DATE.findall(value)
    if len(values) != 2:
        raise GeumsanContractError(f"{context}: date range missing")
    start, end = date.fromisoformat(values[0]), date.fromisoformat(values[1])
    if end < start:
        raise GeumsanContractError(f"{context}: reversed date range")
    return start, end


def _current_contract(row: Mapping[str, Any], cutoff: date) -> dict[str, Any]:
    identity = _clean(row.get("identity"))
    state = _clean(row.get("education_state"))
    fields = row.get("fields")
    if state not in _CURRENT_STATES or not isinstance(fields, Mapping):
        raise GeumsanContractError(f"course {identity}: current source context changed")
    missing = _CURRENT_REQUIRED_FIELDS - set(fields)
    if missing or any(not _clean(fields.get(key)) for key in _CURRENT_REQUIRED_FIELDS):
        raise GeumsanContractError(f"course {identity}: current structured fields missing")

    education_period = _clean(fields.get("교육기간"))
    if education_period == "상시":
        start = end = None
    else:
        start, end = _date_pair(education_period, f"course {identity} education")
        if end < cutoff:
            raise GeumsanContractError(f"course {identity}: current state has expired period")

    apply_period = _clean(fields.get("접수기간"))
    if apply_period in {"상시접수", "~"}:
        apply_start = apply_end = None
    else:
        apply_start, apply_end = _date_pair(apply_period, f"course {identity} application")
    source_status = _clean(row.get("source_status"))
    if source_status == "접수중" and apply_start is not None:
        if not apply_start <= cutoff <= apply_end:
            raise GeumsanContractError(f"course {identity}: open source status/application dates disagree")
    if source_status == "접수예정":
        if apply_start is None or apply_start < cutoff:
            raise GeumsanContractError(
                f"course {identity}: scheduled source status/application dates disagree"
            )

    capacity_match = _CAPACITY.fullmatch(_clean(fields.get("신청/정원")))
    if capacity_match is None:
        raise GeumsanContractError(f"course {identity}: capacity changed")
    return {
        "start": start,
        "end": end,
        "apply_start": apply_start,
        "apply_end": apply_end,
        "capacity_current": int(capacity_match.group("current").replace(",", "")),
        "capacity_total": int(capacity_match.group("total").replace(",", "")),
    }


def _same_title(list_title: str, detail_title: str) -> bool:
    if list_title.endswith("..."):
        prefix = list_title[:-3].rstrip().rstrip("&").rstrip()
        return bool(prefix and _normalized(detail_title).startswith(_normalized(prefix)))
    return _normalized(list_title) == _normalized(detail_title)


def _same_public_field(field: str, listed: Any, detailed: Any) -> bool:
    if field == "교육대상":
        # The catalogue card unconditionally renders a separator between the
        # two target select levels.  When the second level is empty this leaves
        # a trailing ``>`` which the detail template correctly omits.
        listed = re.sub(r"\s*>\s*$", "", _clean(listed))
        detailed = re.sub(r"\s*>\s*$", "", _clean(detailed))
    return _normalized(listed) == _normalized(detailed)


def _application_control(soup: BeautifulSoup, identity: str) -> str:
    controls: list[Any] = []
    for anchor in soup.select(".text-right.mt_30 a.btn[href]"):
        text = _clean(anchor.get_text(" ", strip=True))
        href = _clean(anchor.get("href"))
        parsed = urlparse(urljoin(GEUMSAN_CANONICAL_URL, href))
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        if "강좌신청" in text or ("mode", "W") in pairs:
            controls.append(anchor)
    if len(controls) > 1:
        raise GeumsanContractError(f"course {identity}: multiple application controls")
    if not controls:
        return ""
    parsed = urlparse(urljoin(GEUMSAN_CANONICAL_URL, _clean(controls[0].get("href"))))
    try:
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise GeumsanContractError(f"course {identity}: application query changed") from exc
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != GEUMSAN_HOST
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.path not in {GEUMSAN_LIST_PATH, GEUMSAN_SITE_ALIAS_PATH}
        or sorted(pairs) != [("edu_mng_no", identity), ("mode", "W")]
        or parsed.fragment
    ):
        raise GeumsanContractError(f"course {identity}: application control identity changed")
    return geumsan_application_url(identity)


def _branch(operator: str, venue: str) -> tuple[str, str]:
    if venue not in _GENERIC_VENUES:
        return venue, "education_venue"
    if operator:
        return operator, "operator_fallback_for_generic_venue"
    if venue:
        return venue, "generic_venue_only"
    raise GeumsanContractError("course branch is absent")


def _branch_code(branch: str) -> str:
    digest = hashlib.sha1(_normalized(branch).encode("utf-8")).hexdigest()[:12].upper()
    return "GEUMSAN_" + digest.translate(str.maketrans("0123456789", "GHIJKLMNOP"))


def _parse_detail(listed: Mapping[str, Any], soup: BeautifulSoup, cutoff: date) -> dict[str, Any]:
    identity = _clean(listed.get("identity"))
    roots = soup.select(".program_view")
    if len(roots) != 1:
        raise GeumsanContractError(f"course {identity}: detail root changed")
    root = roots[0]
    title, education_state = _heading(root, f"course {identity} detail")
    source_status, method = _accept(root, f"course {identity} detail")
    category_node = root.select_one(".in_top .cate")
    category = _clean(category_node.get_text(" ", strip=True) if category_node else "")
    if (
        not _same_title(_clean(listed.get("list_title")), title)
        or education_state != _clean(listed.get("education_state"))
        or source_status != _clean(listed.get("source_status"))
        or method != _clean(listed.get("method"))
        or category != _clean(listed.get("category"))
    ):
        raise GeumsanContractError(f"course {identity}: list/detail identity drift")

    detail_fields = _structured_fields(
        root,
        allowed=_DETAIL_FIELDS,
        discarded={"문의"},
        context=f"course {identity} detail",
    )
    if detail_fields.get("신청방법") != method:
        raise GeumsanContractError(f"course {identity}: application method drift")
    listed_fields = listed.get("fields")
    if not isinstance(listed_fields, Mapping):
        raise GeumsanContractError(f"course {identity}: list field context missing")
    for field in _LIST_FIELDS:
        if not _same_public_field(field, listed_fields.get(field), detail_fields.get(field)):
            raise GeumsanContractError(f"course {identity}: list/detail {field} drift")

    dates = _current_contract(listed, cutoff)
    control = _application_control(soup, identity)
    is_open = source_status == "접수중"
    is_scheduled = source_status == "접수예정"
    online = method in {"인터넷", "혼합"}
    if is_open and online and not control:
        raise GeumsanContractError(f"course {identity}: open online course lacks application control")
    if control and not (is_open and online):
        raise GeumsanContractError(f"course {identity}: unexpected application control")
    if is_open and not online and method not in {"전화", "방문"}:
        raise GeumsanContractError(f"course {identity}: unsupported open method")

    operator = _clean(detail_fields.get("운영주체"))
    venue = _clean(detail_fields.get("교육장소"))
    branch, branch_basis = _branch(operator, venue)
    status = "OPEN" if is_open else "SCHEDULED" if is_scheduled else "CLOSED"
    if control:
        application_type = "ONLINE_RESERVATION_LOGIN_REQUIRED"
    elif is_open and method == "전화":
        application_type = "OFFLINE_PHONE"
    elif is_open and method == "방문":
        application_type = "OFFLINE_VISIT"
    else:
        application_type = "INFO_ONLY"
    start, end = dates["start"], dates["end"]
    apply_start, apply_end = dates["apply_start"], dates["apply_end"]
    capacity_current = dates["capacity_current"]
    capacity_total = dates["capacity_total"]
    period = _clean(detail_fields.get("교육기간"))
    apply_period = _clean(detail_fields.get("접수기간"))
    row: dict[str, Any] = {
        "provider": GEUMSAN_PROVIDER,
        "provider_course_id": f"{GEUMSAN_PROVIDER}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": title,
        "branch": branch,
        "branch_code": _branch_code(branch),
        "preserve_branch": True,
        "category": "평생학습",
        "program_type": "교육",
        "raw_url": geumsan_detail_url(identity),
        "application_url": control,
        "application_type": application_type,
        "application_method": method,
        "application_methods": [method],
        "reservation_available": bool(control),
        "status": status,
        "fee": "요금 별도 안내",
        "period": period,
        "start_date": start.isoformat() if start is not None else "",
        "end_date": end.isoformat() if end is not None else "",
        "apply_period": apply_period,
        "apply_start": apply_start.isoformat() if apply_start is not None else "",
        "apply_end": apply_end.isoformat() if apply_end is not None else "",
        "schedule_raw": _clean(detail_fields.get("교육주기")) or _clean(detail_fields.get("교육시간")),
        "capacity": f"{capacity_total}명",
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "waitlist_current": max(0, capacity_current - capacity_total),
        "target": _clean(detail_fields.get("교육대상")),
        "venue": venue,
        "venue_name": branch,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": GEUMSAN_PARSER,
        "municipality_code": GEUMSAN_MUNICIPALITY_CODE,
        "municipality_full_name": GEUMSAN_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "list_page": int(listed["list_page"]),
            "source_category": category,
            "source_status": source_status,
            "source_education_state": education_state,
            "source_method": method,
            "source_operator": operator,
            "source_venue": venue,
            "source_apply_period": apply_period,
            "source_education_period": period,
            "source_schedule": _clean(detail_fields.get("교육주기")),
            "source_target": _clean(detail_fields.get("교육대상")),
            "source_fee_omitted": True,
            "branch_basis": branch_basis,
            "detail_verified": True,
            "application_control_present": bool(control),
            "service_family": "education",
        },
    }
    return row


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_PERSISTED_KEYS:
        errors.append("forbidden detail/PII key persisted")
    raw_fields = row.get("raw_fields")
    if not isinstance(raw_fields, Mapping) or not set(raw_fields) <= _SAFE_RAW_FIELDS:
        errors.append("raw_fields exceeded the PII-safe allowlist")
    payload = repr({key: value for key, value in row.items() if key not in {"raw_url", "application_url"}})
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


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value)
    raise ValueError("today must be an ISO date")


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "data_pages": 0,
        "declared_last_page": 0,
        "post_last_clamp_page": 0,
        "boundary_rechecks": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "source_total": 0,
        "source_rows": 0,
        "current_candidate_count": 0,
        "current_source_count": 0,
        "expired_count": 0,
        "archived_rows_skipped_before_detail": 0,
        "identity_duplicate_count": 0,
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
        "municipality_code": GEUMSAN_MUNICIPALITY_CODE,
        "municipality_name": GEUMSAN_MUNICIPALITY_NAME,
        "canonical_candidate_id": GEUMSAN_CANONICAL_CANDIDATE_ID,
        "canonical_url": GEUMSAN_CANONICAL_URL,
        "boundary_mode": ("declared last page plus exact post-last clamp and stable first/last rechecks"),
    }


def collect_geumsan_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 100,
    detail_limit: int = 500,
    max_workers: int = GEUMSAN_MAX_WORKERS,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect the complete current/future Geumsan education snapshot."""

    meta = _base_meta()
    if not is_geumsan_education_target(target):
        meta["configured_collection_error"] = "target does not match canonical Geumsan education owner"
        return [], GEUMSAN_PARSER, meta
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
        or max_workers > 32
    ):
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": "invalid collection limits",
            }
        )
        return [], GEUMSAN_PARSER, meta
    try:
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], GEUMSAN_PARSER, meta

    factory = session_factory or _default_session_factory
    current_fetcher = fetcher or _default_fetcher
    errors: list[str] = []
    result: list[dict[str, Any]] = []
    try:
        first = _parse_page(_request_soup(geumsan_list_url(1), timeout, factory, current_fetcher), 1)
        meta["list_requests"] += 1
        meta["pages"] += 1
        last = first.last
        meta["declared_last_page"] = last
        if last > max_pages:
            meta.update(
                {
                    "source_cap_reached": True,
                    "configured_collection_error": (f"max_pages cap allows {max_pages} of {last} declared pages"),
                }
            )
            return [], GEUMSAN_PARSER, meta

        pages: dict[int, _Page] = {1: first}

        def fetch_page(page: int) -> _Page:
            return _parse_page(
                _request_soup(geumsan_list_url(page), timeout, factory, current_fetcher),
                page,
            )

        if last > 1:
            with ThreadPoolExecutor(max_workers=min(max_workers, last - 1)) as pool:
                fetched = list(pool.map(fetch_page, range(2, last + 1)))
            for page in fetched:
                pages[page.requested] = page
            meta["list_requests"] += len(fetched)
            meta["pages"] += len(fetched)
        if any(pages[index].last != last for index in range(1, last + 1)):
            raise GeumsanContractError("declared last page changed during traversal")

        clamp = fetch_page(last + 1)
        meta["list_requests"] += 1
        meta["pages"] += 1
        meta["post_last_clamp_page"] = last + 1
        if clamp.observed != last or _page_signature(clamp) != _page_signature(pages[last]):
            raise GeumsanContractError("post-last clamp boundary changed")

        boundary_numbers = [1] if last == 1 else [1, last]
        with ThreadPoolExecutor(max_workers=len(boundary_numbers)) as pool:
            rechecks = list(pool.map(fetch_page, boundary_numbers))
        meta["list_requests"] += len(rechecks)
        meta["pages"] += len(rechecks)
        meta["boundary_rechecks"] = len(rechecks)
        for rechecked in rechecks:
            if _page_signature(rechecked) != _page_signature(pages[rechecked.requested]):
                raise GeumsanContractError(f"page {rechecked.requested}: boundary stability recheck changed")

        required_requests = last + 1 + len(boundary_numbers)
        meta["required_list_requests"] = required_requests
        listed = [row for page in range(1, last + 1) for row in pages[page].rows]
        identities = [_clean(row.get("identity")) for row in listed]
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count:
            raise GeumsanContractError(f"{duplicate_count} duplicate identities across catalogue pages")
        expected_total = (last - 1) * GEUMSAN_PAGE_SIZE + len(pages[last].rows)
        if len(listed) != expected_total:
            raise GeumsanContractError("complete catalogue cardinality changed")
        list_complete = bool(meta["list_requests"] == required_requests)
        source_state_counts = Counter(row["education_state"] for row in listed)
        source_status_counts = Counter(row["source_status"] for row in listed)
        source_method_counts = Counter(row["method"] for row in listed)
        meta.update(
            {
                "data_pages": last,
                "source_total": len(listed),
                "source_rows": len(listed),
                "identity_duplicate_count": duplicate_count,
                "source_education_state_counts": dict(source_state_counts),
                "source_status_counts": dict(source_status_counts),
                "source_method_counts": dict(source_method_counts),
                "pagination_complete": list_complete,
            }
        )
        if not list_complete:
            raise GeumsanContractError("list request boundary incomplete")

        current = [row for row in listed if row["education_state"] in _CURRENT_STATES]
        for row in current:
            _current_contract(row, cutoff)
        meta.update(
            {
                "current_candidate_count": len(current),
                "archived_rows_skipped_before_detail": len(listed) - len(current),
            }
        )
        if len(current) > detail_limit:
            meta.update(
                {
                    "source_cap_reached": True,
                    "configured_collection_error": (
                        f"detail_limit cap allows {detail_limit} of {len(current)} current/future details"
                    ),
                }
            )
            return [], GEUMSAN_PARSER, meta

        meta["detail_attempts"] = len(current)

        def fetch_detail(listed_row: Mapping[str, Any]) -> tuple[Optional[dict[str, Any]], str]:
            identity = _clean(listed_row.get("identity"))
            try:
                soup = _request_soup(geumsan_detail_url(identity), timeout, factory, current_fetcher)
                return _parse_detail(listed_row, soup, cutoff), ""
            except Exception as exc:  # fail the complete snapshot, never a single row
                return None, f"detail {identity}: {type(exc).__name__}: {_clean(exc)}"

        detailed: list[dict[str, Any]] = []
        if current:
            with ThreadPoolExecutor(max_workers=min(max_workers, len(current))) as pool:
                detail_results = list(pool.map(fetch_detail, current))
            for row, error in detail_results:
                if error:
                    errors.append(error)
                    meta["detail_errors"] += 1
                elif row is not None:
                    detailed.append(row)
                    meta["detail_pages"] += 1
                    meta["pages"] += 1
        details_complete = bool(
            not errors and meta["detail_attempts"] == meta["detail_pages"] and len(detailed) == len(current)
        )
        controls_complete = bool(
            details_complete and all(bool(row.get("raw_fields", {}).get("detail_verified")) for row in detailed)
        )
        if details_complete and controls_complete:
            for row in detailed:
                errors.extend(_privacy_errors(row))
            if not errors:
                deduper = dedupe_rows or _dedupe_default
                try:
                    result = list(deduper(detailed))
                except Exception as exc:
                    errors.append(f"dedupe failed: {type(exc).__name__}: {_clean(exc)}")
                    result = []
                if len(result) != len(detailed):
                    errors.append(f"dedupe changed official identity cardinality {len(detailed)} to {len(result)}")
                    result = []

        snapshot_complete = bool(list_complete and details_complete and controls_complete and not errors)
        if not snapshot_complete:
            result = []
        meta.update(
            {
                "current_source_count": len(detailed),
                "expired_count": len(listed) - len(current),
                "branch_counts": dict(sorted(Counter(_clean(row.get("branch")) for row in result).items())),
                "status_counts": dict(Counter(_clean(row.get("status")) for row in result)),
                "application_control_count": sum(
                    bool(row.get("raw_fields", {}).get("application_control_present")) for row in detailed
                ),
                "offline_open_count": sum(
                    row.get("status") == "OPEN" and not row.get("reservation_available") for row in detailed
                ),
                "details_complete": details_complete,
                "application_controls_complete": controls_complete,
                "snapshot_complete": snapshot_complete,
                "full_snapshot_validated": snapshot_complete,
                "returned_count": len(result),
                "no_current_data": bool(snapshot_complete and not current),
                "no_current_reason": (
                    "all official Geumsan education records are marked 교육종료"
                    if snapshot_complete and not current
                    else ""
                ),
                "municipality_coverage": [GEUMSAN_MUNICIPALITY_CODE],
                "discovery_audit": dict(GEUMSAN_DISCOVERY_AUDIT),
                "owner_boundary_audit": {key: dict(value) for key, value in GEUMSAN_OWNER_BOUNDARY_AUDIT.items()},
                "pii_fields_never_persisted": list(GEUMSAN_PII_FIELDS_NEVER_PERSISTED),
                "pii_payload_persisted": False,
                "forbidden_applicant_endpoint_requests": 0,
                "configured_collection_error": "; ".join(dict.fromkeys(errors)),
            }
        )
        return result, GEUMSAN_PARSER, meta
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        meta["pagination_complete"] = False
        meta["snapshot_complete"] = False
        meta["full_snapshot_validated"] = False
        return [], GEUMSAN_PARSER, meta


collect = collect_geumsan_education


__all__ = [
    "GEUMSAN_CANONICAL_CANDIDATE_ID",
    "GEUMSAN_CANONICAL_URL",
    "GEUMSAN_DARAGWON_SUBSET_CANDIDATE_ID",
    "GEUMSAN_DARAGWON_SUBSET_PROVIDER",
    "GEUMSAN_DARAGWON_SUBSET_URL",
    "GEUMSAN_DISCOVERY_AUDIT",
    "GEUMSAN_HOST",
    "GEUMSAN_LIST_PATH",
    "GEUMSAN_MUNICIPALITY_CODE",
    "GEUMSAN_MUNICIPALITY_NAME",
    "GEUMSAN_OWNER_BOUNDARY_AUDIT",
    "GEUMSAN_PARSER",
    "GEUMSAN_PII_FIELDS_NEVER_PERSISTED",
    "GEUMSAN_PROVIDER",
    "GEUMSAN_REJECTED_JOB_CANDIDATE_ID",
    "GEUMSAN_REJECTED_JOB_PROVIDER",
    "GEUMSAN_REJECTED_JOB_URL",
    "GEUMSAN_SITE_ALIAS_CANDIDATE_ID",
    "GEUMSAN_SITE_ALIAS_PATH",
    "GEUMSAN_SITE_ALIAS_PROVIDER",
    "GEUMSAN_SITE_ALIAS_URL",
    "GeumsanContractError",
    "collect",
    "collect_geumsan_education",
    "geumsan_application_url",
    "geumsan_detail_url",
    "geumsan_list_url",
    "is_geumsan_education_target",
    "is_target",
]
