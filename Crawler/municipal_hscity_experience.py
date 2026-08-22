"""Fail-closed collector for Hwaseong's official experience catalogues.

The portal owns separate public ledgers for ``견학/관람`` and
``체험/캠프``.  This module reads only those list pages and their public
details.  Application, login, attachment, applicant and personal-information
routes are rejected before a network request can be made.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
from html import unescape
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import requests


HSCITY_EXPERIENCE_PROVIDER = "MUNI_YEYAK_HSCITY_GO_KR_2DFD650A"
HSCITY_EXPERIENCE_HOST = "yeyak.hscity.go.kr"
HSCITY_EXPERIENCE_URL = (
    "https://yeyak.hscity.go.kr/1012/3008/visitList.do"
)
HSCITY_EXPERIENCE_PAGE_SIZE = 10
HSCITY_EXPERIENCE_MAX_HTML_BYTES = 5_000_000
HSCITY_EXPERIENCE_PARSER = (
    "hscity_experience_official_visit_and_exprn_ledgers+pagination_last_"
    "boundaries+exact_empty_post_last_sentinels+stable_first_last_sentinel+"
    "all_open_scheduled_public_details+2026_four_district_kakao_addresses+"
    "locked_experience+no_application_login_attachment_applicant_or_pii_calls"
)


@dataclass(frozen=True)
class HwaseongExperienceSource:
    key: str
    label: str
    list_path: str
    detail_path: str
    identity_param: str
    list_fee_label: str


HSCITY_EXPERIENCE_SOURCES: tuple[HwaseongExperienceSource, ...] = (
    HwaseongExperienceSource(
        "visit",
        "견학/관람",
        "/1012/3008/visitList.do",
        "/1012/3008/visitDetail.do",
        "visitIdx",
        "비용",
    ),
    HwaseongExperienceSource(
        "exprn",
        "체험/캠프",
        "/1013/3009/exprnList.do",
        "/1013/3009/exprnDetail.do",
        "exprnIdx",
        "이용료",
    ),
)
_SOURCE_BY_LIST_PATH = {
    source.list_path: source for source in HSCITY_EXPERIENCE_SOURCES
}
_SOURCE_BY_DETAIL_PATH = {
    source.detail_path: source for source in HSCITY_EXPERIENCE_SOURCES
}

HSCITY_EXPERIENCE_DISTRICTS: dict[str, tuple[str, str]] = {
    "만세구": ("4159100000", "경기도 화성시 만세구"),
    "효행구": ("4159300000", "경기도 화성시 효행구"),
    "병점구": ("4159500000", "경기도 화성시 병점구"),
    "동탄구": ("4159700000", "경기도 화성시 동탄구"),
}

_STATUS_MAP = {
    "신청하기": "OPEN",
    "접수예정": "SCHEDULED",
    "신청마감": "CLOSED",
    "강좌폐강": "CANCELLED",
}
_CURRENT_SOURCE_STATUSES = {"신청하기", "접수예정"}
_DATE = re.compile(r"(?<!\d)(20\d{2})[./-](\d{1,2})[./-](\d{1,2})(?!\d)")
_POSITIVE = re.compile(r"[1-9]\d{0,19}")
_PHONE = re.compile(r"(?<!\d)(?:01[016789]|0\d{1,2})[- ]?\d{3,4}[- ]?\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
_FORBIDDEN_OUTPUT_KEYS = {
    "applicant",
    "attachment",
    "contact",
    "email",
    "instructor",
    "login",
    "phone",
    "resident_number",
}
_DETAIL_REQUIRED = {
    "운영기관",
    "장소",
    "주요대상",
    "이용료",
    "신청기간",
    "정원수",
}
_DETAIL_ALLOWED = _DETAIL_REQUIRED | {
    "이용기간",
    "신청기간 안내",
    "결제방법",
    "선정방법",
    "부대시설",
    "첨부파일",
    "문의처",
}

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class HwaseongExperienceContractError(RuntimeError):
    """Raised when an official public-source invariant changes."""


@dataclass(frozen=True)
class _ListRow:
    source: HwaseongExperienceSource
    identity: str
    title: str
    institution: str
    place: str
    fee: str
    source_status: str
    page: int


@dataclass(frozen=True)
class _ListPage:
    source: HwaseongExperienceSource
    page: int
    last: int
    rows: tuple[_ListRow, ...]


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _query_key(url: str) -> tuple[str, str, tuple[tuple[str, str], ...]]:
    parsed = urlparse(_clean(url))
    return (
        parsed.scheme.lower(),
        parsed.netloc.lower() + parsed.path,
        tuple(sorted(parse_qsl(parsed.query, keep_blank_values=True))),
    )


def is_hscity_experience_target(target: Any) -> bool:
    return (
        _clean(_target_value(target, "provider"))
        == HSCITY_EXPERIENCE_PROVIDER
        and _clean(_target_value(target, "url")) == HSCITY_EXPERIENCE_URL
    )


def hscity_experience_list_url(
    source: HwaseongExperienceSource, page: int
) -> str:
    if source not in HSCITY_EXPERIENCE_SOURCES or page < 1:
        raise ValueError("invalid Hwaseong experience list request")
    query = urlencode(
        {
            "currentPageNo": str(page),
            "recordCountPerPage": str(HSCITY_EXPERIENCE_PAGE_SIZE),
        }
    )
    return f"https://{HSCITY_EXPERIENCE_HOST}{source.list_path}?{query}"


def hscity_experience_detail_url(
    source: HwaseongExperienceSource, identity: Any
) -> str:
    value = _clean(identity)
    if source not in HSCITY_EXPERIENCE_SOURCES or not _POSITIVE.fullmatch(value):
        raise ValueError("invalid Hwaseong experience detail request")
    return (
        f"https://{HSCITY_EXPERIENCE_HOST}{source.detail_path}?"
        + urlencode({source.identity_param: value})
    )


def _request_kind(method: str, url: str) -> str:
    if method.upper() != "GET":
        raise HwaseongExperienceContractError("only GET is allowed")
    parsed = urlparse(_clean(url))
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != HSCITY_EXPERIENCE_HOST
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise HwaseongExperienceContractError("unsafe source URL")
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if parsed.path in _SOURCE_BY_LIST_PATH:
        if set(query) != {"currentPageNo", "recordCountPerPage"}:
            raise HwaseongExperienceContractError("unsafe list query")
        if (
            not _POSITIVE.fullmatch(query["currentPageNo"])
            or query["recordCountPerPage"]
            != str(HSCITY_EXPERIENCE_PAGE_SIZE)
        ):
            raise HwaseongExperienceContractError("unsafe list boundary")
        return "list"
    source = _SOURCE_BY_DETAIL_PATH.get(parsed.path)
    if source is not None:
        if set(query) != {source.identity_param} or not _POSITIVE.fullmatch(
            query[source.identity_param]
        ):
            raise HwaseongExperienceContractError("unsafe detail query")
        return "detail"
    raise HwaseongExperienceContractError("private or unrelated route rejected")


def _default_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": "Mozilla/5.0 municipal-course-crawler/1.0",
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
        kind = _request_kind("GET", url)
        self.meta["logical_requests"] += 1
        self.meta[f"{kind}_requests"] += 1
        response = self.fetcher(self.session, url, self.timeout)
        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            raise HwaseongExperienceContractError(f"HTTP {status}")
        if tuple(getattr(response, "history", ()) or ()):
            raise HwaseongExperienceContractError("redirect history is forbidden")
        headers = getattr(response, "headers", {}) or {}
        if any(
            str(key).lower() == "location" and value
            for key, value in headers.items()
        ):
            raise HwaseongExperienceContractError("redirect location is forbidden")
        final_url = _clean(getattr(response, "url", ""))
        if final_url and _query_key(final_url) != _query_key(url):
            raise HwaseongExperienceContractError("response URL changed")
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
            raise HwaseongExperienceContractError("unexpected content type")
        body = getattr(response, "content", None)
        if body is None:
            body = str(getattr(response, "text", response)).encode("utf-8")
        body = bytes(body)
        if not body or len(body) > HSCITY_EXPERIENCE_MAX_HTML_BYTES:
            raise HwaseongExperienceContractError("empty or oversized response")
        soup = BeautifulSoup(body, "html.parser")
        title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
        if title != "화성특례시 통합예약시스템":
            raise HwaseongExperienceContractError("official page title changed")
        return soup

    def close(self) -> None:
        close = getattr(self.session, "close", None)
        if callable(close):
            close()


def _validate_source_registry(soup: BeautifulSoup) -> None:
    expected = {source.label: source.list_path for source in HSCITY_EXPERIENCE_SOURCES}
    observed: dict[str, set[str]] = {label: set() for label in expected}
    for anchor in soup.select(".header-gnb-depth3-item > a[href]"):
        label = _clean(anchor.get_text(" ", strip=True))
        if label in observed:
            observed[label].add(urlparse(_clean(anchor.get("href"))).path)
    if observed != {label: {path} for label, path in expected.items()}:
        raise HwaseongExperienceContractError(
            "official visit/experience source registry changed"
        )


def _list_pairs(card: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for description in card.select(".sub-card-info-list dl"):
        label = description.select_one("dt")
        value = description.select_one("dd")
        if label is None or value is None:
            raise HwaseongExperienceContractError("list field pairing changed")
        key = _clean(label.get_text(" ", strip=True))
        if not key or key in result:
            raise HwaseongExperienceContractError("duplicate list field")
        result[key] = _clean(value.get_text(" ", strip=True))
    return result


def _declared_last(soup: BeautifulSoup, page: int) -> int:
    pages: set[int] = set()
    for node in soup.select(".table-pagination [onclick*='fnList']"):
        match = re.search(r"fnList\((\d+)\)", _clean(node.get("onclick")))
        if match:
            pages.add(int(match.group(1)))
    active = soup.select_one(".table-pagination .page-list li.active")
    if active is not None:
        value = _clean(active.get_text(" ", strip=True))
        if value.isdigit():
            pages.add(int(value))
    if not pages or page not in pages:
        raise HwaseongExperienceContractError("pagination boundary changed")
    return max(pages)


def _parse_list_page(
    soup: BeautifulSoup,
    source: HwaseongExperienceSource,
    page: int,
    *,
    expected_last: Optional[int] = None,
) -> _ListPage:
    form = soup.select_one("form[name='paramForm']")
    if form is None or _clean(form.get("method")).upper() != "GET":
        raise HwaseongExperienceContractError("public list form changed")
    page_input = form.select_one("input[name='currentPageNo']")
    size_input = form.select_one("input[name='recordCountPerPage']")
    if (
        page_input is None
        or _clean(page_input.get("value")) != str(page)
        or size_input is None
        or _clean(size_input.get("value"))
        != str(HSCITY_EXPERIENCE_PAGE_SIZE)
    ):
        raise HwaseongExperienceContractError("public list controls changed")

    cards = soup.select(".sub-card-list .sub-card-item")
    if not cards:
        if expected_last is None or page != expected_last + 1:
            raise HwaseongExperienceContractError("unexpected empty list page")
        if soup.select(".table-pagination [onclick*='fnList']"):
            raise HwaseongExperienceContractError("post-last sentinel changed")
        return _ListPage(source, page, expected_last, ())

    last = _declared_last(soup, page)
    if expected_last is not None and last != expected_last:
        raise HwaseongExperienceContractError("declared last page drift")
    if page > last:
        raise HwaseongExperienceContractError("rows appeared after last page")

    rows: list[_ListRow] = []
    expected_fields = {"기관", "장소", source.list_fee_label}
    for card in cards:
        identities: set[str] = set()
        for node in card.select("[onclick*='fnDetail']"):
            match = re.search(
                r"fnDetail\('(\d+)'\)", _clean(node.get("onclick"))
            )
            if match:
                identities.add(match.group(1))
        if len(identities) != 1:
            raise HwaseongExperienceContractError("list identity binding changed")
        identity = identities.pop()
        if not _POSITIVE.fullmatch(identity):
            raise HwaseongExperienceContractError("invalid list identity")
        title_node = card.select_one(".sub-card-info-title")
        title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
        fields = _list_pairs(card)
        button = card.select_one(".sub-card-btn-box .sub-card-btn")
        source_status = _clean(
            button.get_text(" ", strip=True) if button else ""
        )
        if not title or set(fields) != expected_fields or source_status not in _STATUS_MAP:
            raise HwaseongExperienceContractError(
                f"{source.key}:{identity}: list card contract changed"
            )
        classes = set(button.get("class", []))
        onclick = _clean(button.get("onclick"))
        if source_status == "신청하기":
            if "orange" not in classes or f"fnApply('{identity}')" not in onclick:
                raise HwaseongExperienceContractError(
                    f"{source.key}:{identity}: open action binding changed"
                )
        elif onclick:
            raise HwaseongExperienceContractError(
                f"{source.key}:{identity}: closed action became callable"
            )
        rows.append(
            _ListRow(
                source,
                identity,
                title,
                fields["기관"],
                fields["장소"],
                fields[source.list_fee_label],
                source_status,
                page,
            )
        )

    expected_count = (
        HSCITY_EXPERIENCE_PAGE_SIZE
        if page < last
        else len(rows)
    )
    if len(rows) != expected_count or not 1 <= len(rows) <= HSCITY_EXPERIENCE_PAGE_SIZE:
        raise HwaseongExperienceContractError("list row boundary changed")
    if len({row.identity for row in rows}) != len(rows):
        raise HwaseongExperienceContractError("duplicate identity within page")
    return _ListPage(source, page, last, tuple(rows))


def _page_signature(page: _ListPage) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (
            row.identity,
            row.title,
            row.institution,
            row.place,
            row.fee,
            row.source_status,
        )
        for row in page.rows
    )


def _detail_pairs(soup: BeautifulSoup) -> dict[str, str]:
    result: dict[str, str] = {}
    for description in soup.select(".detail-info-list dl"):
        label = description.select_one("dt")
        value = description.select_one("dd")
        if label is None or value is None:
            raise HwaseongExperienceContractError("detail field pairing changed")
        key = _clean(label.get_text(" ", strip=True))
        if not key or key in result:
            raise HwaseongExperienceContractError("duplicate detail field")
        result[key] = _clean(value.get_text(" ", strip=True))
    if not _DETAIL_REQUIRED.issubset(result) or not set(result).issubset(
        _DETAIL_ALLOWED
    ):
        raise HwaseongExperienceContractError("detail field registry changed")
    if any(not result[key] for key in _DETAIL_REQUIRED):
        raise HwaseongExperienceContractError("required detail value is empty")
    return result


def _date_bounds(value: str, field: str) -> tuple[str, str]:
    values: list[date] = []
    for match in _DATE.finditer(_clean(value)):
        try:
            values.append(date(*(int(item) for item in match.groups())))
        except ValueError as exc:
            raise HwaseongExperienceContractError(
                f"invalid {field} date"
            ) from exc
    if not values:
        return "", ""
    if len(values) == 1:
        return values[0].isoformat(), values[0].isoformat()
    if len(values) != 2 or values[0] > values[1]:
        raise HwaseongExperienceContractError(f"invalid {field} period")
    return values[0].isoformat(), values[1].isoformat()


def _official_location(soup: BeautifulSoup) -> tuple[str, float, float]:
    markup = unescape(str(soup))
    match = re.search(
        r"kakao\.com/link/map/([^,\"\r\n]+),([0-9.]+),([0-9.]+)",
        markup,
    )
    if match is None:
        raise HwaseongExperienceContractError("official Kakao location changed")
    address, latitude_text, longitude_text = (
        _clean(item) for item in match.groups()
    )
    latitude = float(latitude_text)
    longitude = float(longitude_text)
    if not 36.0 <= latitude <= 38.5 or not 125.0 <= longitude <= 129.5:
        raise HwaseongExperienceContractError(
            "official Kakao coordinates changed"
        )
    districts = [name for name in HSCITY_EXPERIENCE_DISTRICTS if name in address]
    if "화성시" not in address or len(districts) != 1:
        raise HwaseongExperienceContractError(
            "official 2026 district address changed"
        )
    return address, latitude, longitude


def _parse_detail(soup: BeautifulSoup, listed: _ListRow) -> dict[str, Any]:
    title_node = soup.select_one(".detail-info-head-title")
    title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
    if title != listed.title:
        raise HwaseongExperienceContractError(
            f"{listed.source.key}:{listed.identity}: detail title mismatch"
        )
    form = soup.select_one("form[name='paramForm']")
    identity = form.select_one(
        f"input[name='{listed.source.identity_param}']"
    ) if form else None
    if identity is None or _clean(identity.get("value")) != listed.identity:
        raise HwaseongExperienceContractError(
            f"{listed.source.key}:{listed.identity}: detail identity mismatch"
        )
    pairs = _detail_pairs(soup)
    detail_institution = re.sub(
        r"\s*바로가기\s*$", "", pairs["운영기관"]
    ).strip()
    if detail_institution != listed.institution:
        raise HwaseongExperienceContractError(
            f"{listed.source.key}:{listed.identity}: detail institution mismatch"
        )
    button = soup.select_one(".detail-info-btn .sub-small-btn")
    detail_status = _clean(button.get_text(" ", strip=True) if button else "")
    if detail_status != listed.source_status:
        raise HwaseongExperienceContractError(
            f"{listed.source.key}:{listed.identity}: detail status mismatch"
        )
    if listed.source_status == "신청하기":
        if "active" not in set(button.get("class", [])) or "fnApply()" not in _clean(
            button.get("onclick")
        ):
            raise HwaseongExperienceContractError(
                f"{listed.source.key}:{listed.identity}: detail action changed"
            )
    elif "active" in set(button.get("class", [])) or _clean(button.get("onclick")):
        raise HwaseongExperienceContractError(
            f"{listed.source.key}:{listed.identity}: scheduled action became callable"
        )

    address, latitude, longitude = _official_location(soup)
    district = next(name for name in HSCITY_EXPERIENCE_DISTRICTS if name in address)
    municipality_code, municipality_name = HSCITY_EXPERIENCE_DISTRICTS[district]
    apply_start, apply_end = _date_bounds(pairs["신청기간"], "application")
    period = pairs.get("이용기간") or pairs["신청기간"]
    start_date, end_date = _date_bounds(
        pairs.get("이용기간", ""), "programme"
    )
    raw_url = hscity_experience_detail_url(listed.source, listed.identity)
    return {
        "provider": HSCITY_EXPERIENCE_PROVIDER,
        "municipality_code": municipality_code,
        "municipality_name": municipality_name,
        "provider_course_id": (
            f"{HSCITY_EXPERIENCE_PROVIDER}:experience:"
            f"{listed.source.key}:{listed.identity}"
        ),
        "source_course_id": (
            f"experience:{listed.source.key}:{listed.identity}"
        ),
        "title": listed.title,
        "branch": listed.institution,
        "branch_code": f"HSCITY_EXPERIENCE_{municipality_code}_{listed.institution}",
        "branch_address": address,
        "branch_address_source": "official_course_detail",
        "branch_lat": latitude,
        "branch_lon": longitude,
        "branch_coordinate_source": "official_course_detail",
        "branch_location_confidence": 100,
        "branch_location_verified": True,
        "branch_location_query": address,
        "preserve_branch": True,
        "category": f"화성특례시 문화/체험/{listed.source.label}",
        "collection_category": "공공예약",
        "domain_category": "체험·견학",
        "source_group": "municipal_reservation",
        "service_group": "체험",
        "service_group_policy": "locked",
        "classification_locked": True,
        "operator_type": "지자체/공공기관",
        "program_type": listed.source.label,
        "source_status": listed.source_status,
        "status": _STATUS_MAP[listed.source_status],
        "reservation_available": listed.source_status == "신청하기",
        "period": period,
        "start_date": start_date,
        "end_date": end_date,
        "apply_period": pairs["신청기간"],
        "apply_start_date": apply_start,
        "apply_end_date": apply_end,
        "target": pairs["주요대상"],
        "venue_name": pairs["장소"],
        "fee": pairs["이용료"],
        "application_url": "",
        "raw_url": raw_url,
        "raw_fields": {
            "parser": HSCITY_EXPERIENCE_PARSER,
            "official_source": listed.source.key,
            "official_identity": listed.identity,
            "official_source_status": listed.source_status,
            "official_district": district,
            "list_page": listed.page,
            "public_detail_verified": True,
            "application_steps_observed_not_called": True,
        },
    }


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                key_text = str(key).lower()
                child_path = f"{path}.{key}" if path else str(key)
                if key_text in _FORBIDDEN_OUTPUT_KEYS:
                    errors.append(f"forbidden key {child_path}")
                walk(child, child_path)
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")
        elif isinstance(value, str) and (_PHONE.search(value) or _EMAIL.search(value)):
            errors.append(f"PII value in {path}")

    walk(row, "")
    return errors


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            output.append(row)
    return output


def _identity_hash(values: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(sorted(values)).encode("utf-8")).hexdigest()


def _today(value: Optional[date | datetime | str]) -> date:
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
        "logical_requests": 0,
        "list_requests": 0,
        "detail_requests": 0,
        "application_endpoint_requests": 0,
        "login_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "applicant_endpoint_requests": 0,
        "pii_endpoint_requests": 0,
    }


def collect_hscity_experience_courses(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 30,
    detail_limit: int = 200,
    today: Optional[date | datetime | str] = None,
    session_factory: SessionFactory = _default_session,
    dedupe_rows: DedupeRows = _dedupe_default,
    fetcher: Fetcher = _default_fetcher,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one atomic snapshot of both official experience ledgers."""

    meta = _meta()
    if not is_hscity_experience_target(target):
        meta["errors"] = ["target does not match the canonical experience route"]
        meta["error_kind"] = "contract"
        return [], HSCITY_EXPERIENCE_PARSER, meta
    if timeout < 1 or max_pages < 1 or detail_limit < 0:
        meta["errors"] = ["invalid collection limits"]
        meta["error_kind"] = "contract"
        return [], HSCITY_EXPERIENCE_PARSER, meta

    requester = _Requester(session_factory, fetcher, timeout, meta)
    try:
        all_rows: list[_ListRow] = []
        source_summaries: dict[str, dict[str, Any]] = {}
        total_pages = 0
        for source_index, source in enumerate(HSCITY_EXPERIENCE_SOURCES):
            first_soup = requester.soup(
                hscity_experience_list_url(source, 1)
            )
            first = _parse_list_page(
                first_soup,
                source,
                1,
            )
            if source_index == 0:
                _validate_source_registry(first_soup)
            if first.last > max_pages:
                raise HwaseongExperienceContractError(
                    f"{source.key}: declared pages exceed collection limit"
                )
            pages = [first]
            for page_number in range(2, first.last + 1):
                pages.append(
                    _parse_list_page(
                        requester.soup(
                            hscity_experience_list_url(source, page_number)
                        ),
                        source,
                        page_number,
                        expected_last=first.last,
                    )
                )
            source_rows = [row for page in pages for row in page.rows]
            if len({row.identity for row in source_rows}) != len(source_rows):
                raise HwaseongExperienceContractError(
                    f"{source.key}: complete identity union changed"
                )
            sentinel_page = first.last + 1
            sentinel = _parse_list_page(
                requester.soup(
                    hscity_experience_list_url(source, sentinel_page)
                ),
                source,
                sentinel_page,
                expected_last=first.last,
            )
            stable_first = _parse_list_page(
                requester.soup(hscity_experience_list_url(source, 1)),
                source,
                1,
                expected_last=first.last,
            )
            stable_last = _parse_list_page(
                requester.soup(
                    hscity_experience_list_url(source, first.last)
                ),
                source,
                first.last,
                expected_last=first.last,
            )
            stable_sentinel = _parse_list_page(
                requester.soup(
                    hscity_experience_list_url(source, sentinel_page)
                ),
                source,
                sentinel_page,
                expected_last=first.last,
            )
            if (
                _page_signature(stable_first) != _page_signature(first)
                or _page_signature(stable_last) != _page_signature(pages[-1])
                or _page_signature(stable_sentinel)
                != _page_signature(sentinel)
            ):
                raise HwaseongExperienceContractError(
                    f"{source.key}: boundary stability changed"
                )
            all_rows.extend(source_rows)
            total_pages += first.last
            source_summaries[source.key] = {
                "label": source.label,
                "source_total": len(source_rows),
                "pages": first.last,
                "last_page_rows": len(pages[-1].rows),
                "sentinel_page": sentinel_page,
            }

        composite_ids = {(row.source.key, row.identity) for row in all_rows}
        if len(composite_ids) != len(all_rows):
            raise HwaseongExperienceContractError(
                "global composite identities changed"
            )
        current_rows = [
            row for row in all_rows if row.source_status in _CURRENT_SOURCE_STATUSES
        ]
        if len(current_rows) > detail_limit:
            raise HwaseongExperienceContractError(
                "detail limit truncates the current/future catalogue"
            )
        output = [
            _parse_detail(
                requester.soup(
                    hscity_experience_detail_url(row.source, row.identity)
                ),
                row,
            )
            for row in current_rows
        ]
        privacy = [error for row in output for error in _privacy_errors(row)]
        if privacy:
            raise HwaseongExperienceContractError(
                f"PII/output allowlist violation: {privacy[0]}"
            )
        deduped = list(dedupe_rows(output))
        if len(deduped) != len(output):
            raise HwaseongExperienceContractError("dedupe changed complete output")

        source_status_counts = Counter(row.source_status for row in all_rows)
        status_counts = Counter(row["status"] for row in deduped)
        source_counts = Counter(row.source.key for row in all_rows)
        current_source_counts = Counter(
            row.source.key for row in current_rows
        )
        district_counts = Counter(row["municipality_name"] for row in deduped)
        meta.update(
            {
                "source_count": len(HSCITY_EXPERIENCE_SOURCES),
                "source_total": len(all_rows),
                "source_pages": total_pages,
                "source_summaries": source_summaries,
                "source_counts": dict(sorted(source_counts.items())),
                "source_status_counts": dict(sorted(source_status_counts.items())),
                "current_source_count": len(current_rows),
                "current_source_counts": dict(
                    sorted(current_source_counts.items())
                ),
                "closed_or_cancelled_count": len(all_rows) - len(current_rows),
                "returned_count": len(deduped),
                "detail_pages": len(current_rows),
                "status_counts": dict(sorted(status_counts.items())),
                "district_counts": {
                    municipality_name: district_counts.get(
                        municipality_name, 0
                    )
                    for _, municipality_name in HSCITY_EXPERIENCE_DISTRICTS.values()
                },
                "source_identity_sha256": _identity_hash(
                    f"{row.source.key}:{row.identity}" for row in all_rows
                ),
                "current_identity_sha256": _identity_hash(
                    f"{row.source.key}:{row.identity}" for row in current_rows
                ),
                "cutoff": _today(today).isoformat(),
                "duplicate_count": 0,
                "semantic_duplicate_count": 0,
                "pagination_complete": True,
                "details_complete": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
            }
        )
        return deduped, HSCITY_EXPERIENCE_PARSER, meta
    except Exception as exc:
        meta["errors"] = [f"{type(exc).__name__}: {exc}"]
        meta["error_kind"] = (
            "contract"
            if isinstance(exc, HwaseongExperienceContractError)
            else "network_or_parse"
        )
        return [], HSCITY_EXPERIENCE_PARSER, meta
    finally:
        requester.close()


collect = collect_hscity_experience_courses


__all__ = [name for name in globals() if name.startswith("HSCITY_EXPERIENCE_")] + [
    "HwaseongExperienceContractError",
    "HwaseongExperienceSource",
    "collect",
    "collect_hscity_experience_courses",
    "hscity_experience_detail_url",
    "hscity_experience_list_url",
    "is_hscity_experience_target",
]
