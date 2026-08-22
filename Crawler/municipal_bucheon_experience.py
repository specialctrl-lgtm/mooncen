"""Fail-closed collector for Bucheon's official visit/experience ledger.

The city public-service portal exposes its currently bookable ``관람/체험``
programmes as one paged public list.  Each identity has a public detail page
whose calendar and application control are embedded in the HTML.  This
collector reads only those list and detail GET routes.  It validates, but
never submits, the reservation form.  Login, group/member, reservation,
applicant, authentication, attachment, download, and PII routes are outside
the request allowlist.
"""

from __future__ import annotations

import calendar
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import requests


BUCHEON_EXPERIENCE_PROVIDER = "MUNI_RESERV_BUCHEON_GO_KR_DADCF414"
BUCHEON_EXPERIENCE_HOST = "reserv.bucheon.go.kr"
BUCHEON_EXPERIENCE_LIST_PATH = "/site/main/see/list"
BUCHEON_EXPERIENCE_DETAIL_PATH = "/site/main/see/detail"
BUCHEON_EXPERIENCE_RESERVE_PATH = "/site/main/see/reserve"
BUCHEON_EXPERIENCE_PAGE_SIZE = 16
BUCHEON_EXPERIENCE_URL = (
    f"https://{BUCHEON_EXPERIENCE_HOST}{BUCHEON_EXPERIENCE_LIST_PATH}?"
    "cp=1&pageSize=16&listType=list&search_prg_div=08&viewMode=image"
)
BUCHEON_EXPERIENCE_MUNICIPALITY_CODE = "4119000000"
BUCHEON_EXPERIENCE_MUNICIPALITY_NAME = "경기도 부천시"
BUCHEON_EXPERIENCE_PARSER = (
    "bucheon_official_visit_experience_current_ledger+declared_total+"
    "complete_16_item_pages+exact_empty_post_last_sentinel+"
    "stable_first_last_sentinel+all_current_public_details+"
    "detail_option_registry+identity_bound_reservation_controls_no_submit+"
    "locked_experience+no_login_group_member_application_attachment_download_or_pii_calls"
)
BUCHEON_EXPERIENCE_OWNERSHIP_SCOPE = (
    "bucheon_public_service_visit_experience_current_booking_ledger"
)
BUCHEON_EXPERIENCE_MAX_HTML_BYTES = 3_000_000

BUCHEON_COVERED_MUNICIPALITIES = (
    {
        "code": "4119000000",
        "sido": "경기도",
        "sigungu": "부천시",
        "full_name": "경기도 부천시",
    },
    {
        "code": "4119200000",
        "sido": "경기도",
        "sigungu": "부천시 원미구",
        "full_name": "경기도 부천시 원미구",
    },
    {
        "code": "4119400000",
        "sido": "경기도",
        "sigungu": "부천시 소사구",
        "full_name": "경기도 부천시 소사구",
    },
    {
        "code": "4119600000",
        "sido": "경기도",
        "sigungu": "부천시 오정구",
        "full_name": "경기도 부천시 오정구",
    },
)

_OJ_DONGS = {
    "성곡동",
    "원종1동",
    "원종2동",
    "고강본동",
    "고강1동",
    "오정동",
    "신흥동",
}
_SOSA_DONGS = {
    "심곡본1동",
    "심곡본동",
    "소사본동",
    "소사본1동",
    "범박동",
    "옥길동",
    "괴안동",
    "역곡3동",
    "송내1동",
    "송내2동",
}
_WONMI_DONGS = {
    "심곡1동",
    "심곡2동",
    "심곡3동",
    "원미1동",
    "원미2동",
    "소사동",
    "역곡1동",
    "역곡2동",
    "춘의동",
    "도당동",
    "약대동",
    "중동",
    "중1동",
    "중2동",
    "중3동",
    "중4동",
    "상동",
    "상1동",
    "상2동",
    "상3동",
}

