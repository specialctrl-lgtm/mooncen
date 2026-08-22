"""Fail-closed collector for Bupyeong integrated-reservation education.

The official portal exposes five catalogues through one reservation service:
resident centres, the lifelong-learning centre, two e-learning centres,
municipal sports, and the women's centre.  The backing controllers retain all
history, so collecting only the first pages silently loses long-running
courses.  This collector exhausts every declared page, validates the server's
last-page clamp, rechecks page one, and enriches every current/future course
from its detail page.  Any cap, ownership, pagination, identity, date, branch,
or detail mismatch suppresses the entire snapshot.

The lifelong-learning slice is the same source exposed by the legacy
``MUNI_WWW_ICBP_GO_KR_D1804D5E`` target.  Production scheduling must use the
integrated provider as the canonical owner and must not schedule that legacy
target in parallel.

This module intentionally is not wired into ``Crawler_MunicipalYaml``.  The
shared router injects its managed session/fetch/dedupe helpers when promotion
is approved.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
import re
import threading
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


BUPYEONG_PROVIDER = "MUNI_WWW_ICBP_GO_KR_61AE4CB0"
BUPYEONG_CANDIDATE_ID = "MUNI_IR_EDC1B2761F14"
BUPYEONG_URL = "https://www.icbp.go.kr/reservation/"
BUPYEONG_HOST = "www.icbp.go.kr"
BUPYEONG_PATH = "/reservation/"
BUPYEONG_MUNICIPALITY_CODE = "2823700000"
BUPYEONG_MUNICIPALITY_NAME = "인천광역시 부평구"
BUPYEONG_PARSER = (
    "bupyeong_integrated_five_education_catalogues+complete_pages+"
    "last_page_clamps+page_one_rechecks+current_details"
)
BUPYEONG_PAGE_SIZE = 12
BUPYEONG_MAX_WORKERS = 4
BUPYEONG_SESSION_REQUEST_LIMIT = 70

Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


@dataclass(frozen=True)
class BupyeongCategory:
    key: str
    label: str
    entry_path: str
    controller_path: str
    default_branch: str

    @property
    def list_path(self) -> str:
        return f"{self.controller_path}/lectureList.do"

    @property
    def detail_path(self) -> str:
        return f"{self.controller_path}/lectureDetail.do"

    @property
    def application_path(self) -> str:
        return f"{self.controller_path}/traineeInsertForm.do"


BUPYEONG_CATEGORIES = (
    BupyeongCategory(
        "dong", "주민자치센터", "/reservation/education/dong.jsp", "/lecture", ""
    ),
    BupyeongCategory(
        "lll",
        "평생학습관",
        "/reservation/education/lll.jsp",
        "/lecturelll",
        "부평구평생학습관",
    ),
    BupyeongCategory(
        "elearning",
        "백운·청천e배움터",
        "/reservation/education/elearning.jsp",
        "/learning",
        "",
    ),
    BupyeongCategory(
        "physical",
        "생활체육",
        "/reservation/education/physical.jsp",
        "/lectureetc",
        "부평구 생활체육",
    ),
    BupyeongCategory(
        "woman",
        "여성센터",
        "/reservation/education/woman.jsp",
        "/lectureetc",
        "부평구 여성센터",
    ),
)
_CATEGORY_BY_KEY = {item.key: item for item in BUPYEONG_CATEGORIES}

_DONG_BRANCHES = frozenset(
    [f"부평{number}동" for number in range(1, 7)]
    + [f"산곡{number}동" for number in range(1, 5)]
    + [f"청천{number}동" for number in range(1, 3)]
    + [f"갈산{number}동" for number in range(1, 3)]
    + [f"삼산{number}동" for number in range(1, 3)]
    + [f"부개{number}동" for number in range(1, 4)]
    + ["일신동"]
    + [f"십정{number}동" for number in range(1, 3)]
)
_ELEARNING_BRANCHES = {
    "백운": "백운 e-배움터",
    "청천": "청천 e-배움터",
}

# A small set of closed legacy records has a blank, one-sided, or invalid
# education range in both list and detail.  An exact per-catalogue allow-list
# prevents a newly-created undated record from being silently treated as
# historical.  Compact but valid ``YYYYMMDD`` history is parsed normally.
_LEGACY_UNDATED_IDS: Mapping[str, frozenset[str]] = {
    "dong": frozenset(
        {
            "16756",
            "16156",
            "14512",
            "14510",
            "13876",
            "13857",
            "13734",
            "13733",
            "13732",
            "13731",
            "13729",
            "13728",
            "13727",
            "13726",
            "13725",
            "10629",
            "10628",
        }
    ),
    "lll": frozenset({"13344"}),
    "elearning": frozenset({"12460"}),
    "physical": frozenset(),
    "woman": frozenset(),
}

_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(
    r"(?<!\d)(?:(20\d{2})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})|"
    r"((?:19|20)\d{6}))(?!\d)"
)
_ID_RE = re.compile(r"\d+")
_PAGE_RE = re.compile(r"(?:[?&])nowPage=(\d+)(?:&|$)")
_STATUS_MAP: Mapping[str, str] = {
    "접수중": "OPEN",
    "접수예정": "SCHEDULED",
    "접수마감": "CLOSED",
}

_COMMON_DETAIL_REQUIRED = {
    "교육대상",
    "접수방법",
    "접수기간",
    "교육기간",
    "수강료",
    "문의전화",
}
_DETAIL_REQUIRED: Mapping[str, frozenset[str]] = {
    "dong": frozenset(
        _COMMON_DETAIL_REQUIRED
        | {"교육기관", "추첨방법", "신청정원", "교육장소", "강사"}
    ),
    "lll": frozenset(
        _COMMON_DETAIL_REQUIRED
        | {"교육기관", "추첨방법", "신청정원", "교육장소", "강사"}
    ),
    "elearning": frozenset(
        _COMMON_DETAIL_REQUIRED
        | {"교육요일", "접수인원", "강의내용"}
    ),
    "physical": frozenset(
        _COMMON_DETAIL_REQUIRED
        | {"교육기관", "추첨방법", "신청정원", "교육장소", "강사"}
    ),
    "woman": frozenset(
        _COMMON_DETAIL_REQUIRED
        | {"교육기관", "추첨방법", "신청정원", "교육장소", "강사"}
    ),
}


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", _clean(value))


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def is_bupyeong_target(target: Any) -> bool:
    """Accept only the canonical provider-owned reservation root."""

    if _provider(target) != BUPYEONG_PROVIDER:
        return False
    parsed = urlparse(_clean(_target_value(target, "url")))
    return (
        parsed.scheme.lower() == "https"
        and parsed.hostname == BUPYEONG_HOST
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == BUPYEONG_PATH
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


is_target = is_bupyeong_target


def bupyeong_list_url(category: BupyeongCategory | str, page: int = 1) -> str:
    item = _CATEGORY_BY_KEY[category] if isinstance(category, str) else category
    query = urlencode(
        (("sitediv", item.key), ("cd", "reservation"), ("nowPage", str(max(1, int(page)))))
    )
    return f"https://{BUPYEONG_HOST}{item.list_path}?{query}"


def bupyeong_detail_url(category: BupyeongCategory | str, identity: Any) -> str:
    item = _CATEGORY_BY_KEY[category] if isinstance(category, str) else category
    source_id = _clean(identity)
    if not _ID_RE.fullmatch(source_id):
        return ""
    query = urlencode((("lecseq", source_id), ("sitediv", item.key), ("cd", "reservation")))
    return f"https://{BUPYEONG_HOST}{item.detail_path}?{query}"


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/125.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            "Referer": BUPYEONG_URL,
        }
    )
    return current


def _default_fetcher(current_session: Any, url: str, timeout: int) -> Any:
    response = current_session.get(url, timeout=timeout, allow_redirects=False)
    if int(getattr(response, "status_code", 0)) != 200:
        raise ValueError(f"unexpected HTTP status {getattr(response, 'status_code', None)}")
    if getattr(response, "headers", {}).get("Location"):
        raise ValueError("redirect response is not accepted")
    if not getattr(response, "content", b""):
        raise ValueError("empty HTTP response")
    return response


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, bytes):
        return BeautifulSoup(value, "lxml")
    if isinstance(value, str):
        return BeautifulSoup(value, "lxml")
    content = getattr(value, "content", None)
    if content is None:
        raise TypeError("fetcher did not return HTML or an HTTP response")
    return BeautifulSoup(content, "lxml")


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value)[:10])
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _dates(value: Any) -> list[date]:
    result: list[date] = []
    for match in _DATE_RE.finditer(_clean(value)):
        compact = match.group(4)
        if compact:
            result.append(
                date(int(compact[:4]), int(compact[4:6]), int(compact[6:8]))
            )
        else:
            result.append(
                date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            )
    return result


def _date_period(value: Any, *, exact: bool = True) -> tuple[str, str, str]:
    text = _clean(value)
    found = list(_DATE_RE.finditer(text))
    if exact and len(found) != 2:
        raise ValueError(f"expected exactly two dates, found {len(found)} in {text!r}")
    if len(found) < 2:
        return "", "", ""
    values = _dates(text)
    start = values[0].isoformat()
    end = values[1].isoformat()
    schedule = _clean(text[found[1].end() :]).lstrip("~～- ")
    return start, end, schedule


def _application_period(value: Any) -> tuple[str, str]:
    values = _dates(value)
    if len(values) < 2:
        return "", ""
    return values[0].isoformat(), values[-1].isoformat()


def _channel_periods(value: Any) -> dict[str, tuple[str, str]]:
    """Return channel-specific ranges from a combined reception field."""

    text = _clean(value)
    markers = list(re.finditer(r"(온라인|방문|전화)\s*:", text))
    result: dict[str, tuple[str, str]] = {}
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        values = _dates(text[marker.end() : end])
        if len(values) >= 2:
            result[marker.group(1)] = (
                values[0].isoformat(),
                values[-1].isoformat(),
            )
    if not result:
        start, end = _application_period(text)
        if start and end:
            result["전체"] = (start, end)
    return result


def _normalized_label(value: Any) -> str:
    return re.sub(r"\s+", "", _clean(value).rstrip(":："))


def _detail_pairs(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for term in soup.select(".board_view dl dt, .data_cell dl dt"):
        value = term.find_next_sibling("dd")
        key = _normalized_label(term.get_text(" ", strip=True))
        if not key or value is None or key in pairs:
            continue
        pairs[key] = _clean(value.get_text(" ", strip=True))
    return pairs


def _list_pairs(item: Any) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for node in item.select("li"):
        label = node.select_one("span.wfont")
        if label is None:
            continue
        raw_label = _clean(label.get_text(" ", strip=True))
        key = _normalized_label(raw_label.split(":", 1)[0])
        if not key:
            continue
        full = _clean(node.get_text(" ", strip=True))
        value = _clean(full.split(":", 1)[1]) if ":" in full else ""
        pairs[key] = value
    return pairs


def _exact_course_url(url: str, category: BupyeongCategory, path: str) -> bool:
    parsed = urlparse(_clean(url))
    query = parse_qs(parsed.query, keep_blank_values=True)
    return (
        parsed.scheme.lower() == "https"
        and parsed.hostname == BUPYEONG_HOST
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == path
        and not parsed.params
        and not parsed.fragment
        and set(query) == {"lecseq", "sitediv", "cd"}
        and len(query["lecseq"]) == 1
        and bool(_ID_RE.fullmatch(query["lecseq"][0]))
        and query["sitediv"] == [category.key]
        and query["cd"] == ["reservation"]
    )


def _course_identity(url: str, category: BupyeongCategory, path: str) -> str:
    if not _exact_course_url(url, category, path):
        return ""
    return parse_qs(urlparse(url).query, keep_blank_values=True)["lecseq"][0]


def _title_owned(soup: BeautifulSoup, category: Optional[BupyeongCategory] = None) -> bool:
    title = _compact(soup.title.get_text(" ", strip=True) if soup.title else "")
    if "인천광역시부평구청통합예약서비스" not in title:
        return False
    return category is None or _compact(category.label) in title


def _root_owned(soup: BeautifulSoup) -> bool:
    title = _compact(soup.title.get_text(" ", strip=True) if soup.title else "")
    if "인천광역시부평구통합예약서비스" not in title:
        return False
    exposed: set[str] = set()
    for anchor in soup.select("a[href]"):
        parsed = urlparse(urljoin(BUPYEONG_URL, _clean(anchor.get("href"))))
        if parsed.scheme == "https" and parsed.hostname == BUPYEONG_HOST:
            exposed.add(parsed.path)
    return {item.entry_path for item in BUPYEONG_CATEGORIES}.issubset(exposed)


def _page_contract(soup: BeautifulSoup, category: BupyeongCategory) -> tuple[int, int] | None:
    if not _title_owned(soup, category):
        return None
    selected_nodes = soup.select(".paging.dp_pc a.num.select[href*='nowPage=']")
    last_nodes = soup.select(".paging.dp_pc a.btn_last[href*='nowPage=']")
    if len(selected_nodes) != 1 or len(last_nodes) != 1:
        return None
    selected_match = _PAGE_RE.search(_clean(selected_nodes[0].get("href")))
    last_match = _PAGE_RE.search(_clean(last_nodes[0].get("href")))
    if selected_match is None or last_match is None:
        return None
    selected = int(selected_match.group(1))
    declared_last = int(last_match.group(1))
    if selected < 1 or declared_last < selected:
        return None
    return selected, declared_last


def _branch(
    category: BupyeongCategory,
    title: str,
    pairs: Mapping[str, str],
    *,
    historical: bool,
) -> str:
    if category.key == "dong":
        value = _clean(pairs.get("교육기관"))
        if value not in _DONG_BRANCHES:
            if historical:
                return "부평구 주민자치센터 (과거)"
            raise ValueError(f"unknown resident-centre branch {value!r}")
        return value
    if category.key == "elearning":
        prefix = _clean(title).split(" ", 1)[0]
        if prefix not in _ELEARNING_BRANCHES:
            if historical:
                return "백운·청천 e-배움터 (과거)"
            raise ValueError(f"unknown e-learning branch prefix {prefix!r}")
        return _ELEARNING_BRANCHES[prefix]
    if category.key == "physical" and _clean(pairs.get("교육기관")) != "생활체육":
        raise ValueError("physical list ownership changed")
    if category.key == "woman" and _clean(pairs.get("교육기관")) not in {"", "여성센터"}:
        raise ValueError("women's-centre list ownership changed")
    return category.default_branch


def _base_row(
    target: Any,
    category: BupyeongCategory,
    identity: str,
    title: str,
    raw_url: str,
) -> dict[str, Any]:
    provider = _provider(target)
    return {
        "provider": provider,
        "provider_course_id": f"{provider}:lecture:{identity}"[:100],
        "prefer_incoming_provider_course_id": True,
        "title": _clean(title),
        "raw_url": raw_url,
        "collection_type": "static_html",
        "collection_category": "공공예약",
        "domain_category": "교육",
        "source_group": "municipal_reservation",
        "operator_type": "지자체/공공기관",
        "service_group": "공공강좌",
        "program_type": "강좌",
        "category": category.label,
        "branch_url": f"https://{BUPYEONG_HOST}{category.entry_path}",
        "preserve_branch": True,
    }


def _parse_list_page(
    target: Any,
    soup: BeautifulSoup,
    category: BupyeongCategory,
    expected_page: int,
    declared_last: int,
    cutoff: date,
) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    if _page_contract(soup, category) != (expected_page, declared_last):
        return [], ["pagination/ownership contract mismatch"]
    rows: list[dict[str, Any]] = []
    anchors = soup.select("p.tit a[href*='lectureDetail.do']")
    for anchor in anchors:
        item = anchor.find_parent("li")
        title = _clean(anchor.get_text(" ", strip=True))
        raw_url = urljoin(BUPYEONG_URL, _clean(anchor.get("href")))
        identity = _course_identity(raw_url, category, category.detail_path)
        prefix = f"row {identity or '?'}"
        if item is None or not title or not identity:
            errors.append(f"{prefix}: malformed title/detail identity")
            continue
        status_text = _clean(
            (item.select_one(".tag_state") or item).get_text(" ", strip=True)
        )
        status = _STATUS_MAP.get(status_text, "")
        if not status:
            errors.append(f"{prefix}: unknown status {status_text!r}")
            continue
        pairs = _list_pairs(item)
        period_text = pairs.get("교육기간", "") if category.key == "elearning" else pairs.get("교육", "")
        apply_text = pairs.get("접수기간", "") if category.key == "elearning" else pairs.get("접수", "")
        historical_invalid = False
        try:
            try:
                period_dates = _dates(period_text)
            except ValueError:
                period_dates = []
            if len(period_dates) != 2:
                if (
                    identity not in _LEGACY_UNDATED_IDS[category.key]
                    or status != "CLOSED"
                ):
                    raise ValueError("unapproved undated or invalid-date course")
                start = end = schedule = ""
                historical_invalid = True
                historical = True
            else:
                start, end, schedule = _date_period(period_text)
                historical = date.fromisoformat(end) < cutoff
                if not historical and date.fromisoformat(end) < date.fromisoformat(start):
                    raise ValueError("current/future course range is reversed")
            branch = _branch(
                category, title, pairs, historical=historical
            )
        except (TypeError, ValueError) as exc:
            errors.append(f"{prefix}: {exc}")
            continue
        apply_start, apply_end = _application_period(apply_text)
        row = _base_row(target, category, identity, title, raw_url)
        row.update(
            {
                "branch": branch,
                "branch_code": branch,
                "status": status,
                "period": f"{start} ~ {end}" if start and end else "",
                "start_date": start,
                "end_date": end,
                "apply_period": (
                    f"{apply_start} ~ {apply_end}" if apply_start and apply_end else ""
                ),
                "schedule_raw": schedule,
                "fee": _clean(pairs.get("수강료")),
                "material_fee": _clean(pairs.get("재료비")),
                "venue_name": _clean(pairs.get("교육장소")),
                "description": _clean(item.get_text(" ", strip=True))[:1200],
                "application_type": "",
                "raw_fields": {
                    "parser": BUPYEONG_PARSER,
                    "lecseq": identity,
                    "category_key": category.key,
                    "source_status": status_text,
                    "list_pairs": pairs,
                    "historical_invalid": historical_invalid,
                },
            }
        )
        rows.append(row)
    identities = [_clean(row.get("raw_fields", {}).get("lecseq")) for row in rows]
    if len(identities) != len(set(identities)):
        errors.append("duplicate identities within page")
    return rows, errors


def _fee_parts(value: str) -> tuple[str, str]:
    text = _clean(value)
    primary = _clean(re.split(r"\((?:재료비|교재비)", text, maxsplit=1)[0])
    match = re.search(r"(?:재료비|교재비)\s*:?[\s]*([^)]*)", text)
    return primary, _clean(match.group(1)) if match else ""


def _application_link(
    soup: BeautifulSoup, category: BupyeongCategory, identity: str
) -> tuple[str, list[str]]:
    errors: list[str] = []
    values: list[str] = []
    for anchor in soup.select("a[href*='traineeInsertForm.do']"):
        value = urljoin(BUPYEONG_URL, _clean(anchor.get("href")))
        found = _course_identity(value, category, category.application_path)
        if found != identity:
            errors.append("application control identity/route mismatch")
        else:
            values.append(value)
    values = list(dict.fromkeys(values))
    if len(values) > 1:
        errors.append("multiple application controls")
    return (values[0] if len(values) == 1 else ""), errors


def _enrich_detail(
    row: dict[str, Any],
    soup: BeautifulSoup,
    category: BupyeongCategory,
    cutoff: date,
) -> list[str]:
    identity = _clean(row.get("raw_fields", {}).get("lecseq"))
    errors: list[str] = []
    if not _title_owned(soup, category):
        return [f"course {identity}: detail ownership mismatch"]
    title_node = soup.select_one(".board_view .title p")
    if _clean(title_node.get_text(" ", strip=True) if title_node else "") != _clean(row.get("title")):
        errors.append(f"course {identity}: detail title mismatch")
    pairs = _detail_pairs(soup)
    missing = _DETAIL_REQUIRED[category.key].difference(pairs)
    if missing:
        errors.append(f"course {identity}: detail fields changed {sorted(missing)!r}")
        return errors
    try:
        start, end, trailing_schedule = _date_period(pairs["교육기간"])
    except (TypeError, ValueError) as exc:
        errors.append(f"course {identity}: detail education period {exc}")
        return errors
    if (start, end) != (_clean(row.get("start_date")), _clean(row.get("end_date"))):
        errors.append(f"course {identity}: list/detail education period mismatch")
    try:
        if date.fromisoformat(end) < cutoff:
            errors.append(f"course {identity}: detail is no longer current")
    except ValueError:
        errors.append(f"course {identity}: invalid detail end date")
    channel_periods = _channel_periods(pairs["접수기간"])
    apply_start, apply_end = _application_period(pairs["접수기간"])
    list_apply = _dates(row.get("apply_period"))
    if len(list_apply) >= 2:
        list_range = (list_apply[0].isoformat(), list_apply[-1].isoformat())
        if channel_periods and list_range not in channel_periods.values():
            errors.append(
                f"course {identity}: list/detail application period mismatch"
            )
        elif channel_periods:
            apply_start, apply_end = list_range
        elif _clean(row.get("status")) == "CLOSED":
            # A handful of still-running resident-centre courses retain their
            # reception dates only on the list after registration closes.
            # Preserve that exact official list range; an open/scheduled row
            # with the same blank detail would still fail closed below.
            apply_start, apply_end = list_range
        else:
            errors.append(
                f"course {identity}: active detail lacks an application period"
            )
    if category.key == "dong" and _clean(pairs.get("교육기관")) != _clean(row.get("branch")):
        errors.append(f"course {identity}: resident-centre branch mismatch")
    if category.key == "physical" and _clean(pairs.get("교육기관")) != "생활체육":
        errors.append(f"course {identity}: physical ownership mismatch")
    application_url, application_errors = _application_link(soup, category, identity)
    errors.extend(f"course {identity}: {item}" for item in application_errors)
    method = _clean(pairs.get("접수방법"))
    online_range = channel_periods.get("온라인")
    if online_range is None and "온라인" in method and set(channel_periods) == {"전체"}:
        online_range = channel_periods["전체"]
    expects_online = bool(
        _clean(row.get("status")) == "OPEN"
        and "온라인" in method
        and online_range
        and date.fromisoformat(online_range[0]) <= cutoff <= date.fromisoformat(online_range[1])
    )
    if expects_online != bool(application_url):
        errors.append(f"course {identity}: application control/status mismatch")
    fee, material_fee = _fee_parts(pairs.get("수강료", ""))
    schedule = trailing_schedule
    if pairs.get("교육일자"):
        schedule = _clean(pairs["교육일자"])
    elif category.key == "elearning" and pairs.get("교육요일"):
        schedule = _clean(f"{pairs['교육요일']} {trailing_schedule}")
    row.update(
        {
            "period": f"{start} ~ {end}",
            "start_date": start,
            "end_date": end,
            "apply_period": (
                f"{apply_start} ~ {apply_end}" if apply_start and apply_end else ""
            ),
            "schedule_raw": schedule,
            "target": _clean(pairs.get("교육대상")),
            "capacity": _clean(pairs.get("신청정원") or pairs.get("접수인원")),
            "fee": fee,
            "material_fee": material_fee,
            "venue_name": _clean(pairs.get("교육장소")) or _clean(row.get("venue_name")),
            "instructor": _clean(pairs.get("강사") or pairs.get("강사명")),
            "application_method_raw": method,
            "description": _clean(pairs.get("강의내용") or pairs.get("안내"))[:1200],
            "application_type": (
                "ONLINE_RESERVATION"
                if application_url
                else ("OFFLINE_APPLY" if "방문" in method and "온라인" not in method else "")
            ),
            "raw_fields": {
                **row.get("raw_fields", {}),
                "detail_pairs": pairs,
                "application_channel_periods": channel_periods,
                "detail_identity_verified": not errors,
            },
        }
    )
    if application_url:
        row["application_url"] = application_url
    else:
        row.pop("application_url", None)
    return errors


def _validate_legacy_detail(
    row: dict[str, Any],
    soup: BeautifulSoup,
    category: BupyeongCategory,
    cutoff: date,
) -> list[str]:
    identity = _clean(row.get("raw_fields", {}).get("lecseq"))
    if not _title_owned(soup, category):
        return [f"legacy course {identity}: detail ownership mismatch"]
    title_node = soup.select_one(".board_view .title p")
    pairs = _detail_pairs(soup)
    errors: list[str] = []
    if _clean(title_node.get_text(" ", strip=True) if title_node else "") != _clean(row.get("title")):
        errors.append(f"legacy course {identity}: detail title mismatch")
    try:
        detail_dates = _dates(pairs.get("교육기간"))
    except ValueError:
        detail_dates = []
    if len(detail_dates) >= 2 and detail_dates[-1] >= cutoff:
        errors.append(
            f"legacy course {identity}: detail now exposes a current/future date"
        )
    application_url, application_errors = _application_link(soup, category, identity)
    errors.extend(f"legacy course {identity}: {item}" for item in application_errors)
    if application_url:
        errors.append(f"legacy course {identity}: unexpected application control")
    return errors


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _failure_meta(message: str, *, source_cap_reached: bool = False) -> dict[str, Any]:
    return {
        "pages": 0,
        "root_requests": 0,
        "list_pages": 0,
        "list_requests": 0,
        "sentinel_requests": 0,
        "page_one_rechecks": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_required_count": 0,
        "source_count": len(BUPYEONG_CATEGORIES),
        "source_total": 0,
        "source_rows": 0,
        "current_count": 0,
        "returned_count": 0,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": source_cap_reached,
        "no_current_data": False,
        "configured_collection_error": message,
    }


def collect_bupyeong_education_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 700,
    detail_limit: int = 500,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = BUPYEONG_MAX_WORKERS,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return a source-complete current/future education snapshot."""

    if not is_bupyeong_target(target):
        return [], BUPYEONG_PARSER, _failure_meta(
            "target does not match the exact Bupyeong integrated-reservation route"
        )
    try:
        allowed_pages = int(max_pages)
        allowed_details = int(detail_limit)
        workers = min(max(1, int(max_workers)), BUPYEONG_MAX_WORKERS)
        cutoff = _today(today)
        if allowed_pages < 0 or allowed_details < 0:
            raise ValueError
    except (TypeError, ValueError):
        return [], BUPYEONG_PARSER, _failure_meta(
            "max_pages/detail_limit/max_workers/today are invalid"
        )

    current_fetcher = fetcher or _default_fetcher
    current_session_factory = session_factory or _default_session_factory
    current_dedupe = dedupe_rows or _dedupe_default
    sessions: list[Any] = []
    sessions_lock = threading.Lock()
    local = threading.local()

    def thread_session() -> Any:
        value = getattr(local, "session", None)
        count = int(getattr(local, "request_count", 0))
        if value is None or count >= BUPYEONG_SESSION_REQUEST_LIMIT:
            if value is not None:
                _close_quietly(value)
            value = current_session_factory()
            local.session = value
            local.request_count = 0
            with sessions_lock:
                sessions.append(value)
        local.request_count = int(getattr(local, "request_count", 0)) + 1
        return value

    def fetch_url(url: str) -> BeautifulSoup:
        return _coerce_soup(current_fetcher(thread_session(), url, timeout))

    errors: list[str] = []
    detail_errors: list[str] = []
    source_cap_reached = False
    root_requests = 0
    list_requests = 0
    sentinel_requests = 0
    page_one_rechecks = 0
    detail_attempts = 0
    detail_pages = 0
    page_rows: dict[tuple[str, int], list[dict[str, Any]]] = {}
    declared_last: dict[str, int] = {}
    try:
        try:
            root = fetch_url(BUPYEONG_URL)
            root_requests = 1
        except Exception as exc:
            return [], BUPYEONG_PARSER, _failure_meta(
                f"official root fetch failed: {type(exc).__name__}"
            )
        if not _root_owned(root):
            return [], BUPYEONG_PARSER, _failure_meta(
                "official root ownership/navigation contract failed"
            )

        for category in BUPYEONG_CATEGORIES:
            try:
                soup = fetch_url(bupyeong_list_url(category, 1))
                list_requests += 1
            except Exception as exc:
                errors.append(f"{category.label} page 1: fetch {type(exc).__name__}")
                continue
            contract = _page_contract(soup, category)
            if contract is None or contract[0] != 1:
                errors.append(f"{category.label} page 1: pagination/ownership mismatch")
                continue
            last = contract[1]
            declared_last[category.key] = last
            parsed, item_errors = _parse_list_page(
                target, soup, category, 1, last, cutoff
            )
            errors.extend(f"{category.label} page 1: {item}" for item in item_errors)
            page_rows[(category.key, 1)] = parsed

        required_list_pages = sum(declared_last.values())
        if not errors and allowed_pages < required_list_pages:
            source_cap_reached = True
            errors.append(
                f"aggregate max_pages {allowed_pages} is below required "
                f"{required_list_pages} data pages"
            )

        if errors:
            meta = _failure_meta(
                "; ".join(dict.fromkeys(errors)), source_cap_reached=source_cap_reached
            )
            meta.update(
                {
                    "pages": root_requests + list_requests,
                    "root_requests": root_requests,
                    "list_requests": list_requests,
                    "category_page_counts": dict(declared_last),
                }
            )
            return [], BUPYEONG_PARSER, meta

        tasks = [
            (category, page, page == declared_last[category.key] + 1)
            for category in BUPYEONG_CATEGORIES
            for page in range(2, declared_last[category.key] + 2)
        ]

        def fetch_page(
            task: tuple[BupyeongCategory, int, bool]
        ) -> tuple[BupyeongCategory, int, bool, Optional[BeautifulSoup], str]:
            category, page, sentinel = task
            try:
                return category, page, sentinel, fetch_url(bupyeong_list_url(category, page)), ""
            except Exception as exc:
                return category, page, sentinel, None, f"fetch {type(exc).__name__}"

        with ThreadPoolExecutor(max_workers=min(workers, max(1, len(tasks)))) as pool:
            results = list(pool.map(fetch_page, tasks))
        sentinel_soups: dict[str, BeautifulSoup] = {}
        for category, page, sentinel, soup, fetch_error in results:
            if sentinel:
                sentinel_requests += 1
            else:
                list_requests += 1
            if fetch_error or soup is None:
                errors.append(
                    f"{category.label} page {page}: {fetch_error or 'empty response'}"
                )
                continue
            last = declared_last[category.key]
            if sentinel:
                if _page_contract(soup, category) != (last, last):
                    errors.append(
                        f"{category.label} sentinel {page}: last-page clamp changed"
                    )
                sentinel_soups[category.key] = soup
                continue
            parsed, item_errors = _parse_list_page(
                target, soup, category, page, last, cutoff
            )
            errors.extend(f"{category.label} page {page}: {item}" for item in item_errors)
            page_rows[(category.key, page)] = parsed

        for category in BUPYEONG_CATEGORIES:
            last = declared_last[category.key]
            for page in range(1, last + 1):
                count = len(page_rows.get((category.key, page), []))
                if page < last and count != BUPYEONG_PAGE_SIZE:
                    errors.append(
                        f"{category.label} page {page}: exposed {count}, expected {BUPYEONG_PAGE_SIZE}"
                    )
                elif page == last and not 1 <= count <= BUPYEONG_PAGE_SIZE:
                    errors.append(
                        f"{category.label} last page {page}: invalid row count {count}"
                    )
            sentinel_soup = sentinel_soups.get(category.key)
            if sentinel_soup is None:
                continue
            sentinel_rows, sentinel_errors = _parse_list_page(
                target, sentinel_soup, category, last, last, cutoff
            )
            errors.extend(
                f"{category.label} sentinel: {item}" for item in sentinel_errors
            )
            last_ids = [
                _clean(row.get("raw_fields", {}).get("lecseq"))
                for row in page_rows.get((category.key, last), [])
            ]
            sentinel_ids = [
                _clean(row.get("raw_fields", {}).get("lecseq")) for row in sentinel_rows
            ]
            if sentinel_ids != last_ids:
                errors.append(f"{category.label}: last-page clamp rows changed")

        all_rows = [
            row
            for category in BUPYEONG_CATEGORIES
            for page in range(1, declared_last[category.key] + 1)
            for row in page_rows.get((category.key, page), [])
        ]
        identities = [
            _clean(row.get("raw_fields", {}).get("lecseq")) for row in all_rows
        ]
        duplicate_count = len(identities) - len(set(identities))
        if not identities or any(not identity for identity in identities):
            errors.append("one or more source rows lacks a stable lecseq")
        if duplicate_count:
            errors.append(f"duplicate lecseq across five catalogues: {duplicate_count}")

        historical_invalid = [
            row
            for row in all_rows
            if bool(row.get("raw_fields", {}).get("historical_invalid"))
        ]
        current_rows: list[dict[str, Any]] = []
        expired_count = 0
        for row in all_rows:
            if row in historical_invalid:
                continue
            try:
                end = date.fromisoformat(_clean(row.get("end_date")))
            except ValueError:
                errors.append(
                    f"course {row.get('raw_fields', {}).get('lecseq')}: invalid end date"
                )
                continue
            if end >= cutoff:
                current_rows.append(row)
            else:
                expired_count += 1

        detail_required = len(current_rows) + len(historical_invalid)
        if allowed_details < detail_required:
            source_cap_reached = True
            errors.append(
                f"detail_limit {allowed_details} is below required {detail_required}"
            )

        if not errors and detail_required:
            detail_tasks = [(row, False) for row in current_rows] + [
                (row, True) for row in historical_invalid
            ]

            def fetch_detail(
                task: tuple[dict[str, Any], bool]
            ) -> tuple[dict[str, Any], bool, bool, Optional[BeautifulSoup], str]:
                row, legacy = task
                try:
                    return row, legacy, True, fetch_url(_clean(row.get("raw_url"))), ""
                except Exception as exc:
                    return row, legacy, False, None, f"fetch {type(exc).__name__}"

            detail_attempts = len(detail_tasks)
            with ThreadPoolExecutor(
                max_workers=min(workers, max(1, len(detail_tasks)))
            ) as pool:
                detail_results = list(pool.map(fetch_detail, detail_tasks))
            for row, legacy, fetched, soup, fetch_error in detail_results:
                identity = _clean(row.get("raw_fields", {}).get("lecseq"))
                if not fetched or soup is None:
                    detail_errors.append(
                        f"course {identity}: {fetch_error or 'empty detail response'}"
                    )
                    continue
                detail_pages += 1
                category = _CATEGORY_BY_KEY[
                    _clean(row.get("raw_fields", {}).get("category_key"))
                ]
                if legacy:
                    detail_errors.extend(
                        _validate_legacy_detail(row, soup, category, cutoff)
                    )
                else:
                    detail_errors.extend(_enrich_detail(row, soup, category, cutoff))

        errors.extend(detail_errors)

        def recheck(
            category: BupyeongCategory,
        ) -> tuple[BupyeongCategory, Optional[BeautifulSoup], str]:
            try:
                return category, fetch_url(bupyeong_list_url(category, 1)), ""
            except Exception as exc:
                return category, None, f"fetch {type(exc).__name__}"

        if not source_cap_reached:
            with ThreadPoolExecutor(max_workers=min(workers, len(BUPYEONG_CATEGORIES))) as pool:
                rechecks = list(pool.map(recheck, BUPYEONG_CATEGORIES))
            page_one_rechecks = len(rechecks)
            for category, soup, fetch_error in rechecks:
                if fetch_error or soup is None:
                    errors.append(f"{category.label} page 1 recheck: {fetch_error}")
                    continue
                checked, checked_errors = _parse_list_page(
                    target, soup, category, 1, declared_last[category.key], cutoff
                )
                errors.extend(
                    f"{category.label} page 1 recheck: {item}"
                    for item in checked_errors
                )
                first_ids = [
                    _clean(row.get("raw_fields", {}).get("lecseq"))
                    for row in page_rows.get((category.key, 1), [])
                ]
                checked_ids = [
                    _clean(row.get("raw_fields", {}).get("lecseq")) for row in checked
                ]
                if checked_ids != first_ids:
                    errors.append(f"{category.label}: page 1 changed during traversal")

        cleaned = current_rows
        if not errors:
            try:
                deduped = list(current_dedupe(cleaned))
            except Exception as exc:
                errors.append(f"dedupe failed {type(exc).__name__}")
                deduped = []
            if len(deduped) != len(cleaned):
                errors.append(
                    f"dedupe changed complete row count {len(cleaned)} to {len(deduped)}"
                )
            cleaned = deduped

        pagination_complete = (
            not errors
            and len(page_rows) == sum(declared_last.values())
            and sentinel_requests == len(BUPYEONG_CATEGORIES)
            and page_one_rechecks == len(BUPYEONG_CATEGORIES)
        )
        details_complete = (
            not detail_errors
            and not source_cap_reached
            and detail_pages == detail_required
        )
        snapshot_complete = pagination_complete and details_complete and not errors
        if not snapshot_complete:
            cleaned = []

        category_source_counts = Counter(
            _clean(row.get("raw_fields", {}).get("category_key")) for row in all_rows
        )
        category_current_counts = Counter(
            _clean(row.get("raw_fields", {}).get("category_key")) for row in current_rows
        )
        branch_counts = Counter(_clean(row.get("branch")) for row in current_rows)
        status_counts = Counter(_clean(row.get("status")) for row in current_rows)
        list_pages = sum(declared_last.values())
        request_count = (
            root_requests
            + list_requests
            + sentinel_requests
            + page_one_rechecks
            + detail_pages
        )
        meta: dict[str, Any] = {
            "pages": list_pages,
            "request_count": request_count,
            "root_requests": root_requests,
            "list_pages": list_pages,
            "list_requests": list_requests,
            "sentinel_requests": sentinel_requests,
            "sentinel_mode": "last_page_clamp",
            "page_one_rechecks": page_one_rechecks,
            "detail_attempts": detail_attempts,
            "detail_pages": detail_pages,
            "detail_required_count": detail_required,
            "source_count": len(BUPYEONG_CATEGORIES),
            "source_total": len(all_rows),
            "source_rows": len(all_rows),
            "unique_id_count": len(set(identities)),
            "duplicate_count": duplicate_count,
            "historical_invalid_count": len(historical_invalid),
            "expired_count": expired_count,
            "current_count": len(current_rows),
            "returned_count": len(cleaned),
            "category_page_counts": dict(declared_last),
            "category_source_counts": dict(category_source_counts),
            "category_current_counts": dict(category_current_counts),
            "branch_counts": dict(branch_counts),
            "current_status_counts": dict(status_counts),
            "pagination_detected": list_pages > len(BUPYEONG_CATEGORIES),
            "pagination_complete": pagination_complete,
            "pagination_exhausted": pagination_complete,
            "details_complete": details_complete,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": snapshot_complete,
            "source_cap_reached": source_cap_reached,
            "reservation_discovery_links": sum(
                bool(row.get("application_url")) for row in current_rows
            ),
            "semantic_duplicate_count": 0,
            "recursion_depth": 0,
            "no_current_data": snapshot_complete and not current_rows,
            "no_current_reason": (
                "all five complete Bupyeong education catalogues have no current/future rows"
                if snapshot_complete and not current_rows
                else ""
            ),
        }
        if errors:
            meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return cleaned, BUPYEONG_PARSER, meta
    finally:
        for value in sessions:
            _close_quietly(value)


collect_bupyeong_target = collect_bupyeong_education_courses


__all__ = [
    "BUPYEONG_CANDIDATE_ID",
    "BUPYEONG_CATEGORIES",
    "BUPYEONG_HOST",
    "BUPYEONG_MAX_WORKERS",
    "BUPYEONG_MUNICIPALITY_CODE",
    "BUPYEONG_MUNICIPALITY_NAME",
    "BUPYEONG_PAGE_SIZE",
    "BUPYEONG_PARSER",
    "BUPYEONG_PATH",
    "BUPYEONG_PROVIDER",
    "BUPYEONG_URL",
    "BupyeongCategory",
    "bupyeong_detail_url",
    "bupyeong_list_url",
    "collect_bupyeong_education_courses",
    "collect_bupyeong_target",
    "is_bupyeong_target",
    "is_target",
]
