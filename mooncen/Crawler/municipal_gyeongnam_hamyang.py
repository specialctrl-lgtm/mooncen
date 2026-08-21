"""Fail-closed collectors for Hamyang-gun's official education catalogues.

The configured municipal provider points only at the Hamyang-eup resident
centre catalogue.  It is not a county-wide, single-page source: the catalogue
has 11 clamped pages and the Education Development Special Zone owns a second
official course catalogue.  The municipal owner therefore traverses both
catalogues and emits one deduplicated current/future snapshot.

The existing social-welfare provider is an independent owner.  Its default
page is only the latest one-course addendum, while the form exposes fourteen
term partitions and 646 public course cards.  This module enumerates every
advertised term and every page instead of relying on ``--per-target-limit`` or
partial-save behaviour.

Both platforms expose all course data needed by the list card; there is no
public course detail page.  Applicant-confirmation pages, identity checks and
application forms are never fetched.  Instructor names, applicant rows,
contacts, downloads and surrounding free text are excluded from output.
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


HAMYANG_HOST = "www.hygn.go.kr"
HAMYANG_PROVIDER = "MUNI_WWW_HYGN_GO_KR_E0B9FF58"
HAMYANG_SPECIAL_PROVIDER = "MUNI_WWW_HYGN_GO_KR_8BE8C6A0"
HAMYANG_WELFARE_PROVIDER = "HAMYANG_WELFARE_OFFICIAL_COURSE"
HAMYANG_MUNICIPALITY_CODE = "4887000000"
HAMYANG_MUNICIPALITY_NAME = "경상남도 함양군"
HAMYANG_RESIDENT_PATH = "/01662/01788/01805.web"
HAMYANG_SPECIAL_PATH = "/01662/07002/07023.web"
HAMYANG_WELFARE_PATH = "/02125/02126.web"
HAMYANG_RESIDENT_CHECK_PATH = "/01662/01788/01806.web"
HAMYANG_SPECIAL_CHECK_PATH = "/01662/07002/07024.web"
HAMYANG_WELFARE_CHECK_PATH = "/02125/02127.web"
HAMYANG_PAGE_SIZE = 10
HAMYANG_WELFARE_PAGE_SIZE = 9
HAMYANG_MAX_WORKERS = 4
HAMYANG_FETCH_ATTEMPTS = 2
HAMYANG_CONFIGURED_URL = f"https://{HAMYANG_HOST}{HAMYANG_RESIDENT_PATH}"
HAMYANG_SPECIAL_URL = f"https://{HAMYANG_HOST}{HAMYANG_SPECIAL_PATH}"
HAMYANG_WELFARE_URL = f"https://{HAMYANG_HOST}{HAMYANG_WELFARE_PATH}"
HAMYANG_PARSER = (
    "gyeongnam_hamyang_all_official_candidate_catalogues+declared_totals+"
    "clamped_last_sentinels+stable_boundaries+current_list_card_allowlist+"
    "course_bound_application_control_no_form_or_applicant_fetch+pii_allowlist"
)
HAMYANG_WELFARE_PARSER = (
    "gyeongnam_hamyang_welfare_all_advertised_terms+declared_totals+"
    "clamped_last_sentinels+stable_boundaries+current_list_card_allowlist+"
    "instructor_and_applicant_excluded+no_partial_save+pii_allowlist"
)
HAMYANG_OWNERSHIP_SCOPE = (
    "official_hamyang_resident_centre_and_education_special_zone_catalogues"
)
HAMYANG_WELFARE_OWNERSHIP_SCOPE = (
    "official_hamyang_social_welfare_all_advertised_course_terms"
)

HAMYANG_CANDIDATE_IDS: Mapping[str, str] = {
    "education_office_home": "MUNI_IR_6F4744156061",
    "resident_existing_owner": "MUNI_IR_BBAEE33C7D10",
    "education_special_zone": "MUNI_IR_BD02D885AA7F",
    "education_office_single_content": "MUNI_IR_D5D209E1BEF2",
}
HAMYANG_CANDIDATE_DECISIONS: Mapping[str, str] = {
    "MUNI_IR_6F4744156061": (
        "exclude_education_office_home_not_a_course_catalogue"
    ),
    "MUNI_IR_BBAEE33C7D10": (
        "include_existing_owner_but_expand_all_pages_and_official_catalogues"
    ),
    "MUNI_IR_BD02D885AA7F": (
        "include_under_existing_municipal_owner_as_second_official_catalogue"
    ),
    "MUNI_IR_D5D209E1BEF2": (
        "exclude_single_education_office_content_page_not_a_complete_catalogue"
    ),
}


@dataclass(frozen=True)
class HamyangAlias:
    provider: str
    url: str
    relationship: str


HAMYANG_ALIASES = (
    HamyangAlias(
        HAMYANG_SPECIAL_PROVIDER,
        HAMYANG_SPECIAL_URL,
        "second official catalogue collected by the existing municipal owner",
    ),
    HamyangAlias(
        "MUNI_HYEDU_GNE_GO_KR_AF6F513A",
        "https://hyedu.gne.go.kr/",
        "education-office home; not a course catalogue",
    ),
    HamyangAlias(
        "MUNI_HYEDU_GNE_GO_KR_E9F6028A",
        "https://hyedu.gne.go.kr/hyedu/cm/cntnts/cntntsView.do?mi=1923&cntntsId=978",
        "one provincial education-office content record; not a complete catalogue",
    ),
)

# Audited official surfaces that must not be fanned into these owners.
HAMYANG_SEPARATE_SURFACES: Mapping[str, str] = {
    "retired_information_education_archive": (
        "https://www.hygn.go.kr/01662/01787/01797.web"
    ),
    "digital_learning_external_catalogue": (
        "https://www.xn--2z1bw8k1pjz5ccumkb.kr/edc/crse/req/list.do?"
        "pno=1&punit=5&psize=10&sch_area_cd=401&sch_signgu_cd=40121"
    ),
    "public_sports_courses": "https://www.hygn.go.kr/04488/04493.web",
}


@dataclass(frozen=True)
class HamyangCatalogue:
    key: str
    path: str
    heading: str
    branch: str
    body_section_class: str

    @property
    def url(self) -> str:
        return f"https://{HAMYANG_HOST}{self.path}"


HAMYANG_CATALOGUES = (
    HamyangCatalogue(
        "resident_hamyang_eup",
        HAMYANG_RESIDENT_PATH,
        "프로그램안내",
        "함양읍 주민자치센터",
        "lv2_01788",
    ),
    HamyangCatalogue(
        "education_special_zone",
        HAMYANG_SPECIAL_PATH,
        "프로그램 신청",
        "함양군교육발전특구 맞춤평생교육원",
        "lv2_07002",
    ),
)


@dataclass(frozen=True)
class WelfareTerm:
    identity: str
    label: str
    year: str


Fetcher = Callable[..., Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[.-](\d{1,2})[.-](\d{1,2})(?!\d)")
_TOTAL_COURSE_RE = re.compile(
    r"총\s*([\d,]+)\s*건의\s*과정이\s*있습니다\.\s*"
    r"\(\s*(\d+)\s*/\s*(\d+)\s*페이지\s*\)"
)
_TOTAL_WELFARE_RE = re.compile(
    r"총\s*([\d,]+)\s*건의\s*게시물이\s*있습니다\.\s*"
    r"\(\s*(\d+)\s*/\s*(\d+)\s*페이지\s*\)"
)
_COUNT_RE = re.compile(r"([\d,]+)\s*명")
_TERM_RE = re.compile(r"^(20\d{2})년\s+.+")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:010[-\s*]+[\d*]{3,4}[-\s*]+[\d*]{4}|"
    r"0\d{1,3}[-\s]?\d{3,4}[-\s]?\d{4}|0\d{8,11})(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_CANCELLED_RE = re.compile(r"(?:^|[<\[(])\s*(?:폐강|취소)\s*(?:$|[>\])])")

_COURSE_FIELDS = ("교육기간", "교육시간", "신청인원", "장소")
_WELFARE_FIELDS = (
    "교육대상",
    "신청기간",
    "교육기간",
    "교육시간",
    "교육요일",
    "교육정원",
    "신청현황",
    "강사",
)
_BLOCKED_PII_PATHS = {
    HAMYANG_RESIDENT_CHECK_PATH,
    HAMYANG_SPECIAL_CHECK_PATH,
    HAMYANG_WELFARE_CHECK_PATH,
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
        and (parsed.hostname or "").rstrip(".").lower() == HAMYANG_HOST
        and parsed.port is None
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


def _exact_public_url(url: str, path: str) -> bool:
    parsed = urlparse(_clean(url))
    return bool(_safe_base(parsed) and parsed.path == path and not parsed.query)


def is_gyeongnam_hamyang_education_target(target: Any) -> bool:
    return bool(
        _provider(target) == HAMYANG_PROVIDER
        and _exact_public_url(_target_url(target), HAMYANG_RESIDENT_PATH)
    )


def is_hamyang_welfare_education_target(target: Any) -> bool:
    return bool(
        _provider(target) == HAMYANG_WELFARE_PROVIDER
        and _exact_public_url(_target_url(target), HAMYANG_WELFARE_PATH)
    )


def is_target(target: Any) -> bool:
    return bool(
        is_gyeongnam_hamyang_education_target(target)
        or is_hamyang_welfare_education_target(target)
    )


def is_gyeongnam_hamyang_alias_target(target: Any) -> bool:
    provider = _provider(target)
    url = _target_url(target)
    return any(provider == alias.provider and url == alias.url for alias in HAMYANG_ALIASES)


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


def hamyang_catalogue_url(catalogue: HamyangCatalogue, page: int = 1) -> str:
    if page < 1:
        return ""
    return catalogue.url if page == 1 else catalogue.url + "?" + urlencode({"cpage": page})


def hamyang_welfare_term_url(term: WelfareTerm, page: int = 1) -> str:
    if page < 1 or not term.identity.isdigit() or not re.fullmatch(r"20\d{2}", term.year):
        return ""
    values: list[tuple[str, str]] = [
        ("stype", "title"),
        ("sessionIdx", term.identity),
        ("pageunit", str(HAMYANG_WELFARE_PAGE_SIZE)),
        ("syear", term.year),
    ]
    if page > 1:
        values.append(("cpage", str(page)))
    return HAMYANG_WELFARE_URL + "?" + urlencode(values)


def _allowed_source_request(url: str) -> bool:
    parsed = urlparse(_clean(url))
    if not _safe_base(parsed) or parsed.path in _BLOCKED_PII_PATHS:
        return False
    query = parse_qs(parsed.query, keep_blank_values=True)
    if parsed.path in {catalogue.path for catalogue in HAMYANG_CATALOGUES}:
        if not query:
            return True
        return bool(
            set(query) == {"cpage"}
            and len(query["cpage"]) == 1
            and query["cpage"][0].isdigit()
            and int(query["cpage"][0]) >= 2
        )
    if parsed.path != HAMYANG_WELFARE_PATH:
        return False
    if not query:
        return True
    expected = {"stype", "sessionIdx", "pageunit", "syear"}
    if "cpage" in query:
        expected.add("cpage")
    return bool(
        set(query) == expected
        and query.get("stype") == ["title"]
        and len(query.get("sessionIdx", [])) == 1
        and query["sessionIdx"][0].isdigit()
        and int(query["sessionIdx"][0]) > 0
        and query.get("pageunit") == [str(HAMYANG_WELFARE_PAGE_SIZE)]
        and len(query.get("syear", [])) == 1
        and re.fullmatch(r"20\d{2}", query["syear"][0])
        and (
            "cpage" not in query
            or (
                len(query["cpage"]) == 1
                and query["cpage"][0].isdigit()
                and int(query["cpage"][0]) >= 2
            )
        )
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
    if not _allowed_source_request(final_url):
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
    if not _allowed_source_request(url):
        raise ValueError("refusing application, applicant-confirmation, or unrelated request")
    messages: list[str] = []
    for attempt in range(1, HAMYANG_FETCH_ATTEMPTS + 1):
        try:
            if fetcher is not None:
                result = fetcher(current, "GET", url, timeout=timeout, data={})
                if (
                    isinstance(result, tuple)
                    and len(result) == 2
                    and isinstance(result[0], BeautifulSoup)
                ):
                    soup, final_url = result
                    if not _allowed_source_request(_clean(final_url or url)):
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


def _direct_pairs(
    card: Any,
    labels: tuple[str, ...],
    *,
    excluded_value_labels: frozenset[str] = frozenset(),
) -> tuple[dict[str, str], list[str]]:
    fields: dict[str, str] = {}
    errors: list[str] = []
    found_labels: list[str] = []
    for item in card.select("div.tg1 > ul > li"):
        names = item.select(":scope > span.t1")
        values = item.select(":scope > span.t2")
        name = _clean(names[0].get_text(" ", strip=True)) if len(names) == 1 else ""
        if not name or len(values) != 1 or name in found_labels:
            errors.append("duplicate or malformed card field")
            continue
        found_labels.append(name)
        if name not in excluded_value_labels:
            fields[name] = _clean(values[0].get_text(" ", strip=True))
    if tuple(found_labels) != labels:
        errors.append("card field vocabulary/order changed")
    return fields, errors


def _parse_count(value: Any) -> Optional[int]:
    match = _COUNT_RE.fullmatch(_clean(value))
    return int(match.group(1).replace(",", "")) if match else None


def _application_route(
    href: Any,
    *,
    path: str,
    source_page: int,
    welfare_term: Optional[WelfareTerm] = None,
) -> tuple[str, list[str]]:
    parsed = urlparse(urljoin(f"https://{HAMYANG_HOST}{path}", _clean(href)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    required = {"amode", "courseIdx"}
    allowed = set(required)
    if source_page > 1:
        allowed.add("cpage")
    if welfare_term is not None:
        allowed.update({"sessionIdx", "syear"})
    errors: list[str] = []
    identity = (query.get("courseIdx") or [""])[0]
    if (
        not _safe_base(parsed)
        or parsed.path != path
        or not required.issubset(query)
        or not set(query).issubset(allowed)
        or query.get("amode") != ["insert"]
        or len(query.get("courseIdx", [])) != 1
        or not identity.isdigit()
        or int(identity or 0) <= 0
        or (
            "cpage" in query and query.get("cpage") != [str(source_page)]
        )
        or (
            welfare_term is not None
            and (
                "sessionIdx" in query
                and query.get("sessionIdx") != [welfare_term.identity]
            )
        )
        or (
            welfare_term is not None
            and "syear" in query
            and query.get("syear") != [welfare_term.year]
        )
    ):
        errors.append("application route is not a safe course-bound insert route")
    canonical = (
        f"https://{HAMYANG_HOST}{path}?"
        + urlencode((("amode", "insert"), ("courseIdx", identity)))
        if identity.isdigit()
        else ""
    )
    return canonical, errors


def _control(
    card: Any,
    *,
    inactive_label: str,
    active_label: str,
    path: str,
    source_page: int,
    welfare_term: Optional[WelfareTerm] = None,
) -> tuple[str, str, bool, list[str]]:
    anchors = card.select("div.btns > a")
    if len(anchors) != 1:
        return "", "", False, ["expected exactly one application-state control"]
    anchor = anchors[0]
    label = _clean(anchor.get_text(" ", strip=True))
    classes = set(anchor.get("class", []))
    if label == inactive_label:
        if anchor.has_attr("href") or not {"button", "muted"}.issubset(classes):
            return label, "", False, ["inactive application control changed"]
        return label, "", False, []
    if label != active_label or "button" not in classes or not anchor.has_attr("href"):
        return label, "", False, ["unknown application-state control"]
    application_url, errors = _application_route(
        anchor.get("href"),
        path=path,
        source_page=source_page,
        welfare_term=welfare_term,
    )
    return label, application_url, not errors, errors


def _course_form_total(
    soup: BeautifulSoup,
    catalogue: HamyangCatalogue,
    *,
    expected_display_page: int,
    expected_request_page: int,
) -> tuple[int, int, list[str]]:
    errors: list[str] = []
    body = soup.select_one("body")
    if body is None or not {"site_depart", "lv1_01662", catalogue.body_section_class}.issubset(
        set(body.get("class", []))
    ):
        errors.append("catalogue body ownership marker changed")
    headings = soup.select("h1.hb1.h1")
    if len(headings) != 1 or _clean(headings[0].get_text(" ", strip=True)) != catalogue.heading:
        errors.append("catalogue heading changed")
    forms = soup.select("form#listForm[name='listForm']")
    if len(forms) != 1:
        return 0, 0, [*errors, "expected one unfiltered listForm"]
    form = forms[0]
    action = urlparse(urljoin(catalogue.url, _clean(form.get("action"))))
    expected_query = (
        {} if expected_request_page == 1 else {"cpage": [str(expected_request_page)]}
    )
    if (
        _clean(form.get("method")).lower() != "get"
        or not _safe_base(action)
        or action.path != catalogue.path
        or parse_qs(action.query, keep_blank_values=True) != expected_query
    ):
        errors.append("unexpected listForm method/action")
    cpage = form.select("input[type='hidden'][name='cpage']")
    if len(cpage) != 1 or _clean(cpage[0].get("value")) != "1":
        errors.append("listForm cpage marker changed")
    stype = form.select("select[name='stype']")
    options = (
        [
            (_clean(option.get("value")), _clean(option.get_text(" ", strip=True)))
            for option in stype[0].select("option")
        ]
        if len(stype) == 1
        else []
    )
    if len(stype) != 1 or _selected_value(stype[0]) != "name" or options != [
        ("name", "교육과정명")
    ]:
        errors.append("listForm search vocabulary changed")
    sstring = form.select("input[name='sstring']")
    if len(sstring) != 1 or _clean(sstring[0].get("value")):
        errors.append("listForm search text is not empty")

    totals = []
    for node in soup.select("div#body_content div.info1"):
        match = _TOTAL_COURSE_RE.fullmatch(_clean(node.get_text(" ", strip=True)))
        if match:
            totals.append(match)
    if len(totals) != 1:
        return 0, 0, [*errors, "expected one declared course total"]
    total, displayed, last = (
        int(value.replace(",", "")) for value in totals[0].groups()
    )
    if displayed != expected_display_page:
        errors.append("declared current page mismatch")
    if last != max(1, math.ceil(total / HAMYANG_PAGE_SIZE)):
        errors.append("declared last page does not match total/page size")
    return total, last, errors


def _course_rows(
    soup: BeautifulSoup,
    catalogue: HamyangCatalogue,
    *,
    source_page: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    roots = soup.select("div#body_content div.edu1list1")
    if len(roots) != 1:
        return [], ["expected one canonical course-card list"]
    cards = roots[0].select("ul > li.column")
    for index, card in enumerate(cards, 1):
        item_errors: list[str] = []
        titles = card.select("div.hg1 > h2.h1")
        title = _clean(titles[0].get_text(" ", strip=True)) if len(titles) == 1 else ""
        if not title:
            item_errors.append("expected one nonempty title")
        fields, field_errors = _direct_pairs(card, _COURSE_FIELDS)
        item_errors.extend(field_errors)
        education_dates = _dates(fields.get("교육기간"))
        if len(education_dates) != 2:
            item_errors.append("education period is malformed")
        schedule = _clean(fields.get("교육시간"))
        venue = _clean(fields.get("장소"))
        enrolled = _parse_count(fields.get("신청인원"))
        if not schedule or not venue:
            item_errors.append("education time/place is empty")
        if enrolled is None:
            item_errors.append("applicant count is malformed")
        control_label, application_url, active, control_errors = _control(
            card,
            inactive_label="참가불가",
            active_label="참가가능",
            path=catalogue.path,
            source_page=source_page,
        )
        item_errors.extend(control_errors)
        if item_errors:
            errors.extend(
                f"{catalogue.key} page {source_page} row {index}: {message}"
                for message in item_errors
            )
            continue
        natural = "|".join((catalogue.key, title, fields["교육기간"], schedule, venue))
        identity = hashlib.sha256(natural.encode("utf-8")).hexdigest()[:24]
        rows.append(
            {
                "provider": HAMYANG_PROVIDER,
                "provider_course_id": f"{HAMYANG_PROVIDER}:education:{identity}",
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "branch": catalogue.branch,
                "branch_code": (
                    "gyeongnam-hamyang-"
                    + hashlib.sha256(catalogue.branch.encode("utf-8")).hexdigest()[:12]
                ),
                "municipality_code": HAMYANG_MUNICIPALITY_CODE,
                "municipality_name": HAMYANG_MUNICIPALITY_NAME,
                "sido": "경상남도",
                "sigungu": "함양군",
                "provider_organizer": catalogue.branch,
                "venue_name": venue,
                "category": "평생학습",
                "program_type": "강좌",
                "raw_url": catalogue.url,
                "application_url": application_url,
                "application_type": "ONLINE_RESERVATION" if active else "INFO_ONLY",
                "reservation_available": active,
                "status": "OPEN" if active else "CLOSED",
                "period": fields["교육기간"],
                "start_date": education_dates[0].isoformat(),
                "end_date": education_dates[1].isoformat(),
                "apply_period": "",
                "apply_start": "",
                "apply_end": "",
                "schedule_raw": schedule,
                "fee": "",
                "target": "",
                "capacity_current": enrolled,
                "description": title,
                "source_group": "municipal_reservation",
                "collection_category": "평생학습",
                "domain_category": "교육·강좌",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "operator_type": "지자체/공공기관",
                "collection_type": "complete_html_pages+authoritative_list_cards",
                "raw_fields": {
                    "parser": HAMYANG_PARSER,
                    "source_catalog": catalogue.key,
                    "source_page": source_page,
                    "source_control": control_label,
                    "detail_required": False,
                    "detail_fetched": False,
                    "application_form_fetched": False,
                    "applicant_confirmation_fetched": False,
                    "contact_excluded": True,
                    "instructor_excluded": True,
                    "applicant_data_excluded": True,
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
            _clean(row.get("schedule_raw")),
            _clean(row.get("venue_name")),
            _clean(row.get("status")),
        )
        for row in rows
    ]
    return hashlib.sha256(repr(values).encode("utf-8")).hexdigest()


def _welfare_inventory(form: Any) -> tuple[list[WelfareTerm], str, list[str]]:
    errors: list[str] = []
    selects = form.select("select[name='sessionIdx']")
    if len(selects) != 1:
        return [], "", ["expected one welfare term selector"]
    selected = _selected_value(selects[0])
    options = selects[0].select("option[value]")
    if not options or _clean(options[0].get("value")) != "0" or _clean(
        options[0].get_text(" ", strip=True)
    ) != "전체":
        errors.append("welfare all-term option changed")
    terms: list[WelfareTerm] = []
    for option in options[1:]:
        identity = _clean(option.get("value"))
        label = _clean(option.get_text(" ", strip=True))
        match = _TERM_RE.fullmatch(label)
        if not identity.isdigit() or int(identity or 0) <= 0 or match is None:
            errors.append("malformed welfare term option")
            continue
        terms.append(WelfareTerm(identity, label, match.group(1)))
    numeric = [int(term.identity) for term in terms]
    if (
        not terms
        or numeric != sorted(numeric)
        or len(numeric) != len(set(numeric))
        or len({term.label for term in terms}) != len(terms)
    ):
        errors.append("welfare term inventory/order changed")
    return terms, selected, errors


def _welfare_search_controls(
    form: Any,
    *,
    expected_term: Optional[WelfareTerm],
) -> list[str]:
    errors: list[str] = []
    years = form.select("select[name='syear']")
    if len(years) != 1:
        errors.append("expected one welfare year selector")
    elif expected_term is not None:
        year_values = [
            _clean(option.get("value"))
            for option in years[0].select("option[value]")
        ]
        selected_year = years[0].select_one("option[selected]")
        if expected_term.year in year_values:
            if _selected_value(years[0]) != expected_term.year:
                errors.append("welfare selected year mismatch")
        elif (
            not year_values
            or any(not re.fullmatch(r"20\d{2}", value) for value in year_values)
            or len(year_values) != len(set(year_values))
            or [int(value) for value in year_values]
            != sorted((int(value) for value in year_values), reverse=True)
            or int(expected_term.year) >= min(int(value) for value in year_values)
            or selected_year is not None
        ):
            errors.append("welfare legacy year selector contract changed")
    apply = form.select("select[name='applyFlag']")
    apply_options = (
        [
            (_clean(option.get("value")), _clean(option.get_text(" ", strip=True)))
            for option in apply[0].select("option")
        ]
        if len(apply) == 1
        else []
    )
    if len(apply) != 1 or _selected_value(apply[0]) or apply_options != [
        ("", "전체"),
        ("Y", "신청가능"),
        ("F", "신청불가"),
    ]:
        errors.append("welfare application filter changed")
    stype = form.select("select[name='stype']")
    stype_options = (
        [
            (_clean(option.get("value")), _clean(option.get_text(" ", strip=True)))
            for option in stype[0].select("option")
        ]
        if len(stype) == 1
        else []
    )
    if len(stype) != 1 or _selected_value(stype[0]) != "title" or stype_options != [
        ("title", "교육명"),
        ("teacher_idx", "강사명"),
    ]:
        errors.append("welfare search vocabulary changed")
    sstring = form.select("input[name='sstring']")
    if len(sstring) != 1 or _clean(sstring[0].get("value")):
        errors.append("welfare search text is not empty")
    return errors


def _welfare_discovery(soup: BeautifulSoup) -> tuple[list[WelfareTerm], list[str]]:
    errors: list[str] = []
    headings = soup.select("h1.hb1.h1")
    if len(headings) != 1 or _clean(headings[0].get_text(" ", strip=True)) != "신청하기":
        errors.append("welfare catalogue heading changed")
    forms = soup.select("form#listForm[name='listForm']")
    if len(forms) != 1:
        return [], [*errors, "expected one welfare listForm"]
    form = forms[0]
    action = urlparse(urljoin(HAMYANG_WELFARE_URL, _clean(form.get("action"))))
    if (
        _clean(form.get("method")).lower() != "get"
        or not _safe_base(action)
        or action.path != HAMYANG_WELFARE_PATH
        or action.query
    ):
        errors.append("unexpected welfare discovery form action")
    terms, selected, inventory_errors = _welfare_inventory(form)
    errors.extend(inventory_errors)
    if terms and selected != terms[-1].identity:
        errors.append("welfare discovery did not select the latest term")
    errors.extend(_welfare_search_controls(form, expected_term=None))
    latest_year = terms[-1].year if terms else ""
    years = form.select_one("select[name='syear']")
    if years is not None and _selected_value(years) != latest_year:
        errors.append("welfare discovery year is not the latest term year")
    return terms, errors


def _welfare_form_total(
    soup: BeautifulSoup,
    term: WelfareTerm,
    expected_inventory: tuple[WelfareTerm, ...],
    *,
    expected_display_page: int,
    expected_request_page: int,
) -> tuple[int, int, list[str]]:
    errors: list[str] = []
    headings = soup.select("h1.hb1.h1")
    if len(headings) != 1 or _clean(headings[0].get_text(" ", strip=True)) != "신청하기":
        errors.append("welfare catalogue heading changed")
    forms = soup.select("form#listForm[name='listForm']")
    if len(forms) != 1:
        return 0, 0, [*errors, "expected one welfare listForm"]
    form = forms[0]
    action = urlparse(urljoin(HAMYANG_WELFARE_URL, _clean(form.get("action"))))
    expected_query = parse_qs(
        urlparse(hamyang_welfare_term_url(term, expected_request_page)).query,
        keep_blank_values=True,
    )
    if (
        _clean(form.get("method")).lower() != "get"
        or not _safe_base(action)
        or action.path != HAMYANG_WELFARE_PATH
        or parse_qs(action.query, keep_blank_values=True) != expected_query
    ):
        errors.append("unexpected welfare listForm method/action")
    cpage = form.select("input[type='hidden'][name='cpage']")
    if len(cpage) != 1 or _clean(cpage[0].get("value")) != "1":
        errors.append("welfare cpage marker changed")
    terms, selected, inventory_errors = _welfare_inventory(form)
    errors.extend(inventory_errors)
    if tuple(terms) != expected_inventory or selected != term.identity:
        errors.append("welfare term inventory/selection changed during traversal")
    errors.extend(_welfare_search_controls(form, expected_term=term))

    totals = []
    for node in soup.select("div#body_content div.info1"):
        match = _TOTAL_WELFARE_RE.fullmatch(_clean(node.get_text(" ", strip=True)))
        if match:
            totals.append(match)
    if len(totals) != 1:
        return 0, 0, [*errors, "expected one declared welfare total"]
    total, displayed, last = (
        int(value.replace(",", "")) for value in totals[0].groups()
    )
    if displayed != expected_display_page:
        errors.append("welfare declared current page mismatch")
    if last != max(1, math.ceil(total / HAMYANG_WELFARE_PAGE_SIZE)):
        errors.append("welfare declared last page does not match total/page size")
    return total, last, errors


def _welfare_rows(
    soup: BeautifulSoup,
    term: WelfareTerm,
    *,
    source_page: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    roots = soup.select("div#body_content div.edu1list1")
    if len(roots) != 1:
        return [], ["expected one canonical welfare course-card list"]
    cards = roots[0].select("ul > li.column")
    for index, card in enumerate(cards, 1):
        item_errors: list[str] = []
        titles = card.select("div.hg1 > h2.h1")
        sessions = card.select("div.hg1 > span.t1")
        title = _clean(titles[0].get_text(" ", strip=True)) if len(titles) == 1 else ""
        session_label = (
            _clean(sessions[0].get_text(" ", strip=True)) if len(sessions) == 1 else ""
        )
        if not title:
            item_errors.append("expected one nonempty title")
        if not session_label.startswith(term.label):
            item_errors.append("card term label does not match selected term")
        fields, field_errors = _direct_pairs(
            card,
            _WELFARE_FIELDS,
            excluded_value_labels=frozenset({"강사"}),
        )
        item_errors.extend(field_errors)
        education_dates = _dates(fields.get("교육기간"))
        application_dates = _dates(fields.get("신청기간"))
        if len(education_dates) != 2:
            item_errors.append("education period is malformed")
        if len(application_dates) != 2:
            item_errors.append("application period is malformed")
        target = _clean(fields.get("교육대상"))
        schedule = _clean(" ".join((fields.get("교육요일", ""), fields.get("교육시간", ""))))
        capacity = _parse_count(fields.get("교육정원"))
        enrolled = _parse_count(fields.get("신청현황"))
        if not target or not schedule:
            item_errors.append("education target/schedule is empty")
        if capacity is None or enrolled is None:
            item_errors.append("welfare capacity/applicant count is malformed")
        control_label, application_url, active, control_errors = _control(
            card,
            inactive_label="신청불가",
            active_label="신청가능",
            path=HAMYANG_WELFARE_PATH,
            source_page=source_page,
            welfare_term=term,
        )
        item_errors.extend(control_errors)
        if item_errors:
            errors.extend(
                f"welfare term {term.identity} page {source_page} row {index}: {message}"
                for message in item_errors
            )
            continue
        natural = "|".join(
            (
                term.identity,
                title,
                fields["교육기간"],
                schedule,
                target,
            )
        )
        identity = hashlib.sha256(natural.encode("utf-8")).hexdigest()[:24]
        branch = "함양군 종합사회복지관"
        rows.append(
            {
                "provider": HAMYANG_WELFARE_PROVIDER,
                "provider_course_id": (
                    f"{HAMYANG_WELFARE_PROVIDER}:education:{identity}"
                ),
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "branch": branch,
                "branch_code": "gyeongnam-hamyang-social-welfare",
                "municipality_code": HAMYANG_MUNICIPALITY_CODE,
                "municipality_name": HAMYANG_MUNICIPALITY_NAME,
                "sido": "경상남도",
                "sigungu": "함양군",
                "provider_organizer": branch,
                "venue_name": branch,
                "category": "복지관 교육",
                "program_type": "강좌",
                "raw_url": HAMYANG_WELFARE_URL,
                "application_url": application_url,
                "application_type": "ONLINE_RESERVATION" if active else "INFO_ONLY",
                "reservation_available": active,
                "status": "OPEN" if active else "CLOSED",
                "period": fields["교육기간"],
                "start_date": education_dates[0].isoformat(),
                "end_date": education_dates[1].isoformat(),
                "apply_period": fields["신청기간"],
                "apply_start": application_dates[0].isoformat(),
                "apply_end": application_dates[1].isoformat(),
                "schedule_raw": schedule,
                "fee": "",
                "target": target,
                "capacity": capacity,
                "capacity_current": enrolled,
                "description": title,
                "source_group": "welfare",
                "collection_category": "복지관",
                "domain_category": "교육·강좌",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "operator_type": "지자체/공공기관",
                "collection_type": "all_terms_complete_html_pages+authoritative_list_cards",
                "raw_fields": {
                    "parser": HAMYANG_WELFARE_PARSER,
                    "source_catalog": "hamyang_social_welfare_all_terms",
                    "source_term_id": term.identity,
                    "source_term_label": term.label,
                    "source_session_label": session_label,
                    "source_page": source_page,
                    "source_control": control_label,
                    "detail_required": False,
                    "detail_fetched": False,
                    "application_form_fetched": False,
                    "applicant_confirmation_fetched": False,
                    "contact_excluded": True,
                    "instructor_excluded": True,
                    "applicant_data_excluded": True,
                    "free_text_excluded": True,
                },
            }
        )
    return rows, errors


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


def _base_meta(scope: str) -> dict[str, Any]:
    municipal = scope == "municipal"
    return {
        "scope": scope,
        "pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "source_total": 0,
        "source_rows": 0,
        "current_count": 0,
        "returned_count": 0,
        "expired_count": 0,
        "cancelled_count": 0,
        "detail_required": False,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "sentinel_mode": "clamped_last_page",
        "sentinel_counts": {},
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
        "municipality_code": HAMYANG_MUNICIPALITY_CODE,
        "municipality_name": HAMYANG_MUNICIPALITY_NAME,
        "ownership_scope": (
            HAMYANG_OWNERSHIP_SCOPE if municipal else HAMYANG_WELFARE_OWNERSHIP_SCOPE
        ),
        "candidate_ids": dict(HAMYANG_CANDIDATE_IDS),
        "candidate_decisions": dict(HAMYANG_CANDIDATE_DECISIONS),
        "ownership_aliases": [
            {
                "provider": alias.provider,
                "url": alias.url,
                "relationship": alias.relationship,
            }
            for alias in HAMYANG_ALIASES
        ],
        "blocked_pii_paths": sorted(_BLOCKED_PII_PATHS),
        "live_audit": {
            "audited_on": "2026-07-21",
            "municipal_catalogues": {
                "resident_hamyang_eup": {"total": 110, "pages": 11, "current_future": 0},
                "education_special_zone": {"total": 20, "pages": 2, "current_future": 2},
            },
            "municipal_total": 130,
            "municipal_current_future": 2,
            "municipal_duplicate_ids": 0,
            "welfare_terms": 14,
            "welfare_total": 646,
            "welfare_current_future": 0,
            "welfare_duplicate_ids": 0,
            "retired_information_archive_total": 788,
            "retired_information_archive_current_future": 0,
            "existing_quality_defects": {
                "municipal_saved": 10,
                "municipal_actual": 110,
                "welfare_default_saved": 1,
                "welfare_all_terms_actual": 646,
                "welfare_partial_save_enabled": True,
                "welfare_instructor_previously_stored": True,
            },
        },
    }


def _validate_options(
    timeout: int,
    max_pages: int,
    detail_limit: int,
    max_workers: int,
    today: Optional[date | datetime | str],
) -> tuple[int, int, int, int, date]:
    return (
        max(1, int(timeout)),
        max(0, int(max_pages)),
        max(0, int(detail_limit)),
        min(max(1, int(max_workers)), HAMYANG_MAX_WORKERS),
        _today(today),
    )


def _finish_result(
    source_rows: list[dict[str, Any]],
    *,
    cutoff: date,
    errors: list[str],
    meta: dict[str, Any],
    dedupe_rows: Optional[DedupeRows],
    required_list_requests: int,
    pagination_valid: bool,
) -> list[dict[str, Any]]:
    identities = [_clean(row.get("provider_course_id")) for row in source_rows]
    duplicate_source_ids = len(identities) - len(set(identities))
    if duplicate_source_ids:
        errors.append(f"{duplicate_source_ids} duplicate source identities")
    semantic = [
        (
            _clean(row.get("title")),
            _clean(row.get("period")),
            _clean(row.get("schedule_raw")),
            _clean(row.get("venue_name")),
        )
        for row in source_rows
    ]
    semantic_duplicates = len(semantic) - len(set(semantic))
    if meta.get("scope") == "municipal" and semantic_duplicates:
        errors.append(f"{semantic_duplicates} semantic duplicates across catalogues")

    current: list[dict[str, Any]] = []
    expired = cancelled = 0
    for row in source_rows:
        try:
            ended = date.fromisoformat(_clean(row.get("end_date")))
        except ValueError:
            errors.append(f"{_clean(row.get('provider_course_id'))}: invalid end date")
            continue
        if ended < cutoff:
            expired += 1
        elif _CANCELLED_RE.search(_clean(row.get("title"))):
            cancelled += 1
        else:
            current.append(row)

    result: list[dict[str, Any]] = []
    if not errors:
        deduper = dedupe_rows or _default_dedupe
        result = list(deduper(current))
        if len(result) != len(current):
            errors.append(f"dedupe changed complete row count {len(current)} to {len(result)}")
            result = []
    result.sort(
        key=lambda row: (
            _clean(row.get("start_date")),
            _clean(row.get("title")),
            _clean(row.get("provider_course_id")),
        )
    )
    privacy = _privacy_violations(result)
    if privacy:
        errors.append(f"{privacy} PII allowlist violations")
        result = []

    pagination_complete = bool(
        pagination_valid
        and not errors
        and meta["list_requests"] == required_list_requests
    )
    details_complete = True
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
            "current_count": len(current),
            "returned_count": len(result),
            "expired_count": expired,
            "cancelled_count": cancelled,
            "duplicate_source_id_count": duplicate_source_ids,
            "semantic_duplicate_count": semantic_duplicates,
            "privacy_violations": privacy,
            "branch_count": len(branch_counts),
            "branch_counts": dict(branch_counts),
            "status_counts": dict(status_counts),
            "application_type_counts": dict(application_counts),
            "pagination_complete": pagination_complete,
            "details_complete": details_complete,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": snapshot_complete,
            "no_current_data": bool(snapshot_complete and not result),
            "no_current_reason": (
                "complete Hamyang scope contains only ended/cancelled courses"
                if snapshot_complete and not result
                else ""
            ),
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
    )
    return result


def _collect_municipal(
    *,
    session_factory: SessionFactory,
    fetcher: Optional[Fetcher],
    timeout: int,
    max_pages: int,
    cutoff: date,
    dedupe_rows: Optional[DedupeRows],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    meta = _base_meta("municipal")
    errors: list[str] = []
    first: dict[str, tuple[int, int, list[dict[str, Any]]]] = {}
    for catalogue in HAMYANG_CATALOGUES:
        current = session_factory()
        try:
            try:
                soup, _ = _request_soup(
                    current,
                    hamyang_catalogue_url(catalogue, 1),
                    timeout=timeout,
                    fetcher=fetcher,
                )
                meta["pages"] += 1
                meta["list_requests"] += 1
                total, last, item_errors = _course_form_total(
                    soup,
                    catalogue,
                    expected_display_page=1,
                    expected_request_page=1,
                )
                errors.extend(item_errors)
                rows, item_errors = _course_rows(soup, catalogue, source_page=1)
                errors.extend(item_errors)
                first[catalogue.key] = (total, last, rows)
            except Exception as exc:
                errors.append(f"{catalogue.key} first page: {type(exc).__name__}: {_clean(exc)}")
        finally:
            _close_quietly(current)

    required = sum(
        last + 1 + (1 if last == 1 else 2)
        for total, last, rows in first.values()
        if last
    )
    meta["required_list_requests"] = required
    if required > max_pages:
        meta["source_cap_reached"] = True
        errors.append(
            f"max_pages cap allows {max_pages} of {required} required "
            "catalogue/sentinel/recheck requests"
        )

    all_rows: list[dict[str, Any]] = []
    pagination_valid = bool(len(first) == len(HAMYANG_CATALOGUES))
    catalog_totals: dict[str, int] = {}
    sentinel_counts: dict[str, int] = {}
    stable_rechecks: dict[str, dict[str, bool]] = {}
    if not errors:
        for catalogue in HAMYANG_CATALOGUES:
            declared_total, last, first_rows = first[catalogue.key]
            pages: dict[int, list[dict[str, Any]]] = {1: first_rows}
            stable: dict[str, bool] = {}
            current = session_factory()
            try:
                for page in range(2, last + 1):
                    soup, _ = _request_soup(
                        current,
                        hamyang_catalogue_url(catalogue, page),
                        timeout=timeout,
                        fetcher=fetcher,
                    )
                    meta["pages"] += 1
                    meta["list_requests"] += 1
                    total, found_last, item_errors = _course_form_total(
                        soup,
                        catalogue,
                        expected_display_page=page,
                        expected_request_page=page,
                    )
                    errors.extend(item_errors)
                    rows, item_errors = _course_rows(soup, catalogue, source_page=page)
                    errors.extend(item_errors)
                    if total != declared_total or found_last != last:
                        errors.append(f"{catalogue.key} page {page}: pagination changed")
                    pages[page] = rows

                soup, _ = _request_soup(
                    current,
                    hamyang_catalogue_url(catalogue, last + 1),
                    timeout=timeout,
                    fetcher=fetcher,
                )
                meta["pages"] += 1
                meta["list_requests"] += 1
                total, found_last, item_errors = _course_form_total(
                    soup,
                    catalogue,
                    expected_display_page=last,
                    expected_request_page=last + 1,
                )
                errors.extend(item_errors)
                sentinel, item_errors = _course_rows(
                    soup, catalogue, source_page=last + 1
                )
                errors.extend(item_errors)
                sentinel_counts[catalogue.key] = len(sentinel)
                if (
                    total != declared_total
                    or found_last != last
                    or _page_signature(sentinel) != _page_signature(pages.get(last, []))
                ):
                    errors.append(f"{catalogue.key}: immediate post-last clamp changed")

                for page in dict.fromkeys((1, last)):
                    soup, _ = _request_soup(
                        current,
                        hamyang_catalogue_url(catalogue, page),
                        timeout=timeout,
                        fetcher=fetcher,
                    )
                    meta["pages"] += 1
                    meta["list_requests"] += 1
                    total, found_last, item_errors = _course_form_total(
                        soup,
                        catalogue,
                        expected_display_page=page,
                        expected_request_page=page,
                    )
                    errors.extend(item_errors)
                    rows, item_errors = _course_rows(soup, catalogue, source_page=page)
                    errors.extend(item_errors)
                    unchanged = bool(
                        total == declared_total
                        and found_last == last
                        and _page_signature(rows) == _page_signature(pages.get(page, []))
                    )
                    stable[str(page)] = unchanged
                    if not unchanged:
                        errors.append(f"{catalogue.key} page {page}: boundary changed")
            except Exception as exc:
                errors.append(f"{catalogue.key} traversal: {type(exc).__name__}: {_clean(exc)}")
            finally:
                _close_quietly(current)

            rows = [row for page in range(1, last + 1) for row in pages.get(page, [])]
            for page in range(1, last):
                if len(pages.get(page, [])) != HAMYANG_PAGE_SIZE:
                    errors.append(f"{catalogue.key} page {page}: expected a full page")
            last_count = len(pages.get(last, [])) if last else 0
            if declared_total and not 1 <= last_count <= HAMYANG_PAGE_SIZE:
                errors.append(f"{catalogue.key}: invalid last-page cardinality")
            if len(rows) != declared_total:
                errors.append(
                    f"{catalogue.key}: declared total {declared_total} != parsed {len(rows)}"
                )
            expected_rechecks = 1 if last == 1 else 2
            if (
                len(stable) != expected_rechecks
                or not all(stable.values())
                or sentinel_counts.get(catalogue.key) != last_count
            ):
                pagination_valid = False
            stable_rechecks[catalogue.key] = stable
            catalog_totals[catalogue.key] = len(rows)
            all_rows.extend(rows)

    meta.update(
        {
            "catalogue_totals": catalog_totals,
            "catalogue_count": len(HAMYANG_CATALOGUES),
            "sentinel_counts": sentinel_counts,
            "stable_rechecks": stable_rechecks,
            "pagination_detected": any(last > 1 for _, last, _ in first.values()),
        }
    )
    result = _finish_result(
        all_rows,
        cutoff=cutoff,
        errors=errors,
        meta=meta,
        dedupe_rows=dedupe_rows,
        required_list_requests=required,
        pagination_valid=pagination_valid,
    )
    return result, meta


def _welfare_first_term(
    term: WelfareTerm,
    inventory: tuple[WelfareTerm, ...],
    *,
    session_factory: SessionFactory,
    fetcher: Optional[Fetcher],
    timeout: int,
) -> tuple[int, int, list[dict[str, Any]], list[str]]:
    current = session_factory()
    try:
        soup, _ = _request_soup(
            current,
            hamyang_welfare_term_url(term, 1),
            timeout=timeout,
            fetcher=fetcher,
        )
        total, last, errors = _welfare_form_total(
            soup,
            term,
            inventory,
            expected_display_page=1,
            expected_request_page=1,
        )
        rows, item_errors = _welfare_rows(soup, term, source_page=1)
        errors.extend(item_errors)
        return total, last, rows, errors
    finally:
        _close_quietly(current)


def _welfare_remaining(
    term: WelfareTerm,
    inventory: tuple[WelfareTerm, ...],
    declared_total: int,
    last: int,
    first_rows: list[dict[str, Any]],
    *,
    session_factory: SessionFactory,
    fetcher: Optional[Fetcher],
    timeout: int,
) -> tuple[list[dict[str, Any]], int, dict[str, bool], int, list[str]]:
    pages: dict[int, list[dict[str, Any]]] = {1: first_rows}
    errors: list[str] = []
    requests_count = 0
    sentinel_count = -1
    stable: dict[str, bool] = {}
    current = session_factory()
    try:
        for page in range(2, last + 1):
            soup, _ = _request_soup(
                current,
                hamyang_welfare_term_url(term, page),
                timeout=timeout,
                fetcher=fetcher,
            )
            requests_count += 1
            total, found_last, item_errors = _welfare_form_total(
                soup,
                term,
                inventory,
                expected_display_page=page,
                expected_request_page=page,
            )
            errors.extend(item_errors)
            rows, item_errors = _welfare_rows(soup, term, source_page=page)
            errors.extend(item_errors)
            if total != declared_total or found_last != last:
                errors.append(f"welfare term {term.identity} page {page}: pagination changed")
            pages[page] = rows

        soup, _ = _request_soup(
            current,
            hamyang_welfare_term_url(term, last + 1),
            timeout=timeout,
            fetcher=fetcher,
        )
        requests_count += 1
        total, found_last, item_errors = _welfare_form_total(
            soup,
            term,
            inventory,
            expected_display_page=last,
            expected_request_page=last + 1,
        )
        errors.extend(item_errors)
        sentinel, item_errors = _welfare_rows(soup, term, source_page=last + 1)
        errors.extend(item_errors)
        sentinel_count = len(sentinel)
        if (
            total != declared_total
            or found_last != last
            or _page_signature(sentinel) != _page_signature(pages.get(last, []))
        ):
            errors.append(f"welfare term {term.identity}: immediate post-last clamp changed")

        for page in dict.fromkeys((1, last)):
            soup, _ = _request_soup(
                current,
                hamyang_welfare_term_url(term, page),
                timeout=timeout,
                fetcher=fetcher,
            )
            requests_count += 1
            total, found_last, item_errors = _welfare_form_total(
                soup,
                term,
                inventory,
                expected_display_page=page,
                expected_request_page=page,
            )
            errors.extend(item_errors)
            rows, item_errors = _welfare_rows(soup, term, source_page=page)
            errors.extend(item_errors)
            unchanged = bool(
                total == declared_total
                and found_last == last
                and _page_signature(rows) == _page_signature(pages.get(page, []))
            )
            stable[str(page)] = unchanged
            if not unchanged:
                errors.append(f"welfare term {term.identity} page {page}: boundary changed")
    except Exception as exc:
        errors.append(f"welfare term {term.identity}: {type(exc).__name__}: {_clean(exc)}")
    finally:
        _close_quietly(current)

    rows = [row for page in range(1, last + 1) for row in pages.get(page, [])]
    for page in range(1, last):
        if len(pages.get(page, [])) != HAMYANG_WELFARE_PAGE_SIZE:
            errors.append(f"welfare term {term.identity} page {page}: expected a full page")
    last_count = len(pages.get(last, [])) if last else 0
    if declared_total and not 1 <= last_count <= HAMYANG_WELFARE_PAGE_SIZE:
        errors.append(f"welfare term {term.identity}: invalid last-page cardinality")
    if len(rows) != declared_total:
        errors.append(
            f"welfare term {term.identity}: declared total {declared_total} != parsed {len(rows)}"
        )
    expected_rechecks = 1 if last == 1 else 2
    if len(stable) != expected_rechecks or not all(stable.values()) or sentinel_count != last_count:
        errors.append(f"welfare term {term.identity}: completeness markers changed")
    return rows, sentinel_count, stable, requests_count, errors


def _collect_welfare(
    *,
    session_factory: SessionFactory,
    fetcher: Optional[Fetcher],
    timeout: int,
    max_pages: int,
    max_workers: int,
    cutoff: date,
    dedupe_rows: Optional[DedupeRows],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    meta = _base_meta("welfare")
    errors: list[str] = []
    inventory: tuple[WelfareTerm, ...] = ()
    discovery_session = session_factory()
    try:
        try:
            soup, _ = _request_soup(
                discovery_session,
                HAMYANG_WELFARE_URL,
                timeout=timeout,
                fetcher=fetcher,
            )
            meta["pages"] += 1
            meta["list_requests"] += 1
            terms, item_errors = _welfare_discovery(soup)
            errors.extend(item_errors)
            inventory = tuple(terms)
        except Exception as exc:
            errors.append(f"welfare discovery: {type(exc).__name__}: {_clean(exc)}")
    finally:
        _close_quietly(discovery_session)

    minimum_required = 1 + 3 * len(inventory)
    if minimum_required > max_pages:
        meta["source_cap_reached"] = True
        errors.append(
            f"max_pages cap allows {max_pages} below the {minimum_required} "
            "minimum discovery/session/sentinel/recheck requests"
        )

    first: dict[str, tuple[int, int, list[dict[str, Any]]]] = {}
    if not errors:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _welfare_first_term,
                    term,
                    inventory,
                    session_factory=session_factory,
                    fetcher=fetcher,
                    timeout=timeout,
                ): term
                for term in inventory
            }
            for future in as_completed(futures):
                term = futures[future]
                meta["pages"] += 1
                meta["list_requests"] += 1
                try:
                    total, last, rows, item_errors = future.result()
                    errors.extend(item_errors)
                    first[term.identity] = (total, last, rows)
                except Exception as exc:
                    errors.append(
                        f"welfare term {term.identity} first page: "
                        f"{type(exc).__name__}: {_clean(exc)}"
                    )

    required = 1 + sum(
        last + 1 + (1 if last == 1 else 2)
        for total, last, rows in first.values()
        if last
    )
    meta["required_list_requests"] = required
    if required > max_pages:
        meta["source_cap_reached"] = True
        errors.append(
            f"max_pages cap allows {max_pages} of {required} required "
            "welfare discovery/term/sentinel/recheck requests"
        )

    all_rows: list[dict[str, Any]] = []
    sentinel_counts: dict[str, int] = {}
    stable_rechecks: dict[str, dict[str, bool]] = {}
    term_totals: dict[str, int] = {}
    pagination_valid = bool(len(first) == len(inventory) and inventory)
    if not errors:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _welfare_remaining,
                    term,
                    inventory,
                    first[term.identity][0],
                    first[term.identity][1],
                    first[term.identity][2],
                    session_factory=session_factory,
                    fetcher=fetcher,
                    timeout=timeout,
                ): term
                for term in inventory
            }
            completed: dict[str, tuple[list[dict[str, Any]], int, dict[str, bool]]] = {}
            for future in as_completed(futures):
                term = futures[future]
                try:
                    rows, sentinel, stable, requests_count, item_errors = future.result()
                    meta["pages"] += requests_count
                    meta["list_requests"] += requests_count
                    errors.extend(item_errors)
                    completed[term.identity] = (rows, sentinel, stable)
                except Exception as exc:
                    errors.append(
                        f"welfare term {term.identity} traversal: "
                        f"{type(exc).__name__}: {_clean(exc)}"
                    )
            for term in inventory:
                if term.identity not in completed:
                    pagination_valid = False
                    continue
                rows, sentinel, stable = completed[term.identity]
                all_rows.extend(rows)
                term_totals[term.identity] = len(rows)
                sentinel_counts[term.identity] = sentinel
                stable_rechecks[term.identity] = stable
                if any(not value for value in stable.values()):
                    pagination_valid = False

    meta.update(
        {
            "term_count": len(inventory),
            "term_inventory": [
                {"id": term.identity, "label": term.label, "year": term.year}
                for term in inventory
            ],
            "term_totals": term_totals,
            "sentinel_counts": sentinel_counts,
            "stable_rechecks": stable_rechecks,
            "pagination_detected": any(last > 1 for _, last, _ in first.values()),
        }
    )
    result = _finish_result(
        all_rows,
        cutoff=cutoff,
        errors=errors,
        meta=meta,
        dedupe_rows=dedupe_rows,
        required_list_requests=required,
        pagination_valid=pagination_valid,
    )
    return result, meta


def collect_gyeongnam_hamyang_education_courses(
    target: Any,
    timeout: int = 30,
    max_pages: int = 160,
    detail_limit: int = 0,
    *,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = HAMYANG_MAX_WORKERS,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect the matching municipal or welfare Hamyang owner fail-closed."""

    municipal = is_gyeongnam_hamyang_education_target(target)
    welfare = is_hamyang_welfare_education_target(target)
    parser = HAMYANG_WELFARE_PARSER if welfare else HAMYANG_PARSER
    if not municipal and not welfare:
        meta = _base_meta("invalid")
        meta["configured_collection_error"] = (
            "target does not match a Hamyang municipal/welfare education owner"
        )
        return [], parser, meta
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta = _base_meta("welfare" if welfare else "municipal")
            meta["configured_collection_error"] = "managed session_factory injection is required"
            return [], parser, meta
        session_factory = _default_session_factory
    try:
        request_timeout, allowed_pages, allowed_details, workers, cutoff = _validate_options(
            timeout, max_pages, detail_limit, max_workers, today
        )
    except (TypeError, ValueError):
        meta = _base_meta("welfare" if welfare else "municipal")
        meta["configured_collection_error"] = (
            "timeout/max_pages/detail_limit/max_workers/today are invalid"
        )
        return [], parser, meta
    if welfare:
        rows, meta = _collect_welfare(
            session_factory=session_factory,
            fetcher=fetcher,
            timeout=request_timeout,
            max_pages=allowed_pages,
            max_workers=workers,
            cutoff=cutoff,
            dedupe_rows=dedupe_rows,
        )
    else:
        rows, meta = _collect_municipal(
            session_factory=session_factory,
            fetcher=fetcher,
            timeout=request_timeout,
            max_pages=allowed_pages,
            cutoff=cutoff,
            dedupe_rows=dedupe_rows,
        )
    return rows, parser, meta