_TOTAL_RE = re.compile(r"총\s*([\d,]+)\s*건")
_IDENTITY_RE = re.compile(r"[1-9]\d{0,11}")
_MONTH_RE = re.compile(r"(20\d{2})-(0[1-9]|1[0-2])")
_POSTAL_PREFIX_RE = re.compile(r"^\d{5}\s*/\s*")
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,2}[- ]?\d{3,4}[- ]?\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
_EXCLUSION_MARKERS = (
    "공지",
    "알림",
    "채용",
    "위원회",
    "지원사업",
    "시설대관",
    "시설대여",
    "물품대여",
    "동호회 모집",
)
_PROGRAM_MARKERS = (
    "체험",
    "관람",
    "교육",
    "해설",
    "프로그램",
    "박물관",
    "식물원",
    "수목원",
    "생태",
    "영화",
    "교통지도",
    "루미나래",
)
_DETAIL_FIELDS = {
    "체험장소",
    "운영시간",
    "유의사항",
    "접수방법",
    "담당기관",
    "연락처",
    "주소",
    "홈페이지",
}
_RESERVE_CONTROL_NAMES = {
    "program_seq",
    "schy",
    "schm",
    "schd",
    "recurrence",
    "reserve_div",
    "able_per",
    "total_pers",
    "see_div0305_cnt",
}

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class BucheonExperienceContractError(RuntimeError):
    """Raised when the audited public-source contract changes."""


@dataclass(frozen=True)
class _ListRow:
    identity: str
    title: str
    source_status: str
    dong: str
    institution: str
    fee: str
    reservation_method: str
    year: int
    month: int
    page: int
    detail_url: str

    @property
    def start_date(self) -> date:
        return date(self.year, self.month, 1)

    @property
    def end_date(self) -> date:
        return date(self.year, self.month, calendar.monthrange(self.year, self.month)[1])


@dataclass(frozen=True)
class _ListPage:
    page: int
    total: int
    last: int
    rows: tuple[_ListRow, ...]


def _clean(value: Any) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def is_bucheon_experience_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")) == BUCHEON_EXPERIENCE_PROVIDER
        and _clean(_target_value(target, "url")) == BUCHEON_EXPERIENCE_URL
    )


is_target = is_bucheon_experience_target


def bucheon_experience_list_url(page: int) -> str:
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("page must be a positive integer")
    query = (
        ("cp", str(page)),
        ("pageSize", str(BUCHEON_EXPERIENCE_PAGE_SIZE)),
        ("listType", "list"),
        ("search_prg_div", "08"),
        ("viewMode", "image"),
    )
    return (
        f"https://{BUCHEON_EXPERIENCE_HOST}{BUCHEON_EXPERIENCE_LIST_PATH}?"
        f"{urlencode(query)}"
    )


def bucheon_experience_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        raise ValueError("invalid Bucheon experience identity")
    return (
        f"https://{BUCHEON_EXPERIENCE_HOST}{BUCHEON_EXPERIENCE_DETAIL_PATH}?"
        f"{urlencode({'program_seq': value})}"
    )


def _canonical_key(url: str) -> tuple[str, str, tuple[tuple[str, str], ...]]:
    parsed = urlparse(_clean(url))
    return (
        parsed.scheme.lower(),
        parsed.netloc.lower() + parsed.path,
        tuple(sorted(parse_qsl(parsed.query, keep_blank_values=True))),
    )


def _request_kind(url: str) -> str:
    parsed = urlparse(_clean(url))
    if not (
        parsed.scheme == "https"
        and parsed.hostname == BUCHEON_EXPERIENCE_HOST
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
    ):
        raise BucheonExperienceContractError("unsafe official request URL")
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if parsed.path == BUCHEON_EXPERIENCE_LIST_PATH:
        values = dict(query)
        if (
            len(query) == 5
            and set(values) == {"cp", "pageSize", "listType", "search_prg_div", "viewMode"}
            and _IDENTITY_RE.fullmatch(values["cp"])
            and values["pageSize"] == str(BUCHEON_EXPERIENCE_PAGE_SIZE)
            and values["listType"] == "list"
            and values["search_prg_div"] == "08"
            and values["viewMode"] == "image"
        ):
            return "list"
    if parsed.path == BUCHEON_EXPERIENCE_DETAIL_PATH:
        if (
            len(query) == 1
            and query[0][0] == "program_seq"
            and _IDENTITY_RE.fullmatch(query[0][1])
        ):
            return "detail"
    raise BucheonExperienceContractError("request is outside the public GET allowlist")


