"""Atomic collector for Hwaseong's official culture and experience catalogue.

The integrated reservation site exposes one combined current catalogue backed
by three service ledgers: visit/viewing, festivals/events, and experiences/
camps.  The collector partitions that catalogue by the four official district
filters and the two statuses included by the unfiltered page, reconciles the
district union with the global status scans, verifies empty sentinels and
stable boundaries, and enriches every item from its service-specific detail.

Application forms are login-gated and are deliberately not fetched.  A row is
marked reservable only when both its list card and public detail expose the
same identity-bound application control.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag


HSCITY_CULTURE_PROVIDER = "MUNI_YEYAK_HSCITY_GO_KR_92F9E762"
HSCITY_CULTURE_HOST = "yeyak.hscity.go.kr"
HSCITY_CULTURE_LIST_PATH = "/1012/3008/cultureAllList.do"
HSCITY_CULTURE_LIST_URL = (
    f"https://{HSCITY_CULTURE_HOST}{HSCITY_CULTURE_LIST_PATH}"
)
HSCITY_CULTURE_PARSER = (
    "hscity_culture_three_services+four_district_status_partitions+"
    "global_union_reconcile+empty_sentinels+stable_boundaries+all_details+"
    "identity_bound_application_controls+truthful_slot_fallbacks+"
    "public_field_allowlist+atomic_snapshot"
)
HSCITY_CULTURE_OWNERSHIP_SCOPE = (
    "hscity_current_visit_festival_and_experience_catalogue"
)

HSCITY_CULTURE_PAGE_SIZE = 15
HSCITY_CULTURE_STATUSES = ("ready", "apply")
HSCITY_CULTURE_STATUS_LABELS = {
    "ready": "접수예정",
    "apply": "접수중",
}
HSCITY_CULTURE_FETCH_ATTEMPTS = 3
HSCITY_CULTURE_MAX_WORKERS = 6
HSCITY_CULTURE_MAX_HTML_BYTES = 8_000_000
HSCITY_CULTURE_DATE_FALLBACK = "회차별 일정 선택"
HSCITY_CULTURE_TIME_FALLBACK = "회차별 시간 선택"
HSCITY_CULTURE_TARGET_FALLBACK = "대상 별도 안내"

HSCITY_CULTURE_DISTRICTS: Mapping[str, Mapping[str, str]] = {
    "401": {
        "name": "만세구",
        "municipality_code": "4159100000",
        "municipality_full_name": "경기도 화성시 만세구",
    },
    "402": {
        "name": "효행구",
        "municipality_code": "4159300000",
        "municipality_full_name": "경기도 화성시 효행구",
    },
    "403": {
        "name": "병점구",
        "municipality_code": "4159500000",
        "municipality_full_name": "경기도 화성시 병점구",
    },
    "404": {
        "name": "동탄구",
        "municipality_code": "4159700000",
        "municipality_full_name": "경기도 화성시 동탄구",
    },
}


@dataclass(frozen=True)
class CultureService:
    key: str
    detail_path: str
    identity_name: str
    category: str
    program_type: str


HSCITY_CULTURE_SERVICES = {
    service.key: service
    for service in (
        CultureService(
            "visit",
            "/1012/3008/visitDetail.do",
            "visitIdx",
            "견학/관람",
            "견학",
        ),
        CultureService(
            "festival",
            "/1071/3010/festivalDetail.do",
            "festivalIdx",
            "축제/행사",
            "행사",
        ),
        CultureService(
            "exprn",
            "/1013/3009/exprnDetail.do",
            "exprnIdx",
            "체험/캠프",
            "체험",
        ),
    )
}


class HscityCultureContractError(ValueError):
    """Raised when the audited public catalogue contract changes."""


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]
Sleeper = Callable[[float], None]

_SPACE_RE = re.compile(r"\s+")
_DETAIL_CALL_RE = re.compile(
    r"fnDetail\(\s*['\"](visit|festival|exprn)['\"]\s*,\s*"
    r"['\"](\d{1,20})['\"](?:\s*,\s*['\"][^'\"]*['\"])?\s*\)",
    re.IGNORECASE,
)
_APPLY_CALL_RE = re.compile(
    r"fnApply\(\s*['\"](visit|festival|exprn)['\"]\s*,\s*"
    r"['\"](\d{1,20})['\"]\s*\)",
    re.IGNORECASE,
)
_INTEREST_CALL_RE = re.compile(
    r"fnInterestInfoRegistProc\(\s*['\"](\d{1,20})['\"]\s*,\s*"
    r"['\"](visit|festival|exprn)['\"]\s*,\s*['\"](\d{1,20})['\"]",
    re.IGNORECASE,
)
_FULL_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})(?!\d)"
)
_TITLE_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})\s*년?")
_SLASH_MD_RANGE_RE = re.compile(
    r"(?<!\d)(\d{1,2})\s*[./]\s*(\d{1,2})\s*"
    r"(?:~|∼|～|-)\s*(\d{1,2})\s*[./]\s*(\d{1,2})(?!\d)"
)
_KOREAN_MD_RANGE_RE = re.compile(
    r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일?\s*"
    r"(?:~|∼|～|-)\s*(?:(\d{1,2})\s*월\s*)?(\d{1,2})\s*일"
)
_SLASH_MD_RE = re.compile(r"(?<!\d)(\d{1,2})\s*[./]\s*(\d{1,2})(?!\d)")
_KOREAN_MD_RE = re.compile(r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_TIME_RANGE_RE = re.compile(
    r"(?<!\d)([01]?\d|2[0-3])\s*:\s*([0-5]\d)\s*"
    r"(?:~|∼|～|-)\s*([01]?\d|2[0-3])\s*:\s*([0-5]\d)(?!\d)"
)
_TIME_ONE_RE = re.compile(
    r"(?<!\d)([01]?\d|2[0-3])\s*:\s*([0-5]\d)(?!\d)"
)
_KOREAN_TIME_RE = re.compile(
    r"(오전|오후)?\s*(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분)?"
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _text(node: Any) -> str:
    return _clean(node.get_text(" ", strip=True) if node is not None else "")


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _normalized_url(value: Any) -> str:
    parsed = urlparse(_clean(value))
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.params
        or parsed.fragment
    ):
        return ""
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return (
        f"https://{parsed.hostname.rstrip('.').lower()}{path}"
        + (f"?{query}" if query else "")
    )


def is_hscity_culture_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != HSCITY_CULTURE_PROVIDER:
        return False
    parsed = urlparse(_clean(_target_value(target, "url")))
    return bool(
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == HSCITY_CULTURE_HOST
        and parsed.port is None
        and not parsed.username
        and not parsed.password
        and parsed.path == HSCITY_CULTURE_LIST_PATH
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


is_target = is_hscity_culture_target


def hscity_culture_list_url(
    page: int,
    district_code: str,
    status_filter: str,
) -> str:
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise HscityCultureContractError("invalid culture page")
    if district_code and district_code not in HSCITY_CULTURE_DISTRICTS:
        raise HscityCultureContractError("invalid culture district")
    if status_filter not in HSCITY_CULTURE_STATUSES:
        raise HscityCultureContractError("invalid culture status")
    query = (
        ("currentPageNo", str(page)),
        ("recordCountPerPage", str(HSCITY_CULTURE_PAGE_SIZE)),
        ("allListYn", "Y"),
        ("searchInstitutionIdx", ""),
        ("searchInstitutionTypeCd", ""),
        ("institutionIdx", ""),
        ("searchAreaEmd", district_code),
        ("statusCd", status_filter),
        ("searchFreeYn", ""),
        ("searchYn", "Y"),
        ("kwrdSetupKindIdxs", ""),
    )
    return f"{HSCITY_CULTURE_LIST_URL}?{urlencode(query)}"


def hscity_culture_detail_url(service_type: str, service_id: Any) -> str:
    service = HSCITY_CULTURE_SERVICES.get(_clean(service_type).lower())
    identity = _clean(service_id)
    if service is None or not re.fullmatch(r"\d{1,20}", identity):
        raise HscityCultureContractError("invalid culture service identity")
    return urlunparse(
        (
            "https",
            HSCITY_CULTURE_HOST,
            service.detail_path,
            "",
            urlencode(((service.identity_name, identity),)),
            "",
        )
    )


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
        }
    )
    return current


def _default_fetcher(current: Any, url: str, timeout: int) -> Any:
    return current.get(url, timeout=timeout, allow_redirects=False)


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


class _RequestBudget:
    def __init__(self, maximum: int):
        self.maximum = maximum
        self.count = 0
        self._lock = threading.Lock()

    def take(self) -> None:
        with self._lock:
            if self.count >= self.maximum:
                raise HscityCultureContractError(
                    f"max_pages cap {self.maximum} exhausted"
                )
            self.count += 1


def _response_soup(response: Any, requested_url: str) -> BeautifulSoup:
    if isinstance(response, BeautifulSoup):
        return response
    try:
        status = int(getattr(response, "status_code", 0))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise HscityCultureContractError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise HscityCultureContractError("redirected source response")
    final_url = _clean(getattr(response, "url", "")) or requested_url
    if _normalized_url(final_url) != _normalized_url(requested_url):
        raise HscityCultureContractError("source response URL changed scope")
    content = getattr(response, "content", None)
    if content is None:
        content = getattr(response, "text", None)
    if not content:
        raise HscityCultureContractError("empty HTML response")
    byte_count = (
        len(content)
        if isinstance(content, bytes)
        else len(str(content).encode("utf-8"))
    )
    if byte_count > HSCITY_CULTURE_MAX_HTML_BYTES:
        raise HscityCultureContractError("source HTML exceeds safety limit")
    return BeautifulSoup(content, "lxml")


def _fetch_soup(
    current: Any,
    url: str,
    *,
    fetcher: Fetcher,
    timeout: int,
    attempts: int,
    sleeper: Sleeper,
    budget: Optional[_RequestBudget] = None,
) -> tuple[BeautifulSoup, int, int]:
    errors: list[str] = []
    for attempt in range(1, attempts + 1):
        try:
            if budget is not None:
                budget.take()
            response = fetcher(current, url, timeout)
            return _response_soup(response, url), attempt - 1, attempt
        except HscityCultureContractError as exc:
            errors.append(f"{type(exc).__name__}: {_clean(exc)}")
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {_clean(exc)}")
        if attempt < attempts:
            sleeper(min(0.2 * (2 ** (attempt - 1)), 0.8))
    raise HscityCultureContractError(
        f"source request failed after {attempts} attempts ({errors[-1]})"
    )


def _declared_total(soup: BeautifulSoup) -> int:
    nodes = soup.select(".table-total .num")
    if len(nodes) != 1:
        raise HscityCultureContractError("culture declared total changed")
    value = _text(nodes[0]).replace(",", "")
    if not value.isdigit():
        raise HscityCultureContractError("culture declared total is invalid")
    return int(value)


def _page_unit(soup: BeautifulSoup) -> int:
    nodes = soup.select("input[name='recordCountPerPage']")
    if len(nodes) != 1:
        raise HscityCultureContractError("culture page-size control changed")
    value = _clean(nodes[0].get("value"))
    if value != str(HSCITY_CULTURE_PAGE_SIZE):
        raise HscityCultureContractError(
            f"culture page size changed from {HSCITY_CULTURE_PAGE_SIZE} to {value}"
        )
    return HSCITY_CULTURE_PAGE_SIZE


def _onclick_identities(card: Tag) -> set[tuple[str, str]]:
    identities: set[tuple[str, str]] = set()
    controls = card.select("[onclick*='fnDetail']")
    if len(controls) < 2:
        raise HscityCultureContractError("culture detail controls changed")
    for control in controls:
        match = _DETAIL_CALL_RE.search(_clean(control.get("onclick")))
        if not match:
            raise HscityCultureContractError("culture detail control is invalid")
        identities.add((match.group(1).lower(), match.group(2)))
    if len(identities) != 1:
        raise HscityCultureContractError("culture card detail identities diverged")
    return identities


def _card_pairs(card: Tag) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for node in card.select("dl.sub-card-desc"):
        key_nodes = node.select(":scope > dt.sub-card-desc-title")
        value_nodes = node.select(":scope > dd.sub-card-desc-txt")
        if len(key_nodes) != 1 or len(value_nodes) != 1:
            raise HscityCultureContractError("culture card fields changed")
        key = _text(key_nodes[0])
        if not key or key in pairs:
            raise HscityCultureContractError("culture card fields duplicated")
        pairs[key] = _text(value_nodes[0])
    if set(pairs) != {"분류", "기관", "장소", "비용"}:
        raise HscityCultureContractError("culture card field boundary changed")
    return pairs


def _same_host_image(page_url: str, value: Any) -> str:
    parsed = urlparse(urljoin(page_url, _clean(value)))
    host = (parsed.hostname or "").rstrip(".").lower()
    if (
        parsed.scheme.lower() != "https"
        or not (
            host == HSCITY_CULTURE_HOST
            or host.endswith(".hscity.go.kr")
        )
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.params
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/")
    ):
        return ""
    return urlunparse(("https", host, parsed.path, "", "", ""))


def _branch_code(district_code: str, institution_idx: str, branch: str) -> str:
    identity = _clean(institution_idx)
    if not re.fullmatch(r"\d{1,20}", identity):
        identity = hashlib.sha1(_clean(branch).encode("utf-8")).hexdigest()[:10]
    return f"HSCITY_CULTURE_{district_code}_{identity}"[:50]


def _parse_card(
    target: Any,
    page_url: str,
    card: Tag,
    *,
    district_code: str,
    status_filter: str,
) -> tuple[str, dict[str, Any]]:
    identities = _onclick_identities(card)
    service_type, service_id = next(iter(identities))
    service = HSCITY_CULTURE_SERVICES[service_type]
    title_nodes = card.select(".sub-card-info-title > a")
    if len(title_nodes) != 1:
        raise HscityCultureContractError("culture card title changed")
    title = _text(title_nodes[0])
    if not title:
        raise HscityCultureContractError("culture card title is empty")
    pairs = _card_pairs(card)
    if pairs["분류"] != service.category:
        raise HscityCultureContractError(
            f"culture service/category mismatch for {service_type}:{service_id}"
        )
    if not pairs["기관"] or not pairs["장소"] or not pairs["비용"]:
        raise HscityCultureContractError("culture card required value is empty")

    interest_nodes = card.select("button[onclick*='fnInterestInfoRegistProc']")
    if len(interest_nodes) != 1:
        raise HscityCultureContractError("culture interest identity control changed")
    interest_match = _INTEREST_CALL_RE.search(
        _clean(interest_nodes[0].get("onclick"))
    )
    if (
        not interest_match
        or interest_match.group(2).lower() != service_type
        or interest_match.group(3) != service_id
    ):
        raise HscityCultureContractError(
            "culture institution/service identity mismatch"
        )
    institution_idx = interest_match.group(1)

    apply_nodes = card.select(".sub-card-btn[onclick*='fnApply']")
    if status_filter == "apply":
        if len(apply_nodes) != 1:
            raise HscityCultureContractError(
                "open culture application control changed"
            )
        match = _APPLY_CALL_RE.search(_clean(apply_nodes[0].get("onclick")))
        if (
            not match
            or match.group(1).lower() != service_type
            or match.group(2) != service_id
            or _text(apply_nodes[0]) != "신청하기"
        ):
            raise HscityCultureContractError(
                "culture application identity mismatch"
            )
        application_available = True
    else:
        ready_nodes = card.select(".sub-card-btn.none")
        if apply_nodes or len(ready_nodes) != 1 or _text(ready_nodes[0]) != "접수예정":
            raise HscityCultureContractError(
                "scheduled culture control changed"
            )
        application_available = False

    image_nodes = card.select(".sub-card-img-link > img[src]")
    if len(image_nodes) != 1:
        raise HscityCultureContractError("culture card image changed")
    image_src = _clean(image_nodes[0].get("src"))
    image_url = _same_host_image(page_url, image_src) if image_src else ""
    if image_src and not image_url:
        raise HscityCultureContractError("culture card image URL left scope")

    detail_url = hscity_culture_detail_url(service_type, service_id)
    district = HSCITY_CULTURE_DISTRICTS[district_code]
    provider = _clean(_target_value(target, "provider"))
    identity = f"{service_type}:{service_id}"
    row: dict[str, Any] = {
        "provider": provider,
        "provider_course_id": identity,
        "title": title,
        "branch": pairs["기관"],
        "branch_code": _branch_code(
            district_code, institution_idx, pairs["기관"]
        ),
        "preserve_branch": True,
        "branch_url": HSCITY_CULTURE_LIST_URL,
        "venue_name": pairs["장소"],
        "category": service.category,
        "fee": pairs["비용"],
        "status": HSCITY_CULTURE_STATUS_LABELS[status_filter],
        "raw_url": detail_url,
        "description": _text(card)[:2000],
        "image_url": image_url,
        "program_type": service.program_type,
        "reservation_available": application_available,
        "municipality_code": district["municipality_code"],
        "municipality_full_name": district["municipality_full_name"],
        "raw_fields": {
            "parser": HSCITY_CULTURE_PARSER,
            "service_type": service_type,
            "service_id": service_id,
            "district_filter": district_code,
            "district_name": district["name"],
            "status_filter": status_filter,
            "institution_idx": institution_idx,
            "list_pairs": pairs,
            "list_application_control": application_available,
            "page_url": page_url,
            "pii_fields_read": [],
        },
    }
    if application_available:
        row["application_url"] = detail_url
    return identity, row


def _page_cards(
    soup: BeautifulSoup,
    *,
    page: int,
    expected_total: Optional[int] = None,
) -> tuple[list[Tag], int, int]:
    total = _declared_total(soup)
    unit = _page_unit(soup)
    if expected_total is not None and total != expected_total:
        raise HscityCultureContractError(
            f"culture declared total changed from {expected_total} to {total}"
        )
    expected_pages = max(1, math.ceil(total / unit))
    cards = list(soup.select(".sub-card-list > .sub-card-item"))
    expected_count = (
        max(0, min(unit, total - ((page - 1) * unit)))
        if page <= expected_pages
        else 0
    )
    if len(cards) != expected_count:
        raise HscityCultureContractError(
            f"culture page {page} exposed {len(cards)} of {expected_count} cards"
        )
    if cards:
        current_nodes = soup.select(".page-list > li.active > a.num")
        if len(current_nodes) != 1 or _text(current_nodes[0]) != str(page):
            raise HscityCultureContractError(
                f"culture active page marker changed on page {page}"
            )
    return cards, total, expected_pages


def _identity_sequence(cards: Iterable[Tag]) -> tuple[str, ...]:
    result: list[str] = []
    for card in cards:
        service_type, service_id = next(iter(_onclick_identities(card)))
        result.append(f"{service_type}:{service_id}")
    if len(result) != len(set(result)):
        raise HscityCultureContractError("culture page duplicated an identity")
    return tuple(result)


def _clean_branch(value: str) -> str:
    return re.sub(r"\s*바로가기\s*$", "", _clean(value))


def _detail_pairs(soup: BeautifulSoup) -> dict[str, str]:
    roots = soup.select(".detail-info-list")
    if len(roots) != 1:
        raise HscityCultureContractError(
            "culture detail information root changed"
        )
    pairs: dict[str, str] = {}
    for node in roots[0].select("dl.item-desc"):
        key_nodes = node.select(":scope > dt.desc-title")
        value_nodes = node.select(":scope > dd.desc-txt")
        if len(key_nodes) != 1 or len(value_nodes) != 1:
            raise HscityCultureContractError(
                "culture detail field structure changed"
            )
        key = _text(key_nodes[0])
        value = _text(value_nodes[0])
        if not key or key in pairs:
            raise HscityCultureContractError(
                "culture detail fields duplicated"
            )
        pairs[key] = value
    required = {
        "운영기관",
        "장소",
        "주요대상",
        "이용료",
        "신청기간",
        "정원수",
        "선정방법",
        "부대시설",
        "문의처",
    }
    allowed = required | {
        "이용기간",
        "결제방법",
        "신청기간 안내",
        "첨부파일",
    }
    if not required.issubset(pairs) or not set(pairs).issubset(allowed):
        raise HscityCultureContractError(
            "culture detail field boundary changed"
        )
    for key in ("운영기관", "장소", "이용료", "신청기간", "정원수"):
        if not pairs[key]:
            raise HscityCultureContractError(
                f"culture detail required value {key!r} is empty"
            )
    return pairs


def _iso_date(year: int, month: int, day: int) -> str:
    try:
        return date(year, month, day).isoformat()
    except ValueError as exc:
        raise HscityCultureContractError(
            f"invalid published culture date {year}-{month}-{day}"
        ) from exc


def _full_date_period(value: str) -> str:
    matches = _FULL_DATE_RE.findall(_clean(value))
    if not matches:
        return ""
    dates = [_iso_date(int(year), int(month), int(day)) for year, month, day in matches]
    if len(dates) == 1:
        return dates[0]
    return f"{dates[0]} ~ {dates[-1]}"


def _inferred_year(month: int, today: date, year_hint: Optional[int]) -> int:
    if year_hint is not None:
        return year_hint
    year = today.year
    if month < today.month - 6:
        year += 1
    elif month > today.month + 6:
        year -= 1
    return year


def _month_day_period(
    value: str,
    *,
    today: date,
    year_hint: Optional[int],
    allow_single: bool,
) -> str:
    text = _clean(value)
    match = _SLASH_MD_RANGE_RE.search(text)
    if match:
        sm, sd, em, ed = (int(part) for part in match.groups())
        start_year = _inferred_year(sm, today, year_hint)
        end_year = start_year + (1 if em < sm else 0)
        return (
            f"{_iso_date(start_year, sm, sd)} ~ "
            f"{_iso_date(end_year, em, ed)}"
        )
    match = _KOREAN_MD_RANGE_RE.search(text)
    if match:
        sm, sd = int(match.group(1)), int(match.group(2))
        em = int(match.group(3) or sm)
        ed = int(match.group(4))
        start_year = _inferred_year(sm, today, year_hint)
        end_year = start_year + (1 if em < sm else 0)
        return (
            f"{_iso_date(start_year, sm, sd)} ~ "
            f"{_iso_date(end_year, em, ed)}"
        )
    if not allow_single:
        return ""
    match = _SLASH_MD_RE.search(text) or _KOREAN_MD_RE.search(text)
    if match:
        month, day = int(match.group(1)), int(match.group(2))
        year = _inferred_year(month, today, year_hint)
        return _iso_date(year, month, day)
    return ""


def _period_from_source(
    detail_period: str,
    title: str,
    description: str,
    *,
    today: date,
) -> tuple[str, str]:
    explicit = _full_date_period(detail_period)
    if explicit:
        return explicit, ""
    year_match = _TITLE_YEAR_RE.search(title)
    year_hint = int(year_match.group(1)) if year_match else None
    title_period = _month_day_period(
        title,
        today=today,
        year_hint=year_hint,
        allow_single=year_hint is not None,
    )
    if title_period:
        return title_period, "title_date"
    label_match = re.search(
        r"(?:행사|운영|체험|관람|심사)\s*(?:일시|일정|기간)\s*[:：]?\s*"
        r"(.{0,120})",
        description,
    )
    if label_match:
        labeled = _full_date_period(label_match.group(1))
        if not labeled:
            labeled = _month_day_period(
                label_match.group(1),
                today=today,
                year_hint=year_hint,
                allow_single=True,
            )
        if labeled:
            return labeled, "description_labeled_date"
    return HSCITY_CULTURE_DATE_FALLBACK, "source_slot_selection"


def _schedule_from_source(description: str) -> tuple[str, str]:
    text = _clean(description)
    match = _TIME_RANGE_RE.search(text)
    if match:
        sh, sm, eh, em = (int(part) for part in match.groups())
        return f"{sh:02d}:{sm:02d} ~ {eh:02d}:{em:02d}", ""
    match = _TIME_ONE_RE.search(text)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        return f"{hour:02d}:{minute:02d}", "description_single_time"
    match = _KOREAN_TIME_RE.search(text)
    if match:
        meridiem, hour_text, minute_text = match.groups()
        hour = int(hour_text)
        minute = int(minute_text or 0)
        if meridiem == "오후" and hour < 12:
            hour += 12
        elif meridiem == "오전" and hour == 12:
            hour = 0
        if hour <= 23 and minute <= 59:
            return f"{hour:02d}:{minute:02d}", "description_korean_time"
    return HSCITY_CULTURE_TIME_FALLBACK, "source_slot_selection"


def _capacity(value: str) -> Optional[int]:
    text = _clean(value).replace(",", "")
    if text in {"제한안함", "제한 없음"}:
        return None
    match = re.search(r"(?<!\d)(\d{1,6})\s*명", text)
    return int(match.group(1)) if match else None


def _detail_description(soup: BeautifulSoup) -> str:
    nodes = soup.select(".detail-tab.info-tab")
    if not nodes:
        return ""
    if len(nodes) != 1:
        raise HscityCultureContractError(
            "culture public detail body changed"
        )
    return _text(nodes[0])[:4000]


def _enrich_detail(
    row: dict[str, Any],
    soup: BeautifulSoup,
    *,
    today: date,
) -> None:
    title_nodes = soup.select(".detail-info-head-title")
    if len(title_nodes) != 1 or _text(title_nodes[0]) != _clean(row.get("title")):
        raise HscityCultureContractError(
            "culture list/detail title mismatch"
        )
    pairs = _detail_pairs(soup)
    description = _detail_description(soup)
    raw_fields = row.setdefault("raw_fields", {})
    status_filter = _clean(raw_fields.get("status_filter"))
    application_nodes = [
        node
        for node in soup.select("button[onclick], a[onclick]")
        if re.fullmatch(
            r"(?:javascript:)?\s*fnApply\(\s*\)\s*;?\s*(?:return\s+false\s*;?)?",
            _clean(node.get("onclick")),
            re.IGNORECASE,
        )
    ]
    service_type = _clean(raw_fields.get("service_type"))
    if status_filter == "apply":
        if not application_nodes and service_type != "festival":
            raise HscityCultureContractError(
                "open culture detail lost application control"
            )
    elif application_nodes:
        raise HscityCultureContractError(
            "scheduled culture detail exposed application control"
        )

    branch = _clean_branch(pairs["운영기관"]) or _clean(row.get("branch"))
    target = pairs["주요대상"] or HSCITY_CULTURE_TARGET_FALLBACK
    period, period_fallback = _period_from_source(
        pairs.get("이용기간", ""),
        _clean(row.get("title")),
        description,
        today=today,
    )
    schedule, schedule_fallback = _schedule_from_source(description)
    row.update(
        {
            "branch": branch,
            "venue_name": pairs["장소"],
            "target": target,
            "fee": pairs["이용료"],
            "period": period,
            "apply_period": pairs["신청기간"],
            "schedule_raw": schedule,
            "capacity": _capacity(pairs["정원수"]),
            "phone": pairs["문의처"],
            "description": description or _clean(row.get("description")),
            "reservation_available": (
                status_filter == "apply" and bool(application_nodes)
            ),
        }
    )
    if row["reservation_available"]:
        row["application_url"] = row["raw_url"]
    else:
        row.pop("application_url", None)
    raw_fields["detail_pairs"] = pairs
    raw_fields["detail_application_controls"] = len(application_nodes)
    raw_fields["list_detail_application_mismatch"] = bool(
        status_filter == "apply" and not application_nodes
    )
    raw_fields["target_fallback"] = (
        "official_source_unspecified"
        if target == HSCITY_CULTURE_TARGET_FALLBACK
        else ""
    )
    raw_fields["period_fallback"] = period_fallback
    raw_fields["schedule_fallback"] = schedule_fallback
    raw_fields["application_form_fetched"] = False


def _local_dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if not identity or identity in seen:
            raise HscityCultureContractError(
                "culture snapshot contains duplicate identities"
            )
        seen.add(identity)
        result.append(row)
    return result


def _today(value: Any) -> date:
    if value is None or value == "":
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(_clean(value))


def collect_hscity_culture(
    target: Any,
    timeout: int = 30,
    max_pages: int = 100,
    detail_limit: int = 1000,
    *,
    today: Any = None,
    fetch_attempts: int = HSCITY_CULTURE_FETCH_ATTEMPTS,
    max_workers: int = HSCITY_CULTURE_MAX_WORKERS,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    sleeper: Sleeper = time.sleep,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    meta: dict[str, Any] = {
        "pages": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "detail_attempts": 0,
        "detail_errors": 0,
        "network_requests": 0,
        "retry_count": 0,
        "source_rows": 0,
        "returned_count": 0,
        "pagination_detected": True,
        "pagination_complete": False,
        "pagination_exhausted": False,
        "list_pagination_complete": False,
        "source_cap_reached": False,
        "fanout_cap_reached": False,
        "snapshot_complete": False,
        "global_union_matches": False,
        "duplicate_count": 0,
        "sentinel_requests": 0,
        "stability_rechecks": 0,
        "scope_counts": {},
        "district_counts": {},
        "status_counts": {},
        "service_type_counts": {},
        "field_counts": {},
        "reservation_available_count": 0,
        "target_fallback_count": 0,
        "period_fallback_count": 0,
        "schedule_fallback_count": 0,
    }
    list_session: Any = None
    try:
        if not is_hscity_culture_target(target):
            raise HscityCultureContractError(
                "target does not match the owned HSCITY culture catalogue"
            )
        if (
            not isinstance(timeout, int)
            or isinstance(timeout, bool)
            or timeout < 1
            or not isinstance(max_pages, int)
            or isinstance(max_pages, bool)
            or max_pages < 1
            or not isinstance(detail_limit, int)
            or isinstance(detail_limit, bool)
            or detail_limit < 0
            or not isinstance(fetch_attempts, int)
            or isinstance(fetch_attempts, bool)
            or fetch_attempts < 1
            or fetch_attempts > 5
            or not isinstance(max_workers, int)
            or isinstance(max_workers, bool)
            or max_workers < 1
            or max_workers > 12
        ):
            raise HscityCultureContractError("invalid collection limits")
        collection_day = _today(today)
        effective_fetcher = fetcher or _default_fetcher
        effective_session_factory = session_factory or _default_session_factory
        budget = _RequestBudget(max_pages)
        list_session = effective_session_factory()
        retry_count = 0
        network_requests = 0
        sentinel_requests = 0
        stability_rechecks = 0

        def fetch_list(url: str) -> BeautifulSoup:
            nonlocal retry_count, network_requests
            soup, retries, attempts_used = _fetch_soup(
                list_session,
                url,
                fetcher=effective_fetcher,
                timeout=timeout,
                attempts=fetch_attempts,
                sleeper=sleeper,
                budget=budget,
            )
            retry_count += retries
            network_requests += attempts_used
            return soup

        def fetch_scope(
            district_code: str,
            status_filter: str,
            *,
            build_rows: bool,
        ) -> dict[str, Any]:
            nonlocal sentinel_requests, stability_rechecks
            scope_name = (
                f"{district_code or 'all'}:{status_filter}"
            )
            first_url = hscity_culture_list_url(
                1, district_code, status_filter
            )
            first_soup = fetch_list(first_url)
            first_cards, total, expected_pages = _page_cards(
                first_soup, page=1
            )
            page_sequences: dict[int, tuple[str, ...]] = {
                1: _identity_sequence(first_cards)
            }
            rows: list[dict[str, Any]] = []
            if build_rows:
                for card in first_cards:
                    _identity, row = _parse_card(
                        target,
                        first_url,
                        card,
                        district_code=district_code,
                        status_filter=status_filter,
                    )
                    rows.append(row)

            for page in range(2, expected_pages + 1):
                current_url = hscity_culture_list_url(
                    page, district_code, status_filter
                )
                soup = fetch_list(current_url)
                cards, _total, _pages = _page_cards(
                    soup,
                    page=page,
                    expected_total=total,
                )
                page_sequences[page] = _identity_sequence(cards)
                if build_rows:
                    for card in cards:
                        _identity, row = _parse_card(
                            target,
                            current_url,
                            card,
                            district_code=district_code,
                            status_filter=status_filter,
                        )
                        rows.append(row)

            identities = tuple(
                identity
                for page in range(1, expected_pages + 1)
                for identity in page_sequences[page]
            )
            if len(identities) != total or len(set(identities)) != total:
                raise HscityCultureContractError(
                    f"culture scope {scope_name} parsed "
                    f"{len(set(identities))} of {total} identities"
                )

            if total:
                sentinel_url = hscity_culture_list_url(
                    expected_pages + 1,
                    district_code,
                    status_filter,
                )
                sentinel_soup = fetch_list(sentinel_url)
                sentinel_requests += 1
                sentinel_cards, _total, _pages = _page_cards(
                    sentinel_soup,
                    page=expected_pages + 1,
                    expected_total=total,
                )
                if sentinel_cards:
                    raise HscityCultureContractError(
                        f"culture scope {scope_name} sentinel was not empty"
                    )

                for page in sorted({1, expected_pages}):
                    recheck_url = hscity_culture_list_url(
                        page, district_code, status_filter
                    )
                    recheck_soup = fetch_list(recheck_url)
                    stability_rechecks += 1
                    recheck_cards, _total, _pages = _page_cards(
                        recheck_soup,
                        page=page,
                        expected_total=total,
                    )
                    if _identity_sequence(recheck_cards) != page_sequences[page]:
                        raise HscityCultureContractError(
                            f"culture scope {scope_name} page {page} "
                            "changed during stable recheck"
                        )
            return {
                "rows": rows,
                "identities": set(identities),
                "total": total,
                "expected_pages": expected_pages,
            }

        candidates: dict[str, dict[str, Any]] = {}
        memberships: dict[str, str] = {}
        scope_counts: dict[str, int] = {}
        duplicate_count = 0
        for district_code in HSCITY_CULTURE_DISTRICTS:
            for status_filter in HSCITY_CULTURE_STATUSES:
                result = fetch_scope(
                    district_code,
                    status_filter,
                    build_rows=True,
                )
                scope_key = f"{district_code}:{status_filter}"
                scope_counts[scope_key] = result["total"]
                for row in result["rows"]:
                    identity = _clean(row.get("provider_course_id"))
                    if identity in candidates:
                        duplicate_count += 1
                        raise HscityCultureContractError(
                            f"culture identity {identity} appeared in "
                            f"{memberships[identity]} and {scope_key}"
                        )
                    candidates[identity] = row
                    memberships[identity] = scope_key

        global_identities: set[str] = set()
        for status_filter in HSCITY_CULTURE_STATUSES:
            result = fetch_scope("", status_filter, build_rows=False)
            scope_key = f"all:{status_filter}"
            scope_counts[scope_key] = result["total"]
            overlap = global_identities.intersection(result["identities"])
            if overlap:
                duplicate_count += len(overlap)
                raise HscityCultureContractError(
                    "global culture status partitions overlapped"
                )
            global_identities.update(result["identities"])

        district_identities = set(candidates)
        if district_identities != global_identities:
            raise HscityCultureContractError(
                "culture district union did not match global current set "
                f"(district={len(district_identities)} "
                f"global={len(global_identities)} "
                f"missing={len(global_identities - district_identities)} "
                f"unexpected={len(district_identities - global_identities)})"
            )

        default_soup = fetch_list(HSCITY_CULTURE_LIST_URL)
        default_cards, default_total, _default_pages = _page_cards(
            default_soup, page=1
        )
        if (
            default_total != len(global_identities)
            or not set(_identity_sequence(default_cards)).issubset(
                global_identities
            )
        ):
            raise HscityCultureContractError(
                "culture default catalogue did not match status partitions"
            )

        rows = list(candidates.values())
        meta.update(
            {
                "source_rows": len(global_identities),
                "scope_counts": scope_counts,
                "duplicate_count": duplicate_count,
                "global_union_matches": True,
                "list_pagination_complete": True,
            }
        )
        if len(rows) > detail_limit:
            meta["source_cap_reached"] = True
            raise HscityCultureContractError(
                f"detail_limit {detail_limit} capped "
                f"{len(rows)} current culture rows"
            )

        detail_results: dict[int, tuple[dict[str, Any], int, int]] = {}
        detail_errors: list[str] = []

        def detail_task(
            index: int,
            listed: dict[str, Any],
        ) -> tuple[int, dict[str, Any], int, int]:
            detail_session = effective_session_factory()
            try:
                soup, retries, attempts_used = _fetch_soup(
                    detail_session,
                    _clean(listed.get("raw_url")),
                    fetcher=effective_fetcher,
                    timeout=timeout,
                    attempts=fetch_attempts,
                    sleeper=sleeper,
                )
                enriched = {
                    **listed,
                    "raw_fields": dict(listed.get("raw_fields") or {}),
                }
                _enrich_detail(enriched, soup, today=collection_day)
                return index, enriched, retries, attempts_used
            finally:
                _close_quietly(detail_session)

        with ThreadPoolExecutor(
            max_workers=min(max_workers, max(1, len(rows)))
        ) as executor:
            futures = {
                executor.submit(detail_task, index, row): (
                    index,
                    _clean(row.get("provider_course_id")),
                )
                for index, row in enumerate(rows)
            }
            for future in as_completed(futures):
                index, identity = futures[future]
                meta["detail_attempts"] += 1
                try:
                    result_index, enriched, retries, attempts_used = (
                        future.result()
                    )
                    detail_results[result_index] = (
                        enriched,
                        retries,
                        attempts_used,
                    )
                except Exception as exc:
                    detail_errors.append(
                        f"{identity}: {type(exc).__name__}: {_clean(exc)}"
                    )

        meta["detail_pages"] = len(futures)
        meta["detail_errors"] = len(detail_errors)
        for _enriched, retries, attempts_used in detail_results.values():
            retry_count += retries
            network_requests += attempts_used
        if detail_errors:
            raise HscityCultureContractError(
                f"detail validation failed for {len(detail_errors)} "
                f"culture rows ({detail_errors[0]})"
            )
        rows = [detail_results[index][0] for index in range(len(rows))]

        local_rows = _local_dedupe(rows)
        if dedupe_rows is not None:
            downstream_rows = list(dedupe_rows(list(local_rows)))
            if (
                len(downstream_rows) != len(local_rows)
                or {
                    _clean(row.get("provider_course_id"))
                    for row in downstream_rows
                }
                != {
                    _clean(row.get("provider_course_id"))
                    for row in local_rows
                }
            ):
                raise HscityCultureContractError(
                    "downstream dedupe changed owned culture snapshot"
                )
            rows = downstream_rows
        else:
            rows = local_rows

        field_counts = {
            "target": sum(bool(_clean(row.get("target"))) for row in rows),
            "fee": sum(bool(_clean(row.get("fee"))) for row in rows),
            "date": sum(bool(_clean(row.get("period"))) for row in rows),
            "place": sum(bool(_clean(row.get("venue_name"))) for row in rows),
            "category": sum(bool(_clean(row.get("category"))) for row in rows),
            "time": sum(bool(_clean(row.get("schedule_raw"))) for row in rows),
            "exact_time": sum(
                bool(re.search(r"\b[0-2]?\d:[0-5]\d\b", _clean(row.get("schedule_raw"))))
                for row in rows
            ),
        }
        if any(count != len(rows) for key, count in field_counts.items() if key != "exact_time"):
            raise HscityCultureContractError(
                "culture required field completeness changed"
            )

        status_counts = Counter(
            _clean((row.get("raw_fields") or {}).get("status_filter"))
            for row in rows
        )
        service_counts = Counter(
            _clean((row.get("raw_fields") or {}).get("service_type"))
            for row in rows
        )
        district_counts = Counter(
            _clean((row.get("raw_fields") or {}).get("district_filter"))
            for row in rows
        )
        meta.update(
            {
                "pages": budget.count,
                "list_requests": budget.count,
                "network_requests": network_requests,
                "retry_count": retry_count,
                "sentinel_requests": sentinel_requests,
                "stability_rechecks": stability_rechecks,
                "returned_count": len(rows),
                "pagination_complete": True,
                "pagination_exhausted": True,
                "snapshot_complete": True,
                "district_counts": dict(sorted(district_counts.items())),
                "status_counts": dict(sorted(status_counts.items())),
                "service_type_counts": dict(sorted(service_counts.items())),
                "field_counts": field_counts,
                "reservation_available_count": sum(
                    bool(row.get("reservation_available")) for row in rows
                ),
                "target_fallback_count": sum(
                    _clean((row.get("raw_fields") or {}).get("target_fallback"))
                    == "official_source_unspecified"
                    for row in rows
                ),
                "period_fallback_count": sum(
                    bool(_clean((row.get("raw_fields") or {}).get("period_fallback")))
                    for row in rows
                ),
                "schedule_fallback_count": sum(
                    bool(_clean((row.get("raw_fields") or {}).get("schedule_fallback")))
                    for row in rows
                ),
                "reservation_discovery_links": sum(
                    bool(_clean(row.get("application_url"))) for row in rows
                ),
                "no_current_data": not rows,
                "no_current_reason": (
                    "official ready/apply partitions returned zero rows"
                    if not rows
                    else ""
                ),
            }
        )
        return rows, HSCITY_CULTURE_PARSER, meta
    except Exception as exc:
        meta.update(
            {
                "pages": (
                    budget.count
                    if "budget" in locals()
                    else int(meta.get("pages") or 0)
                ),
                "list_requests": (
                    budget.count
                    if "budget" in locals()
                    else int(meta.get("list_requests") or 0)
                ),
                "network_requests": (
                    network_requests
                    if "network_requests" in locals()
                    else int(meta.get("network_requests") or 0)
                ),
                "retry_count": (
                    retry_count
                    if "retry_count" in locals()
                    else int(meta.get("retry_count") or 0)
                ),
                "sentinel_requests": (
                    sentinel_requests
                    if "sentinel_requests" in locals()
                    else int(meta.get("sentinel_requests") or 0)
                ),
                "stability_rechecks": (
                    stability_rechecks
                    if "stability_rechecks" in locals()
                    else int(meta.get("stability_rechecks") or 0)
                ),
                "pagination_complete": False,
                "pagination_exhausted": False,
                "snapshot_complete": False,
                "configured_collection_error": (
                    f"{type(exc).__name__}: {_clean(exc)}"
                ),
            }
        )
        if "max_pages cap" in _clean(exc) or "detail_limit" in _clean(exc):
            meta["source_cap_reached"] = True
        return [], HSCITY_CULTURE_PARSER, meta
    finally:
        _close_quietly(list_session)