collect = collect_gyeongnam_hamyang_education_courses


__all__ = [
    "HAMYANG_ALIASES",
    "HAMYANG_CANDIDATE_DECISIONS",
    "HAMYANG_CANDIDATE_IDS",
    "HAMYANG_CATALOGUES",
    "HAMYANG_CONFIGURED_URL",
    "HAMYANG_MUNICIPALITY_CODE",
    "HAMYANG_MUNICIPALITY_NAME",
    "HAMYANG_OWNERSHIP_SCOPE",
    "HAMYANG_PARSER",
    "HAMYANG_PROVIDER",
    "HAMYANG_SEPARATE_SURFACES",
    "HAMYANG_SPECIAL_PROVIDER",
    "HAMYANG_SPECIAL_URL",
    "HAMYANG_WELFARE_OWNERSHIP_SCOPE",
    "HAMYANG_WELFARE_PARSER",
    "HAMYANG_WELFARE_PROVIDER",
    "HAMYANG_WELFARE_URL",
    "HamyangAlias",
    "HamyangCatalogue",
    "WelfareTerm",
    "collect",
    "collect_gyeongnam_hamyang_education_courses",
    "hamyang_catalogue_url",
    "hamyang_welfare_term_url",
    "is_gyeongnam_hamyang_alias_target",
    "is_gyeongnam_hamyang_education_target",
    "is_hamyang_welfare_education_target",
    "is_target",
]
