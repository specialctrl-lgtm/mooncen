"""Fail-closed collector for Jeongseon-gun lifelong-learning courses.

The registered ``quarter=LifeEdu`` URL is only one of three catalogue
partitions.  The official application also exposes the unfiltered catalogue
with an empty ``quarter`` value.  This module accepts the existing registered
target as its owner identity, but always traverses that unfiltered catalogue.

Unlike the legacy central helper, pagination is not a shared request budget,
expired rows do not consume the detail cap, inactive courses do not receive a
reservation URL, and detail/application HTML (including applicant name and
telephone inputs) is never persisted.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import html
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


JEONGSEON_PROVIDER = "MUNI_WWW_JEONGSEON_GO_KR_38CBA90A"
JEONGSEON_CANONICAL_CANDIDATE_ID = "MUNI_IR_084F0E972514"
JEONGSEON_MUNICIPALITY_CODE = "5177000000"
JEONGSEON_MUNICIPALITY_NAME = "강원특별자치도 정선군"
JEONGSEON_BRANCH = "정선군평생학습관"
JEONGSEON_HOST = "edu.jeongseon.go.kr"
JEONGSEON_BASE_URL = f"https://{JEONGSEON_HOST}"
JEONGSEON_LIST_PATH = "/lecture"
JEONGSEON_DETAIL_PATH = "/lec_view"
JEONGSEON_APPLICATION_PATH = "/lec_apply"
JEONGSEON_REGISTERED_URL = f"{JEONGSEON_BASE_URL}/lecture?quarter=LifeEdu"
JEONGSEON_ALL_URL = f"{JEONGSEON_BASE_URL}/lecture?quarter="
JEONGSEON_PAGE_SIZE = 10
JEONGSEON_FETCH_ATTEMPTS = 2
JEONGSEON_MAX_WORKERS = 12
JEONGSEON_MAX_HTML_BYTES = 3_000_000
JEONGSEON_PARSER = (
    "jeongseon_official_unfiltered_lifelong_courses+all_pages+"
    "three_partition_total_crosscheck+sentinel_or_exact_clamp+"
    "stable_first_last_boundaries+current_details+identity_bound_post_"
    "application_controls+facility_branches+pii_allowlist"
)
JEONGSEON_OWNERSHIP_SCOPE = (
    "jeongseon_official_unfiltered_three_partition_lifelong_catalogue"
)

JEONGSEON_QUARTERS: Mapping[str, str] = {
    "LifeEdu": "평생교육강좌",
    "InfoEdu": "기초문해교육강좌",
    "OnlineEdu": "디지털문해교육강좌",
}

JEONGSEON_EDUCATION_SUPPORT_BOARD_URL = (
    "https://gwjsed.gwe.go.kr/boardCnts/list.do?m=0201&boardID=3132"
)
JEONGSEON_GENERAL_HOMEPAGE_URL = "https://www.jeongseon.go.kr/"
JEONGSEON_WRONG_MUNICIPALITY_URL = (
    "https://yd.familynet.or.kr/center/lay1/program/"
    "S295T322C451/recruitReceipt/view.do?seq=221704"
)
JEONGSEON_EXCLUDED_CANDIDATE_IDS = frozenset(
    {
        "MUNI_IR_F6E1A01BDD2D",
        "MUNI_IR_D36373945448",
        "MUNI_IR_97C2B3C7F75C",
    }
)
JEONGSEON_CANDIDATE_AUDIT: Mapping[str, Mapping[str, str]] = {
    "MUNI_IR_F6E1A01BDD2D": {
        "decision": "excluded_administrative_notice_board",
        "provider": "MUNI_GWJSED_GWE_GO_KR_56810487",
        "url": JEONGSEON_EDUCATION_SUPPORT_BOARD_URL,
        "owner": "",
        "reason": (
            "education-support-office administrative notice board; its "
            "disabled duplicate target is already classified non-course"
        ),
    },
    "MUNI_IR_D36373945448": {
        "decision": "excluded_general_county_homepage_candidate",
        "provider": JEONGSEON_PROVIDER,
        "url": JEONGSEON_GENERAL_HOMEPAGE_URL,
        "owner": "",
        "reason": (
            "candidate URL is the general county intro, not a course list; "
            "the provider string is retained only by the existing separate "
            "edu.jeongseon.go.kr operational target"
        ),
    },
    "MUNI_IR_97C2B3C7F75C": {
        "decision": "excluded_wrong_municipality_single_program",
        "provider": "MUNI_YD_FAMILYNET_OR_KR_21A0B828",
        "url": JEONGSEON_WRONG_MUNICIPALITY_URL,
        "owner": "",
        "reason": (
            "Yeongdeok family-center detail accidentally matched a snippet "
            "mentioning Jeongseon Education Library"
        ),
    },
}

JEONGSEON_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-22",
    "canonical_candidate_id": JEONGSEON_CANONICAL_CANDIDATE_ID,
    "registered_partition_url": JEONGSEON_REGISTERED_URL,
    "canonical_unfiltered_url": JEONGSEON_ALL_URL,
    "official_navigation_partitions": dict(JEONGSEON_QUARTERS),
    "cached_public_index_partition_totals": {
        "LifeEdu": 35,
        "InfoEdu": 17,
        "OnlineEdu": 43,
    },
    "cached_public_index_unfiltered_total": 95,
    "cached_unfiltered_page_marker": "95 rows; page 2 of 10",
    "partition_sum_matches_unfiltered_total": True,
    "cached_current_or_future_detail_evidence": (
        "cls_no=123 has education period 2026-04-22 through 2026-12-31; "
        "cached evidence is not treated as a live snapshot"
    ),
    "direct_live_availability_at_audit": (
        "unavailable: root returned HTTP 200 with an empty body while /lecture "
        "and /lec_view returned HTTP 404"
    ),
    "direct_live_probe": {
        "/": "HTTP 200, 0 bytes",
        "/lecture?quarter=": "HTTP 404, 205 bytes",
        "/lecture?quarter=LifeEdu": "HTTP 404, 205 bytes",
        "/lecture?quarter=InfoEdu": "HTTP 404, 205 bytes",
        "/lecture?quarter=OnlineEdu": "HTTP 404, 205 bytes",
        "/lec_view?cls_no=123": "HTTP 404, 206 bytes",
    },
    "legacy_defects": (
        "shared max_pages budget across partitions; generated wrapper "
        "explicitly truncates the 95-row source to per-target-limit=50 and "
        "allows partial save; default detail_limit=30 leaves later rows "
        "unenriched; inactive application URLs; unbounded detail/free-form "
        "raw_fields persistence"
    ),
}

JEONGSEON_PII_FIELDS_DISCARDED = (
    "신청자명",
    "전화번호",
    "신청 form payload",
    "detail HTML",
    "source HTML",
    "arbitrary free-form summary",
)


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)")
_SUMMARY_RE = re.compile(
    r"전체\s*:\s*([\d,]+)\s*\(\s*(\d+)\s*/\s*(\d+)\s*페이지\s*\)"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2})[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_SOURCE_STATUS_TOKENS = (
    "접수중",
    "신청하기",
    "온라인신청",
    "접수예정",
    "신청예정",
    "정원초과",
    "신청종료",
    "신청마감",
    "마감",
    "강의중",
    "종강",
    "폐강",
)
_OPEN_SOURCE_STATUSES = frozenset({"접수중", "신청하기", "온라인신청"})
_ACTION_LABELS = frozenset({"신청하기", "온라인신청"})
_INACTIVE_LABELS = frozenset(
    {"신청종료", "접수예정", "신청예정", "정원초과", "신청마감", "마감"}
)
_DETAIL_FIELDS = frozenset(
    {
        "분야",
        "강좌명",
        "위치",
        "신청기간",
        "교육기간",
        "교육시간",
        "교재정보",
        "정원",
        "강의상태",
        "요약",
    }
)
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "list_page",
        "source_status",
        "source_application_period",
        "source_period",
        "source_schedule",
        "source_capacity_current",
        "source_capacity_total",
        "source_category",
        "source_venue",
        "source_detail_status",
        "service_family",
        "detail_verified",
        "application_control_present",
        "application_control_label",
        "application_control_identity",
        "application_control_method",
        "application_control_action",
        "application_control_verified",
    }
)
_FORBIDDEN_PERSISTED_KEYS = frozenset(
    {
        "detail_pairs",
        "card_pairs",
        "source_html",
        "raw_html",
        "applicant_name",
        "phone",
        "email",
        "form_payload",
        "instructor",
        "contact",
        "attachments",
    }
)


class JeongseonContractError(ValueError):
    """Raised when the official source no longer satisfies its contract."""


@dataclass
class _ListPage:
    rows: list[dict[str, Any]]
    total: int
    current_page: int
    last_page: int
    errors: list[str]


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(
        " ", html.unescape(str(value or "")).replace("\xa0", " ")
    ).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[\W_]+", "", _clean(value).casefold(), flags=re.UNICODE)


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    try:
        return date.fromisoformat(_clean(value))
    except ValueError as exc:
        raise ValueError("today must be an ISO date") from exc


def _compare_url(value: Any) -> str:
    try:
        parsed = urlparse(_clean(value))
        port = parsed.port
    except ValueError:
        return ""
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or port
        or parsed.fragment
    ):
        return ""
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return f"https://{parsed.hostname.lower()}{parsed.path}" + (
        f"?{query}" if query else ""
    )


def is_jeongseon_education_target(target: Any) -> bool:
    return (
        _clean(_target_value(target, "provider")) == JEONGSEON_PROVIDER
        and _compare_url(_target_value(target, "url"))
        == _compare_url(JEONGSEON_REGISTERED_URL)
    )


def is_jeongseon_excluded_candidate(target: Any) -> bool:
    candidate_id = _clean(_target_value(target, "candidate_id"))
    compared = _compare_url(_target_value(target, "url"))
    return candidate_id in JEONGSEON_EXCLUDED_CANDIDATE_IDS or compared in {
        _compare_url(JEONGSEON_EDUCATION_SUPPORT_BOARD_URL),
        _compare_url(JEONGSEON_GENERAL_HOMEPAGE_URL),
        _compare_url(JEONGSEON_WRONG_MUNICIPALITY_URL),
    }


def is_jeongseon_unfiltered_source(target: Any) -> bool:
    return _compare_url(_target_value(target, "url")) == _compare_url(
        JEONGSEON_ALL_URL
    )


def jeongseon_all_list_url(page: Any = 1) -> str:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        return ""
    pairs: list[tuple[str, Any]] = [("quarter", "")]
    if page > 1:
        pairs.append(("page", page))
    return f"{JEONGSEON_BASE_URL}{JEONGSEON_LIST_PATH}?{urlencode(pairs)}"


def jeongseon_partition_url(quarter: Any, page: Any = 1) -> str:
    value = _clean(quarter)
    if (
        value not in JEONGSEON_QUARTERS
        or isinstance(page, bool)
        or not isinstance(page, int)
        or page < 1
    ):
        return ""
    pairs: list[tuple[str, Any]] = [("quarter", value)]
    if page > 1:
        pairs.append(("page", page))
    return f"{JEONGSEON_BASE_URL}{JEONGSEON_LIST_PATH}?{urlencode(pairs)}"


def jeongseon_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not re.fullmatch(r"[1-9]\d*", value):
        return ""
    return f"{JEONGSEON_BASE_URL}{JEONGSEON_DETAIL_PATH}?" + urlencode(
        {"cls_no": value}
    )


def _default_session_factory() -> requests.Session:
    value = requests.Session()
    value.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (compatible; MooncenMunicipalAudit/1.0; "
                "+https://edu.jeongseon.go.kr/)"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return value


def _is_official_request_url(value: Any) -> bool:
    try:
        parsed = urlparse(_clean(value))
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname != JEONGSEON_HOST
        or parsed.username
        or parsed.password
        or port
        or parsed.fragment
    ):
        return False
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if parsed.path == JEONGSEON_LIST_PATH:
        quarters = [item for key, item in pairs if key == "quarter"]
        pages = [item for key, item in pairs if key == "page"]
        if (
            len(quarters) != 1
            or quarters[0] not in ({""} | set(JEONGSEON_QUARTERS))
            or len(pages) > 1
            or len(pairs) != 1 + len(pages)
        ):
            return False
        return not pages or bool(
            re.fullmatch(r"[1-9]\d*", pages[0]) and int(pages[0]) > 1
        )
    if parsed.path == JEONGSEON_DETAIL_PATH:
        return bool(
            len(pairs) == 1
            and pairs[0][0] == "cls_no"
            and re.fullmatch(r"[1-9]\d*", pairs[0][1])
        )
    return False


def _default_fetcher(session: Any, url: str, timeout: int) -> BeautifulSoup:
    if not _is_official_request_url(url):
        raise ValueError("request left the exact Jeongseon catalogue contract")
    response = session.get(url, timeout=timeout, allow_redirects=False)
    response.raise_for_status()
    status_code = int(getattr(response, "status_code", 0))
    if not 200 <= status_code < 300:
        raise ValueError(f"unexpected HTTP status {status_code}")
    final_url = _clean(getattr(response, "url", url))
    if not _is_official_request_url(final_url) or _compare_url(
        final_url
    ) != _compare_url(url):
        raise ValueError("response left the exact Jeongseon request URL")
    content_type = _clean(response.headers.get("Content-Type")).lower()
    if "html" not in content_type:
        raise ValueError("response is not HTML")
    content = response.content
    if not content:
        raise ValueError("HTML response was empty")
    if len(content) > JEONGSEON_MAX_HTML_BYTES:
        raise ValueError("HTML response exceeded the bounded size limit")
    return BeautifulSoup(content, "html.parser")


def _close_quietly(value: Any) -> None:
    try:
        close = getattr(value, "close", None)
        if callable(close):
            close()
    except Exception:
        pass


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, bytes):
        if len(value) > JEONGSEON_MAX_HTML_BYTES:
            raise ValueError("fixture HTML exceeded the bounded size limit")
        return BeautifulSoup(value, "html.parser")
    if isinstance(value, str):
        if len(value.encode("utf-8")) > JEONGSEON_MAX_HTML_BYTES:
            raise ValueError("fixture HTML exceeded the bounded size limit")
        return BeautifulSoup(value, "html.parser")
    content = getattr(value, "content", None)
    if isinstance(content, (bytes, bytearray)):
        return _coerce_soup(bytes(content))
    raise TypeError("fetcher must return HTML, bytes, a response, or BeautifulSoup")


def _fetch_parse_many(
    items: Iterable[tuple[Any, str, Callable[[BeautifulSoup], Any]]],
    *,
    fetcher: Fetcher,
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
) -> tuple[dict[Any, Any], list[str]]:
    tasks = list(items)
    if not tasks:
        return {}, []

    def worker(key: Any, url: str, parser: Callable[[BeautifulSoup], Any]):
        last_error: Optional[Exception] = None
        for _attempt in range(JEONGSEON_FETCH_ATTEMPTS):
            current = session_factory()
            try:
                return key, parser(_coerce_soup(fetcher(current, url, timeout)))
            except Exception as exc:
                last_error = exc
            finally:
                _close_quietly(current)
        raise RuntimeError(_clean(last_error))

    values: dict[Any, Any] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(tasks))) as executor:
        futures = {
            executor.submit(worker, key, url, parser): key
            for key, url, parser in tasks
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                result_key, result = future.result()
                values[result_key] = result
            except Exception as exc:
                errors.append(f"{key}: {_clean(exc)}")
    return values, errors


def _date_pair(value: Any, field: str) -> tuple[date, date]:
    matches = _DATE_RE.findall(_clean(value))
    if len(matches) != 2:
        raise JeongseonContractError(f"{field}: expected exactly two dates")
    values: list[date] = []
    for year, month, day_value in matches:
        try:
            values.append(date(int(year), int(month), int(day_value)))
        except ValueError as exc:
            raise JeongseonContractError(f"{field}: invalid calendar date") from exc
    if values[0] > values[1]:
        raise JeongseonContractError(f"{field}: reversed dates")
    return values[0], values[1]


def _capacity_pair(value: Any) -> tuple[int, int]:
    match = re.search(
        r"(\d{1,5})\s*(?:명|팀)?\s*/\s*(\d{1,5})\s*(?:명|팀)?",
        _clean(value).replace(",", ""),
    )
    if not match:
        raise JeongseonContractError("capacity pair changed")
    current, total = int(match.group(1)), int(match.group(2))
    if total < 1 or current < 0 or current > total:
        raise JeongseonContractError("capacity values are invalid")
    return current, total


def _script_value(node: Any) -> str:
    for script in node.find_all("script"):
        script_text = script.string or script.get_text(" ", strip=True)
        matches = re.findall(
            r"`\s*<div>(.*?)</div>\s*`", script_text, flags=re.DOTALL
        )
        values = [
            _clean(BeautifulSoup(item, "html.parser").get_text(" ", strip=True))
            for item in matches
        ]
        values = [item for item in values if item]
        if len(values) > 1:
            raise JeongseonContractError("card script value duplicated")
        if values:
            return values[0]
    return ""


def _card_pairs(item: Any) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for block in item.select("div.nomargin"):
        labels = block.select(":scope > b") or block.find_all("b", recursive=False)
        if len(labels) != 1:
            continue
        key = _clean(labels[0].get_text(" ", strip=True)).replace(" ", "")
        values = block.select(".commonValue")
        if len(values) > 1:
            raise JeongseonContractError("card value duplicated")
        value = _clean(values[0].get_text(" ", strip=True)) if values else ""
        if not value:
            value = _script_value(block)
        if key:
            if key in pairs:
                raise JeongseonContractError("card label duplicated")
            pairs[key] = value
    required = {"신청기간", "교육기간", "강의시간", "요약"}
    if not required <= set(pairs):
        raise JeongseonContractError("card field set changed")
    return pairs


def _source_status(value: Any) -> str:
    text = _clean(value)
    matches = [token for token in _SOURCE_STATUS_TOKENS if token in text]
    if not matches:
        raise JeongseonContractError("public list status changed")
    return matches[0]


def _parse_card(item: Any, page: int) -> dict[str, Any]:
    title_links = item.select("p.tit a.click_move[data-move]")
    if len(title_links) != 1:
        raise JeongseonContractError("course identity link missing or duplicated")
    title_link = title_links[0]
    title = _clean(title_link.get_text(" ", strip=True))
    identity = _clean(title_link.get("data-move"))
    if not title or not re.fullmatch(r"[1-9]\d*", identity):
        raise JeongseonContractError("course title/identity changed")
    pairs = _card_pairs(item)
    apply_start, apply_end = _date_pair(pairs["신청기간"], "application period")
    start, end = _date_pair(pairs["교육기간"], "education period")
    states = item.select(".util .state")
    locations = item.select(".util .loc")
    if len(states) != 1 or len(locations) != 1:
        raise JeongseonContractError("course status/capacity structure changed")
    status = _source_status(states[0].get_text(" ", strip=True))
    capacity_current, capacity_total = _capacity_pair(
        locations[0].get_text(" ", strip=True)
    )
    return {
        "identity": identity,
        "title": title,
        "list_page": page,
        "source_status": status,
        "source_application_period": _clean(pairs["신청기간"]),
        "source_period": _clean(pairs["교육기간"]),
        "source_schedule": _clean(pairs["강의시간"]).replace("|", " "),
        "apply_start": apply_start.isoformat(),
        "apply_end": apply_end.isoformat(),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "raw_url": jeongseon_detail_url(identity),
    }


def _parse_list(soup: BeautifulSoup, requested_page: int) -> _ListPage:
    errors: list[str] = []
    text = _clean(soup.get_text(" ", strip=True))
    summaries = {
        (
            int(total.replace(",", "")),
            int(current),
            int(last),
        )
        for total, current, last in _SUMMARY_RE.findall(text)
    }
    if len(summaries) != 1:
        total, current_page, last_page = 0, requested_page, 1
        errors.append(f"page {requested_page}: total/page summary changed")
    else:
        total, current_page, last_page = next(iter(summaries))
        expected_last = max(1, math.ceil(total / JEONGSEON_PAGE_SIZE))
        if last_page != expected_last:
            errors.append(f"page {requested_page}: advertised last page changed")
    roots = soup.select("div.program_list.apply_type1")
    bodies = soup.select("div.program_list.apply_type1 > ul")
    if len(roots) != 1 or len(bodies) != 1:
        return _ListPage(
            [],
            total,
            current_page,
            last_page,
            errors + [f"page {requested_page}: official course list changed"],
        )
    items = bodies[0].select(":scope > li.clearfix")
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        try:
            rows.append(_parse_card(item, current_page))
        except Exception as exc:
            errors.append(
                f"page {requested_page} row {index + 1}: {_clean(exc)}"
            )
    return _ListPage(rows, total, current_page, last_page, errors)


def _detail_pairs(soup: BeautifulSoup) -> tuple[dict[str, str], list[str]]:
    values: dict[str, set[str]] = {}
    for row in soup.select("table tr"):
        labels = row.find_all("th")
        cells = row.find_all("td")
        if len(labels) != 1 or len(cells) != 1:
            continue
        label = _clean(labels[0].get_text(" ", strip=True))
        if label in _DETAIL_FIELDS:
            values.setdefault(label, set()).add(
                _clean(cells[0].get_text(" ", strip=True))
            )
    errors: list[str] = []
    pairs: dict[str, str] = {}
    for label, candidates in values.items():
        if len(candidates) != 1:
            errors.append(f"detail field {label} conflicted")
        else:
            pairs[label] = next(iter(candidates))
    if set(pairs) != _DETAIL_FIELDS:
        errors.append("detail field set changed")
    return pairs, errors


def _control_label(node: Any) -> str:
    if getattr(node, "name", "") == "input":
        return _clean(node.get("value"))
    return _clean(node.get_text(" ", strip=True))


def _validate_open_form(soup: BeautifulSoup, identity: str) -> list[str]:
    forms = [
        form
        for form in soup.select("form")
        if form.select("input[name='cls_no']")
    ]
    if len(forms) != 1:
        return ["open application form identity missing or duplicated"]
    form = forms[0]
    identity_nodes = form.select("input[name='cls_no']")
    if (
        len(identity_nodes) != 1
        or _clean(identity_nodes[0].get("type")).lower() != "hidden"
        or _clean(identity_nodes[0].get("value")) != identity
    ):
        return ["open application form identity missing or duplicated"]
    errors: list[str] = []
    try:
        action = urlparse(urljoin(JEONGSEON_BASE_URL, _clean(form.get("action"))))
        action_port = action.port
    except ValueError:
        return ["open application form method/action changed"]
    if (
        _clean(form.get("method")).lower() != "post"
        or action.scheme != "https"
        or action.hostname != JEONGSEON_HOST
        or action.username
        or action.password
        or action_port
        or action.fragment
        or action.path != JEONGSEON_APPLICATION_PATH
        or action.query
    ):
        errors.append("open application form method/action changed")
    return errors


def _facility_branch(value: Any) -> str:
    venue = _clean(value)
    facility = _clean(venue.split(">", 1)[0])
    if (
        not facility
        or facility in {"-", "없음", "정선군", "정선군 일원"}
        or (
            re.search(r"(?:로|길)\s*\d", facility)
            and ("정선" in facility or "강원" in facility)
        )
    ):
        return JEONGSEON_BRANCH
    return facility


def _base_output(listed: Mapping[str, Any]) -> dict[str, Any]:
    identity = _clean(listed["identity"])
    return {
        "provider": JEONGSEON_PROVIDER,
        "provider_course_id": f"{JEONGSEON_PROVIDER}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": _clean(listed["title"]),
        "description": _clean(listed["title"]),
        "branch": JEONGSEON_BRANCH,
        "branch_code": "jeongseon:lifelong-learning-center",
        "preserve_branch": True,
        "provider_organizer": JEONGSEON_BRANCH,
        "category": "",
        "program_type": "교육",
        "raw_url": _clean(listed["raw_url"]),
        "application_url": "",
        "application_type": "INFO_ONLY",
        "application_method": "",
        "application_methods": [],
        "reservation_available": False,
        "status": "CLOSED",
        "fee": "",
        "fee_amount": 0,
        "period": _clean(listed["source_period"]),
        "start_date": _clean(listed["start_date"]),
        "end_date": _clean(listed["end_date"]),
        "apply_period": _clean(listed["source_application_period"]),
        "apply_start": _clean(listed["apply_start"]),
        "apply_end": _clean(listed["apply_end"]),
        "schedule_raw": _clean(listed["source_schedule"]),
        "capacity": f"{listed['capacity_current']}/{listed['capacity_total']}",
        "capacity_current": int(listed["capacity_current"]),
        "capacity_total": int(listed["capacity_total"]),
        "capacity_remaining": max(
            int(listed["capacity_total"]) - int(listed["capacity_current"]), 0
        ),
        "target": "",
        "venue": "",
        "venue_name": "",
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": JEONGSEON_PARSER,
        "municipality_code": JEONGSEON_MUNICIPALITY_CODE,
        "municipality_full_name": JEONGSEON_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "list_page": int(listed["list_page"]),
            "source_status": _clean(listed["source_status"]),
            "source_application_period": _clean(
                listed["source_application_period"]
            ),
            "source_period": _clean(listed["source_period"]),
            "source_schedule": _clean(listed["source_schedule"]),
            "source_capacity_current": int(listed["capacity_current"]),
            "source_capacity_total": int(listed["capacity_total"]),
            "source_category": "",
            "source_venue": "",
            "source_detail_status": "",
            "service_family": "education",
            "detail_verified": False,
            "application_control_present": False,
            "application_control_label": "",
            "application_control_identity": "",
            "application_control_method": "",
            "application_control_action": "",
            "application_control_verified": False,
        },
    }


def _validate_detail(
    listed: Mapping[str, Any], soup: BeautifulSoup, cutoff: date
) -> tuple[dict[str, Any], list[str]]:
    row = _base_output(listed)
    identity = _clean(listed["identity"])
    label = f"course {identity} detail"
    errors: list[str] = []
    pairs, pair_errors = _detail_pairs(soup)
    errors.extend(f"{label}: {item}" for item in pair_errors)
    if set(pairs) == _DETAIL_FIELDS:
        if _clean(pairs["강좌명"]) != _clean(listed["title"]):
            errors.append(f"{label}: title list/detail mismatch")
        for field, detail_value, listed_value in (
            ("신청기간", pairs["신청기간"], listed["source_application_period"]),
            ("교육기간", pairs["교육기간"], listed["source_period"]),
        ):
            try:
                if _date_pair(detail_value, f"{label} {field}") != tuple(
                    date.fromisoformat(item)
                    for item in (
                        (listed["apply_start"], listed["apply_end"])
                        if field == "신청기간"
                        else (listed["start_date"], listed["end_date"])
                    )
                ):
                    errors.append(f"{label}: {field} list/detail mismatch")
            except Exception as exc:
                errors.append(_clean(exc))
        try:
            detail_current, detail_total = _capacity_pair(pairs["정원"])
            if (detail_current, detail_total) != (
                int(listed["capacity_current"]),
                int(listed["capacity_total"]),
            ):
                errors.append(f"{label}: capacity list/detail mismatch")
        except Exception as exc:
            errors.append(_clean(exc))
        detail_schedule = _clean(pairs["교육시간"]).replace("|", " ")
        if _normalized(detail_schedule) != _normalized(listed["source_schedule"]):
            errors.append(f"{label}: schedule list/detail mismatch")
        category = _clean(pairs["분야"])
        if category not in set(JEONGSEON_QUARTERS.values()):
            errors.append(f"{label}: category left official partitions")
        row["category"] = category
        venue = _clean(pairs["위치"])
        if not venue:
            errors.append(f"{label}: venue missing")
        branch = _facility_branch(venue)
        row["branch"] = branch
        row["branch_code"] = f"jeongseon:{_normalized(branch)}"
        row["venue"] = row["venue_name"] = venue
        row["raw_fields"].update(
            {
                "source_category": category,
                "source_venue": row["venue"],
                "source_detail_status": _clean(pairs["강의상태"]),
            }
        )

        control_nodes = [
            node
            for node in soup.select("button, a, input[type='submit'], input[type='button']")
            if _control_label(node) in (_ACTION_LABELS | _INACTIVE_LABELS)
        ]
        action_nodes = [
            node for node in control_nodes if _control_label(node) in _ACTION_LABELS
        ]
        inactive_nodes = [
            node for node in control_nodes if _control_label(node) in _INACTIVE_LABELS
        ]
        source_status = _clean(listed["source_status"])
        apply_start = date.fromisoformat(_clean(listed["apply_start"]))
        apply_end = date.fromisoformat(_clean(listed["apply_end"]))
        if source_status in _OPEN_SOURCE_STATUSES:
            if len(action_nodes) != 1 or inactive_nodes:
                errors.append(f"{label}: open application control changed")
            if not (apply_start <= cutoff <= apply_end):
                errors.append(f"{label}: open status/application dates mismatch")
            errors.extend(
                f"{label}: {item}"
                for item in _validate_open_form(soup, identity)
            )
            if not errors:
                row.update(
                    {
                        "application_url": _clean(listed["raw_url"]),
                        "application_type": "ONLINE_RESERVATION",
                        "application_method": "온라인",
                        "application_methods": ["온라인"],
                        "reservation_available": True,
                        "status": "OPEN",
                    }
                )
                row["raw_fields"].update(
                    {
                        "application_control_present": True,
                        "application_control_label": _control_label(
                            action_nodes[0]
                        ),
                        "application_control_identity": identity,
                        "application_control_method": "POST",
                        "application_control_action": JEONGSEON_APPLICATION_PATH,
                    }
                )
        else:
            if action_nodes:
                errors.append(f"{label}: inactive course exposes application control")
            if len(inactive_nodes) != 1:
                errors.append(f"{label}: inactive application marker changed")
            if source_status in {"접수예정", "신청예정"} and cutoff < apply_start:
                row["status"] = "SCHEDULED"
            else:
                row["status"] = "CLOSED"
            if inactive_nodes:
                row["raw_fields"]["application_control_label"] = _control_label(
                    inactive_nodes[0]
                )
    row["raw_fields"]["detail_verified"] = not errors
    row["raw_fields"]["application_control_verified"] = not errors
    return row, errors


def _row_signature(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        _clean(row.get("identity")),
        _clean(row.get("title")),
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
        _clean(row.get("source_status")),
    )


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    return tuple(_row_signature(row) for row in rows)


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
            if key not in {"raw_url", "application_url", "venue", "venue_name"}
        }
    )
    if _PHONE_RE.search(payload) or _EMAIL_RE.search(payload):
        errors.append("PII-like contact data persisted")
    if row.get("description") != row.get("title"):
        errors.append("arbitrary free-form detail persisted")
    if _clean(row.get("raw_fields", {}).get("service_family")) != "education":
        errors.append("non-education row reached education persistence")
    return errors


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity in seen:
            continue
        seen.add(identity)
        result.append(row)
    return result


def _base_meta(error: str = "") -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "boundary_requests": 0,
        "stability_rechecks": 0,
        "partition_requests": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "source_cap_reached": False,
        "pagination_complete": False,
        "partitions_complete": False,
        "details_complete": False,
        "application_controls_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "returned_count": 0,
        "configured_collection_error": error,
    }


def collect_jeongseon_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 100,
    detail_limit: int = 500,
    today: Optional[date | datetime | str] = None,
    max_workers: int = JEONGSEON_MAX_WORKERS,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect a complete current/future Jeongseon education snapshot."""

    meta = _base_meta()
    if not is_jeongseon_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match registered Jeongseon education owner"
        )
        return [], JEONGSEON_PARSER, meta
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
                "configured_collection_error": (
                    "invalid timeout/max_pages/detail_limit/max_workers cap"
                ),
            }
        )
        return [], JEONGSEON_PARSER, meta
    try:
        cutoff = _today(today)
    except ValueError as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], JEONGSEON_PARSER, meta
    factory = session_factory or _default_session_factory
    current_fetcher = fetcher or _default_fetcher
    errors: list[str] = []

    initial, fetch_errors = _fetch_parse_many(
        [
            (
                ("list", 1, "data"),
                jeongseon_all_list_url(1),
                lambda soup: _parse_list(soup, 1),
            )
        ],
        fetcher=current_fetcher,
        session_factory=factory,
        timeout=timeout,
        max_workers=max_workers,
    )
    errors.extend(fetch_errors)
    meta["pages"] += len(initial)
    meta["list_requests"] += len(initial)
    first = initial.get(("list", 1, "data"))
    if not isinstance(first, _ListPage):
        errors.append("page 1: official unfiltered source unavailable")
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], JEONGSEON_PARSER, meta
    errors.extend(first.errors)
    total, last = first.total, first.last_page
    required = last + 6
    meta.update(
        {
            "source_total": total,
            "declared_pages": last,
            "required_list_requests": required,
        }
    )
    if required > max_pages:
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": (
                    f"max_pages cap allows {max_pages} of {required} "
                    "required list requests"
                ),
            }
        )
        return [], JEONGSEON_PARSER, meta
    if errors:
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], JEONGSEON_PARSER, meta

    items: list[tuple[Any, str, Callable[[BeautifulSoup], Any]]] = []
    for page in range(2, last + 1):
        items.append(
            (
                ("list", page, "data"),
                jeongseon_all_list_url(page),
                lambda soup, requested=page: _parse_list(soup, requested),
            )
        )
    items.extend(
        [
            (
                ("list", last + 1, "boundary"),
                jeongseon_all_list_url(last + 1),
                lambda soup, requested=last + 1: _parse_list(soup, requested),
            ),
            (
                ("list", 1, "recheck"),
                jeongseon_all_list_url(1),
                lambda soup: _parse_list(soup, 1),
            ),
            (
                ("list", last, "last_recheck"),
                jeongseon_all_list_url(last),
                lambda soup, requested=last: _parse_list(soup, requested),
            ),
        ]
    )
    for quarter in JEONGSEON_QUARTERS:
        items.append(
            (
                ("partition", quarter, "audit"),
                jeongseon_partition_url(quarter),
                lambda soup: _parse_list(soup, 1),
            )
        )
    remaining, fetch_errors = _fetch_parse_many(
        items,
        fetcher=current_fetcher,
        session_factory=factory,
        timeout=timeout,
        max_workers=max_workers,
    )
    errors.extend(fetch_errors)
    meta["pages"] += len(remaining)
    meta["list_requests"] += len(remaining)
    meta["boundary_requests"] = int(
        ("list", last + 1, "boundary") in remaining
    )
    meta["stability_rechecks"] = int(("list", 1, "recheck") in remaining)
    meta["stability_rechecks"] += int(
        ("list", last, "last_recheck") in remaining
    )
    meta["partition_requests"] = sum(
        ("partition", quarter, "audit") in remaining
        for quarter in JEONGSEON_QUARTERS
    )

    all_rows: list[dict[str, Any]] = []
    signatures: dict[int, tuple[tuple[Any, ...], ...]] = {}
    page_counts: dict[int, int] = {}
    parsed_pages: dict[int, _ListPage] = {}
    for page in range(1, last + 1):
        parsed = first if page == 1 else remaining.get(("list", page, "data"))
        if not isinstance(parsed, _ListPage):
            errors.append(f"page {page}: response missing")
            continue
        parsed_pages[page] = parsed
        errors.extend(parsed.errors)
        if (parsed.total, parsed.current_page, parsed.last_page) != (
            total,
            page,
            last,
        ):
            errors.append(f"page {page}: total/page/last changed")
        expected = (
            JEONGSEON_PAGE_SIZE
            if page < last
            else total - (last - 1) * JEONGSEON_PAGE_SIZE
        )
        if total == 0:
            expected = 0
        if len(parsed.rows) != expected:
            errors.append(
                f"page {page}: expected {expected} rows, got {len(parsed.rows)}"
            )
        signatures[page] = _page_signature(parsed.rows)
        page_counts[page] = len(parsed.rows)
        all_rows.extend(parsed.rows)

    boundary_mode = ""
    boundary = remaining.get(("list", last + 1, "boundary"))
    if not isinstance(boundary, _ListPage):
        errors.append(f"page {last + 1}: boundary response missing")
    else:
        errors.extend(boundary.errors)
        if boundary.total != total or boundary.last_page != last:
            errors.append(f"page {last + 1}: boundary total/last changed")
        elif not boundary.rows and boundary.current_page == last + 1:
            boundary_mode = "empty_sentinel"
        elif (
            boundary.current_page == last
            and _page_signature(boundary.rows) == signatures.get(last, ())
        ):
            boundary_mode = "exact_last_page_clamp"
        else:
            errors.append(f"page {last + 1}: boundary is neither sentinel nor clamp")
    recheck = remaining.get(("list", 1, "recheck"))
    if not isinstance(recheck, _ListPage):
        errors.append("page 1: stability response missing")
    else:
        errors.extend(recheck.errors)
        if (
            (recheck.total, recheck.current_page, recheck.last_page)
            != (total, 1, last)
            or _page_signature(recheck.rows) != signatures.get(1, ())
        ):
            errors.append("page-one stability recheck changed")

    last_recheck = remaining.get(("list", last, "last_recheck"))
    if not isinstance(last_recheck, _ListPage):
        errors.append(f"page {last}: last-page stability response missing")
    else:
        errors.extend(last_recheck.errors)
        if (
            (last_recheck.total, last_recheck.current_page, last_recheck.last_page)
            != (total, last, last)
            or _page_signature(last_recheck.rows) != signatures.get(last, ())
        ):
            errors.append("last-page stability recheck changed")

    identities = [_clean(row["identity"]) for row in all_rows]
    identity_duplicate_count = len(identities) - len(set(identities))
    if identity_duplicate_count:
        errors.append(f"{identity_duplicate_count} duplicate source identities")
    partition_errors: list[str] = []
    partition_declared_counts: dict[str, int] = {}
    partition_first_page_identity_counts: dict[str, int] = {}
    partition_first_page_identities: set[str] = set()
    all_identity_set = set(identities)
    for quarter in JEONGSEON_QUARTERS:
        parsed = remaining.get(("partition", quarter, "audit"))
        if not isinstance(parsed, _ListPage):
            partition_errors.append(f"partition {quarter}: response missing")
            continue
        partition_errors.extend(
            f"partition {quarter}: {item}" for item in parsed.errors
        )
        expected_last = max(
            1, math.ceil(parsed.total / JEONGSEON_PAGE_SIZE)
        )
        expected_rows = min(JEONGSEON_PAGE_SIZE, parsed.total)
        if (parsed.current_page, parsed.last_page) != (1, expected_last):
            partition_errors.append(
                f"partition {quarter}: first-page boundary changed"
            )
        if len(parsed.rows) != expected_rows:
            partition_errors.append(
                f"partition {quarter}: expected {expected_rows} first-page rows, "
                f"got {len(parsed.rows)}"
            )
        partition_ids = [_clean(row["identity"]) for row in parsed.rows]
        if len(partition_ids) != len(set(partition_ids)):
            partition_errors.append(
                f"partition {quarter}: duplicate first-page identities"
            )
        if not set(partition_ids) <= all_identity_set:
            partition_errors.append(
                f"partition {quarter}: identities left the unfiltered catalogue"
            )
        if partition_first_page_identities & set(partition_ids):
            partition_errors.append(
                f"partition {quarter}: identity overlaps another partition"
            )
        partition_first_page_identities.update(partition_ids)
        partition_declared_counts[quarter] = parsed.total
        partition_first_page_identity_counts[quarter] = len(partition_ids)
    if (
        len(partition_declared_counts) != len(JEONGSEON_QUARTERS)
        or sum(partition_declared_counts.values()) != total
    ):
        partition_errors.append(
            "three partition totals do not equal the unfiltered total"
        )
    errors.extend(partition_errors)
    partitions_complete = bool(
        not partition_errors
        and len(partition_declared_counts) == len(JEONGSEON_QUARTERS)
        and sum(partition_declared_counts.values()) == total
    )
    current_rows = [
        row
        for row in all_rows
        if date.fromisoformat(_clean(row["end_date"])) >= cutoff
    ]
    list_complete = bool(
        not errors
        and len(all_rows) == total
        and meta["list_requests"] == required
        and meta["boundary_requests"] == 1
        and meta["stability_rechecks"] == 2
        and meta["partition_requests"] == len(JEONGSEON_QUARTERS)
        and partitions_complete
        and bool(boundary_mode)
    )
    if len(current_rows) > detail_limit:
        meta["source_cap_reached"] = True
        errors.append(
            f"detail_limit cap allows {detail_limit} of "
            f"{len(current_rows)} required current details"
        )

    detailed_rows: list[dict[str, Any]] = []
    detail_errors: list[str] = []
    if list_complete and not errors:
        detail_items = [
            (
                ("detail", _clean(row["identity"])),
                _clean(row["raw_url"]),
                lambda soup, listed=dict(row): _validate_detail(
                    listed, soup, cutoff
                ),
            )
            for row in current_rows
        ]
        meta["detail_attempts"] = len(detail_items)
        details, detail_fetch_errors = _fetch_parse_many(
            detail_items,
            fetcher=current_fetcher,
            session_factory=factory,
            timeout=timeout,
            max_workers=max_workers,
        )
        detail_errors.extend(detail_fetch_errors)
        meta["pages"] += len(details)
        for listed in current_rows:
            identity = _clean(listed["identity"])
            value = details.get(("detail", identity))
            if not isinstance(value, tuple) or len(value) != 2:
                detail_errors.append(f"course {identity}: detail response missing")
                continue
            detailed, item_errors = value
            if item_errors:
                detail_errors.extend(item_errors)
            else:
                detailed_rows.append(detailed)
                meta["detail_pages"] += 1
    errors.extend(detail_errors)
    meta["detail_errors"] = len(detail_errors)
    details_complete = bool(
        list_complete
        and meta["detail_attempts"] == len(current_rows)
        and meta["detail_pages"] == len(current_rows)
        and not detail_errors
    )
    application_controls_complete = bool(
        details_complete
        and all(
            bool(row["raw_fields"].get("application_control_verified"))
            for row in detailed_rows
        )
    )

    result: list[dict[str, Any]] = []
    if details_complete and application_controls_complete and not errors:
        for row in detailed_rows:
            errors.extend(_privacy_errors(row))
        if not errors:
            deduper = dedupe_rows or _dedupe_default
            try:
                result = list(deduper(detailed_rows))
            except Exception as exc:
                errors.append(f"dedupe failed: {_clean(exc)}")
            if len(result) != len(detailed_rows):
                errors.append(
                    "dedupe changed official identity cardinality "
                    f"{len(detailed_rows)} to {len(result)}"
                )
                result = []
            else:
                for row in result:
                    errors.extend(_privacy_errors(row))
                if errors:
                    result = []
    snapshot_complete = bool(
        list_complete
        and details_complete
        and application_controls_complete
        and not errors
    )
    if not snapshot_complete:
        result = []
    meta.update(
        {
            "ownership_scope": JEONGSEON_OWNERSHIP_SCOPE,
            "registered_target_url": JEONGSEON_REGISTERED_URL,
            "canonical_url": JEONGSEON_ALL_URL,
            "boundary_mode": boundary_mode,
            "page_counts": page_counts,
            "source_rows": len(all_rows),
            "current_source_count": len(current_rows),
            "expired_source_count": len(all_rows) - len(current_rows),
            "identity_duplicate_count": identity_duplicate_count,
            "partition_declared_counts": partition_declared_counts,
            "partition_first_page_identity_counts": (
                partition_first_page_identity_counts
            ),
            "source_status_counts": dict(
                Counter(_clean(row["source_status"]) for row in all_rows)
            ),
            "category_counts": dict(
                Counter(_clean(row["category"]) for row in result)
            ),
            "status_counts": dict(Counter(_clean(row["status"]) for row in result)),
            "branch_counts": dict(
                Counter(_clean(row["branch"]) for row in result)
            ),
            "venue_counts": dict(
                Counter(_clean(row["venue"]) for row in result)
            ),
            "online_open_count": sum(
                row.get("reservation_available") is True
                and row.get("application_type") == "ONLINE_RESERVATION"
                for row in result
            ),
            "pagination_complete": list_complete,
            "partitions_complete": partitions_complete,
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
            "municipality_coverage": [JEONGSEON_MUNICIPALITY_CODE],
            "candidate_audit": {
                key: dict(value)
                for key, value in JEONGSEON_CANDIDATE_AUDIT.items()
            },
            "discovery_audit": dict(JEONGSEON_DISCOVERY_AUDIT),
            "pii_fields_discarded": list(JEONGSEON_PII_FIELDS_DISCARDED),
            "pii_payload_persisted": False,
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
    )
    return result, JEONGSEON_PARSER, meta


collect = collect_jeongseon_education


__all__ = [
    "JEONGSEON_ALL_URL",
    "JEONGSEON_APPLICATION_PATH",
    "JEONGSEON_BASE_URL",
    "JEONGSEON_BRANCH",
    "JEONGSEON_CANONICAL_CANDIDATE_ID",
    "JEONGSEON_CANDIDATE_AUDIT",
    "JEONGSEON_DISCOVERY_AUDIT",
    "JEONGSEON_EDUCATION_SUPPORT_BOARD_URL",
    "JEONGSEON_EXCLUDED_CANDIDATE_IDS",
    "JEONGSEON_GENERAL_HOMEPAGE_URL",
    "JEONGSEON_HOST",
    "JEONGSEON_MUNICIPALITY_CODE",
    "JEONGSEON_MUNICIPALITY_NAME",
    "JEONGSEON_PAGE_SIZE",
    "JEONGSEON_PARSER",
    "JEONGSEON_PII_FIELDS_DISCARDED",
    "JEONGSEON_PROVIDER",
    "JEONGSEON_QUARTERS",
    "JEONGSEON_REGISTERED_URL",
    "JEONGSEON_WRONG_MUNICIPALITY_URL",
    "JeongseonContractError",
    "collect",
    "collect_jeongseon_education",
    "is_jeongseon_education_target",
    "is_jeongseon_excluded_candidate",
    "is_jeongseon_unfiltered_source",
    "jeongseon_all_list_url",
    "jeongseon_detail_url",
    "jeongseon_partition_url",
]