def _default_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 municipal-course-crawler/1.0",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=False)


class _Requester:
    def __init__(
        self,
        session_factory: SessionFactory,
        fetcher: Fetcher,
        timeout: int,
        meta: dict[str, Any],
    ) -> None:
        self.session = session_factory()
        self.fetcher = fetcher
        self.timeout = timeout
        self.meta = meta

    def soup(self, url: str) -> BeautifulSoup:
        kind = _request_kind(url)
        self.meta["logical_requests"] += 1
        self.meta[f"{kind}_requests"] += 1
        response = self.fetcher(self.session, url, self.timeout)
        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            raise BucheonExperienceContractError(f"unexpected HTTP status {status}")
        if tuple(getattr(response, "history", ()) or ()):
            raise BucheonExperienceContractError("redirect history is forbidden")
        headers = getattr(response, "headers", {}) or {}
        if any(str(key).lower() == "location" and value for key, value in headers.items()):
            raise BucheonExperienceContractError("redirect location is forbidden")
        final_url = _clean(getattr(response, "url", ""))
        if final_url and _canonical_key(final_url) != _canonical_key(url):
            raise BucheonExperienceContractError("official response URL changed")
        content_type = _clean(
            next(
                (
                    value
                    for key, value in headers.items()
                    if str(key).lower() == "content-type"
                ),
                "text/html",
            )
        ).lower()
        if "html" not in content_type:
            raise BucheonExperienceContractError("official response is not HTML")
        body = getattr(response, "content", None)
        if body is None:
            body = str(getattr(response, "text", response)).encode("utf-8")
        body = bytes(body)
        if not body or len(body) > BUCHEON_EXPERIENCE_MAX_HTML_BYTES:
            raise BucheonExperienceContractError("empty or oversized official response")
        soup = BeautifulSoup(body, "html.parser")
        title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
        if title != "부천시 공공서비스예약":
            raise BucheonExperienceContractError("official page title changed")
        return soup

    def close(self) -> None:
        close = getattr(self.session, "close", None)
        if callable(close):
            close()


def _validate_list_form(soup: BeautifulSoup) -> None:
    form = soup.select_one("form#seeInfo")
    if form is None or _clean(form.get("method")).lower() != "post":
        raise BucheonExperienceContractError("visit/experience list form changed")
    action = urlparse(urljoin(BUCHEON_EXPERIENCE_URL, _clean(form.get("action"))))
    heading = form.select_one("h2.s-tit")
    selected = form.select_one("select[name='search_prg_div'] option[selected]")
    if not (
        action.hostname == BUCHEON_EXPERIENCE_HOST
        and action.path == BUCHEON_EXPERIENCE_LIST_PATH
        and heading is not None
        and _clean(heading.get_text(" ", strip=True)) == "관람/체험"
        and selected is not None
        and _clean(selected.get("value")) == "08"
    ):
        raise BucheonExperienceContractError("visit/experience list scope changed")


def _validate_list_identity_link(href: Any, page: int) -> tuple[str, str]:
    parsed = urlparse(urljoin(BUCHEON_EXPERIENCE_URL, _clean(href)))
    query = parse_qsl(parsed.query, keep_blank_values=True)
    values = dict(query)
    expected_keys = {
        "program_seq",
        "cp",
        "pageSize",
        "listType",
        "search_prg_div",
        "viewMode",
    }
    identity = values.get("program_seq", "")
    if not (
        parsed.scheme == "https"
        and parsed.hostname == BUCHEON_EXPERIENCE_HOST
        and parsed.path == BUCHEON_EXPERIENCE_DETAIL_PATH
        and not parsed.fragment
        and len(query) == len(expected_keys)
        and set(values) == expected_keys
        and _IDENTITY_RE.fullmatch(identity)
        and values["cp"] == str(page)
        and values["pageSize"] == str(BUCHEON_EXPERIENCE_PAGE_SIZE)
        and values["listType"] == "list"
        and values["search_prg_div"] == "08"
        and values["viewMode"] == "image"
    ):
        raise BucheonExperienceContractError("public list identity link changed")
    return identity, bucheon_experience_detail_url(identity)


