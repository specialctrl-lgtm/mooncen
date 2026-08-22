"""Complete, fail-closed education collector for Hadong-gun.

The configured ``/edu.web`` provider is the official lifelong-learning
discovery page.  Its course menu leads to one unfiltered, server-rendered
catalogue at ``/04326/03830/03831.web``.  The older ``/03826/...`` provider
redirects to that catalogue and an existing ``idx=1538`` provider is only one
record from it; neither is an independent source.

A snapshot is returned only after every advertised page, the site's clamped
immediate post-last request, and stable first/last rechecks agree.  Only
current/future rows receive detail requests.  Application buttons are
validated from the public list, but application forms are never fetched.
Contact numbers, instructor names, attachments, preparation/free text and
any applicant information are outside the returned allowlist.
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


HADONG_HOST = "www.hadong.go.kr"
HADONG_DISCOVERY_PATH = "/edu.web"
HADONG_LIST_PATH = "/04326/03830/03831.web"
HADONG_OLD_LIST_PATH = "/03826/03830/03831.web"
HADONG_FAC_CODE = "info-service"
HADONG_PROVIDER = "MUNI_WWW_HADONG_GO_KR_73A18CEA"
HADONG_MUNICIPALITY_CODE = "4885000000"
HADONG_MUNICIPALITY_NAME = "경상남도 하동군"
HADONG_PAGE_SIZE = 9
HADONG_MAX_WORKERS = 4
HADONG_FETCH_ATTEMPTS = 2
HADONG_CONFIGURED_URL = f"https://{HADONG_HOST}{HADONG_DISCOVERY_PATH}"
HADONG_URL = f"https://{HADONG_HOST}{HADONG_LIST_PATH}"
HADONG_PARSER = (
    "gyeongnam_hadong_complete_unfiltered_catalogue+declared_total+"
    "clamped_last_sentinel+stable_boundaries+current_detail_summary_only+"
    "public_application_control_no_form_fetch+pii_allowlist"
)
HADONG_OWNERSHIP_SCOPE = (
    "official_hadong_lifelong_learning_all_organizations_catalogue"
)

HADONG_CANDIDATE_IDS: Mapping[str, str] = {
    "official_lifelong_learning_home": "MUNI_IR_174FEF33F767",
    "rejected_general_notice_board": "MUNI_IR_5A5CE379E392",
}
HADONG_CANDIDATE_DECISIONS: Mapping[str, str] = {
    "MUNI_IR_174FEF33F767": (
        "include_existing_owner_as_discovery_for_complete_canonical_catalogue"
    ),
    "MUNI_IR_5A5CE379E392": (
        "exclude_general_notice_board_not_a_course_catalogue"
    ),
}


@dataclass(frozen=True)
class HadongAlias:
    provider: str
    url: str
    relationship: str


HADONG_ALIASES = (
    HadongAlias(
        "HADONG_WELFARE_ACADEMY_COURSE",
        f"https://{HADONG_HOST}{HADONG_OLD_LIST_PATH}",
        "legacy route redirects to the canonical complete catalogue",
    ),
    HadongAlias(
        "MUNI_WWW_HADONG_GO_KR_9CDD757E",
        f"https://{HADONG_HOST}{HADONG_LIST_PATH}?amode=view&idx=1538",
        "individual catalogue record; duplicate of the canonical owner",
    ),
    HadongAlias(
        "MUNI_WWW_HADONG_GO_KR_5C76EC72",
        f"https://{HADONG_HOST}/media/00012.web",
        "general notices board; rejected discovery candidate, not courses",
    ),
)

Fetcher = Callable[..., Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"[1-9]\d*")
_TOTAL_RE = re.compile(
    r"총\s*([\d,]+)\s*건의\s*교육이\s*있습니다\.\s*"
    r"\(\s*(\d+)\s*/\s*(\d+)\s*페이지\s*\)"
)
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[.-](\d{2})[.-](\d{2})(?:\.)?(?!\d)")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:010[-\s*]+[\d*]{3,4}[-\s*]+[\d*]{4}|"
    r"0\d{1,3}[-\s]?\d{3,4}[-\s]?\d{4}|0\d{8,11})(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_CANCELLED_RE = re.compile(r"(?:^|[<\[(])\s*(?:폐강|취소)\s*(?:$|[>\])])")

_DONG_OPTIONS: Mapping[str, str] = {
    "hadong": "하동읍",
    "hwagae": "화개면",
    "acyang": "악양면",
    "jucklyang": "적량면",
    "hoingchun": "횡천면",
    "gogun": "고전면",
    "gumnam": "금남면",
    "gumsung": "금성면",
    "jinkyo": "진교면",
    "yangbo": "양보면",
    "bukchun": "북천면",
    "chungam": "청암면",
    "okjong": "옥종면",
}
_LIST_REQUIRED = {
    "운영기관",
    "교육기간",
    "접수기간",
    "신청방법",
    "접수정원",
    "문의전화",
    "요일/시간",
    "교육대상",
    "수강료",
}
_LIST_OPTIONAL = {"대기정원"}
_DETAIL_REQUIRED = {
    "운영기관",
    "교육기간",
    "접수기간",
    "문의전화",
    "교육대상",
    "교육장소",
    "요일/시간",
    "수강료",
    "접수정원",
    "신청방법",
}
_DETAIL_OPTIONAL = {"대기정원", "강의자료", "준비물", "강사명"}
_METHODS = {
    "온라인신청",
    "온라인신청 , 전화접수",
    "온라인신청 , 전화접수 , 내방접수",
    "온라인신청 , 내방접수",
    "신청바로가기",
    "전화접수 , 내방접수 신청바로가기",
    "전화접수 , 내방접수",
    "온라인신청 , 전화접수 , 내방접수 신청바로가기",
}
_STATUS_MAP: Mapping[str, str] = {
    "접수대기": "SCHEDULED",
    "접수중": "OPEN",
    "대기접수": "OPEN",
    "접수마감": "CLOSED",
    "교육중": "CLOSED",
    "교육종료": "ARCHIVED",
}
_ACTIVE_CONTROL: Mapping[str, str] = {
    "접수중": "교육신청",
    "대기접수": "대기자신청",
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
        and (parsed.hostname or "").rstrip(".").lower() == HADONG_HOST
        and parsed.port is None
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


def is_gyeongnam_hadong_education_target(target: Any) -> bool:
    """Match only the existing owner at its discovery or canonical scope."""

    if _provider(target) != HADONG_PROVIDER:
        return False
    parsed = urlparse(_target_url(target))
    if not _safe_base(parsed):
        return False
    query = parse_qs(parsed.query, keep_blank_values=True)
    if parsed.path == HADONG_DISCOVERY_PATH:
        return not query
    return bool(
        parsed.path == HADONG_LIST_PATH
        and (
            not query
            or (
                set(query) == {"facCode"}
                and query.get("facCode") == [HADONG_FAC_CODE]
            )
        )
    )


is_target = is_gyeongnam_hadong_education_target


def is_gyeongnam_hadong_alias_target(target: Any) -> bool:
    provider = _provider(target)
    url = _target_url(target)
    return any(provider == alias.provider and url == alias.url for alias in HADONG_ALIASES)


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


def hadong_list_url(page: int = 1) -> str:
    if page < 1:
        return ""
    if page == 1:
        return HADONG_URL
    return HADONG_URL + "?" + urlencode(
        (("facCode", HADONG_FAC_CODE), ("cpage", str(page)))
    )


def hadong_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        return ""
    return HADONG_URL + "?" + urlencode(
        (("amode", "view"), ("idx", value), ("facCode", HADONG_FAC_CODE))
    )


def hadong_application_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        return ""
    return HADONG_URL + "?" + urlencode(
        (("amode", "ins"), ("lecIdx", value), ("facCode", HADONG_FAC_CODE))
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
    parsed = urlparse(final_url)
    if not _safe_base(parsed) or parsed.path != HADONG_LIST_PATH:
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
    messages: list[str] = []
    for attempt in range(1, HADONG_FETCH_ATTEMPTS + 1):
        try:
            if fetcher is not None:
                result = fetcher(current, "GET", url, timeout=timeout, data={})
                if (
                    isinstance(result, tuple)
                    and len(result) == 2
                    and isinstance(result[0], BeautifulSoup)
                ):
                    soup, final_url = result
                    parsed = urlparse(_clean(final_url or url))
                    if not _safe_base(parsed) or parsed.path != HADONG_LIST_PATH:
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


def _form_and_total(
    soup: BeautifulSoup,
    *,
    expected_display_page: int,
    expected_request_page: Optional[int] = None,
) -> tuple[int, int, list[str]]:
    errors: list[str] = []
    request_page = expected_display_page if expected_request_page is None else expected_request_page
    forms = soup.select("form#frmLecture[name='frmLecture']")
    if len(forms) != 1:
        return 0, 0, ["expected one unfiltered frmLecture"]
    form = forms[0]
    action = urlparse(urljoin(HADONG_URL, _clean(form.get("action"))))
    expected_action_query = (
        {}
        if request_page == 1
        else {"facCode": [HADONG_FAC_CODE], "cpage": [str(request_page)]}
    )
    if (
        _clean(form.get("method")).lower() != "get"
        or action.path != HADONG_LIST_PATH
        or parse_qs(action.query, keep_blank_values=True) != expected_action_query
    ):
        errors.append("unexpected frmLecture method/action")
    fac = form.select("input[type='hidden'][name='facCode']")
    if len(fac) != 1 or _clean(fac[0].get("value")) != HADONG_FAC_CODE:
        errors.append("frmLecture facCode mismatch")
    for name in ("dong", "target"):
        fields = form.select(f"select[name='{name}']")
        if len(fields) != 1 or _selected_value(fields[0]):
            errors.append(f"frmLecture {name} is not unfiltered")
    dong = form.select_one("select[name='dong']")
    dong_options = (
        {
            _clean(option.get("value")): _clean(option.get_text(" ", strip=True))
            for option in dong.select("option[value]:not([value=''])")
        }
        if dong is not None
        else {}
    )
    if dong_options != dict(_DONG_OPTIONS):
        errors.append("frmLecture township vocabulary changed")
    stype = form.select("select[name='stype']")
    if len(stype) != 1 or _selected_value(stype[0]) != "title":
        errors.append("frmLecture stype mismatch")
    sstring = form.select("input[name='sstring']")
    if len(sstring) != 1 or _clean(sstring[0].get("value")):
        errors.append("frmLecture sstring is not empty")

    totals = []
    for node in soup.select("div#body_content div.info1"):
        match = _TOTAL_RE.fullmatch(_clean(node.get_text(" ", strip=True)))
        if match:
            totals.append(match)
    if len(totals) != 1:
        return 0, 0, [*errors, "expected one declared education total"]
    total, displayed, last = (
        int(value.replace(",", "")) for value in totals[0].groups()
    )
    if displayed != expected_display_page:
        errors.append("declared current page mismatch")
    expected_last = max(1, math.ceil(total / HADONG_PAGE_SIZE))
    if last != expected_last:
        errors.append("declared last page does not match total/page size")
    return total, last, errors


def _pairs(nodes: Iterable[Any]) -> tuple[dict[str, str], list[str]]:
    result: dict[str, str] = {}
    errors: list[str] = []
    for node in nodes:
        label, separator, value = node.get_text(" ", strip=True).partition(":")
        key = _clean(label)
        if not separator or not key or key in result:
            errors.append("duplicate or malformed labelled field")
            continue
        result[key] = _clean(value)
    return result, errors


def _route_query(
    href: Any,
    *,
    source_page: int,
    mode: str,
    identity_key: str,
    identity: str = "",
) -> tuple[str, list[str]]:
    errors: list[str] = []
    parsed = urlparse(urljoin(HADONG_URL, _clean(href)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    expected_keys = {"amode", identity_key, "facCode"}
    if source_page > 1:
        expected_keys.add("cpage")
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != HADONG_HOST
        or parsed.path != HADONG_LIST_PATH
        or set(query) != expected_keys
        or query.get("amode") != [mode]
        or query.get("facCode") != [HADONG_FAC_CODE]
        or (source_page > 1 and query.get("cpage") != [str(source_page)])
    ):
        errors.append(f"malformed {mode} route")
    found = (query.get(identity_key) or [""])[0]
    if not _IDENTITY_RE.fullmatch(found):
        errors.append(f"missing {mode} source identity")
    if identity and found != identity:
        errors.append(f"{mode} source identity mismatch")
    return found, errors


def _parse_list_page(
    soup: BeautifulSoup, *, source_page: int
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    cards = soup.select(
        "div#body_content div.card1t3b1 > div.wrap1 > ul.even-grid > li.column"
    )
    detail_anchors = soup.select(
        "div#body_content div.card1t3b1 a[href*='amode=view']"
    )
    if len(cards) != len(detail_anchors):
        errors.append("course detail anchors are outside canonical cards")
    for index, card in enumerate(cards, 1):
        item_errors: list[str] = []
        titles = card.select("div.tg1.hybrid3row2 strong.t1")
        statuses = card.select("div.tg1.hybrid3row2 b.t2")
        title = _clean(titles[0].get_text(" ", strip=True)) if len(titles) == 1 else ""
        source_status = (
            _clean(statuses[0].get_text(" ", strip=True)) if len(statuses) == 1 else ""
        )
        if not title:
            item_errors.append("expected one nonempty title")
        if source_status not in _STATUS_MAP:
            item_errors.append("unknown source status")

        fields, pair_errors = _pairs(card.select("div.tg1:not(.hybrid3row2) ul > li"))
        item_errors.extend(pair_errors)
        if not _LIST_REQUIRED.issubset(fields) or not set(fields).issubset(
            _LIST_REQUIRED | _LIST_OPTIONAL
        ):
            item_errors.append("list field vocabulary changed")
        method = _clean(fields.get("신청방법"))
        omitted_closed_method = (
            not method
            and source_status in {"접수마감", "교육중", "교육종료"}
        )
        if method not in _METHODS and not omitted_closed_method:
            item_errors.append("unknown application method")
        education_dates = _dates(fields.get("교육기간"))
        application_dates = _dates(fields.get("접수기간"))
        if len(education_dates) != 2:
            item_errors.append("education period is malformed")
        if len(application_dates) != 2:
            item_errors.append("application period is malformed")
        organizer = _clean(fields.get("운영기관"))
        if not organizer:
            item_errors.append("operating organization is empty")

        anchors = card.select("div.btns a")
        detail = [a for a in anchors if _clean(a.get_text(" ", strip=True)) == "상세보기"]
        if len(detail) != 1 or not detail[0].has_attr("href"):
            item_errors.append("expected one detail control")
            identity = ""
        else:
            identity, route_errors = _route_query(
                detail[0].get("href"),
                source_page=source_page,
                mode="view",
                identity_key="idx",
            )
            item_errors.extend(route_errors)

        application_control = ""
        if source_status in _ACTIVE_CONTROL:
            expected_label = _ACTIVE_CONTROL[source_status]
            active = [
                a for a in anchors if _clean(a.get_text(" ", strip=True)) == expected_label
            ]
            if len(active) != 1 or _clean(active[0].get("data-online")) != "Y":
                item_errors.append("active application control changed")
            elif not active[0].has_attr("href"):
                item_errors.append("active application control has no route")
            else:
                _, route_errors = _route_query(
                    active[0].get("href"),
                    source_page=source_page,
                    mode="ins",
                    identity_key="lecIdx",
                    identity=identity,
                )
                item_errors.extend(route_errors)
                application_control = expected_label
            if len(anchors) != 2:
                item_errors.append("unexpected active card controls")
        elif source_status == "접수대기":
            disabled = [
                a for a in anchors if _clean(a.get_text(" ", strip=True)) == "접수대기"
            ]
            if (
                len(disabled) != 1
                or disabled[0].has_attr("href")
                or _clean(disabled[0].get("data-online")) not in {"Y", "N"}
                or len(anchors) != 2
            ):
                item_errors.append("scheduled application control changed")
            application_control = "접수대기"
        elif len(anchors) != 1:
            item_errors.append("closed course exposes unexpected controls")

        if item_errors:
            errors.extend(
                f"page {source_page} row {index}: {message}"
                for message in item_errors
            )
            continue
        active_application = source_status in _ACTIVE_CONTROL
        rows.append(
            {
                "provider": HADONG_PROVIDER,
                "provider_course_id": f"{HADONG_PROVIDER}:education:{identity}",
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "branch": organizer,
                "branch_code": (
                    "gyeongnam-hadong-"
                    + hashlib.sha256(organizer.encode("utf-8")).hexdigest()[:12]
                ),
                "municipality_code": HADONG_MUNICIPALITY_CODE,
                "municipality_name": HADONG_MUNICIPALITY_NAME,
                "sido": "경상남도",
                "sigungu": "하동군",
                "provider_organizer": organizer,
                "venue_name": organizer,
                "category": "평생학습",
                "program_type": "강좌",
                "raw_url": hadong_detail_url(identity),
                "application_url": (
                    hadong_application_url(identity) if active_application else ""
                ),
                "application_type": (
                    "ONLINE_RESERVATION" if active_application else "INFO_ONLY"
                ),
                "reservation_available": active_application,
                "status": _STATUS_MAP[source_status],
                "period": fields["교육기간"],
                "start_date": education_dates[0].isoformat(),
                "end_date": education_dates[1].isoformat(),
                "apply_period": fields["접수기간"],
                "apply_start": application_dates[0].isoformat(),
                "apply_end": application_dates[1].isoformat(),
                "schedule_raw": fields["요일/시간"],
                "fee": fields["수강료"],
                "target": fields["교육대상"],
                "description": title,
                "source_group": "lifelong_learning",
                "collection_category": "평생학습",
                "domain_category": "교육·강좌",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "operator_type": "지자체/공공기관",
                "collection_type": "complete_html_pages+current_detail_summary",
                "raw_fields": {
                    "parser": HADONG_PARSER,
                    "source_catalog": "hadong_lifelong_learning_complete",
                    "source_lecture_id": identity,
                    "source_page": source_page,
                    "source_status": source_status,
                    "source_application_method": fields["신청방법"],
                    "source_schedule": fields["요일/시간"],
                    "schedule_evidence": (
                        "official_list_and_detail"
                        if _clean(fields["요일/시간"])
                        else "official_list_and_detail_omit_time"
                    ),
                    "source_application_control": application_control,
                    "detail_validated": False,
                    "application_form_fetched": False,
                    "contact_excluded": True,
                    "instructor_excluded": True,
                    "attachments_excluded": True,
                    "preparation_and_free_text_excluded": True,
                    "applicant_data_excluded": True,
                },
            }
        )
        if not _clean(rows[-1]["schedule_raw"]):
            rows[-1]["schedule_raw"] = "시간 별도 안내"
    return rows, errors


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> str:
    values = [
        (
            _clean(row.get("provider_course_id")),
            _clean(row.get("title")),
            _clean(row.get("branch")),
            _clean(row.get("period")),
            _clean(row.get("apply_period")),
            _clean(row.get("raw_fields", {}).get("source_status")),
        )
        for row in rows
    ]
    return hashlib.sha256(repr(values).encode("utf-8")).hexdigest()


def _detail_row(
    parent: Mapping[str, Any], soup: BeautifulSoup, final_url: str
) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    raw = parent.get("raw_fields", {})
    identity = _clean(raw.get("source_lecture_id"))
    parsed = urlparse(final_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        set(query) != {"amode", "idx", "facCode"}
        or query.get("amode") != ["view"]
        or query.get("idx") != [identity]
        or query.get("facCode") != [HADONG_FAC_CODE]
    ):
        errors.append(f"detail {identity}: response scope changed")

    summaries = soup.select("div#body_content div.view1pic1info1")
    panels = soup.select("div#body_content div.view1pic1info1 div.panel10")
    headings = soup.select("div#body_content div.view1pic1info1 h1.h1")
    if len(summaries) != 1 or len(panels) != 1 or len(headings) != 1:
        return dict(parent), [*errors, f"detail {identity}: summary structure changed"]
    title = _clean(headings[0].get_text(" ", strip=True))
    if title != _clean(parent.get("title")):
        errors.append(f"detail {identity}: title mismatch")
    pairs, pair_errors = _pairs(panels[0].select("li"))
    errors.extend(f"detail {identity}: {message}" for message in pair_errors)
    if not _DETAIL_REQUIRED.issubset(pairs) or not set(pairs).issubset(
        _DETAIL_REQUIRED | _DETAIL_OPTIONAL
    ):
        errors.append(f"detail {identity}: field vocabulary changed")
    if soup.select("div#body_content table"):
        errors.append(f"detail {identity}: unexpected table/applicant surface")

    detail_education = _dates(pairs.get("교육기간"))
    detail_application = _dates(pairs.get("접수기간"))
    if len(detail_education) != 2 or [item.isoformat() for item in detail_education] != [
        _clean(parent.get("start_date")),
        _clean(parent.get("end_date")),
    ]:
        errors.append(f"detail {identity}: education period mismatch")
    if len(detail_application) != 2 or [
        item.isoformat() for item in detail_application
    ] != [
        _clean(parent.get("apply_start")),
        _clean(parent.get("apply_end")),
    ]:
        errors.append(f"detail {identity}: application period mismatch")
    comparisons = {
        "운영기관": "branch",
        "교육대상": "target",
        "수강료": "fee",
    }
    for label, key in comparisons.items():
        if _clean(pairs.get(label)) != _clean(parent.get(key)):
            errors.append(f"detail {identity}: {label} mismatch")
    if _clean(pairs.get("요일/시간")) != _clean(raw.get("source_schedule")):
        errors.append(f"detail {identity}: 요일/시간 mismatch")
    if _clean(pairs.get("신청방법")) != _clean(raw.get("source_application_method")):
        errors.append(f"detail {identity}: application method mismatch")
    venue = _clean(pairs.get("교육장소"))
    if not venue:
        errors.append(f"detail {identity}: education venue is empty")

    row = dict(parent)
    row["venue_name"] = venue
    row["raw_fields"] = {
        **raw,
        "detail_validated": not errors,
        "detail_summary_only": True,
        "application_form_fetched": False,
        "contact_excluded": True,
        "instructor_excluded": True,
        "attachments_excluded": True,
        "preparation_and_free_text_excluded": True,
        "applicant_data_excluded": True,
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
                hadong_detail_url(identity),
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
                errors.append(
                    f"detail {identity}: {type(exc).__name__}: {_clean(exc)}"
                )
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
        "municipality_code": HADONG_MUNICIPALITY_CODE,
        "municipality_name": HADONG_MUNICIPALITY_NAME,
        "ownership_scope": HADONG_OWNERSHIP_SCOPE,
        "candidate_ids": dict(HADONG_CANDIDATE_IDS),
        "candidate_decisions": dict(HADONG_CANDIDATE_DECISIONS),
        "ownership_aliases": [
            {
                "provider": alias.provider,
                "url": alias.url,
                "relationship": alias.relationship,
            }
            for alias in HADONG_ALIASES
        ],
    }


def collect_gyeongnam_hadong_education_courses(
    target: Any,
    timeout: int = 30,
    max_pages: int = 80,
    detail_limit: int = 250,
    *,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = HADONG_MAX_WORKERS,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect the complete current/future official Hadong course snapshot."""

    meta = _base_meta()
    if not is_gyeongnam_hadong_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match the Hadong discovery/canonical education owner"
        )
        return [], HADONG_PARSER, meta
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta["configured_collection_error"] = "managed session_factory injection is required"
            return [], HADONG_PARSER, meta
        session_factory = _default_session_factory
    try:
        allowed_pages = max(0, int(max_pages))
        allowed_details = max(0, int(detail_limit))
        request_timeout = max(1, int(timeout))
        workers = min(max(1, int(max_workers)), HADONG_MAX_WORKERS)
        cutoff = _today(today)
    except (TypeError, ValueError):
        meta["configured_collection_error"] = (
            "timeout/max_pages/detail_limit/max_workers/today are invalid"
        )
        return [], HADONG_PARSER, meta

    errors: list[str] = []
    source_cap_reached = False
    first_rows: list[dict[str, Any]] = []
    declared_total = last_page = 0
    initial = session_factory()
    try:
        try:
            first_soup, _ = _request_soup(
                initial,
                hadong_list_url(1),
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
                    hadong_list_url(page),
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
                hadong_list_url(last_page + 1),
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
                    hadong_list_url(page),
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
        if len(pages.get(page, [])) != HADONG_PAGE_SIZE:
            errors.append(f"page {page}: expected a full page")
    last_count = len(pages.get(last_page, [])) if last_page else 0
    if declared_total == 0:
        if last_count:
            errors.append("empty catalogue has a nonempty last page")
    elif last_page and not 1 <= last_count <= HADONG_PAGE_SIZE:
        errors.append("last page cardinality is invalid")
    if declared_total != len(source_rows):
        errors.append(
            f"declared total {declared_total} != parsed total {len(source_rows)}"
        )
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
                "complete Hadong catalogue contains only ended/cancelled courses"
                if snapshot_complete and not result
                else ""
            ),
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
    )
    return result, HADONG_PARSER, meta


collect = collect_gyeongnam_hadong_education_courses


__all__ = [
    "HADONG_ALIASES",
    "HADONG_CANDIDATE_DECISIONS",
    "HADONG_CANDIDATE_IDS",
    "HADONG_CONFIGURED_URL",
    "HADONG_MUNICIPALITY_CODE",
    "HADONG_MUNICIPALITY_NAME",
    "HADONG_OWNERSHIP_SCOPE",
    "HADONG_PARSER",
    "HADONG_PROVIDER",
    "HADONG_URL",
    "HadongAlias",
    "collect",
    "collect_gyeongnam_hadong_education_courses",
    "hadong_application_url",
    "hadong_detail_url",
    "hadong_list_url",
    "is_gyeongnam_hadong_alias_target",
    "is_gyeongnam_hadong_education_target",
    "is_target",
]
