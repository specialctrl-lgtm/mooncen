"""Fail-closed collector for Changnyeong-gun's integrated education ledger.

The official Changnyeong-gun lifelong-learning page is an unfiltered ledger
covering every education facility exposed by the municipality.  The shorter
``/02389.web`` route redirects to the canonical page and is not a second
owner.  The Gyeongsangnam-do Office of Education Changnyeong Library is a
separate public owner and must not be used as municipality coverage.

The source advertises its final page but no numeric total.  A snapshot is
published only after every advertised page, the server's exact clamped
post-last page, and stable first/last boundary rechecks agree.  Detail pages
are requested only for current/future courses.  Application controls are
validated but application forms are never fetched.  Instructor/contact
fields, images, attachments, and free-text detail bodies are excluded from
the returned public-summary allowlist.
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


CHANGNYEONG_HOST = "www.cng.go.kr"
CHANGNYEONG_LIST_PATH = "/00595/00606/02389.web"
CHANGNYEONG_SHORT_PATH = "/02389.web"
CHANGNYEONG_PROVIDER = "MUNI_WWW_CNG_GO_KR_84B93860"
CHANGNYEONG_MUNICIPALITY_CODE = "4874000000"
CHANGNYEONG_MUNICIPALITY_NAME = "경상남도 창녕군"
CHANGNYEONG_PAGE_SIZE = 9
CHANGNYEONG_MAX_WORKERS = 3
CHANGNYEONG_FETCH_ATTEMPTS = 2
CHANGNYEONG_URL = f"https://{CHANGNYEONG_HOST}{CHANGNYEONG_LIST_PATH}"
CHANGNYEONG_PARSER = (
    "gyeongnam_changnyeong_complete_integrated_education+advertised_last_page+"
    "clamped_last_sentinel+stable_boundaries+current_detail_summary_only+"
    "application_control_no_form_fetch+pii_allowlist"
)
CHANGNYEONG_OWNERSHIP_SCOPE = (
    "official_changnyeong_integrated_education_all_facilities"
)

CHANGNYEONG_CANDIDATE_IDS: Mapping[str, str] = {
    "canonical_complete_ledger": "MUNI_IR_04BAD6FB9F65",
    "short_redirect_alias": "MUNI_IR_35DA60303072",
    "separate_provincial_library": "MUNI_IR_CC8A962EE8FB",
    "metadata_api_page": "MUNI_IR_633F4DEC9CBE",
}
CHANGNYEONG_CANDIDATE_DECISIONS: Mapping[str, str] = {
    "MUNI_IR_04BAD6FB9F65": "schedule_new_complete_unfiltered_education_ledger",
    "MUNI_IR_35DA60303072": "retarget_redirect_alias_to_canonical_ledger",
    "MUNI_IR_CC8A962EE8FB": "exclude_separate_provincial_library_owner",
    "MUNI_IR_633F4DEC9CBE": "exclude_metadata_page_not_course_ledger",
}


@dataclass(frozen=True)
class ChangnyeongAlias:
    provider: str
    url: str
    relationship: str


CHANGNYEONG_ALIASES = (
    ChangnyeongAlias(
        "MUNI_WWW_CNG_GO_KR_361E8A30",
        f"https://{CHANGNYEONG_HOST}{CHANGNYEONG_SHORT_PATH}",
        "short official route redirects to the canonical complete ledger",
    ),
)

CHANGNYEONG_SEPARATE_PUBLIC_PROVIDERS = (
    "MUNI_CNLIB_GNE_GO_KR_A3514402",
)

# Exact vocabulary exposed by the official unfiltered facility selector on
# 2026-07-22.  A change deliberately fails closed until ownership is reviewed.
CHANGNYEONG_FACILITIES: Mapping[str, str] = {
    "001": "군청 평생학습관",
    "053": "군민아카데미",
    "032": "EBS 강사초청 진학설명회",
    "003": "창녕군여성회관",
    "006": "창녕군청소년수련관",
    "025": "남지청소년문화의집",
    "007": "영산청소년문화의집",
    "024": "문화예술회관",
    "004": "농업기술센터",
    "021": "우포늪생태관",
    "018": "남지읍주민자치센터",
    "009": "대합면주민자치센터",
    "010": "도천면주민자치센터",
    "026": "대합노인복지회관",
    "008": "남지종합복지관",
    "012": "창녕노인복지회관",
    "020": "영산노인복지회관",
    "054": "창녕다움 식생활 교육관",
    "030": "영산도서관",
    "040": "창녕읍주민자치센터",
    "042": "고암면주민자치센터",
    "043": "성산면주민자치센터",
    "044": "이방면주민자치센터",
    "045": "유어면주민자치센터",
    "046": "대지면주민자치센터",
    "047": "계성면주민자치센터",
    "048": "영산면주민자치센터",
    "049": "장마면주민자치센터",
    "050": "길곡면주민자치센터",
    "051": "부곡면주민자치센터",
    "055": "청년센터",
}

Fetcher = Callable[..., Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"[1-9]\d*")
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[.-](\d{2})[.-](\d{2})(?:\.)?(?!\d)")
_TITLE_RE = re.compile(r"^\[([^\]]+)\]\s*-\s*(.+)$")
_CANCELLED_RE = re.compile(
    r"(?:^|[<\[(])\s*(?:취소|폐강)\s*(?:$|[>\])])",
    re.IGNORECASE,
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:010[-\s*]+[\d*]{3,4}[-\s*]+[\d*]{4}|"
    r"0\d{1,3}[-\s]?\d{3,4}[-\s]?\d{4}|0\d{8,11})(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_MASKED_NAME_RE = re.compile(r"(?<![가-힣])[가-힣][OoＯ○*]{2}(?![가-힣])")

_LIST_LABELS = ("모집인원", "교육기간", "교육시간", "모집기간", "접수방법")
_DETAIL_LABELS = {
    "시설구분",
    "접수방법",
    "교육기간",
    "교육시간",
    "모집기간",
    "대상",
    "강사명",
    "교육장소",
    "수강료",
    "문의전화",
}
_FILTER_STATUSES: Mapping[str, str] = {
    "1": "접수중",
    "2": "교육신청",
    "3": "신청마감",
    "4": "접수대기",
    "5": "대기자신청",
    "6": "신청완료",
}
_STATUS_MAP: Mapping[str, str] = {
    "접수중": "OPEN",
    "교육신청": "OPEN",
    "대기자신청": "WAITING",
    "접수대기": "SCHEDULED",
    "신청마감": "CLOSED",
    "신청완료": "CLOSED",
}
_CONTROL_LABELS: Mapping[str, tuple[str, ...]] = {
    # Online rows show "교육신청" while otherwise-active onsite-only rows
    # show "접수중".  Both share the same data-progress value.
    "접수중": ("교육신청", "접수중"),
    "교육신청": ("교육신청",),
    "대기자신청": ("대기자신청",),
}
_CAPACITY_SCHEMAS = {
    ("모집정원",),
    ("모집정원", "신청인원"),
    ("모집정원", "대기인원"),
    ("모집정원", "신청인원", "대기인원"),
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


def is_gyeongnam_changnyeong_education_target(target: Any) -> bool:
    parsed = urlparse(_target_url(target))
    return bool(
        _provider(target) == CHANGNYEONG_PROVIDER
        and parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == CHANGNYEONG_HOST
        and parsed.port is None
        and parsed.path == CHANGNYEONG_LIST_PATH
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


is_target = is_gyeongnam_changnyeong_education_target


def is_gyeongnam_changnyeong_alias_target(target: Any) -> bool:
    return any(
        _provider(target) == alias.provider and _target_url(target) == alias.url
        for alias in CHANGNYEONG_ALIASES
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


def changnyeong_list_url(page: int = 1) -> str:
    if page < 1:
        return ""
    if page == 1:
        return CHANGNYEONG_URL
    return f"{CHANGNYEONG_URL}?{urlencode((('cpage', str(page)),))}"


def changnyeong_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        return ""
    return f"{CHANGNYEONG_URL}?{urlencode((('amode', 'view'), ('idx', value)))}"


def changnyeong_application_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        return ""
    return (
        f"{CHANGNYEONG_URL}?"
        + urlencode((('amode', 'ins_realname'), ('lecIdx', value)))
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
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != CHANGNYEONG_HOST
        or parsed.port is not None
        or parsed.path != CHANGNYEONG_LIST_PATH
        or parsed.username
        or parsed.password
    ):
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
) -> tuple[BeautifulSoup, str, int]:
    messages: list[str] = []
    for attempt in range(1, CHANGNYEONG_FETCH_ATTEMPTS + 1):
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
                    if (
                        parsed.scheme.lower() != "https"
                        or (parsed.hostname or "").rstrip(".").lower()
                        != CHANGNYEONG_HOST
                        or parsed.path != CHANGNYEONG_LIST_PATH
                        or parsed.port is not None
                    ):
                        raise ValueError("source response URL changed")
                    return soup, _clean(final_url or url), attempt - 1
                if isinstance(result, BeautifulSoup):
                    return result, url, attempt - 1
                if isinstance(result, (str, bytes, bytearray)):
                    if not result:
                        raise ValueError("empty HTML response")
                    return BeautifulSoup(result, "lxml"), url, attempt - 1
                soup, final_url = _response_soup(result)
                return soup, final_url, attempt - 1
            soup, final_url = _response_soup(current.get(url, timeout=timeout))
            return soup, final_url, attempt - 1
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


def _pagination_page(anchor: Any) -> int:
    parsed = urlparse(urljoin(CHANGNYEONG_URL, _clean(anchor.get("href"))))
    query = parse_qs(parsed.query, keep_blank_values=True)
    value = _clean((query.get("cpage") or [""])[0])
    if (
        parsed.path != CHANGNYEONG_LIST_PATH
        or set(query) != {"cpage"}
        or any(len(values) != 1 for values in query.values())
        or not _IDENTITY_RE.fullmatch(value)
    ):
        return 0
    return int(value)


def _form_and_last_page(
    soup: BeautifulSoup,
    *,
    requested_page: int,
    expected_display_page: int,
    known_last_page: int = 0,
) -> tuple[int, list[str]]:
    errors: list[str] = []
    forms = soup.select("form#frmLecture[name='frmLecture']")
    if len(forms) != 1:
        return 0, ["expected one unfiltered frmLecture"]
    form = forms[0]
    action = urlparse(urljoin(CHANGNYEONG_URL, _clean(form.get("action"))))
    action_query = parse_qs(action.query, keep_blank_values=True)
    expected_action_query = (
        {} if requested_page == 1 else {"cpage": [str(requested_page)]}
    )
    if (
        _clean(form.get("method")).lower() != "get"
        or action.path != CHANGNYEONG_LIST_PATH
        or action_query != expected_action_query
    ):
        errors.append("unexpected frmLecture method/action")

    facilities = form.select("select[name='facCode']")
    if len(facilities) != 1 or _selected_value(facilities[0]):
        errors.append("frmLecture facCode is not unfiltered")
    else:
        actual = {
            _clean(option.get("value")): _clean(option.get_text(" ", strip=True))
            for option in facilities[0].select("option[value]")
            if _clean(option.get("value"))
        }
        if actual != dict(CHANGNYEONG_FACILITIES):
            errors.append("official facility vocabulary changed")

    statuses = form.select("select[name='applyGubun']")
    if len(statuses) != 1 or _selected_value(statuses[0]):
        errors.append("frmLecture applyGubun is not unfiltered")
    else:
        actual = {
            _clean(option.get("value")): _clean(option.get_text(" ", strip=True))
            for option in statuses[0].select("option[value]")
            if _clean(option.get("value"))
        }
        if actual != dict(_FILTER_STATUSES):
            errors.append("official application-status vocabulary changed")

    stype = form.select("select[name='stype']")
    if len(stype) != 1 or _selected_value(stype[0]) != "title":
        errors.append("frmLecture stype mismatch")
    sstring = form.select("input[name='sstring']")
    if len(sstring) != 1 or _clean(sstring[0].get("value")):
        errors.append("frmLecture search string is not empty")

    pagers = soup.select("div.pagination")
    if len(pagers) != 1:
        return 0, [*errors, "expected one pagination block"]
    pager = pagers[0]
    current = pager.select("span.pages span.m.on a")
    if (
        len(current) != 1
        or not _clean(current[0].get_text(" ", strip=True)).isdigit()
        or int(_clean(current[0].get_text(" ", strip=True)))
        != expected_display_page
    ):
        errors.append("displayed current page mismatch")

    linked_pages: list[int] = []
    for anchor in pager.select("a[href]"):
        value = _pagination_page(anchor)
        if not value:
            errors.append("malformed pagination link")
        else:
            linked_pages.append(value)

    last_links = pager.select("span.last a[href]")
    if known_last_page:
        if len(last_links) > 1:
            errors.append("multiple last-page controls")
        elif last_links and _pagination_page(last_links[0]) != known_last_page:
            errors.append("last-page control changed")
        if any(page > known_last_page for page in linked_pages):
            errors.append("pagination link exceeds advertised final page")
        return known_last_page, errors

    if len(last_links) != 1:
        return 0, [*errors, "first page lacks one advertised final-page control"]
    last_page = _pagination_page(last_links[0])
    if not last_page or last_page < expected_display_page:
        errors.append("advertised final page is invalid")
    return last_page, errors


def _capacity(value: Any) -> Optional[int]:
    match = re.search(r"(?<!\d)(\d[\d,]*)\s*명", _clean(value))
    return int(match.group(1).replace(",", "")) if match else None


def _parse_list_page(
    soup: BeautifulSoup,
    *,
    source_page: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    cards = soup.select("div.cp8card1 > ul > li.column")
    scoped_anchors = soup.select("div.cp8card1 a.a1[href*='amode=view']")
    if len(cards) != len(scoped_anchors):
        errors.append("course anchors are outside canonical card scope")

    official_facilities = set(CHANGNYEONG_FACILITIES.values())
    for index, card in enumerate(cards, 1):
        item_errors: list[str] = []
        anchors = card.select("a.a1[href]")
        anchor = anchors[0] if len(anchors) == 1 else None
        query: dict[str, list[str]] = {}
        if anchor is None:
            item_errors.append("expected one course detail anchor")
        else:
            parsed = urlparse(urljoin(CHANGNYEONG_URL, _clean(anchor.get("href"))))
            query = parse_qs(parsed.query, keep_blank_values=True)
            expected_query_keys = (
                {"amode", "idx"}
                if source_page == 1
                else {"amode", "idx", "cpage"}
            )
            if (
                parsed.scheme.lower() != "https"
                or (parsed.hostname or "").rstrip(".").lower()
                != CHANGNYEONG_HOST
                or parsed.path != CHANGNYEONG_LIST_PATH
                or set(query) != expected_query_keys
                or query.get("amode") != ["view"]
                or (
                    source_page > 1
                    and query.get("cpage") != [str(source_page)]
                )
                or any(len(values) != 1 for values in query.values())
            ):
                item_errors.append("malformed course detail route")
        identity = _clean((query.get("idx") or [""])[0])
        if not _IDENTITY_RE.fullmatch(identity):
            item_errors.append("missing source identity")

        title_node = anchor.select_one("div.tg1 strong.t1") if anchor else None
        displayed_title = _clean(
            title_node.get_text(" ", strip=True) if title_node is not None else ""
        )
        title_match = _TITLE_RE.fullmatch(displayed_title)
        if not title_match:
            item_errors.append("title does not expose a facility prefix")
            facility = title = ""
        else:
            facility, title = (_clean(value) for value in title_match.groups())

        place_nodes = anchor.select("div.tg2 span.place1") if anchor else []
        place = ""
        if len(place_nodes) != 1:
            item_errors.append("expected one course facility label")
        else:
            place = _clean(place_nodes[0].get_text(" ", strip=True)).strip("[] ")
            if place != facility:
                item_errors.append("title/facility label mismatch")
        if facility not in official_facilities:
            item_errors.append("course facility is outside official vocabulary")

        fields: dict[str, str] = {}
        for node in anchor.select("div.tg2 span.li1") if anchor else []:
            labels = node.select("span.t1")
            values = node.select("span.t2")
            if len(labels) != 1 or len(values) != 1:
                item_errors.append("malformed list field")
                continue
            key = _clean(labels[0].get_text(" ", strip=True))
            if not key or key in fields:
                item_errors.append("duplicate or empty list field")
                continue
            fields[key] = _clean(values[0].get_text(" ", strip=True))
        if set(fields) != set(_LIST_LABELS):
            item_errors.append("list field vocabulary changed")

        status_nodes = anchor.select("div.tg1 i.c[data-progress]") if anchor else []
        source_status = control_label = ""
        if len(status_nodes) != 1:
            item_errors.append("expected one source status badge")
        else:
            source_status = _clean(status_nodes[0].get("data-progress"))
            control_label = _clean(status_nodes[0].get_text(" ", strip=True))
            if source_status not in _STATUS_MAP:
                item_errors.append(f"unknown source status {source_status!r}")
            expected_labels = _CONTROL_LABELS.get(
                source_status, (source_status,)
            )
            if control_label not in expected_labels:
                item_errors.append("source status badge label mismatch")

        period_dates = _dates(fields.get("교육기간"))
        apply_dates = _dates(fields.get("모집기간"))
        if len(period_dates) != 2:
            item_errors.append("education period is malformed")
        if len(apply_dates) != 2:
            item_errors.append("application period is malformed")
        capacity = _capacity(fields.get("모집인원"))
        if capacity is None:
            item_errors.append("list capacity is malformed")

        if item_errors:
            errors.extend(
                f"page {source_page} row {index}: {message}"
                for message in item_errors
            )
            continue

        rows.append(
            {
                "provider": CHANGNYEONG_PROVIDER,
                "provider_course_id": (
                    f"{CHANGNYEONG_PROVIDER}:education:{identity}"
                ),
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "branch": facility,
                "branch_code": (
                    "gyeongnam-changnyeong-"
                    + hashlib.sha256(facility.encode("utf-8")).hexdigest()[:12]
                ),
                "municipality_code": CHANGNYEONG_MUNICIPALITY_CODE,
                "municipality_name": CHANGNYEONG_MUNICIPALITY_NAME,
                "sido": "경상남도",
                "sigungu": "창녕군",
                "provider_organizer": facility,
                "venue_name": facility,
                "category": "평생학습",
                "program_type": "강좌",
                "raw_url": changnyeong_detail_url(identity),
                "application_url": "",
                "application_type": "INFO_ONLY",
                "reservation_available": False,
                "status": _STATUS_MAP[source_status],
                "period": fields["교육기간"],
                "start_date": period_dates[0].isoformat(),
                "end_date": period_dates[1].isoformat(),
                "apply_period": fields["모집기간"],
                "apply_start": apply_dates[0].isoformat(),
                "apply_end": apply_dates[1].isoformat(),
                "schedule_raw": fields["교육시간"],
                "fee": "",
                "target": "",
                "capacity": capacity,
                "capacity_total": capacity,
                "description": title,
                "source_group": "lifelong_learning",
                "collection_category": "평생학습",
                "domain_category": "교육·강좌",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "operator_type": "지자체/공공기관",
                "collection_type": "complete_html_pages+current_detail_summary",
                "raw_fields": {
                    "parser": CHANGNYEONG_PARSER,
                    "source_catalog": "changnyeong_integrated_education",
                    "source_education_id": identity,
                    "source_page": source_page,
                    "source_facility": facility,
                    "source_status": source_status,
                    "source_badge_label": control_label,
                    "source_application_method": fields["접수방법"],
                },
            }
        )
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


def _detail_pairs(root: Any) -> tuple[dict[str, str], list[str]]:
    pairs: dict[str, str] = {}
    rows = root.select("div.texts > ul.bu > li")
    for row in rows:
        labels = row.select("span.dt")
        values = row.select("span.dd")
        if len(labels) != 1 or len(values) != 1:
            return {}, ["malformed public summary row"]
        key = _clean(labels[0].get_text(" ", strip=True)).rstrip(":").strip()
        if not key or key in pairs:
            return {}, ["duplicate or empty public summary label"]
        pairs[key] = _clean(values[0].get_text(" ", strip=True))
    if set(pairs) != _DETAIL_LABELS:
        return pairs, ["course summary field vocabulary changed"]
    return pairs, []


def _capacity_table(soup: BeautifulSoup) -> tuple[dict[str, str], list[str]]:
    tables = soup.select("table.tbl1.t2")
    if len(tables) != 1:
        return {}, ["expected one public capacity table"]
    headings = tuple(
        _clean(node.get_text(" ", strip=True))
        for node in tables[0].select("thead tr > th")
    )
    values = tuple(
        _clean(node.get_text(" ", strip=True))
        for node in tables[0].select("tbody tr:first-child > td")
    )
    if headings not in _CAPACITY_SCHEMAS or len(values) != len(headings):
        return {}, ["capacity table schema changed"]
    return dict(zip(headings, values)), []


def _detail_row(
    parent: dict[str, Any], soup: BeautifulSoup
) -> tuple[dict[str, Any], list[str]]:
    raw = dict(parent.get("raw_fields", {}))
    identity = _clean(raw.get("source_education_id"))
    source_status = _clean(raw.get("source_status"))
    errors: list[str] = []
    roots = soup.select("div.cp31edu1view1")
    if len(roots) != 1:
        return dict(parent), [f"detail {identity}: expected one course detail root"]
    root = roots[0]
    titles = root.select("div.texts > h2.h2")
    if len(titles) != 1:
        errors.append(f"detail {identity}: expected one title")
    elif _clean(titles[0].get_text(" ", strip=True)) != _clean(parent.get("title")):
        errors.append(f"detail {identity}: list/detail title mismatch")

    pairs, pair_errors = _detail_pairs(root)
    errors.extend(f"detail {identity}: {message}" for message in pair_errors)
    if _clean(pairs.get("시설구분")) != _clean(parent.get("branch")):
        errors.append(f"detail {identity}: list/detail facility mismatch")
    expected_period = [
        date.fromisoformat(_clean(parent.get("start_date"))),
        date.fromisoformat(_clean(parent.get("end_date"))),
    ]
    expected_apply = [
        date.fromisoformat(_clean(parent.get("apply_start"))),
        date.fromisoformat(_clean(parent.get("apply_end"))),
    ]
    if _dates(pairs.get("교육기간")) != expected_period:
        errors.append(f"detail {identity}: list/detail education period mismatch")
    if _dates(pairs.get("모집기간")) != expected_apply:
        errors.append(f"detail {identity}: list/detail application period mismatch")
    method = _clean(pairs.get("접수방법"))
    if not method:
        errors.append(f"detail {identity}: application method is empty")
    schedule = _clean(pairs.get("교육시간"))

    capacity_pairs, capacity_errors = _capacity_table(soup)
    errors.extend(f"detail {identity}: {message}" for message in capacity_errors)
    capacity = _capacity(capacity_pairs.get("모집정원"))
    if capacity is None or capacity != parent.get("capacity_total"):
        errors.append(f"detail {identity}: list/detail capacity mismatch")
    capacity_current = _capacity(capacity_pairs.get("신청인원"))

    controls = soup.select("div.infomenu1 a.button.primary[href]")
    if len(controls) > 1:
        errors.append(f"detail {identity}: multiple primary application controls")
    control_label = _clean(controls[0].get_text(" ", strip=True)) if controls else ""
    control_url = ""
    if controls:
        parsed = urlparse(urljoin(CHANGNYEONG_URL, _clean(controls[0].get("href"))))
        query = parse_qs(parsed.query, keep_blank_values=True)
        if (
            parsed.scheme.lower() != "https"
            or (parsed.hostname or "").rstrip(".").lower() != CHANGNYEONG_HOST
            or parsed.path != CHANGNYEONG_LIST_PATH
            or set(query) != {"amode", "lecIdx"}
            or query.get("amode") != ["ins_realname"]
            or query.get("lecIdx") != [identity]
            or any(len(values) != 1 for values in query.values())
        ):
            errors.append(f"detail {identity}: malformed application control")
        elif source_status not in _CONTROL_LABELS:
            errors.append(f"detail {identity}: inactive status exposes application control")
        elif control_label not in _CONTROL_LABELS[source_status]:
            errors.append(f"detail {identity}: list/detail application-control mismatch")
        elif "온라인" not in method:
            errors.append(f"detail {identity}: active control lacks online method")
        else:
            control_url = changnyeong_application_url(identity)

    reservation_available = bool(control_url)
    application_type = "INFO_ONLY"
    if reservation_available:
        application_type = (
            "WAITLIST_APPLY"
            if source_status == "대기자신청"
            else "ONLINE_RESERVATION"
        )

    venue = _clean(pairs.get("교육장소")) or _clean(parent.get("branch"))
    row = dict(parent)
    row.update(
        {
            "application_url": control_url,
            "application_type": application_type,
            "reservation_available": reservation_available,
            "status": _STATUS_MAP.get(source_status, ""),
            "period": _clean(pairs.get("교육기간")),
            "apply_period": _clean(pairs.get("모집기간")),
            "schedule_raw": schedule or "공식 페이지 시간 미기재",
            "fee": _clean(pairs.get("수강료")),
            "target": _clean(pairs.get("대상")),
            "venue_name": venue,
            "capacity": capacity,
            "capacity_total": capacity,
            "capacity_current": capacity_current,
        }
    )
    row["raw_fields"] = {
        **raw,
        "source_application_method": method,
        "source_application_control": control_label,
        "source_capacity_schema": list(capacity_pairs),
        "source_waiting_capacity_raw": _clean(capacity_pairs.get("대기인원")),
        "schedule_source_omission": not schedule,
        "detail_validated": not errors,
        "application_form_fetched": False,
        "instructor_excluded": True,
        "contact_excluded": True,
        "images_excluded": True,
        "attachments_excluded": True,
        "free_text_excluded": True,
        "applicant_data_excluded": True,
    }
    return row, errors


def _fetch_page(
    page: int,
    *,
    session_factory: SessionFactory,
    timeout: int,
    fetcher: Optional[Fetcher],
) -> tuple[BeautifulSoup, str, int]:
    current = session_factory()
    try:
        return _request_soup(
            current,
            changnyeong_list_url(page),
            timeout=timeout,
            fetcher=fetcher,
        )
    finally:
        _close_quietly(current)


def _fetch_detail(
    row: dict[str, Any],
    *,
    session_factory: SessionFactory,
    timeout: int,
    fetcher: Optional[Fetcher],
) -> tuple[dict[str, Any], list[str], int]:
    current = session_factory()
    try:
        identity = _clean(row.get("raw_fields", {}).get("source_education_id"))
        soup, _, retries = _request_soup(
            current,
            changnyeong_detail_url(identity),
            timeout=timeout,
            fetcher=fetcher,
        )
        detailed, errors = _detail_row(row, soup)
        return detailed, errors, retries
    finally:
        _close_quietly(current)


def _default_dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _semantic_signature(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        _clean(row.get("branch")),
        _clean(row.get("title")).casefold(),
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
        _clean(row.get("schedule_raw")).casefold(),
    )


def _privacy_violations(rows: Iterable[Mapping[str, Any]]) -> int:
    violations = 0
    forbidden = {
        "phone",
        "email",
        "instructor",
        "teacher",
        "applicant",
        "contact",
        "preparation",
    }
    for row in rows:
        visible = " ".join(
            _clean(row.get(key))
            for key in (
                "title",
                "branch",
                "provider_organizer",
                "venue_name",
                "target",
                "description",
                "fee",
                "schedule_raw",
            )
        )
        violations += len(_PHONE_RE.findall(visible))
        violations += len(_EMAIL_RE.findall(visible))
        violations += len(_MASKED_NAME_RE.findall(visible))
        violations += sum(key in row for key in forbidden)
        raw = row.get("raw_fields", {})
        if isinstance(raw, Mapping):
            violations += sum(key in raw for key in forbidden)
            source_labels = " ".join(
                _clean(raw.get(key))
                for key in (
                    "source_facility",
                    "source_status",
                    "source_badge_label",
                    "source_application_method",
                    "source_application_control",
                )
            )
            violations += len(_PHONE_RE.findall(source_labels))
            violations += len(_EMAIL_RE.findall(source_labels))
            violations += len(_MASKED_NAME_RE.findall(source_labels))
    return violations


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "network_retry_count": 0,
        "source_total": 0,
        "source_rows": 0,
        "current_count": 0,
        "returned_count": 0,
        "expired_count": 0,
        "cancelled_count": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "sentinel_mode": "",
        "sentinel_count": None,
        "stable_rechecks": {},
        "duplicate_source_id_count": 0,
        "semantic_duplicate_count": 0,
        "privacy_violations": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": "",
        "municipality_code": CHANGNYEONG_MUNICIPALITY_CODE,
        "municipality_name": CHANGNYEONG_MUNICIPALITY_NAME,
        "ownership_scope": CHANGNYEONG_OWNERSHIP_SCOPE,
        "candidate_ids": dict(CHANGNYEONG_CANDIDATE_IDS),
        "candidate_decisions": dict(CHANGNYEONG_CANDIDATE_DECISIONS),
        "official_facilities": dict(CHANGNYEONG_FACILITIES),
        "ownership_aliases": [
            {
                "provider": alias.provider,
                "url": alias.url,
                "relationship": alias.relationship,
            }
            for alias in CHANGNYEONG_ALIASES
        ],
        "separate_public_providers": list(
            CHANGNYEONG_SEPARATE_PUBLIC_PROVIDERS
        ),
    }


def collect_gyeongnam_changnyeong_education_courses(
    target: Any,
    timeout: int = 30,
    max_pages: int = 200,
    detail_limit: int = 200,
    *,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = CHANGNYEONG_MAX_WORKERS,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect the complete current/future Changnyeong education snapshot."""

    meta = _base_meta()
    if not is_gyeongnam_changnyeong_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match the canonical Gyeongnam Changnyeong education route"
        )
        return [], CHANGNYEONG_PARSER, meta
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta["configured_collection_error"] = (
                "managed session_factory injection is required"
            )
            return [], CHANGNYEONG_PARSER, meta
        session_factory = _default_session_factory
    try:
        allowed_pages = max(0, int(max_pages))
        allowed_details = max(0, int(detail_limit))
        request_timeout = max(1, int(timeout))
        workers = min(max(1, int(max_workers)), CHANGNYEONG_MAX_WORKERS)
        cutoff = _today(today)
    except (TypeError, ValueError):
        meta["configured_collection_error"] = (
            "timeout/max_pages/detail_limit/max_workers/today are invalid"
        )
        return [], CHANGNYEONG_PARSER, meta

    errors: list[str] = []
    source_cap_reached = False
    first_soup: Optional[BeautifulSoup] = None
    last_page = 0
    try:
        first_soup, _, retries = _fetch_page(
            1,
            session_factory=session_factory,
            timeout=request_timeout,
            fetcher=fetcher,
        )
        meta["pages"] += 1
        meta["list_requests"] += 1
        meta["network_retry_count"] += retries
    except Exception as exc:
        errors.append(f"first page: {type(exc).__name__}: {_clean(exc)}")

    first_rows: list[dict[str, Any]] = []
    if first_soup is not None:
        last_page, item_errors = _form_and_last_page(
            first_soup, requested_page=1, expected_display_page=1
        )
        errors.extend(item_errors)
        first_rows, item_errors = _parse_list_page(first_soup, source_page=1)
        errors.extend(item_errors)
        if last_page and not first_rows:
            errors.append("first page contains no course rows")

    boundary_count = 1 if last_page == 1 else 2
    required_list_requests = last_page + 1 + boundary_count if last_page else 0
    meta["required_list_requests"] = required_list_requests
    if required_list_requests > allowed_pages:
        source_cap_reached = True
        errors.append(
            f"max_pages cap allows {allowed_pages} of {required_list_requests} "
            "required list/clamp/recheck requests"
        )

    pages: dict[int, list[dict[str, Any]]] = {1: first_rows}
    sentinel_count: Optional[int] = None
    sentinel_mode = ""
    stable_rechecks: dict[str, bool] = {}
    if not errors:
        try:
            if last_page > 1:
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = {
                        executor.submit(
                            _fetch_page,
                            page,
                            session_factory=session_factory,
                            timeout=request_timeout,
                            fetcher=fetcher,
                        ): page
                        for page in range(2, last_page + 1)
                    }
                    for future in as_completed(futures):
                        page = futures[future]
                        soup, _, retries = future.result()
                        meta["pages"] += 1
                        meta["list_requests"] += 1
                        meta["network_retry_count"] += retries
                        found_last, item_errors = _form_and_last_page(
                            soup,
                            requested_page=page,
                            expected_display_page=page,
                            known_last_page=last_page,
                        )
                        errors.extend(f"page {page}: {value}" for value in item_errors)
                        if found_last != last_page:
                            errors.append(f"page {page}: advertised pagination changed")
                        parsed, item_errors = _parse_list_page(
                            soup, source_page=page
                        )
                        errors.extend(item_errors)
                        pages[page] = parsed

            sentinel_page = last_page + 1
            sentinel_soup, _, retries = _fetch_page(
                sentinel_page,
                session_factory=session_factory,
                timeout=request_timeout,
                fetcher=fetcher,
            )
            meta["pages"] += 1
            meta["list_requests"] += 1
            meta["network_retry_count"] += retries
            found_last, item_errors = _form_and_last_page(
                sentinel_soup,
                requested_page=sentinel_page,
                expected_display_page=last_page,
                known_last_page=last_page,
            )
            errors.extend(f"clamped sentinel: {value}" for value in item_errors)
            sentinel_rows, item_errors = _parse_list_page(
                sentinel_soup, source_page=sentinel_page
            )
            errors.extend(item_errors)
            sentinel_count = len(sentinel_rows)
            if (
                found_last == last_page
                and _page_signature(sentinel_rows)
                == _page_signature(pages.get(last_page, []))
            ):
                sentinel_mode = "clamped_last"
            else:
                errors.append("immediate post-last clamp differs from exact last page")

            for page in dict.fromkeys((1, last_page)):
                soup, _, retries = _fetch_page(
                    page,
                    session_factory=session_factory,
                    timeout=request_timeout,
                    fetcher=fetcher,
                )
                meta["pages"] += 1
                meta["list_requests"] += 1
                meta["network_retry_count"] += retries
                found_last, item_errors = _form_and_last_page(
                    soup,
                    requested_page=page,
                    expected_display_page=page,
                    known_last_page=last_page,
                )
                errors.extend(f"page {page} recheck: {value}" for value in item_errors)
                parsed, item_errors = _parse_list_page(soup, source_page=page)
                errors.extend(item_errors)
                stable = bool(
                    found_last == last_page
                    and _page_signature(parsed) == _page_signature(pages.get(page, []))
                )
                stable_rechecks[str(page)] = stable
                if not stable:
                    errors.append(f"page {page}: stable boundary recheck changed")
        except Exception as exc:
            errors.append(f"catalogue traversal: {type(exc).__name__}: {_clean(exc)}")

    source_rows = [
        row for page in range(1, last_page + 1) for row in pages.get(page, [])
    ]
    if last_page:
        for page in range(1, last_page):
            if len(pages.get(page, [])) != CHANGNYEONG_PAGE_SIZE:
                errors.append(
                    f"page {page}: expected {CHANGNYEONG_PAGE_SIZE} rows, "
                    f"got {len(pages.get(page, []))}"
                )
        last_count = len(pages.get(last_page, []))
        if not 1 <= last_count <= CHANGNYEONG_PAGE_SIZE:
            errors.append(
                f"page {last_page}: invalid final-page row count {last_count}"
            )
    inferred_total = (
        (last_page - 1) * CHANGNYEONG_PAGE_SIZE
        + len(pages.get(last_page, []))
        if last_page
        else 0
    )
    if inferred_total != len(source_rows):
        errors.append(
            f"inferred total {inferred_total} != parsed total {len(source_rows)}"
        )
    identities = [_clean(row.get("provider_course_id")) for row in source_rows]
    duplicate_source_ids = len(identities) - len(set(identities))
    if duplicate_source_ids:
        errors.append(f"{duplicate_source_ids} duplicate source identities")

    current_rows: list[dict[str, Any]] = []
    expired_count = cancelled_count = 0
    for row in source_rows:
        ended = date.fromisoformat(_clean(row.get("end_date")))
        if ended < cutoff:
            expired_count += 1
        elif _CANCELLED_RE.search(_clean(row.get("title"))):
            cancelled_count += 1
        else:
            current_rows.append(row)

    semantic_signatures = [_semantic_signature(row) for row in current_rows]
    semantic_duplicate_count = len(semantic_signatures) - len(
        set(semantic_signatures)
    )
    if semantic_duplicate_count:
        errors.append(
            f"{semantic_duplicate_count} duplicate current semantic signatures"
        )

    if len(current_rows) > allowed_details:
        source_cap_reached = True
        errors.append(
            f"detail_limit cap allows {allowed_details} of {len(current_rows)} "
            "required current/future details"
        )

    detailed: list[dict[str, Any]] = []
    detail_errors: list[str] = []
    detail_attempts = 0
    if not errors and current_rows:
        found: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    _fetch_detail,
                    row,
                    session_factory=session_factory,
                    timeout=request_timeout,
                    fetcher=fetcher,
                ): row
                for row in current_rows
            }
            for future in as_completed(futures):
                parent = futures[future]
                identity = _clean(
                    parent.get("raw_fields", {}).get("source_education_id")
                )
                detail_attempts += 1
                try:
                    row, item_errors, retries = future.result()
                    meta["network_retry_count"] += retries
                    if item_errors:
                        detail_errors.extend(item_errors)
                    else:
                        found[identity] = row
                except Exception as exc:
                    detail_errors.append(
                        f"detail {identity}: {type(exc).__name__}: {_clean(exc)}"
                    )
        detailed = [
            found[_clean(row.get("raw_fields", {}).get("source_education_id"))]
            for row in current_rows
            if _clean(row.get("raw_fields", {}).get("source_education_id")) in found
        ]
    errors.extend(detail_errors)
    details_complete = bool(
        not detail_errors
        and detail_attempts == len(current_rows)
        and len(detailed) == len(current_rows)
    )

    result: list[dict[str, Any]] = []
    if not errors and details_complete:
        result = list((dedupe_rows or _default_dedupe)(detailed))
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
        and sentinel_mode == "clamped_last"
        and len(stable_rechecks) == expected_rechecks
        and all(stable_rechecks.values())
        and meta["list_requests"] == required_list_requests
    )
    snapshot_complete = bool(pagination_complete and details_complete and not errors)
    if not snapshot_complete:
        result = []

    facility_counts = Counter(_clean(row.get("branch")) for row in result)
    status_counts = Counter(_clean(row.get("status")) for row in result)
    application_counts = Counter(
        _clean(row.get("application_type")) for row in result
    )
    source_status_counts = Counter(
        _clean(row.get("raw_fields", {}).get("source_status")) for row in result
    )
    active_without_control = sum(
        _clean(row.get("raw_fields", {}).get("source_status")) in _CONTROL_LABELS
        and not row.get("reservation_available")
        for row in result
    )
    meta.update(
        {
            "source_total": len(source_rows),
            "source_rows": len(source_rows),
            "inferred_total": inferred_total,
            "data_pages": last_page,
            "current_count": len(current_rows),
            "returned_count": len(result),
            "expired_count": expired_count,
            "cancelled_count": cancelled_count,
            "detail_attempts": detail_attempts,
            "detail_pages": len(detailed),
            "detail_errors": len(detail_errors),
            "sentinel_mode": sentinel_mode,
            "sentinel_count": sentinel_count,
            "stable_rechecks": stable_rechecks,
            "duplicate_source_id_count": duplicate_source_ids,
            "semantic_duplicate_count": semantic_duplicate_count,
            "privacy_violations": privacy_violations,
            "facility_count": len(facility_counts),
            "facility_counts": dict(facility_counts),
            "status_counts": dict(status_counts),
            "source_status_counts": dict(source_status_counts),
            "application_type_counts": dict(application_counts),
            "active_status_without_control_count": active_without_control,
            "pagination_detected": last_page > 1,
            "pagination_complete": pagination_complete,
            "details_complete": details_complete,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": snapshot_complete,
            "source_cap_reached": source_cap_reached,
            "no_current_data": bool(snapshot_complete and not result),
            "no_current_reason": (
                "complete Changnyeong ledger contains only ended/cancelled courses"
                if snapshot_complete and not result
                else ""
            ),
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
    )
    return result, CHANGNYEONG_PARSER, meta


collect = collect_gyeongnam_changnyeong_education_courses


__all__ = [
    "CHANGNYEONG_ALIASES",
    "CHANGNYEONG_CANDIDATE_DECISIONS",
    "CHANGNYEONG_CANDIDATE_IDS",
    "CHANGNYEONG_FACILITIES",
    "CHANGNYEONG_LIST_PATH",
    "CHANGNYEONG_MUNICIPALITY_CODE",
    "CHANGNYEONG_MUNICIPALITY_NAME",
    "CHANGNYEONG_OWNERSHIP_SCOPE",
    "CHANGNYEONG_PAGE_SIZE",
    "CHANGNYEONG_PARSER",
    "CHANGNYEONG_PROVIDER",
    "CHANGNYEONG_SEPARATE_PUBLIC_PROVIDERS",
    "CHANGNYEONG_URL",
    "ChangnyeongAlias",
    "changnyeong_application_url",
    "changnyeong_detail_url",
    "changnyeong_list_url",
    "collect",
    "collect_gyeongnam_changnyeong_education_courses",
    "is_gyeongnam_changnyeong_alias_target",
    "is_gyeongnam_changnyeong_education_target",
    "is_target",
]