def _parse_list_page(soup: BeautifulSoup, page: int) -> _ListPage:
    _validate_list_form(soup)
    total_node = soup.select_one("form#seeInfo span.num")
    match = _TOTAL_RE.fullmatch(
        _clean(total_node.get_text(" ", strip=True) if total_node else "")
    )
    if match is None:
        raise BucheonExperienceContractError("declared visit/experience total changed")
    total = int(match.group(1).replace(",", ""))
    last = max(1, (total + BUCHEON_EXPERIENCE_PAGE_SIZE - 1) // BUCHEON_EXPERIENCE_PAGE_SIZE)
    pager_numbers = {
        int(text)
        for anchor in soup.select("form#seeInfo div.page_bk span a[href]")
        if (text := _clean(anchor.get_text(" ", strip=True))).isdigit()
    }
    if total and pager_numbers and max(pager_numbers) != last:
        raise BucheonExperienceContractError("declared pager boundary changed")

    rows: list[_ListRow] = []
    for card in soup.select("form#seeInfo ul.img-list > li"):
        anchor = card.select_one(":scope > a[href]")
        title_node = anchor.select_one("span.tit") if anchor else None
        status_node = anchor.select_one("span.area") if anchor else None
        dong_node = anchor.select_one("em.dong") if anchor else None
        institution_node = card.select_one(":scope > div.white-bg span.lf")
        fee_node = card.select_one(":scope > div.white-bg > p")
        month_node = card.select_one(":scope > div.apl-time")
        if not all(
            (
                anchor,
                title_node,
                status_node,
                dong_node,
                institution_node,
                fee_node,
                month_node,
            )
        ):
            raise BucheonExperienceContractError("visit/experience card shape changed")
        identity, detail_url = _validate_list_identity_link(anchor.get("href"), page)
        title = _clean(title_node.get_text(" ", strip=True))
        source_status = _clean(status_node.get_text(" ", strip=True))
        dong = _clean(dong_node.get_text(" ", strip=True))
        institution = _clean(institution_node.get_text(" ", strip=True))
        fee_method = _clean(fee_node.get_text(" ", strip=True))
        month_match = _MONTH_RE.fullmatch(_clean(month_node.get_text(" ", strip=True)))
        if not title or source_status != "예약중" or not dong or not institution:
            raise BucheonExperienceContractError(f"{identity}: active card identity changed")
        if month_match is None or "/" not in fee_method:
            raise BucheonExperienceContractError(f"{identity}: month/booking method changed")
        fee, method = (_clean(value) for value in fee_method.split("/", 1))
        if method not in {"온라인접수", "별도사이트", "온라인 방문 전화"}:
            raise BucheonExperienceContractError(f"{identity}: unknown booking method")
        evidence = f"{title} {institution}"
        if any(marker in evidence for marker in _EXCLUSION_MARKERS):
            raise BucheonExperienceContractError(f"{identity}: non-program row entered ledger")
        if not any(marker in evidence for marker in _PROGRAM_MARKERS):
            raise BucheonExperienceContractError(f"{identity}: unclassified programme row")
        rows.append(
            _ListRow(
                identity,
                title,
                source_status,
                dong,
                institution,
                fee,
                method,
                int(month_match.group(1)),
                int(month_match.group(2)),
                page,
                detail_url,
            )
        )

    expected_count = (
        min(BUCHEON_EXPERIENCE_PAGE_SIZE, total - (page - 1) * BUCHEON_EXPERIENCE_PAGE_SIZE)
        if page <= last
        else 0
    )
    if expected_count < 0 or len(rows) != expected_count:
        raise BucheonExperienceContractError(
            f"page {page}: declared total differs from identity rows"
        )
    return _ListPage(page, total, last, tuple(rows))


def _page_signature(page: _ListPage) -> tuple[Any, ...]:
    return (
        page.total,
        page.last,
        tuple(
            (
                row.identity,
                row.title,
                row.source_status,
                row.dong,
                row.institution,
                row.year,
                row.month,
            )
            for row in page.rows
        ),
    )


def _detail_fields(soup: BeautifulSoup) -> dict[str, str]:
    fields: dict[str, str] = {}
    for table_row in soup.select("#sub-contents tr"):
        heading = table_row.find("th", recursive=False)
        value = table_row.find("td", recursive=False)
        if heading is None or value is None:
            continue
        name = _clean(heading.get_text(" ", strip=True))
        if name not in _DETAIL_FIELDS:
            continue
        if name in fields:
            raise BucheonExperienceContractError("duplicate public detail field")
        clone = BeautifulSoup(str(value), "html.parser")
        for element in clone.select("a, script, style"):
            element.decompose()
        fields[name] = _clean(clone.get_text(" ", strip=True))
    if set(fields) != _DETAIL_FIELDS:
        raise BucheonExperienceContractError("public detail field contract changed")
    return fields


def _municipality_for(dong: str, address: str) -> Mapping[str, str]:
    if "오정구" in address or dong in _OJ_DONGS:
        return BUCHEON_COVERED_MUNICIPALITIES[3]
    if "소사구" in address or dong in _SOSA_DONGS:
        return BUCHEON_COVERED_MUNICIPALITIES[2]
    if "원미구" in address or dong in _WONMI_DONGS:
        return BUCHEON_COVERED_MUNICIPALITIES[1]
    raise BucheonExperienceContractError(f"unknown Bucheon dong attribution: {dong!r}")


def _venue_address(value: str) -> str:
    text = _POSTAL_PREFIX_RE.sub("", _clean(value)).replace("지도보기", "").strip()
    parts = [_clean(part) for part in text.split("/") if _clean(part)]
    return ", ".join(parts)


def _detail_registry(soup: BeautifulSoup) -> tuple[tuple[str, str], ...]:
    form = soup.select_one("form#seeSch")
    if form is None or _clean(form.get("method")).lower() != "get":
        raise BucheonExperienceContractError("public detail selector form changed")
    action = urlparse(urljoin(BUCHEON_EXPERIENCE_URL, _clean(form.get("action"))))
    if action.hostname != BUCHEON_EXPERIENCE_HOST or action.path != BUCHEON_EXPERIENCE_DETAIL_PATH:
        raise BucheonExperienceContractError("public detail selector action changed")
    result: list[tuple[str, str]] = []
    for option in form.select("select#pSeq[name='program_seq'] > option[value]"):
        identity = _clean(option.get("value"))
        title = _clean(option.get_text(" ", strip=True))
        if not _IDENTITY_RE.fullmatch(identity) or not title:
            raise BucheonExperienceContractError("public detail registry identity changed")
        result.append((identity, title))
    if not result or len({identity for identity, _title in result}) != len(result):
        raise BucheonExperienceContractError("public detail registry is empty or duplicated")
    return tuple(result)


def _row_from_detail(
    listed: _ListRow,
    soup: BeautifulSoup,
    expected_registry: tuple[tuple[str, str], ...],
) -> tuple[dict[str, Any], int]:
    registry = _detail_registry(soup)
    if registry != expected_registry:
        raise BucheonExperienceContractError(
            f"{listed.identity}: detail option registry changed"
        )
    selected = soup.select_one("form#seeSch select#pSeq option[selected]")
    if not (
        selected is not None
        and _clean(selected.get("value")) == listed.identity
        and _clean(selected.get_text(" ", strip=True)) == listed.title
    ):
        raise BucheonExperienceContractError(
            f"{listed.identity}: list/detail identity mismatch"
        )

    reserve_forms = []
    for form in soup.find_all("form"):
        parsed = urlparse(urljoin(listed.detail_url, _clean(form.get("action"))))
        if parsed.hostname == BUCHEON_EXPERIENCE_HOST and parsed.path == BUCHEON_EXPERIENCE_RESERVE_PATH:
            reserve_forms.append(form)
    if len(reserve_forms) != 1:
        raise BucheonExperienceContractError(
            f"{listed.identity}: reservation control count changed"
        )
    reserve_form = reserve_forms[0]
    if _clean(reserve_form.get("method")).lower() != "post":
        raise BucheonExperienceContractError(
            f"{listed.identity}: reservation control method changed"
        )
    controls = {
        _clean(control.get("name")): _clean(control.get("value"))
        for control in reserve_form.select("input[name]")
    }
    if set(controls) != _RESERVE_CONTROL_NAMES:
        raise BucheonExperienceContractError(
            f"{listed.identity}: reservation control fields changed"
        )
    if (
        controls["program_seq"] != listed.identity
        or controls["schy"] != str(listed.year)
        or controls["schm"] != str(listed.month)
    ):
        raise BucheonExperienceContractError(
            f"{listed.identity}: reservation calendar identity changed"
        )
    days = [
        int(text)
        for node in reserve_form.select("p.dateNum")
        if (text := _clean(node.get_text(" ", strip=True))).isdigit()
    ]
    expected_days = list(range(1, calendar.monthrange(listed.year, listed.month)[1] + 1))
    if days != expected_days:
        raise BucheonExperienceContractError(
            f"{listed.identity}: public calendar day boundary changed"
        )
    application_buttons = reserve_form.select("button[onclick*='fncGoReserveForm']")

    fields = _detail_fields(soup)
    if not fields["담당기관"]:
        raise BucheonExperienceContractError(
            f"{listed.identity}: public detail institution is blank"
        )
    if "온라인접수" not in fields["접수방법"]:
        raise BucheonExperienceContractError(
            f"{listed.identity}: public booking method changed"
        )
    if not _PHONE_RE.fullmatch(fields["연락처"]):
        raise BucheonExperienceContractError(
            f"{listed.identity}: contact shape changed"
        )
    evidence = f"{listed.title} {fields['체험장소']} {fields['유의사항']}"
    if not any(marker in evidence for marker in _PROGRAM_MARKERS):
        raise BucheonExperienceContractError(
            f"{listed.identity}: detail no longer proves a programme"
        )
    address = _venue_address(fields["주소"])
    municipality = _municipality_for(listed.dong, address)
    period = f"{listed.start_date.isoformat()} ~ {listed.end_date.isoformat()}"
    row = {
        "provider": BUCHEON_EXPERIENCE_PROVIDER,
        "municipality_code": municipality["code"],
        "municipality_name": municipality["full_name"],
        "provider_course_id": f"{BUCHEON_EXPERIENCE_PROVIDER}:experience:{listed.identity}",
        "source_course_id": f"experience:{listed.identity}",
        "title": listed.title,
        "branch": fields["담당기관"],
        "branch_code": f"BUCHEON_EXP_{listed.identity}",
        "preserve_branch": True,
        "category": "부천시/관람·체험",
        "collection_category": "공공예약",
        "domain_category": "체험·견학",
        "source_group": "municipal_reservation",
        "service_group": "체험",
        "service_group_policy": "locked",
        "classification_locked": True,
        "operator_type": "지자체/공공기관",
        "program_type": "체험·견학",
        "source_status": listed.source_status,
        "status": "OPEN",
        "reservation_available": False,
        "period": period,
        "start_date": listed.start_date.isoformat(),
        "end_date": listed.end_date.isoformat(),
        "schedule_raw": fields["운영시간"],
        "fee": listed.fee,
        "venue_name": fields["체험장소"],
        "venue_address": address,
        "application_url": "",
        "raw_url": listed.detail_url,
        "description": f"체험장소: {fields['체험장소']} | 운영시간: {fields['운영시간']}",
        "raw_fields": {
            "parser": BUCHEON_EXPERIENCE_PARSER,
            "official_identity": listed.identity,
            "official_dong": listed.dong,
            "official_list_institution": listed.institution,
            "official_month": f"{listed.year:04d}-{listed.month:02d}",
            "official_source_status": listed.source_status,
            "list_page": listed.page,
            "public_detail_verified": True,
            "public_calendar_days_verified": len(days),
            "application_button_count": len(application_buttons),
            "application_controls_observed_not_called": True,
            "contact_validated_then_discarded": True,
        },
    }
    return row, len(application_buttons)


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    forbidden = {
        "phone",
        "email",
        "contact",
        "manager",
        "applicant",
        "member",
        "attachment",
        "download",
        "login",
    }

    def walk(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                key_text = str(key).lower()
                child_path = f"{path}.{key}" if path else str(key)
                if key_text in forbidden:
                    errors.append(f"forbidden key {child_path}")
                walk(child, child_path)
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")
        elif isinstance(value, str) and (_PHONE_RE.search(value) or _EMAIL_RE.search(value)):
            errors.append(f"PII value in {path}")

    walk(row, "")
    return errors


def _default_dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _identity_hash(values: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(sorted(values)).encode("utf-8")).hexdigest()


def _audit_date(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(_clean(value))


def _meta() -> dict[str, Any]:
    return {
        "errors": [],
        "error_kind": "",
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "pagination_complete": False,
        "details_complete": False,
        "list_requests": 0,
        "detail_requests": 0,
        "logical_requests": 0,
        "application_endpoint_requests": 0,
        "login_endpoint_requests": 0,
        "group_endpoint_requests": 0,
        "member_endpoint_requests": 0,
        "applicant_endpoint_requests": 0,
        "identity_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "download_endpoint_requests": 0,
        "pii_endpoint_requests": 0,
        "pii_values_persisted": 0,
    }


def collect_bucheon_experience(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 20,
    detail_limit: int = 100,
    today: Optional[date | datetime | str] = None,
    session_factory: SessionFactory = _default_session,
    fetcher: Fetcher = _default_fetcher,
    dedupe_rows: DedupeRows = _default_dedupe,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one atomic current/future visit/experience snapshot."""

    meta = _meta()
    if not is_bucheon_experience_target(target):
        meta["errors"] = ["target does not match the canonical Bucheon experience route"]
        meta["error_kind"] = "contract"
        return [], BUCHEON_EXPERIENCE_PARSER, meta
    try:
        cutoff = _audit_date(today)
        if timeout < 1 or max_pages < 1 or detail_limit < 0:
            raise ValueError("invalid collection limits")
    except (TypeError, ValueError) as exc:
        meta["errors"] = [str(exc)]
        meta["error_kind"] = "contract"
        return [], BUCHEON_EXPERIENCE_PARSER, meta

    requester = _Requester(session_factory, fetcher, int(timeout), meta)
    try:
        first = _parse_list_page(requester.soup(bucheon_experience_list_url(1)), 1)
        if first.last > max_pages:
            raise BucheonExperienceContractError("declared page count exceeds max_pages")
        pages = [first]
        for page_number in range(2, first.last + 1):
            page = _parse_list_page(
                requester.soup(bucheon_experience_list_url(page_number)), page_number
            )
            if page.total != first.total or page.last != first.last:
                raise BucheonExperienceContractError("pagination declaration drift")
            pages.append(page)
        source_rows = [row for page in pages for row in page.rows]
        if len(source_rows) != first.total:
            raise BucheonExperienceContractError("declared total differs from source identities")
        source_ids = [row.identity for row in source_rows]
        if len(source_ids) != len(set(source_ids)):
            raise BucheonExperienceContractError("duplicate source identities")

        sentinel_number = first.last + 1
        sentinel = _parse_list_page(
            requester.soup(bucheon_experience_list_url(sentinel_number)), sentinel_number
        )
        if sentinel.total != first.total or sentinel.last != first.last or sentinel.rows:
            raise BucheonExperienceContractError("immediate post-last sentinel changed")

        current_rows = [row for row in source_rows if row.end_date >= cutoff]
        expired_rows = [row for row in source_rows if row.end_date < cutoff]
        if len(current_rows) > detail_limit:
            raise BucheonExperienceContractError(
                "detail_limit truncates the current/future experience ledger"
            )
        expected_registry = tuple((row.identity, row.title) for row in source_rows)
        output: list[dict[str, Any]] = []
        application_button_count = 0
        for listed in current_rows:
            row, button_count = _row_from_detail(
                listed, requester.soup(listed.detail_url), expected_registry
            )
            output.append(row)
            application_button_count += button_count

        stable_first = _parse_list_page(
            requester.soup(bucheon_experience_list_url(1)), 1
        )
        stable_last = stable_first
        if first.last != 1:
            stable_last = _parse_list_page(
                requester.soup(bucheon_experience_list_url(first.last)), first.last
            )
        stable_sentinel = _parse_list_page(
            requester.soup(bucheon_experience_list_url(sentinel_number)), sentinel_number
        )
        boundary_checks = {
            "first": _page_signature(stable_first) == _page_signature(first),
            "last": _page_signature(stable_last) == _page_signature(pages[-1]),
            "sentinel": _page_signature(stable_sentinel) == _page_signature(sentinel),
        }
        if not all(boundary_checks.values()):
            raise BucheonExperienceContractError("list boundary changed during crawl")

        deduped = list(dedupe_rows(output))
        if len(deduped) != len(output):
            raise BucheonExperienceContractError("dedupe changed the complete snapshot")
        privacy = [error for row in deduped for error in _privacy_errors(row)]
        if privacy:
            raise BucheonExperienceContractError("; ".join(privacy))
        deduped.sort(
            key=lambda row: (
                _clean(row.get("start_date")),
                _clean(row.get("title")),
                _clean(row.get("provider_course_id")),
            )
        )
        meta.update(
            {
                "checked_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(
                    timespec="seconds"
                ),
                "cutoff": cutoff.isoformat(),
                "municipality_code": BUCHEON_EXPERIENCE_MUNICIPALITY_CODE,
                "municipality_name": BUCHEON_EXPERIENCE_MUNICIPALITY_NAME,
                "owner_provider": BUCHEON_EXPERIENCE_PROVIDER,
                "canonical_url": BUCHEON_EXPERIENCE_URL,
                "ownership_scope": BUCHEON_EXPERIENCE_OWNERSHIP_SCOPE,
                "source_total": len(source_rows),
                "experience_source_count": len(source_rows),
                "excluded_count": 0,
                "current_source_count": len(current_rows),
                "expired_count": len(expired_rows),
                "returned_count": len(deduped),
                "pages": first.last,
                "data_pages": first.last,
                "page_counts": {page.page: len(page.rows) for page in pages},
                "sentinel_page": sentinel_number,
                "sentinel_count": len(sentinel.rows),
                "detail_verified": len(output),
                "detail_registry_count": len(expected_registry),
                "application_controls_observed": len(output),
                "application_buttons_observed": application_button_count,
                "status_counts": dict(Counter(row["status"] for row in deduped)),
                "branch_counts": dict(Counter(row["branch"] for row in deduped)),
                "municipality_counts": dict(
                    Counter(row["municipality_code"] for row in deduped)
                ),
                "source_identity_sha256": _identity_hash(source_ids),
                "returned_identity_sha256": _identity_hash(
                    str(row["provider_course_id"]) for row in deduped
                ),
                "boundary_rechecks": boundary_checks,
                "pagination_complete": True,
                "details_complete": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
                "no_current_data": not deduped,
                "no_current_reason": (
                    "complete official Bucheon experience ledger has no current/future rows"
                    if not deduped
                    else ""
                ),
            }
        )
        return deduped, BUCHEON_EXPERIENCE_PARSER, meta
    except Exception as exc:
        meta.update(
            {
                "errors": [f"{type(exc).__name__}: {_clean(exc)}"],
                "configured_collection_error": f"{type(exc).__name__}: {_clean(exc)}",
                "error_kind": "contract",
                "returned_count": 0,
                "snapshot_complete": False,
                "full_snapshot_validated": False,
                "pagination_complete": False,
                "details_complete": False,
            }
        )
        return [], BUCHEON_EXPERIENCE_PARSER, meta
    finally:
        requester.close()


collect = collect_bucheon_experience


__all__ = [
    "BUCHEON_COVERED_MUNICIPALITIES",
    "BUCHEON_EXPERIENCE_DETAIL_PATH",
    "BUCHEON_EXPERIENCE_HOST",
    "BUCHEON_EXPERIENCE_LIST_PATH",
    "BUCHEON_EXPERIENCE_MUNICIPALITY_CODE",
    "BUCHEON_EXPERIENCE_MUNICIPALITY_NAME",
    "BUCHEON_EXPERIENCE_PAGE_SIZE",
    "BUCHEON_EXPERIENCE_PARSER",
    "BUCHEON_EXPERIENCE_PROVIDER",
    "BUCHEON_EXPERIENCE_RESERVE_PATH",
    "BUCHEON_EXPERIENCE_URL",
    "BucheonExperienceContractError",
    "bucheon_experience_detail_url",
    "bucheon_experience_list_url",
    "collect",
    "collect_bucheon_experience",
    "is_bucheon_experience_target",
    "is_target",
]
