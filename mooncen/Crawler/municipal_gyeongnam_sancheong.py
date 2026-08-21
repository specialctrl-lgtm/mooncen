"""Complete, fail-closed education collector for Sancheong-gun.

The two configured legacy providers are township-filtered views of the old
``/edu/courseList.do`` catalogue.  Sancheong moved education applications to
the integrated reservation service in 2026.  The canonical owner used here is
the unfiltered ``/yeyak/00001/00162.web`` all-course catalogue; the legacy
Geumseo-myeon provider remains the dispatch owner so existing configuration can
be upgraded without creating a second source.

A snapshot is emitted only when the declared catalogue total, every data page,
the site's clamped immediate post-last page, and stable first/last rechecks all
agree.  Details are fetched for every current/future course.  Only the public,
course-bound ``신청하기`` control is retained.  Application forms and the
asynchronous applicant-list endpoint are never requested.  Applicant rows,
addresses, contacts, managers, instructors and free-form detail text are not
stored.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


SANCHEONG_HOST = "www.sancheong.go.kr"
SANCHEONG_LEGACY_PATH = "/edu/courseList.do"
SANCHEONG_LIST_PATH = "/yeyak/00001/00162.web"
SANCHEONG_LIFELONG_SUBSET_PATH = "/yeyak/00001/00005.web"
SANCHEONG_PROVIDER = "MUNI_WWW_SANCHEONG_GO_KR_8C2EE340"
SANCHEONG_MUNICIPALITY_CODE = "4886000000"
SANCHEONG_MUNICIPALITY_NAME = "경상남도 산청군"
SANCHEONG_PAGE_SIZE = 9
SANCHEONG_MAX_WORKERS = 4
SANCHEONG_FETCH_ATTEMPTS = 2
SANCHEONG_CONFIGURED_URL = (
    f"https://{SANCHEONG_HOST}{SANCHEONG_LEGACY_PATH}?key=51&srcField=AREA05"
)
SANCHEONG_URL = f"https://{SANCHEONG_HOST}{SANCHEONG_LIST_PATH}"
SANCHEONG_APPLICANT_PATH = "/yeyak/json/yeyak/edu/applicant/list.do"
SANCHEONG_PARSER = (
    "gyeongnam_sancheong_complete_integrated_all_courses+declared_total+"
    "clamped_last_sentinel+stable_boundaries+current_detail_allowlist+"
    "course_bound_public_application_no_form_fetch+applicant_ajax_blocked+pii_allowlist"
)
SANCHEONG_OWNERSHIP_SCOPE = (
    "official_sancheong_integrated_reservation_all_education_categories"
)

SANCHEONG_CANDIDATE_IDS: Mapping[str, str] = {
    "legacy_geumseo_existing_owner": "MUNI_IR_46E931CCD66E",
    "legacy_sinan_subset": "MUNI_IR_121A0D1312F1",
    "login_page": "MUNI_IR_BCE09FA07259",
    "township_home": "MUNI_IR_2040BF0DC291",
}
SANCHEONG_CANDIDATE_DECISIONS: Mapping[str, str] = {
    "MUNI_IR_46E931CCD66E": (
        "include_existing_owner_and_dispatch_to_complete_integrated_catalogue"
    ),
    "MUNI_IR_121A0D1312F1": (
        "exclude_legacy_sinan_township_subset_duplicate_of_canonical_catalogue"
    ),
    "MUNI_IR_BCE09FA07259": "exclude_login_page_not_a_course_catalogue",
    "MUNI_IR_2040BF0DC291": "exclude_township_home_not_a_course_catalogue",
}


@dataclass(frozen=True)
class SancheongAlias:
    provider: str
    url: str
    relationship: str


SANCHEONG_ALIASES = (
    SancheongAlias(
        "MUNI_WWW_SANCHEONG_GO_KR_D2A9B35A",
        f"https://{SANCHEONG_HOST}{SANCHEONG_LEGACY_PATH}?key=51&srcField=AREA09",
        "legacy Sinan-myeon filtered subset of the canonical catalogue",
    ),
    SancheongAlias(
        "MUNI_WWW_SANCHEONG_GO_KR_4E7BA03A",
        f"https://{SANCHEONG_HOST}/login.jsp",
        "login page; rejected discovery candidate",
    ),
    SancheongAlias(
        "MUNI_WWW_SANCHEONG_GO_KR_6BB7F77B",
        f"https://{SANCHEONG_HOST}/sancheong/index.do",
        "township home; rejected discovery candidate",
    ),
    SancheongAlias(
        "SANCHEONG_EDU_DISCOVERY",
        f"https://{SANCHEONG_HOST}/edu/index.do",
        "education discovery home linking to the canonical catalogue",
    ),
    SancheongAlias(
        "SANCHEONG_EDU_LEGACY_ALL",
        f"https://{SANCHEONG_HOST}{SANCHEONG_LEGACY_PATH}?key=51",
        "retired unfiltered legacy catalogue; all rows ended before the cutover",
    ),
    SancheongAlias(
        "SANCHEONG_RESERVATION_LIFELONG_SUBSET",
        f"https://{SANCHEONG_HOST}{SANCHEONG_LIFELONG_SUBSET_PATH}",
        "lifelong-learning subset contained by the all-course catalogue",
    ),
)

Fetcher = Callable[..., Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"LT\d{6}")
_TOTAL_RE = re.compile(
    r"총\s*([\d,]+)\s*개의\s*강좌신청이\s*있습니다\.\s*"
    r"\(\s*(\d+)\s*/\s*(\d+)\s*페이지\s*\)"
)
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[.-](\d{1,2})[.-](\d{1,2})(?!\d)")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:010[-\s*]+[\d*]{3,4}[-\s*]+[\d*]{4}|"
    r"0\d{1,3}[-\s]?\d{3,4}[-\s]?\d{4}|0\d{8,11})(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_CANCELLED_RE = re.compile(r"(?:^|[<\[(])\s*(?:폐강|취소)\s*(?:$|[>\])])")
_CAPACITY_RE = re.compile(
    r"정원\s*(\d+)명\s*/\s*접수\s*(\d+)명\s*/\s*대기\s*(\d+)명"
)
_DETAIL_CAPACITY_RE = re.compile(
    r"(\d+)명\s*접수\s*/\s*총\s*(\d+)\s*명\s*모집(?:\s+.*)?"
)

_FCD_OPTIONS: Mapping[str, str] = {
    "F093": "농업기술센터",
    "F100": "차황면",
    "F101": "오부면",
    "F102": "생초면",
    "F103": "금서면",
    "F104": "삼장면",
    "F105": "시천면",
    "F106": "단성면",
    "F107": "신안면",
    "F108": "생비량면",
    "F109": "신등면",
    "F118": "산청군 평생교육",
    "F119": "산청요",
    "F120": "농업기술센터",
    "F122": "순한쌀빵",
    "F123": "농업인 정보화교육장",
    "F124": "산청읍",
    "F126": "산청군 평생학습관",
    "F044": "청소년수련관",
    "F099": "산청군 평생학습센터",
    "F121": "산엔청 청년 베이스캠프",
    "F092": "산청 영재컴퓨터학원",
    "F111": "광명도자기",
    "F037": "시천 솔로넷컴퓨터학원",
    "F112": "산청푸른학당(유휴학습공간)",
}
_STYPE_OPTIONS: Mapping[str, str] = {
    "title": "교육강좌명",
    "content": "교육내용",
    "titleContent": "교육강좌명+내용",
    "place": "교육장소",
}
_LIST_REQUIRED = {"접수기간", "교육기간", "모집인원", "수강료"}
_LIST_OPTIONAL = {"추첨발표"}
_DETAIL_REQUIRED = {
    "분야",
    "교육대상",
    "교육장소",
    "모집인원",
    "교육방법",
    "접수방법",
    "접수기간",
    "교육기간",
    "교육시간",
    "강사명",
    "수강료",
}
_DETAIL_OPTIONAL = {"추첨발표"}
_INSTITUTION_FIELDS = ("교육기관", "주소", "담당자", "연락처")
_METHODS = {"인터넷", "방문", "전화"}
_STATUS_MAP: Mapping[str, str] = {
    "접수중": "OPEN",
    "접수대기": "SCHEDULED",
    "접수마감": "CLOSED",
}


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def _safe_base(parsed: Any) -> bool:
    return bool(
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == SANCHEONG_HOST
        and parsed.port is None
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


def is_gyeongnam_sancheong_education_target(target: Any) -> bool:
    """Match only the existing Geumseo owner at configured/canonical scope."""

    if _provider(target) != SANCHEONG_PROVIDER:
        return False
    parsed = urlparse(_target_url(target))
    if not _safe_base(parsed):
        return False
    query = parse_qs(parsed.query, keep_blank_values=True)
    if parsed.path == SANCHEONG_LEGACY_PATH:
        return query == {"key": ["51"], "srcField": ["AREA05"]}
    return parsed.path == SANCHEONG_LIST_PATH and not query


is_target = is_gyeongnam_sancheong_education_target


def is_gyeongnam_sancheong_alias_target(target: Any) -> bool:
    provider = _provider(target)
    url = _target_url(target)
    return any(provider == alias.provider and url == alias.url for alias in SANCHEONG_ALIASES)


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is not None:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


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


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def sancheong_list_url(page: int = 1) -> str:
    if page < 1:
        return ""
    if page == 1:
        return SANCHEONG_URL
    return SANCHEONG_URL + "?" + urlencode({"cpage": str(page)})


def sancheong_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        return ""
    return SANCHEONG_URL + "?" + urlencode((("amode", "view"), ("lectureId", value)))


def sancheong_application_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        return ""
    return SANCHEONG_URL + "?" + urlencode((("amode", "agree"), ("lectureId", value)))


def _allowed_request_url(url: str) -> bool:
    parsed = urlparse(_clean(url))
    if not _safe_base(parsed) or parsed.path != SANCHEONG_LIST_PATH:
        return False
    query = parse_qs(parsed.query, keep_blank_values=True)
    if not query:
        return True
    if set(query) == {"cpage"}:
        value = (query.get("cpage") or [""])[0]
        return len(query["cpage"]) == 1 and value.isdigit() and int(value) >= 2
    return bool(
        set(query) == {"amode", "lectureId"}
        and query.get("amode") == ["view"]
        and len(query.get("lectureId", [])) == 1
        and _IDENTITY_RE.fullmatch(query["lectureId"][0])
    )


def _response_soup(response: Any) -> tuple[BeautifulSoup, str]:
    try:
        status = int(getattr(response, "status_code", 0))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise ValueError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise ValueError("HTTP redirects are not accepted")
    final_url = _clean(getattr(response, "url", ""))
    if not _allowed_request_url(final_url):
        raise ValueError("source response URL changed")
    content = getattr(response, "content", None)
    if content is None:
        content = getattr(response, "text", None)
    if not content:
        raise ValueError("empty HTML response")
    return BeautifulSoup(content, "lxml"), final_url


def _request_soup(
    current: Any,
    url: str,
    *,
    timeout: int,
    fetcher: Optional[Fetcher] = None,
) -> tuple[BeautifulSoup, str]:
    if not _allowed_request_url(url):
        raise ValueError("refusing non-catalogue, application, or applicant-list request")
    messages: list[str] = []
    for attempt in range(1, SANCHEONG_FETCH_ATTEMPTS + 1):
        try:
            if fetcher is not None:
                result = fetcher(current, "GET", url, timeout=timeout, data={})
                if (
                    isinstance(result, tuple)
                    and len(result) == 2
                    and isinstance(result[0], BeautifulSoup)
                ):
                    soup, final_url = result
                    if not _allowed_request_url(_clean(final_url or url)):
                        raise ValueError("source response URL changed")
                    return soup, _clean(final_url or url)
                if isinstance(result, BeautifulSoup):
                    return result, url
                if isinstance(result, (str, bytes, bytearray)):
                    if not result:
                        raise ValueError("empty HTML response")
                    return BeautifulSoup(result, "lxml"), url
                return _response_soup(result)
            return _response_soup(current.get(url, timeout=timeout))
        except Exception as exc:
            messages.append(f"attempt {attempt}: {type(exc).__name__}: {_clean(exc)}")
    raise ValueError("; ".join(messages))


def _dates(value: Any) -> list[date]:
    result: list[date] = []
    for year, month, day in _DATE_RE.findall(_clean(value)):
        try:
            result.append(date(int(year), int(month), int(day)))
        except ValueError:
            return []
    return result


def _selected_value(select: Any) -> str:
    selected = select.select_one("option[selected]")
    if selected is not None:
        return _clean(selected.get("value"))
    first = select.select_one("option")
    return _clean(first.get("value")) if first is not None else ""


def _option_map(select: Any) -> dict[str, str]:
    return {
        _clean(option.get("value")): _clean(option.get_text(" ", strip=True))
        for option in select.select("option[value]:not([value=''])")
    }


def _form_and_total(
    soup: BeautifulSoup,
    *,
    expected_display_page: int,
    expected_request_page: int,
) -> tuple[int, int, list[str]]:
    errors: list[str] = []
    forms = soup.select("form#listForm[name='listForm']")
    if len(forms) != 1:
        return 0, 0, ["expected one unfiltered listForm"]
    form = forms[0]
    action = urlparse(urljoin(SANCHEONG_URL, _clean(form.get("action"))))
    expected_action_query = (
        {} if expected_request_page == 1 else {"cpage": [str(expected_request_page)]}
    )
    if (
        _clean(form.get("method")).lower() != "get"
        or not _safe_base(action)
        or action.path != SANCHEONG_LIST_PATH
        or parse_qs(action.query, keep_blank_values=True) != expected_action_query
    ):
        errors.append("unexpected listForm method/action")
    cpage = form.select("input[type='hidden'][name='cpage']")
    if len(cpage) != 1 or _clean(cpage[0].get("value")) != "1":
        errors.append("listForm cpage marker changed")
    fcd = form.select("select[name='fcd']")
    if len(fcd) != 1 or _selected_value(fcd[0]) or _option_map(fcd[0]) != dict(_FCD_OPTIONS):
        errors.append("listForm organization filter is not complete/unfiltered")
    stype = form.select("select[name='stype']")
    if (
        len(stype) != 1
        or _selected_value(stype[0]) != "title"
        or _option_map(stype[0]) != dict(_STYPE_OPTIONS)
    ):
        errors.append("listForm search vocabulary changed")
    sstring = form.select("input[name='sstring']")
    if len(sstring) != 1 or _clean(sstring[0].get("value")):
        errors.append("listForm search text is not empty")

    totals = []
    for node in form.select("p.info1"):
        match = _TOTAL_RE.fullmatch(_clean(node.get_text(" ", strip=True)))
        if match:
            totals.append(match)
    if len(totals) != 1:
        return 0, 0, [*errors, "expected one declared course total"]
    total, displayed, last = (
        int(value.replace(",", "")) for value in totals[0].groups()
    )
    if displayed != expected_display_page:
        errors.append("declared current page mismatch")
    expected_last = max(1, math.ceil(total / SANCHEONG_PAGE_SIZE))
    if last != expected_last:
        errors.append("declared last page does not match total/page size")
    return total, last, errors


def _span_pairs(nodes: Iterable[Any]) -> tuple[dict[str, str], list[str]]:
    result: dict[str, str] = {}
    errors: list[str] = []
    for node in nodes:
        labels = node.select(":scope > span.t1")
        values = node.select(":scope > span.t2")
        key = _clean(labels[0].get_text(" ", strip=True)) if len(labels) == 1 else ""
        value = _clean(values[0].get_text(" ", strip=True)) if len(values) == 1 else ""
        if not key or key in result or len(values) != 1:
            errors.append("duplicate or malformed labelled field")
            continue
        result[key] = value
    return result, errors


def _data_values(nodes: Iterable[Any], attribute: str) -> tuple[tuple[str, ...], list[str]]:
    result: list[str] = []
    errors: list[str] = []
    for node in nodes:
        value = _clean(node.get(attribute))
        text = _clean(node.get_text(" ", strip=True))
        if not value or (text and text != value) or value in result:
            errors.append(f"malformed or duplicate {attribute} marker")
        else:
            result.append(value)
    return tuple(result), errors


def _detail_progress_values(nodes: Iterable[Any]) -> tuple[tuple[str, ...], list[str]]:
    """Read detail progress labels, including the site's lottery marker quirk.

    The Sancheong template renders ``추첨`` with ``data-progress=\"전화\"``.
    This exact, audited mismatch is allowed; all other labels must agree with
    their data attribute.
    """

    result: list[str] = []
    errors: list[str] = []
    for node in nodes:
        marker = _clean(node.get("data-progress"))
        text = _clean(node.get_text(" ", strip=True))
        valid = bool(
            (text == "추첨" and marker == "전화")
            or (text in _METHODS and marker == text)
        )
        if not valid or text in result:
            errors.append("malformed or duplicate data-progress marker")
        else:
            result.append(text)
    return tuple(result), errors


def _view_identity(href: Any, *, source_page: int) -> tuple[str, list[str]]:
    parsed = urlparse(urljoin(SANCHEONG_URL, _clean(href)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    expected = {"amode", "lectureId"} | ({"cpage"} if source_page > 1 else set())
    errors: list[str] = []
    if (
        not _safe_base(parsed)
        or parsed.path != SANCHEONG_LIST_PATH
        or set(query) != expected
        or query.get("amode") != ["view"]
        or (source_page > 1 and query.get("cpage") != [str(source_page)])
    ):
        errors.append("malformed detail route")
    identity = (query.get("lectureId") or [""])[0]
    if len(query.get("lectureId", [])) != 1 or not _IDENTITY_RE.fullmatch(identity):
        errors.append("missing detail source identity")
    return identity, errors


def _parse_list_page(
    soup: BeautifulSoup, *, source_page: int
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    cards = soup.select("div#body_content div.cp1list1.full > div.lst")
    anchors = soup.select("div#body_content div.cp1list1.full > div.lst > a")
    if len(cards) != len(anchors):
        errors.append("detail anchors are outside canonical course cards")
    for index, card in enumerate(cards, 1):
        item_errors: list[str] = []
        direct_anchors = card.find_all("a", recursive=False)
        if len(direct_anchors) != 1 or not direct_anchors[0].has_attr("href"):
            identity = ""
            item_errors.append("expected one direct detail anchor")
        else:
            identity, route_errors = _view_identity(
                direct_anchors[0].get("href"), source_page=source_page
            )
            item_errors.extend(route_errors)

        titles = card.select("strong.h1")
        title = _clean(titles[0].get_text(" ", strip=True)) if len(titles) == 1 else ""
        if not title:
            item_errors.append("expected one nonempty title")

        statuses, value_errors = _data_values(
            card.select("span.stat em[data-status]"), "data-status"
        )
        item_errors.extend(value_errors)
        source_status = statuses[0] if len(statuses) == 1 else ""
        if source_status not in _STATUS_MAP:
            item_errors.append("unknown source status")

        categories, value_errors = _data_values(
            card.select("span.stat em[data-category]"), "data-category"
        )
        item_errors.extend(value_errors)
        source_category = categories[0] if len(categories) == 1 else ""
        if source_category not in {"무료", "유료"}:
            item_errors.append("unknown fee category")

        methods, value_errors = _data_values(
            card.select("div.stat2 span[data-progress]"), "data-progress"
        )
        item_errors.extend(value_errors)
        if not methods or any(method not in _METHODS for method in methods):
            item_errors.append("unknown or empty application method")

        fields, pair_errors = _span_pairs(card.select("div.texts > ul.clist > li"))
        item_errors.extend(pair_errors)
        if not _LIST_REQUIRED.issubset(fields) or not set(fields).issubset(
            _LIST_REQUIRED | _LIST_OPTIONAL
        ):
            item_errors.append("list field vocabulary changed")
        education_dates = _dates(fields.get("교육기간"))
        application_dates = _dates(fields.get("접수기간"))
        if len(education_dates) < 2:
            item_errors.append("education period is malformed")
        if len(application_dates) != 2:
            item_errors.append("application period is malformed")
        lottery_dates = _dates(fields.get("추첨발표")) if "추첨발표" in fields else []
        if "추첨발표" in fields and len(lottery_dates) != 1:
            item_errors.append("lottery announcement is malformed")
        capacity = _CAPACITY_RE.fullmatch(_clean(fields.get("모집인원")))
        if capacity is None:
            item_errors.append("capacity is malformed")
            capacity_values = (0, 0, 0)
        else:
            capacity_values = tuple(int(value) for value in capacity.groups())
        fee = _clean(fields.get("수강료"))
        if not fee or (source_category == "무료") != (fee == "무료"):
            item_errors.append("fee/category mismatch")

        if item_errors:
            errors.extend(
                f"page {source_page} row {index}: {message}" for message in item_errors
            )
            continue

        total_capacity, enrolled, waitlisted = capacity_values
        rows.append(
            {
                "provider": SANCHEONG_PROVIDER,
                "provider_course_id": f"{SANCHEONG_PROVIDER}:education:{identity}",
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "branch": SANCHEONG_MUNICIPALITY_NAME,
                "branch_code": "gyeongnam-sancheong-countywide",
                "municipality_code": SANCHEONG_MUNICIPALITY_CODE,
                "municipality_name": SANCHEONG_MUNICIPALITY_NAME,
                "sido": "경상남도",
                "sigungu": "산청군",
                "provider_organizer": SANCHEONG_MUNICIPALITY_NAME,
                "venue_name": SANCHEONG_MUNICIPALITY_NAME,
                "category": "평생학습",
                "program_type": "강좌",
                "raw_url": sancheong_detail_url(identity),
                "application_url": "",
                "application_type": "INFO_ONLY",
                "reservation_available": False,
                "status": _STATUS_MAP[source_status],
                "period": fields["교육기간"],
                "start_date": education_dates[0].isoformat(),
                "end_date": education_dates[1].isoformat(),
                "apply_period": fields["접수기간"],
                "apply_start": application_dates[0].isoformat(),
                "apply_end": application_dates[1].isoformat(),
                "schedule_raw": "",
                "fee": fee,
                "target": "",
                "capacity": total_capacity,
                "enrolled_count": enrolled,
                "waitlisted_count": waitlisted,
                "description": title,
                "source_group": "integrated_reservation",
                "collection_category": "평생학습",
                "domain_category": "교육·강좌",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "operator_type": "지자체/공공기관",
                "collection_type": "complete_html_pages+current_detail_allowlist",
                "raw_fields": {
                    "parser": SANCHEONG_PARSER,
                    "source_catalog": "sancheong_integrated_all_education",
                    "source_lecture_id": identity,
                    "source_page": source_page,
                    "source_status": source_status,
                    "source_fee_category": source_category,
                    "source_application_methods": list(methods),
                    "lottery_announcement": (
                        lottery_dates[0].isoformat() if lottery_dates else ""
                    ),
                    "detail_validated": False,
                    "application_form_fetched": False,
                    "applicant_endpoint_fetched": False,
                    "applicant_data_excluded": True,
                    "institution_address_excluded": True,
                    "institution_manager_excluded": True,
                    "institution_contact_excluded": True,
                    "instructor_excluded": True,
                    "free_text_excluded": True,
                },
            }
        )
    return rows, errors


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> str:
    values = [
        (
            _clean(row.get("provider_course_id")),
            _clean(row.get("title")),
            _clean(row.get("period")),
            _clean(row.get("apply_period")),
            _clean(row.get("fee")),
            _clean(row.get("raw_fields", {}).get("source_status")),
            tuple(row.get("raw_fields", {}).get("source_application_methods", [])),
        )
        for row in rows
    ]
    return hashlib.sha256(repr(values).encode("utf-8")).hexdigest()


def _detail_institution(root: Any) -> tuple[str, list[str]]:
    errors: list[str] = []
    tables = root.select("div.rspnsv > table.t3")
    if len(tables) != 1:
        return "", ["expected one institution table"]
    labels: list[str] = []
    institution = ""
    for row in tables[0].select("tr"):
        heads = row.select(":scope > th")
        values = row.select(":scope > td")
        if len(heads) != 1 or len(values) != 1:
            errors.append("malformed institution table row")
            continue
        label = _clean(heads[0].get_text(" ", strip=True))
        labels.append(label)
        if label == "교육기관":
            institution = _clean(values[0].get_text(" ", strip=True))
        # Deliberately do not read address, manager, or contact cell values.
    if tuple(labels) != _INSTITUTION_FIELDS:
        errors.append("institution field vocabulary changed")
    if not institution:
        errors.append("education institution is empty")
    return institution, errors


def _detail_row(
    parent: Mapping[str, Any], soup: BeautifulSoup, final_url: str
) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    raw = parent.get("raw_fields", {})
    identity = _clean(raw.get("source_lecture_id"))
    parsed = urlparse(final_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        set(query) != {"amode", "lectureId"}
        or query.get("amode") != ["view"]
        or query.get("lectureId") != [identity]
    ):
        errors.append(f"detail {identity}: response scope changed")

    roots = soup.select("div#body_content div.edu1view1")
    if len(roots) != 1:
        return dict(parent), [*errors, f"detail {identity}: summary structure changed"]
    root = roots[0]
    headings = root.select("div.hg1 > h3.h1")
    title = _clean(headings[0].get_text(" ", strip=True)) if len(headings) == 1 else ""
    if title != _clean(parent.get("title")):
        errors.append(f"detail {identity}: title mismatch")

    statuses, value_errors = _data_values(
        root.select("div.hg1 em[data-status]"), "data-status"
    )
    errors.extend(f"detail {identity}: {message}" for message in value_errors)
    if statuses != (_clean(raw.get("source_status")),):
        errors.append(f"detail {identity}: status mismatch")
    categories, value_errors = _data_values(
        root.select("div.hg1 em[data-category]"), "data-category"
    )
    errors.extend(f"detail {identity}: {message}" for message in value_errors)
    if categories != (_clean(raw.get("source_fee_category")),):
        errors.append(f"detail {identity}: fee category mismatch")
    methods, value_errors = _detail_progress_values(
        root.select("div.hg1 em[data-progress]")
    )
    errors.extend(f"detail {identity}: {message}" for message in value_errors)

    fields, pair_errors = _span_pairs(root.select("div.wrap1 div.texts > ul.lst > li"))
    errors.extend(f"detail {identity}: {message}" for message in pair_errors)
    if not _DETAIL_REQUIRED.issubset(fields) or not set(fields).issubset(
        _DETAIL_REQUIRED | _DETAIL_OPTIONAL
    ):
        errors.append(f"detail {identity}: field vocabulary changed")
    expected_methods = tuple(raw.get("source_application_methods", []))
    if "추첨발표" in fields:
        expected_methods = (*expected_methods, "추첨")
    if methods != expected_methods:
        errors.append(f"detail {identity}: application method markers mismatch")

    education_dates = _dates(fields.get("교육기간"))
    application_dates = _dates(fields.get("접수기간"))
    if len(education_dates) != 2 or [item.isoformat() for item in education_dates] != [
        _clean(parent.get("start_date")),
        _clean(parent.get("end_date")),
    ]:
        errors.append(f"detail {identity}: education period mismatch")
    if len(application_dates) != 2 or [item.isoformat() for item in application_dates] != [
        _clean(parent.get("apply_start")),
        _clean(parent.get("apply_end")),
    ]:
        errors.append(f"detail {identity}: application period mismatch")
    if _clean(fields.get("수강료")) != _clean(parent.get("fee")):
        errors.append(f"detail {identity}: fee mismatch")
    detail_capacity = _DETAIL_CAPACITY_RE.fullmatch(_clean(fields.get("모집인원")))
    if detail_capacity is None:
        errors.append(f"detail {identity}: capacity is malformed")
    elif (int(detail_capacity.group(1)), int(detail_capacity.group(2))) != (
        int(parent.get("enrolled_count", -1)),
        int(parent.get("capacity", -1)),
    ):
        errors.append(f"detail {identity}: capacity mismatch")
    target = _clean(fields.get("교육대상"))
    venue = _clean(fields.get("교육장소"))
    schedule = _clean(fields.get("교육시간"))
    if not venue or not schedule:
        errors.append(f"detail {identity}: venue/schedule is empty")

    institution, institution_errors = _detail_institution(soup)
    errors.extend(f"detail {identity}: {message}" for message in institution_errors)

    pane = soup.select("div#body_content #tabs1pane1")
    if len(pane) != 1:
        errors.append(f"detail {identity}: expected one detail tab pane")
    else:
        applicant_tables = [
            table for table in pane[0].select("table.t3") if table.find_parent("div", class_="rspnsv") is None
        ]
        if len(applicant_tables) != 1:
            errors.append(f"detail {identity}: applicant surface marker changed")
        scripts = [
            script
            for script in soup.select("script")
            if SANCHEONG_APPLICANT_PATH in str(script)
        ]
        if len(scripts) != 1:
            errors.append(f"detail {identity}: applicant AJAX marker changed")
        # Applicant cells and free-form pane content are deliberately never read.

    application_controls = [
        node
        for node in soup.select("div#body_content div.btns1 a.button")
        if "list" not in (node.get("class") or [])
    ]
    application_label = ""
    active = _clean(parent.get("status")) == "OPEN"
    if active:
        if len(application_controls) != 1:
            errors.append(f"detail {identity}: public application control changed")
        else:
            control = application_controls[0]
            application_label = _clean(control.get_text(" ", strip=True))
            control_classes = set(control.get("class") or [])
            valid_control = (
                application_label == "신청하기" and "reserve" in control_classes
            ) or (
                application_label == "대기접수" and "reserve" not in control_classes
            )
            if not valid_control:
                errors.append(
                    f"detail {identity}: public application control changed"
                )
            route = urlparse(urljoin(SANCHEONG_URL, _clean(control.get("href"))))
            route_query = parse_qs(route.query, keep_blank_values=True)
            if (
                not _safe_base(route)
                or route.path != SANCHEONG_LIST_PATH
                or set(route_query) != {"amode", "lectureId"}
                or route_query.get("amode") != ["agree"]
                or route_query.get("lectureId") != [identity]
            ):
                errors.append(f"detail {identity}: application route is not course-bound")
    elif application_controls:
        errors.append(f"detail {identity}: inactive course exposes application control")
    back = soup.select("div#body_content div.btns1 a.button.list")
    if (
        len(back) != 1
        or _clean(back[0].get_text(" ", strip=True)) != "목록으로"
        or _clean(back[0].get("href")) != "?"
    ):
        errors.append(f"detail {identity}: list return control changed")

    row = dict(parent)
    row.update(
        {
            "branch": institution,
            "branch_code": (
                "gyeongnam-sancheong-"
                + hashlib.sha256(institution.encode("utf-8")).hexdigest()[:12]
            ),
            "provider_organizer": institution,
            "venue_name": venue,
            "target": target,
            "schedule_raw": schedule,
            "application_url": sancheong_application_url(identity) if active else "",
            "application_type": "ONLINE_RESERVATION" if active else "INFO_ONLY",
            "reservation_available": active,
        }
    )
    row["raw_fields"] = {
        **raw,
        "detail_validated": not errors,
        "application_control_label": application_label,
        "detail_summary_only": True,
        "application_form_fetched": False,
        "applicant_endpoint_fetched": False,
        "applicant_data_excluded": True,
        "institution_address_excluded": True,
        "institution_manager_excluded": True,
        "institution_contact_excluded": True,
        "instructor_excluded": True,
        "free_text_excluded": True,
    }
    return row, errors


def _details(
    rows: list[dict[str, Any]],
    *,
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
    fetcher: Optional[Fetcher],
) -> tuple[list[dict[str, Any]], list[str], int]:
    if not rows:
        return [], [], 0

    def one(parent: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        current = session_factory()
        try:
            identity = _clean(parent.get("raw_fields", {}).get("source_lecture_id"))
            soup, final_url = _request_soup(
                current,
                sancheong_detail_url(identity),
                timeout=timeout,
                fetcher=fetcher,
            )
            return _detail_row(parent, soup, final_url)
        finally:
            _close_quietly(current)

    found: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    attempts = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(one, row): row for row in rows}
        for future in as_completed(futures):
            parent = futures[future]
            identity = _clean(parent.get("raw_fields", {}).get("source_lecture_id"))
            attempts += 1
            try:
                row, item_errors = future.result()
                if item_errors:
                    errors.extend(item_errors)
                else:
                    found[identity] = row
            except Exception as exc:
                errors.append(f"detail {identity}: {type(exc).__name__}: {_clean(exc)}")
    ordered = [
        found[_clean(row.get("raw_fields", {}).get("source_lecture_id"))]
        for row in rows
        if _clean(row.get("raw_fields", {}).get("source_lecture_id")) in found
    ]
    return ordered, errors, attempts


def _default_dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _privacy_violations(rows: Iterable[Mapping[str, Any]]) -> int:
    violations = 0
    forbidden = {"phone", "email", "instructor", "teacher", "applicant", "contact"}
    for row in rows:
        serialized = repr(row)
        violations += len(_PHONE_RE.findall(serialized))
        violations += len(_EMAIL_RE.findall(serialized))
        violations += sum(key in row for key in forbidden)
        raw = row.get("raw_fields", {})
        if isinstance(raw, Mapping):
            violations += sum(key in raw for key in forbidden)
    return violations


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "source_total": 0,
        "source_rows": 0,
        "current_count": 0,
        "returned_count": 0,
        "expired_count": 0,
        "cancelled_count": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "sentinel_count": None,
        "sentinel_mode": "clamped_last_page",
        "stable_rechecks": {},
        "duplicate_source_id_count": 0,
        "privacy_violations": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": "",
        "municipality_code": SANCHEONG_MUNICIPALITY_CODE,
        "municipality_name": SANCHEONG_MUNICIPALITY_NAME,
        "ownership_scope": SANCHEONG_OWNERSHIP_SCOPE,
        "candidate_ids": dict(SANCHEONG_CANDIDATE_IDS),
        "candidate_decisions": dict(SANCHEONG_CANDIDATE_DECISIONS),
        "ownership_aliases": [
            {
                "provider": alias.provider,
                "url": alias.url,
                "relationship": alias.relationship,
            }
            for alias in SANCHEONG_ALIASES
        ],
        "live_audit": {
            "audited_on": "2026-07-21",
            "canonical_total": 197,
            "canonical_pages": 22,
            "canonical_current_future": 153,
            "canonical_expired": 44,
            "canonical_duplicate_ids": 0,
            "canonical_statuses": {"접수마감": 190, "접수중": 5, "접수대기": 2},
            "legacy_total": 1400,
            "legacy_current_future": 0,
            "legacy_duplicate_ids": 0,
            "legacy_geumseo_subset": 126,
            "legacy_sinan_subset": 177,
        },
    }


def collect_gyeongnam_sancheong_education_courses(
    target: Any,
    timeout: int = 30,
    max_pages: int = 60,
    detail_limit: int = 250,
    *,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = SANCHEONG_MAX_WORKERS,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect the complete current/future official Sancheong course snapshot."""

    meta = _base_meta()
    if not is_gyeongnam_sancheong_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match the Sancheong configured/canonical education owner"
        )
        return [], SANCHEONG_PARSER, meta
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta["configured_collection_error"] = "managed session_factory injection is required"
            return [], SANCHEONG_PARSER, meta
        session_factory = _default_session_factory
    try:
        allowed_pages = max(0, int(max_pages))
        allowed_details = max(0, int(detail_limit))
        request_timeout = max(1, int(timeout))
        workers = min(max(1, int(max_workers)), SANCHEONG_MAX_WORKERS)
        cutoff = _today(today)
    except (TypeError, ValueError):
        meta["configured_collection_error"] = (
            "timeout/max_pages/detail_limit/max_workers/today are invalid"
        )
        return [], SANCHEONG_PARSER, meta

    errors: list[str] = []
    source_cap_reached = False
    first_rows: list[dict[str, Any]] = []
    declared_total = last_page = 0
    initial = session_factory()
    try:
        try:
            first_soup, _ = _request_soup(
                initial,
                sancheong_list_url(1),
                timeout=request_timeout,
                fetcher=fetcher,
            )
            meta["pages"] += 1
            meta["list_requests"] += 1
            declared_total, last_page, item_errors = _form_and_total(
                first_soup, expected_display_page=1, expected_request_page=1
            )
            errors.extend(item_errors)
            first_rows, item_errors = _parse_list_page(first_soup, source_page=1)
            errors.extend(item_errors)
            if declared_total and not first_rows:
                errors.append("first page contains no course rows")
        except Exception as exc:
            errors.append(f"first page: {type(exc).__name__}: {_clean(exc)}")
    finally:
        _close_quietly(initial)

    boundary_count = 1 if last_page == 1 else 2
    required_list_requests = last_page + 1 + boundary_count if last_page else 0
    meta["required_list_requests"] = required_list_requests
    if required_list_requests > allowed_pages:
        source_cap_reached = True
        errors.append(
            f"max_pages cap allows {allowed_pages} of {required_list_requests} "
            "required list/sentinel/recheck requests"
        )

    pages: dict[int, list[dict[str, Any]]] = {1: first_rows}
    sentinel_count: Optional[int] = None
    stable_rechecks: dict[str, bool] = {}
    crawl_session = session_factory()
    try:
        if not errors:
            for page in range(2, last_page + 1):
                soup, _ = _request_soup(
                    crawl_session,
                    sancheong_list_url(page),
                    timeout=request_timeout,
                    fetcher=fetcher,
                )
                meta["pages"] += 1
                meta["list_requests"] += 1
                total, found_last, item_errors = _form_and_total(
                    soup,
                    expected_display_page=page,
                    expected_request_page=page,
                )
                errors.extend(item_errors)
                parsed, item_errors = _parse_list_page(soup, source_page=page)
                errors.extend(item_errors)
                if total != declared_total or found_last != last_page:
                    errors.append(f"page {page}: declared pagination changed")
                pages[page] = parsed

            soup, _ = _request_soup(
                crawl_session,
                sancheong_list_url(last_page + 1),
                timeout=request_timeout,
                fetcher=fetcher,
            )
            meta["pages"] += 1
            meta["list_requests"] += 1
            total, found_last, item_errors = _form_and_total(
                soup,
                expected_display_page=last_page,
                expected_request_page=last_page + 1,
            )
            errors.extend(item_errors)
            sentinel_rows, item_errors = _parse_list_page(
                soup, source_page=last_page + 1
            )
            errors.extend(item_errors)
            sentinel_count = len(sentinel_rows)
            if (
                total != declared_total
                or found_last != last_page
                or _page_signature(sentinel_rows)
                != _page_signature(pages.get(last_page, []))
            ):
                errors.append("immediate post-last clamp is not the stable last page")

            for page in dict.fromkeys((1, last_page)):
                soup, _ = _request_soup(
                    crawl_session,
                    sancheong_list_url(page),
                    timeout=request_timeout,
                    fetcher=fetcher,
                )
                meta["pages"] += 1
                meta["list_requests"] += 1
                total, found_last, item_errors = _form_and_total(
                    soup,
                    expected_display_page=page,
                    expected_request_page=page,
                )
                errors.extend(item_errors)
                parsed, item_errors = _parse_list_page(soup, source_page=page)
                errors.extend(item_errors)
                stable = bool(
                    total == declared_total
                    and found_last == last_page
                    and _page_signature(parsed) == _page_signature(pages.get(page, []))
                )
                stable_rechecks[str(page)] = stable
                if not stable:
                    errors.append(f"page {page}: stable boundary recheck changed")
    except Exception as exc:
        errors.append(f"catalogue traversal: {type(exc).__name__}: {_clean(exc)}")
    finally:
        _close_quietly(crawl_session)

    source_rows = [
        row for page in range(1, last_page + 1) for row in pages.get(page, [])
    ]
    for page in range(1, last_page):
        if len(pages.get(page, [])) != SANCHEONG_PAGE_SIZE:
            errors.append(f"page {page}: expected a full page")
    last_count = len(pages.get(last_page, [])) if last_page else 0
    if declared_total == 0:
        if last_count:
            errors.append("empty catalogue has a nonempty last page")
    elif last_page and not 1 <= last_count <= SANCHEONG_PAGE_SIZE:
        errors.append("last page cardinality is invalid")
    if declared_total != len(source_rows):
        errors.append(f"declared total {declared_total} != parsed total {len(source_rows)}")
    identities = [_clean(row.get("provider_course_id")) for row in source_rows]
    duplicate_source_ids = len(identities) - len(set(identities))
    if duplicate_source_ids:
        errors.append(f"{duplicate_source_ids} duplicate source identities")

    current_rows: list[dict[str, Any]] = []
    expired_count = cancelled_count = 0
    for row in source_rows:
        try:
            ended = date.fromisoformat(_clean(row.get("end_date")))
        except ValueError:
            errors.append(f"{_clean(row.get('provider_course_id'))}: invalid end date")
            continue
        if ended < cutoff:
            expired_count += 1
        elif _CANCELLED_RE.search(_clean(row.get("title"))):
            cancelled_count += 1
        else:
            current_rows.append(row)

    if len(current_rows) > allowed_details:
        source_cap_reached = True
        errors.append(
            f"detail_limit cap allows {allowed_details} of {len(current_rows)} "
            "required current/future details"
        )

    detailed: list[dict[str, Any]] = []
    detail_errors: list[str] = []
    detail_attempts = 0
    if not errors:
        detailed, detail_errors, detail_attempts = _details(
            current_rows,
            session_factory=session_factory,
            timeout=request_timeout,
            max_workers=workers,
            fetcher=fetcher,
        )
    errors.extend(detail_errors)
    details_complete = bool(
        not detail_errors
        and detail_attempts == len(current_rows)
        and len(detailed) == len(current_rows)
    )

    result: list[dict[str, Any]] = []
    if not errors and details_complete:
        deduper = dedupe_rows or _default_dedupe
        result = list(deduper(detailed))
        if len(result) != len(detailed):
            errors.append(
                f"dedupe changed complete row count {len(detailed)} to {len(result)}"
            )
            result = []
    result.sort(
        key=lambda row: (
            _clean(row.get("start_date")),
            _clean(row.get("title")),
            _clean(row.get("provider_course_id")),
        )
    )

    privacy_violations = _privacy_violations(result)
    if privacy_violations:
        errors.append(f"{privacy_violations} PII allowlist violations")
        result = []

    expected_rechecks = 1 if last_page == 1 else 2
    pagination_complete = bool(
        not errors
        and sentinel_count == len(pages.get(last_page, []))
        and len(stable_rechecks) == expected_rechecks
        and all(stable_rechecks.values())
        and meta["list_requests"] == required_list_requests
    )
    snapshot_complete = bool(pagination_complete and details_complete and not errors)
    if not snapshot_complete:
        result = []

    branch_counts = Counter(_clean(row.get("branch")) for row in result)
    status_counts = Counter(_clean(row.get("status")) for row in result)
    application_counts = Counter(_clean(row.get("application_type")) for row in result)
    meta.update(
        {
            "source_total": len(source_rows),
            "source_rows": len(source_rows),
            "declared_total": declared_total,
            "data_pages": last_page,
            "current_count": len(current_rows),
            "returned_count": len(result),
            "expired_count": expired_count,
            "cancelled_count": cancelled_count,
            "detail_attempts": detail_attempts,
            "detail_pages": len(detailed),
            "detail_errors": len(detail_errors),
            "sentinel_count": sentinel_count,
            "stable_rechecks": stable_rechecks,
            "duplicate_source_id_count": duplicate_source_ids,
            "privacy_violations": privacy_violations,
            "branch_count": len(branch_counts),
            "branch_counts": dict(branch_counts),
            "status_counts": dict(status_counts),
            "application_type_counts": dict(application_counts),
            "pagination_detected": last_page > 1,
            "pagination_complete": pagination_complete,
            "details_complete": details_complete,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": snapshot_complete,
            "source_cap_reached": source_cap_reached,
            "no_current_data": bool(snapshot_complete and not result),
            "no_current_reason": (
                "complete Sancheong catalogue contains only ended/cancelled courses"
                if snapshot_complete and not result
                else ""
            ),
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
    )
    return result, SANCHEONG_PARSER, meta


collect = collect_gyeongnam_sancheong_education_courses


__all__ = [
    "SANCHEONG_ALIASES",
    "SANCHEONG_APPLICANT_PATH",
    "SANCHEONG_CANDIDATE_DECISIONS",
    "SANCHEONG_CANDIDATE_IDS",
    "SANCHEONG_CONFIGURED_URL",
    "SANCHEONG_MUNICIPALITY_CODE",
    "SANCHEONG_MUNICIPALITY_NAME",
    "SANCHEONG_OWNERSHIP_SCOPE",
    "SANCHEONG_PARSER",
    "SANCHEONG_PROVIDER",
    "SANCHEONG_URL",
    "SancheongAlias",
    "collect",
    "collect_gyeongnam_sancheong_education_courses",
    "is_gyeongnam_sancheong_alias_target",
    "is_gyeongnam_sancheong_education_target",
    "is_target",
    "sancheong_application_url",
    "sancheong_detail_url",
    "sancheong_list_url",
]
